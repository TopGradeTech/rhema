import time
from threading import Thread, Event

_REALTIME_STT_RETRY_DELAY = 5.0   # seconds to wait after a failure before retrying


class RealtimeSttMixin:
    """Integration with the RealtimeSTT library for low-latency local transcription.

    RealtimeSTT owns audio capture, dual VAD, and model scheduling entirely.
    We provide two seams:
      - Interim (stabilized) text  → live_line (word-by-word visual feedback)
      - Final text                 → translation/display pipeline (unchanged)

    The normal speech_recognition capture loop is suspended while active so
    both paths never compete for the microphone.
    """

    def _realtime_stt_defaults(self):
        self.realtime_stt_active = False
        self._realtime_stt_stop_event = Event()
        self._realtime_stt_thread = None
        self._realtime_stt_recorder = None
        self._realtime_stt_last_start = 0.0   # for retry cooldown
        # Persisted settings
        self.realtime_stt_final_model = "large-v3"
        self.realtime_stt_realtime_model = "tiny"
        self.realtime_stt_silero_sensitivity = 0.4
        self.realtime_stt_post_speech_silence = 0.6
        self.realtime_stt_min_recording_length = 0.5
        self.realtime_stt_enable_interim = True

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def _start_realtime_stt(self):
        if self.realtime_stt_active:
            return
        # Cooldown: don't restart within 5 s of the last attempt so a
        # repeated failure doesn't spam the log and consume CPU.
        if time.time() - self._realtime_stt_last_start < _REALTIME_STT_RETRY_DELAY:
            return
        self._realtime_stt_last_start = time.time()
        self.realtime_stt_active = True
        self._realtime_stt_stop_event.clear()
        self._realtime_stt_thread = Thread(
            target=self._realtime_stt_worker, daemon=True
        )
        self._realtime_stt_thread.start()

    def _stop_realtime_stt(self):
        if not self.realtime_stt_active:
            return
        self.realtime_stt_active = False
        self._realtime_stt_stop_event.set()
        recorder = self._realtime_stt_recorder
        if recorder:
            try:
                recorder.stop()
            except Exception:
                pass
        try:
            self.root.after(0, lambda: self._realtime_stt_set_live_line(""))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Worker                                                                #
    # ------------------------------------------------------------------ #

    def _realtime_stt_worker(self):
        try:
            from RealtimeSTT import AudioToTextRecorder
        except ImportError:
            self.update_status(
                "RealtimeSTT not installed. Run: pip install RealtimeSTT"
            )
            self.realtime_stt_active = False
            return

        def on_interim_stabilized(text):
            if not self.realtime_stt_active:
                return
            try:
                self.root.after(
                    0, lambda t=text: self._realtime_stt_set_live_line(t)
                )
            except Exception:
                pass

        lang = self._normalized_source_lang_code()
        # Use int8 compute type: RealtimeSTT loads the realtime model on CPU
        # even when device=cuda, and float16 is not supported on CPU backends.
        # int8 works on both CPU and GPU and is fast enough for the tiny model.
        kwargs = {
            "model": self.realtime_stt_final_model,
            "realtime_model_type": self.realtime_stt_realtime_model,
            "enable_realtime_transcription": bool(self.realtime_stt_enable_interim),
            "on_realtime_transcription_stabilized": (
                on_interim_stabilized if self.realtime_stt_enable_interim else None
            ),
            "device": self.faster_whisper_device,
            "compute_type": "int8",
            "silero_sensitivity": float(self.realtime_stt_silero_sensitivity),
            "post_speech_silence_duration": float(self.realtime_stt_post_speech_silence),
            "min_length_of_recording": float(self.realtime_stt_min_recording_length),
        }
        if lang and len(lang) == 2 and lang.isalpha():
            kwargs["language"] = lang
        if self.microphone_index is not None:
            kwargs["input_device_index"] = self.microphone_index

        try:
            recorder = AudioToTextRecorder(**kwargs)
            self._realtime_stt_recorder = recorder
            self._trace_pipeline(
                "realtime_stt_started", "",
                final_model=self.realtime_stt_final_model,
                realtime_model=self.realtime_stt_realtime_model,
                device=self.faster_whisper_device,
            )
            try:
                self.update_status("RealtimeSTT ready")
            except Exception:
                pass

            while not self._realtime_stt_stop_event.is_set():
                try:
                    text = recorder.text()
                except Exception:
                    break
                if text and not self._realtime_stt_stop_event.is_set():
                    self._on_realtime_stt_final(text)
        except Exception as exc:
            self._trace_pipeline("realtime_stt_error", "", error=str(exc))
            try:
                self.update_status(f"RealtimeSTT error: {exc}")
            except Exception:
                pass  # root may be shutting down
        finally:
            self._realtime_stt_recorder = None
            self.realtime_stt_active = False

    # ------------------------------------------------------------------ #
    # Text handlers                                                         #
    # ------------------------------------------------------------------ #

    def _realtime_stt_set_live_line(self, text):
        self.live_line = text
        self.render_text()

    def _on_realtime_stt_final(self, text):
        text = (text or "").strip()
        if not text or getattr(self, "is_paused", False):
            return
        # Clear live line now that we have a committed final
        try:
            self.root.after(0, lambda: self._realtime_stt_set_live_line(""))
        except Exception:
            pass
        # Light sanitization — remove hallucination patterns
        try:
            cleaned, _ = self._sanitize_faster_whisper_output(text)
        except Exception:
            cleaned = text
        if not cleaned:
            return
        if self._source_filter_blocks_recognized_text(cleaned, engine="realtime-stt"):
            return
        self._log_transcribed_text(
            cleaned,
            engine="realtime-stt",
            mode="single_pass_source",
            source_lang=self._normalized_source_lang_code(),
            selected=True,
            selected_output_pretranslated=False,
        )
        self._reset_speech_counters()
        self._trace_pipeline("realtime_stt_final", cleaned)
        # Feed directly into sentence pipeline — no boundary trim needed since
        # RealtimeSTT produces clean non-overlapping segments.
        self._enqueue_flushed_sentences_from_buffer(cleaned, overlap_words=0)
