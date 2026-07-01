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
        self._realtime_stt_last_start = 0.0        # for retry cooldown
        self._realtime_stt_pending_interim = ""    # latest unrendered interim text
        self._realtime_stt_interim_after_id = None # pending debounce timer
        self._realtime_stt_prev_update_text = ""   # tracks previous partial for silence logic
        # Persisted settings
        self.realtime_stt_final_model = "large-v3"
        self.realtime_stt_realtime_model = "tiny"
        self.realtime_stt_silero_sensitivity = 0.4
        self.realtime_stt_post_speech_silence = 0.6
        self.realtime_stt_min_recording_length = 0.5

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
        self._realtime_stt_recorder = None
        if recorder:
            try:
                recorder.abort()   # unblock the recorder.text() call in the worker
            except Exception:
                pass
            try:
                recorder.shutdown()  # kill multiprocessing worker processes
            except Exception:
                pass
        try:
            self.root.after(0, lambda: self._realtime_stt_set_live_line(""))
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Worker                                                                #
    # ------------------------------------------------------------------ #

    _SENTENCE_END_MARKS = frozenset('.!?。')
    _SILENCE_MID_SENTENCE = 2.0   # text ends with ... — speaker still going
    _SILENCE_SENTENCE_END = 0.45  # clear sentence end punctuation — commit fast
    _SILENCE_UNKNOWN = 0.7        # everything else — middle ground

    def _realtime_stt_recorder_kwargs(self, on_update, on_interim):
        """Build the AudioToTextRecorder constructor kwargs from current settings."""
        lang = self._normalized_source_lang_code()
        # int8 compute type: RealtimeSTT loads the realtime model on CPU even
        # when device=cuda; float16 is unsupported on CPU ctranslate2 backends.
        # enable_realtime_transcription is always True so on_update fires for
        # dynamic silence adjustment regardless of whether interim display is on.
        kwargs = {
            "model": self.realtime_stt_final_model,
            "realtime_model_type": self.realtime_stt_realtime_model,
            "enable_realtime_transcription": True,
            "on_realtime_transcription_update": on_update,
            "on_realtime_transcription_stabilized": on_interim,
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
        return kwargs

    def _realtime_stt_run_loop(self, recorder):
        """Consume transcriptions from recorder until the stop event fires."""
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
                    0, lambda t=text: self._realtime_stt_queue_interim(t)
                )
            except Exception:
                pass

        def on_update(text):
            self._realtime_stt_adjust_silence(text)
            if not self.realtime_stt_active:
                return
            try:
                self.root.after(0, lambda t=text: self._realtime_stt_queue_interim(t))
            except Exception:
                pass

        try:
            recorder = AudioToTextRecorder(
                **self._realtime_stt_recorder_kwargs(on_update, on_interim_stabilized)
            )
            self._realtime_stt_recorder = recorder
            self._realtime_stt_run_loop(recorder)
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

    def _realtime_stt_adjust_silence(self, text):
        """Dynamically tune post-speech silence on every partial update.

        Mirrors the three-tier logic from the RealtimeSTT demo:
          - ends with '...'  → 2.0 s  (mid-sentence, keep waiting)
          - double sentence-end punctuation → 0.45 s  (commit fast)
          - everything else  → 0.7 s  (middle ground)
        """
        rec = self._realtime_stt_recorder
        if not rec or not self.realtime_stt_active:
            return
        text = text.lstrip().lstrip(".")
        if text:
            text = text[0].upper() + text[1:]
        prev = self._realtime_stt_prev_update_text
        self._realtime_stt_prev_update_text = text
        if text.endswith("..."):
            rec.post_speech_silence_duration = self._SILENCE_MID_SENTENCE
        elif text and text[-1] in self._SENTENCE_END_MARKS and prev and prev[-1] in self._SENTENCE_END_MARKS:
            rec.post_speech_silence_duration = self._SILENCE_SENTENCE_END
        else:
            rec.post_speech_silence_duration = self._SILENCE_UNKNOWN

    def _realtime_stt_set_live_line(self, text):
        """Direct live-line update — used for clears only."""
        self.live_line = text
        self.render_text()

    def _realtime_stt_queue_interim(self, text):
        """Debounced live-line update for interim text (called on main thread)."""
        self._realtime_stt_pending_interim = text
        if self._realtime_stt_interim_after_id is not None:
            try:
                self.root.after_cancel(self._realtime_stt_interim_after_id)
            except Exception:
                pass
        self._realtime_stt_interim_after_id = self.root.after(
            100, self._realtime_stt_flush_interim
        )

    def _realtime_stt_flush_interim(self):
        self._realtime_stt_interim_after_id = None
        self.live_line = self._realtime_stt_pending_interim
        self.render_text()

    def _on_realtime_stt_final(self, text):
        text = (text or "").strip()
        if not text:
            return
        self._trace_pipeline("realtime_stt_final", text)
        try:
            self.root.after(0, lambda t=text: self._realtime_stt_show(t))
        except Exception:
            pass

    def _realtime_stt_show(self, text):
        """Put a RealtimeSTT final directly on screen — no processing whatsoever."""
        self.live_line = ""
        self.translations.append(text)
        self._trim_translation_history()
        self.render_text()
