import json
import time
from threading import Thread, Event, Lock


class KrokoStreamingMixin:
    """Live-streaming transcription via Kroko's streaming WebSocket API.

    One persistent WebSocket connection per session:
      - Send thread: PyAudio → 100ms float32 chunks → WebSocket
      - Recv thread: WebSocket → partial (live line) / final (sentence queue)

    The normal speech_recognition capture loop is suspended while streaming
    is active so both paths don't compete for the microphone.
    """

    def _kroko_stream_defaults(self):
        self.kroko_live_active = False
        self.kroko_live_ws = None
        self.kroko_live_ws_lock = Lock()
        self._kroko_stop_event = Event()
        self._kroko_send_thread = None
        self._kroko_recv_thread = None
        self._kroko_stream_device_index = None
        self._kroko_stream_api_key = ""
        self._kroko_stream_language = ""

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def _start_kroko_live_stream(self):
        if self.kroko_live_active:
            return
        api_key = (self.kroko_api_key or "").strip()
        if not api_key:
            self.update_status("Kroko API key is empty. Enter it in Settings.")
            return
        self.kroko_live_active = True
        self._kroko_stop_event.clear()
        self._kroko_stream_api_key = api_key
        self._kroko_stream_language = (self.kroko_language_code or "en-US").strip()
        self._kroko_stream_device_index = self.microphone_index
        self._kroko_recv_thread = Thread(target=self._kroko_recv_worker, daemon=True)
        self._kroko_send_thread = Thread(target=self._kroko_send_worker, daemon=True)
        self._kroko_recv_thread.start()
        self._kroko_send_thread.start()
        self._trace_pipeline("stt_kroko_stream_started", "",
                             language=self._kroko_stream_language)

    def _stop_kroko_live_stream(self):
        if not self.kroko_live_active:
            return
        self.kroko_live_active = False
        self._kroko_stop_event.set()
        with self.kroko_live_ws_lock:
            ws = self.kroko_live_ws
            self.kroko_live_ws = None
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        try:
            self.root.after(0, lambda: self._kroko_set_live_line(""))
        except Exception:
            pass
        self._trace_pipeline("stt_kroko_stream_stopped", "")

    def _kroko_stream_needs_restart(self):
        """True when settings changed in a way that requires a fresh connection."""
        return (
            self._kroko_stream_api_key != (self.kroko_api_key or "").strip()
            or self._kroko_stream_language != (self.kroko_language_code or "en-US").strip()
            or self._kroko_stream_device_index != self.microphone_index
        )

    # ------------------------------------------------------------------ #
    # WebSocket connection                                                  #
    # ------------------------------------------------------------------ #

    def _kroko_open_ws(self):
        try:
            import websocket
            from urllib.parse import urlencode
        except ImportError:
            self.update_status("websocket-client not installed: pip install websocket-client")
            return None
        api_key = (self.kroko_api_key or "").strip()
        language_code = (self.kroko_language_code or "en-US").strip()
        if not api_key:
            return None
        try:
            qs = urlencode({"apiKey": api_key, "languageCode": language_code})
        except Exception:
            qs = f"apiKey={api_key}&languageCode={language_code}"
        url = f"wss://app.kroko.ai/api/v1/transcripts/streaming?{qs}"
        try:
            ws = websocket.WebSocket()
            ws.connect(url, timeout=10)
            ws.settimeout(60)
            connected = ws.recv()
            self._trace_pipeline(
                "stt_kroko_stream_connected", "",
                raw=connected if isinstance(connected, str) else repr(connected),
                language=language_code,
            )
            return ws
        except Exception as exc:
            self._trace_pipeline("stt_kroko_stream_connect_error", "", error=str(exc))
            self.update_status(f"Kroko connection failed: {exc}")
            return None

    # ------------------------------------------------------------------ #
    # Send worker — PyAudio → WebSocket                                    #
    # ------------------------------------------------------------------ #

    def _kroko_send_worker(self):
        import pyaudio
        SAMPLE_RATE = 16000
        CHUNK_FRAMES = 1600  # 100 ms

        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                input_device_index=self._kroko_stream_device_index,
                frames_per_buffer=CHUNK_FRAMES,
            )
            while not self._kroko_stop_event.is_set():
                with self.kroko_live_ws_lock:
                    ws = self.kroko_live_ws
                if not ws:
                    time.sleep(0.05)
                    continue
                try:
                    data = stream.read(CHUNK_FRAMES, exception_on_overflow=False)
                    ws.send_binary(data)
                except Exception as exc:
                    self._trace_pipeline("stt_kroko_send_error", "", error=str(exc))
                    with self.kroko_live_ws_lock:
                        if self.kroko_live_ws is ws:
                            self.kroko_live_ws = None
        except Exception as exc:
            self._trace_pipeline("stt_kroko_send_worker_error", "", error=str(exc))
            self.update_status(f"Kroko audio capture error: {exc}")
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            pa.terminate()

    # ------------------------------------------------------------------ #
    # Recv worker — WebSocket → partial/final                              #
    # ------------------------------------------------------------------ #

    def _kroko_recv_worker(self):
        while not self._kroko_stop_event.is_set():
            with self.kroko_live_ws_lock:
                ws = self.kroko_live_ws
            if not ws:
                ws = self._kroko_open_ws()
                if ws:
                    with self.kroko_live_ws_lock:
                        self.kroko_live_ws = ws
                else:
                    time.sleep(2)
                    continue
            try:
                msg = ws.recv()
                if not msg:
                    continue
                raw = msg if isinstance(msg, str) else msg.decode("utf-8", errors="replace")
                payload = json.loads(raw)
                msg_type = payload.get("type", "")
                text = (payload.get("text") or "").strip()
                self._trace_pipeline("stt_kroko_stream_msg", text, msg_type=msg_type)
                if msg_type == "partial":
                    self._on_kroko_partial(text)
                elif msg_type == "final":
                    self._on_kroko_final(text)
            except json.JSONDecodeError:
                pass
            except Exception as exc:
                if self._kroko_stop_event.is_set():
                    break
                self._trace_pipeline("stt_kroko_recv_error", "", error=str(exc))
                with self.kroko_live_ws_lock:
                    if self.kroko_live_ws is ws:
                        self.kroko_live_ws = None
                time.sleep(1)

    # ------------------------------------------------------------------ #
    # Partial and final handlers                                           #
    # ------------------------------------------------------------------ #

    def _on_kroko_partial(self, text):
        # Partials cause rapid screen redraws; only finals are displayed.
        self._trace_pipeline("stt_kroko_stream_partial", text)

    def _on_kroko_final(self, text):
        if not text or getattr(self, "is_paused", False):
            return
        try:
            cleaned, _ = self._sanitize_faster_whisper_output(text)
        except Exception:
            cleaned = text.strip()
        if not cleaned:
            return
        if self._source_filter_blocks_recognized_text(cleaned, engine="kroko"):
            return
        self._log_transcribed_text(
            cleaned,
            engine="kroko",
            mode="live_stream",
            source_lang=self.kroko_language_code,
            selected=True,
            selected_output_pretranslated=False,
        )
        self._reset_speech_counters()
        cleaned, overlap = self._trim_repeated_boundary_words(cleaned)
        if not cleaned:
            return
        self._enqueue_flushed_sentences_from_buffer(cleaned, overlap_words=overlap)

    def _kroko_set_live_line(self, text):
        """Update the live partial line — must be called on the main thread."""
        self.live_line = text
        self.render_text()
