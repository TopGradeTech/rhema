import time
from threading import Thread, Event

_REALTIME_STT_RETRY_DELAY = 5.0   # seconds to wait after a failure before retrying


class RealtimeSttMixin:
    """Integration with the RealtimeSTT library for low-latency local transcription.

    RealtimeSTT owns audio capture, dual VAD, and model scheduling entirely.
    Only finalized text is ever shown — no live word-by-word preview, since
    the display had to fully re-wrap/reflow on every partial update, which
    read as jerky.

    The normal speech_recognition capture loop is suspended while active so
    both paths never compete for the microphone.
    """

    def _realtime_stt_defaults(self):
        self.realtime_stt_active = False
        self._realtime_stt_stop_event = Event()
        self._realtime_stt_thread = None
        self._realtime_stt_recorder = None
        self._realtime_stt_last_start = 0.0        # for retry cooldown
        self._realtime_stt_prev_update_text = ""   # tracks previous partial for silence logic
        self._realtime_stt_restart_requested = False  # serviced by _run_listen_iteration
        # Persisted settings
        self.realtime_stt_final_model = "large-v3"
        self.realtime_stt_realtime_model = "tiny"
        self.realtime_stt_silero_sensitivity = 0.4
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

    def _request_capture_restart(self):
        """Ask the capture loop to rebuild the recorder with fresh settings
        (device index, STT model, etc.), which RealtimeSTT only reads at
        construction time. The actual stop/start happens on the capture
        thread via _service_realtime_stt_restart, never inline here, so
        callers on the Tk main thread (e.g. the device_var trace) don't
        block on RealtimeSTT's teardown, which can take several seconds.
        """
        self._realtime_stt_last_start = 0.0  # bypass the failure-retry cooldown
        self._realtime_stt_restart_requested = True

    def _service_realtime_stt_restart(self):
        """Perform a pending restart. Must only be called from the capture
        thread (_run_listen_iteration) so stop-then-start stays sequential
        and never races the self-healing rebuild against an in-progress
        teardown.
        """
        if self._realtime_stt_restart_requested:
            self._realtime_stt_restart_requested = False
            self._stop_realtime_stt()

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
    _SILENCE_INITIAL = 0.6        # constructor value, before the first dynamic adjustment
    _SILENCE_MID_SENTENCE = 2.0   # text ends with ... — speaker still going
    _SILENCE_SENTENCE_END = 0.45  # clear sentence end punctuation — commit fast
    _SILENCE_UNKNOWN = 0.7        # everything else — middle ground
    # Continuous speech never satisfies the silence thresholds above, so
    # without a cap an uninterrupted stretch accumulates into one giant
    # utterance that only finalizes (and floods the display) when the
    # speaker finally pauses. As the current utterance grows, first relax
    # the silence requirement so natural micro-pauses start committing
    # finals, then force a split outright via recorder.stop(), which
    # finalizes what's recorded so far through the normal pipeline while
    # listening resumes immediately (min_gap_between_recordings is 0).
    _SPLIT_SOFT_SEC = 6.0    # halve silence thresholds beyond this
    _SPLIT_HARD_SEC = 12.0   # force a split at the next sentence-end partial
    _SPLIT_MAX_SEC = 18.0    # force a split even mid-sentence

    def _resolve_stt_device(self, device):
        """Resolve auto/cuda/cpu to an actually-usable device.

        Mirrors _resolve_local_nllb_device (translation_mixin.py): "cuda" is
        only ever handed to the recorder if CUDA is actually available, so a
        non-NVIDIA machine (AMD GPU, CPU-only, missing drivers) doesn't spin
        forever retrying a construction that will never succeed.
        """
        device = self._normalize_stt_device(device)
        if device == "cpu":
            return "cpu"
        try:
            import torch
            cuda_available = bool(torch.cuda.is_available())
        except Exception:
            cuda_available = False
        return "cuda" if cuda_available else "cpu"

    def _realtime_stt_recorder_kwargs(self, on_update):
        """Build the AudioToTextRecorder constructor kwargs from current settings."""
        # Must agree with _source_language_filter_expected(), which gates the
        # final text this recorder produces. If Whisper is told a different
        # language than the filter expects (e.g. legacy self.source_lang="es"
        # while Local NLLB is configured for English), it mis-transcribes
        # speech into the wrong language and the filter then silently drops
        # that segment as a language mismatch -- losing real speech.
        lang = self._source_language_filter_expected(engine="realtime-stt") or (
            self._normalized_source_lang_code()
        )
        # int8 compute type: RealtimeSTT loads the realtime model on CPU even
        # when device=cuda; float16 is unsupported on CPU ctranslate2 backends.
        # enable_realtime_transcription is always True so on_update fires for
        # dynamic silence adjustment even though its text is never displayed.
        kwargs = {
            "model": self.realtime_stt_final_model,
            "realtime_model_type": self.realtime_stt_realtime_model,
            "enable_realtime_transcription": True,
            "on_realtime_transcription_update": on_update,
            "device": self._resolve_stt_device(self.stt_device),
            "compute_type": "int8",
            "silero_sensitivity": float(self.realtime_stt_silero_sensitivity),
            "post_speech_silence_duration": self._SILENCE_INITIAL,
            "min_length_of_recording": float(self.realtime_stt_min_recording_length),
            # spinner=False: RealtimeSTT's own "speak now"/"recording"/
            # "transcribing" spinner writes straight to the terminal. Route
            # the same state transitions through the app's Status bar instead
            # via the on_vad_detect_start/on_recording_start/
            # on_transcription_start callbacks below.
            "spinner": False,
            "on_vad_detect_start": self._realtime_stt_on_listening,
            "on_recording_start": self._realtime_stt_on_recording_start,
            "on_transcription_start": self._realtime_stt_on_transcription_start,
        }
        if lang and len(lang) == 2 and lang.isalpha():
            kwargs["language"] = lang
        device_name = self._get_selected_device_name()
        real_device_index = self.device_indices.get(device_name) if device_name else None
        if real_device_index is not None:
            kwargs["input_device_index"] = real_device_index
        return kwargs

    def _realtime_stt_on_listening(self):
        """Fires when RealtimeSTT starts waiting for voice (was "speak now")."""
        try:
            self.update_status(self.STATUS_LISTENING)
        except Exception:
            pass

    def _realtime_stt_on_recording_start(self):
        try:
            self.update_status("Recording speech...")
        except Exception:
            pass

    def _realtime_stt_on_transcription_start(self, _audio):
        try:
            self.update_status("Transcribing...")
        except Exception:
            pass
        return None  # falsy: must not abort RealtimeSTT's transcription

    def _mark_startup_stt_ready(self):
        """Signal that RealtimeSTT's initial load has reached a terminal
        state (ready, failed to import, or failed to construct), so the
        startup loading overlay (settings_ui_mixin.py) can drop its half of
        the readiness gate.
        """
        if self.startup_stt_ready:
            return
        self.startup_stt_ready = True
        try:
            self.root.after(0, self._check_startup_ready)
        except Exception:
            pass

    def _realtime_stt_run_loop(self, recorder):
        """Consume transcriptions from recorder until the stop event fires."""
        self._trace_pipeline(
            "realtime_stt_started", "",
            final_model=self.realtime_stt_final_model,
            realtime_model=self.realtime_stt_realtime_model,
            device=self._resolve_stt_device(self.stt_device),
        )
        try:
            self.update_status("RealtimeSTT ready")
        except Exception:
            pass
        self._mark_startup_stt_ready()
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
            self._mark_startup_stt_ready()
            return

        def on_update(text):
            self._realtime_stt_adjust_silence(text)

        try:
            recorder = AudioToTextRecorder(
                **self._realtime_stt_recorder_kwargs(on_update)
            )
            self._realtime_stt_recorder = recorder
            self._realtime_stt_run_loop(recorder)
        except Exception as exc:
            self._trace_pipeline("realtime_stt_error", "", error=str(exc))
            try:
                self.update_status(f"RealtimeSTT error: {exc}")
            except Exception:
                pass  # root may be shutting down
            self._mark_startup_stt_ready()
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

        On top of that, long-running utterances are split so continuous
        speech doesn't buffer indefinitely (see the _SPLIT_* constants):
        past the soft cap all three tiers are halved, and past the hard
        cap the recording is stopped outright — preferring a partial that
        ends at a sentence boundary, but unconditionally at the max cap.
        """
        rec = self._realtime_stt_recorder
        if not rec or not self.realtime_stt_active:
            return
        text = text.lstrip().lstrip(".")
        if text:
            text = text[0].upper() + text[1:]
        prev = self._realtime_stt_prev_update_text
        self._realtime_stt_prev_update_text = text
        elapsed = self._realtime_stt_recording_elapsed(rec)
        if self._realtime_stt_force_split_if_due(rec, text, elapsed):
            return
        scale = 0.5 if elapsed >= self._SPLIT_SOFT_SEC else 1.0
        if text.endswith("..."):
            rec.post_speech_silence_duration = self._SILENCE_MID_SENTENCE * scale
        elif text and text[-1] in self._SENTENCE_END_MARKS and prev and prev[-1] in self._SENTENCE_END_MARKS:
            rec.post_speech_silence_duration = self._SILENCE_SENTENCE_END * scale
        else:
            rec.post_speech_silence_duration = self._SILENCE_UNKNOWN * scale

    def _realtime_stt_recording_elapsed(self, rec):
        """Seconds the current utterance has been recording, 0 if idle."""
        try:
            if not getattr(rec, "is_recording", False):
                return 0.0
            start = float(getattr(rec, "recording_start_time", 0.0) or 0.0)
            if start <= 0.0:
                return 0.0
            return max(0.0, time.time() - start)
        except Exception:
            return 0.0

    def _realtime_stt_force_split_if_due(self, rec, text, elapsed):
        """Force-finalize an over-long utterance mid-speech.

        recorder.stop() snapshots the recorded frames and queues them for
        final transcription through the normal recorder.text() path, then
        listening resumes immediately, so the split is invisible except
        that the text arrives as two finals instead of one giant one.
        """
        if elapsed < self._SPLIT_HARD_SEC:
            return False
        at_sentence_end = bool(text) and text[-1] in self._SENTENCE_END_MARKS
        if not at_sentence_end and elapsed < self._SPLIT_MAX_SEC:
            return False
        try:
            rec.stop()
        except Exception:
            return False
        self._trace_pipeline(
            "realtime_stt_forced_split",
            text,
            elapsed_sec=round(elapsed, 2),
            at_sentence_end=at_sentence_end,
        )
        return True

    def _realtime_stt_set_live_line(self, text):
        """Direct live-line update — used for clears only."""
        self.live_line = text
        self.render_text()

    def _on_realtime_stt_final(self, text):
        text = (text or "").strip()
        if not text:
            return
        cleaned, _ = self._sanitize_faster_whisper_output(text)
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
        if self.translation_enabled:
            self._enqueue_flushed_sentences_from_buffer(cleaned, overlap_words=0)
        else:
            try:
                self.root.after(0, lambda t=cleaned: self._realtime_stt_show(t))
            except Exception:
                pass

    def _realtime_stt_show(self, text):
        """Direct commit to display — used when translation is off. Large
        multi-sentence finals are metered out sentence-by-sentence instead
        of dumping at once (see _meter_display_commit)."""
        self.live_line = ""
        self._meter_display_commit(text, stage="realtime_stt_commit")
