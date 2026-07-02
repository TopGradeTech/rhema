import speech_recognition as sr
import tkinter as tk
from tkinter import messagebox
from tkinter import colorchooser
from tkinter import filedialog
from tkinter import font as tkfont
from threading import Thread, Lock, Event
import queue
import time
import re
import requests
import struct
import json
import pyaudio
from collections import deque, Counter
import os
import sys
import traceback
import io
import math
import statistics
import tempfile
import gc
import wave
import importlib.util
import ttkbootstrap as ttkb
from ttkbootstrap.constants import PRIMARY


class AudioCaptureMixin:
    # audioop replacements (audioop removed in Python 3.13)
    def _audio_rms(self, data, sample_width):
        fmt = {1: "b", 2: "h", 4: "i"}.get(int(sample_width), "h")
        n = len(data) // int(sample_width)
        if n == 0:
            return 0
        samples = struct.unpack_from(f"{n}{fmt}", data)
        return int(math.sqrt(sum(s * s for s in samples) / n))

    def _audio_max(self, data, sample_width):
        fmt = {1: "b", 2: "h", 4: "i"}.get(int(sample_width), "h")
        n = len(data) // int(sample_width)
        if n == 0:
            return 0
        samples = struct.unpack_from(f"{n}{fmt}", data)
        return max(abs(s) for s in samples) if samples else 0

    def listen_and_translate(self):
        self._start_capture_thread()
        while self.listening:
            try:
                self._run_listen_iteration()
            except sr.RequestError as e:
                self.update_status(f"API Error: {e}")
            except Exception as e:
                self.update_status(f"Error: {e}")

    def _run_listen_iteration(self):
        engine = (self.speech_engine or "").strip().lower()
        is_kroko = engine == "kroko"
        is_realtime_stt = engine == "realtime-stt"

        if is_kroko:
            if not getattr(self, "kroko_live_active", False):
                self._start_kroko_live_stream()
            elif self._kroko_stream_needs_restart():
                self._stop_kroko_live_stream()
                self._start_kroko_live_stream()
            if self._pause_if_needed():
                return
            self._flush_sentence_buffer_if_due()
            time.sleep(0.05)
            return

        if getattr(self, "kroko_live_active", False):
            self._stop_kroko_live_stream()

        if is_realtime_stt:
            if not getattr(self, "realtime_stt_active", False):
                self._start_realtime_stt()
            if self._pause_if_needed():
                return
            self._flush_sentence_buffer_if_due()
            time.sleep(0.05)
            return

        if getattr(self, "realtime_stt_active", False):
            self._stop_realtime_stt()
            # Give the audio device time to fully release before the
            # speech_recognition capture loop tries to reopen it.
            time.sleep(1.5)
            # Drain any stale audio that built up while RealtimeSTT was active.
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except Exception:
                    break

        if self._pause_if_needed():
            return
        self._flush_sentence_buffer_if_due()
        if self.capture_thread is None or not self.capture_thread.is_alive():
            self._start_capture_thread()
            time.sleep(0.2)
            return
        payload = self._dequeue_audio_payload(timeout=0.1)
        if payload is None:
            return
        audio, capture_meta = self._unpack_audio_payload(payload)
        if audio is None:
            return
        self.process_audio(audio, capture_meta=capture_meta)

    def _dequeue_audio_payload(self, timeout=0.1):
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _unpack_audio_payload(self, payload):
        if isinstance(payload, tuple) and len(payload) == 2:
            audio = payload[0]
            meta = payload[1] if isinstance(payload[1], dict) else {}
            return audio, meta
        return payload, {}

    def _queue_fill_ratio(self, queue_obj):
        try:
            size = int(queue_obj.qsize())
        except Exception:
            size = 0
        maxsize = int(getattr(queue_obj, "maxsize", 0) or 0)
        if maxsize <= 0:
            return size, maxsize, 0.0
        return size, maxsize, (float(size) / float(maxsize))

    def _queue_is_hot(self, queue_obj, ratio):
        _size, maxsize, fill_ratio = self._queue_fill_ratio(queue_obj)
        if maxsize <= 0:
            return False
        try:
            threshold = float(ratio)
        except Exception:
            threshold = 1.0
        threshold = max(0.0, min(1.0, threshold))
        return fill_ratio >= threshold

    def _trim_queue_to_fill_ratio(self, queue_obj, target_ratio):
        _size, maxsize, _fill_ratio = self._queue_fill_ratio(queue_obj)
        if maxsize <= 0:
            return 0
        try:
            ratio = float(target_ratio)
        except Exception:
            ratio = 0.5
        ratio = max(0.0, min(1.0, ratio))
        target_size = max(0, min(maxsize - 1, int(maxsize * ratio)))
        dropped = 0
        while queue_obj.qsize() > target_size:
            try:
                queue_obj.get_nowait()
                dropped += 1
            except queue.Empty:
                break
            except Exception:
                break
        return dropped

    def _maybe_report_queue_backpressure(self, queue_name, queue_obj, action=""):
        now = time.time()
        if now - self.last_queue_backpressure_notice < self.queue_backpressure_notice_interval_sec:
            return
        self.last_queue_backpressure_notice = now
        size, maxsize, fill_ratio = self._queue_fill_ratio(queue_obj)
        percent = int(round(fill_ratio * 100.0)) if maxsize > 0 else 0
        action_text = f"; {action}" if action else ""
        self.update_status(
            f"Queue pressure: {queue_name} {size}/{maxsize} ({percent}%){action_text}"
        )
        self._trace_pipeline(
            "queue_backpressure",
            "",
            queue=queue_name,
            size=size,
            maxsize=maxsize,
            fill_ratio=round(fill_ratio, 3),
            action=action,
        )

    def _effective_loopback_chunk_seconds(self):
        try:
            base_seconds = max(0.35, float(self.loopback_chunk_seconds))
        except Exception:
            base_seconds = 1.0
        if not bool(getattr(self, "dynamic_loopback_chunking", True)):
            return base_seconds
        _size, _maxsize, fill_ratio = self._queue_fill_ratio(self.audio_queue)
        tuned_seconds = base_seconds
        if fill_ratio >= 0.9:
            tuned_seconds = base_seconds * 1.55
        elif fill_ratio >= self.audio_queue_high_water_ratio:
            tuned_seconds = base_seconds * 1.3
        elif fill_ratio <= 0.15:
            tuned_seconds = base_seconds * 0.9
        try:
            min_seconds = max(0.35, float(self.loopback_chunk_seconds_min))
        except Exception:
            min_seconds = 0.65
        try:
            max_seconds = max(min_seconds, float(self.loopback_chunk_seconds_max))
        except Exception:
            max_seconds = 1.85
        tuned_seconds = max(min_seconds, min(max_seconds, tuned_seconds))
        last_seconds = self._last_effective_loopback_chunk_seconds
        self._last_effective_loopback_chunk_seconds = tuned_seconds
        if (
            last_seconds is not None
            and abs(tuned_seconds - last_seconds) >= 0.15
            and time.time() - self._last_chunk_tuning_notice >= 3.0
        ):
            self._last_chunk_tuning_notice = time.time()
            self._trace_pipeline(
                "loopback_chunk_tuned",
                "",
                queue_fill_ratio=round(fill_ratio, 3),
                chunk_seconds=round(tuned_seconds, 3),
                chunk_base_seconds=round(base_seconds, 3),
            )
        return tuned_seconds

    def _record_loopback_chunk_metrics(self, capture_meta, text, overlap_words=0):
        if not self._should_record_loopback_metric(capture_meta):
            return
        chunk_seconds = self._resolve_loopback_metric_chunk_seconds(capture_meta)
        if chunk_seconds <= 0.0:
            return
        stt_ms = self._resolve_loopback_metric_stt_ms()
        if stt_ms is None:
            return
        words = self._count_spoken_words(text)
        if words <= 0:
            return
        confidence = self._resolve_loopback_metric_confidence()
        sample = {
            "chunk_seconds": float(chunk_seconds),
            "stt_ms": float(stt_ms),
            "words": int(words),
            "overlap_words": max(0, int(overlap_words or 0)),
            "confidence": confidence,
            "ts": time.time(),
        }
        self.loopback_chunk_metrics.append(sample)
        self._trace_pipeline(
            "loopback_chunk_metric",
            "",
            chunk_seconds=round(float(chunk_seconds), 3),
            stt_openai_ms=int(stt_ms),
            words=words,
            overlap_words=sample["overlap_words"],
            stt_confidence=confidence,
        )
        self._maybe_autotune_loopback_chunk_seconds()

    def _should_record_loopback_metric(self, capture_meta):
        if (self.speech_engine or "").strip().lower() != "faster-whisper":
            return False
        return bool((capture_meta or {}).get("loopback"))

    def _resolve_loopback_metric_chunk_seconds(self, capture_meta):
        try:
            chunk_seconds = float((capture_meta or {}).get("chunk_seconds") or 0.0)
        except Exception:
            chunk_seconds = 0.0
        if chunk_seconds > 0.0:
            return chunk_seconds
        try:
            return float(self._last_effective_loopback_chunk_seconds or 0.0)
        except Exception:
            return 0.0

    def _resolve_loopback_metric_stt_ms(self):
        stt_ms = self.last_openai_stt_ms
        if isinstance(stt_ms, (int, float)) and stt_ms > 0:
            return float(stt_ms)
        return None

    def _resolve_loopback_metric_confidence(self):
        confidence = self.last_faster_whisper_confidence
        if isinstance(confidence, (int, float)):
            return float(confidence)
        return None

    def _count_spoken_words(self, text):
        return len(re.findall(self.UNICODE_WORD_PATTERN, text or "", flags=re.UNICODE))

    def _suggest_optimal_loopback_chunk_seconds(self):
        samples = list(self.loopback_chunk_metrics)
        if not samples:
            return None, []
        min_samples = max(2, int(self.loopback_chunk_autotune_min_samples))
        buckets = {}
        for sample in samples:
            try:
                bucket = round(float(sample.get("chunk_seconds", 0.0)), 2)
            except Exception:
                continue
            if bucket <= 0.0:
                continue
            buckets.setdefault(bucket, []).append(sample)
        scored = []
        for chunk_seconds, bucket_samples in buckets.items():
            if len(bucket_samples) < min_samples:
                continue
            ms_per_sec = [
                item["stt_ms"] / max(0.25, float(item.get("chunk_seconds", chunk_seconds)))
                for item in bucket_samples
            ]
            overlap_rates = [
                float(item.get("overlap_words", 0)) / max(1, int(item.get("words", 1)))
                for item in bucket_samples
            ]
            words_per_sec = [
                float(item.get("words", 0)) / max(0.25, float(item.get("chunk_seconds", chunk_seconds)))
                for item in bucket_samples
            ]
            confidence_values = [
                float(item["confidence"])
                for item in bucket_samples
                if isinstance(item.get("confidence"), (int, float))
            ]
            median_ms_per_sec = float(statistics.median(ms_per_sec))
            median_overlap_rate = float(statistics.median(overlap_rates))
            median_words_per_sec = float(statistics.median(words_per_sec))
            avg_confidence = (
                sum(confidence_values) / len(confidence_values)
                if confidence_values
                else 0.75
            )
            confidence_penalty = max(0.0, 1.0 - avg_confidence)
            sparse_penalty = 180.0 if median_words_per_sec < 0.8 else 0.0
            score = (
                median_ms_per_sec
                + (median_overlap_rate * 900.0)
                + (confidence_penalty * 450.0)
                + sparse_penalty
            )
            scored.append(
                {
                    "chunk_seconds": chunk_seconds,
                    "score": score,
                    "samples": len(bucket_samples),
                    "stt_ms_per_sec": median_ms_per_sec,
                    "overlap_rate": median_overlap_rate,
                    "avg_confidence": avg_confidence,
                }
            )
        if not scored:
            return None, []
        scored.sort(key=lambda item: (item["score"], -item["samples"]))
        best_chunk_seconds = float(scored[0]["chunk_seconds"])
        summary = [
            {
                "chunk_seconds": item["chunk_seconds"],
                "score": round(item["score"], 1),
                "samples": item["samples"],
                "stt_ms_per_sec": round(item["stt_ms_per_sec"], 1),
                "overlap_rate": round(item["overlap_rate"], 3),
                "avg_confidence": round(item["avg_confidence"], 3),
            }
            for item in scored[:5]
        ]
        return best_chunk_seconds, summary

    def _maybe_autotune_loopback_chunk_seconds(self):
        if not bool(getattr(self, "loopback_chunk_autotune_enabled", True)):
            return
        if not bool(getattr(self, "dynamic_loopback_chunking", True)):
            return
        now = time.time()
        interval_sec = max(2.0, float(self.loopback_chunk_autotune_interval_sec))
        if now - self.loopback_chunk_autotune_last_eval < interval_sec:
            return
        self.loopback_chunk_autotune_last_eval = now
        recommended, summary = self._suggest_optimal_loopback_chunk_seconds()
        if recommended is None:
            return
        try:
            min_seconds = max(0.35, float(self.loopback_chunk_seconds_min))
        except Exception:
            min_seconds = 0.65
        try:
            max_seconds = max(min_seconds, float(self.loopback_chunk_seconds_max))
        except Exception:
            max_seconds = 1.85
        target = max(min_seconds, min(max_seconds, float(recommended)))
        try:
            current = float(self.loopback_chunk_seconds)
        except Exception:
            current = target
        blended = round((current * 0.7) + (target * 0.3), 3)
        blended = max(min_seconds, min(max_seconds, blended))
        if abs(blended - current) < 0.05:
            return
        self.loopback_chunk_seconds = blended
        self._trace_pipeline(
            "loopback_chunk_autotune",
            "",
            recommended_seconds=round(target, 3),
            updated_base_seconds=round(blended, 3),
            ranked_candidates=summary,
        )

    def _pause_if_needed(self):
        if not self.is_paused:
            return False
        self.update_status("Paused")
        try:
            while True:
                self.audio_queue.get_nowait()
        except Exception:
            pass
        self.loopback_tail_raw = b""
        self.transcript_context_words = []
        self.transcript_context_updated_at = 0.0
        time.sleep(0.2)
        return True

    def _resolve_capture_device(self):
        device_name = self._get_selected_device_name()
        if not device_name:
            self.update_status("No audio device selected")
            time.sleep(1)
            return None
        if self.device_types.get(device_name) != "input":
            return self._resolve_loopback_device(device_name)
        return self.device_indices.get(device_name, 0)

    def _get_selected_device_name(self):
        if (
            self.microphone_index is None
            or not self.devices
            or self.microphone_index >= len(self.devices)
        ):
            return None
        return self.devices[self.microphone_index]

    def _resolve_loopback_device(self, device_name):
        if not self.allow_loopback:
            self.update_status("Selected device is output-only (enable loopback)")
            time.sleep(1)
            return None
        loopback_index = self.loopback_output_map.get(device_name)
        if loopback_index is None:
            self.update_status(
                "No loopback input for selected output (enable Stereo Mix or install virtual cable)"
            )
            time.sleep(1)
            return None
        self.update_status("Loopback capture (output)")
        return loopback_index

    def _audio_callback(self, _recognizer, audio, use_loopback=False, chunk_seconds=None):
        if not self.listening or self.is_paused:
            return
        self._capture_audio_level(audio)
        self.no_speech_timeout_count = 0
        payload_meta = {"loopback": bool(use_loopback)}
        if chunk_seconds is not None:
            try:
                payload_meta["chunk_seconds"] = round(float(chunk_seconds), 3)
            except Exception:
                pass
        payload = (audio, payload_meta)
        if self._queue_is_hot(self.audio_queue, self.audio_queue_high_water_ratio):
            dropped = self._trim_queue_to_fill_ratio(
                self.audio_queue, self.audio_queue_relief_ratio
            )
            if dropped > 0:
                self._maybe_report_queue_backpressure(
                    "audio", self.audio_queue, action=f"dropped {dropped} old chunks"
                )
        try:
            self.audio_queue.put_nowait(payload)
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self.audio_queue.put_nowait(payload)
                self._maybe_report_queue_backpressure(
                    "audio", self.audio_queue, action="overflow fallback drop"
                )
            except queue.Full:
                pass

    def _start_capture_thread(self):
        if self.capture_thread is not None and self.capture_thread.is_alive():
            return
        self.capture_thread = Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

    def _suspend_capture_for_device_scan(self, timeout=2.0):
        if self.capture_thread is None or not self.capture_thread.is_alive():
            return True
        self.capture_suspend_event.set()
        self.capture_restart_requested = True
        start = time.time()
        while time.time() - start < timeout:
            if self.capture_suspended_event.is_set():
                return True
            time.sleep(0.05)
        self._log_status("Capture suspend timed out")
        return False

    def _resume_capture_after_device_scan(self):
        self.capture_suspend_event.clear()
        self.capture_suspended_event.clear()
        self._request_capture_restart()
        self._request_audio_level_stream_restart()

    def _request_capture_restart(self):
        now = time.time()
        if now - self.listener_restart_time < self.listener_restart_min_interval:
            return
        self.listener_restart_time = now
        self.capture_restart_requested = True

    def _request_audio_level_stream_restart(self):
        self.audio_level_restart_requested = True

    def _capture_loop(self):
        while self.listening:
            if self._capture_loop_should_wait():
                continue
            device_label = self._get_selected_device_name()
            device_index = self._resolve_capture_device()
            if device_index is None:
                time.sleep(0.2)
                continue
            sample_rate = self.device_sample_rates_by_index.get(device_index, 16000)
            use_loopback = self._capture_should_use_loopback(device_label)
            try:
                self._run_capture_session(
                    device_label,
                    device_index,
                    sample_rate,
                    use_loopback=use_loopback,
                )
            except Exception as exc:
                self.update_status(f"Audio listener error: {exc}")
                time.sleep(0.5)

    def _capture_loop_should_wait(self):
        if getattr(self, "kroko_live_active", False):
            # Kroko streaming opens its own PyAudio stream; suspend the
            # speech_recognition capture loop to avoid mic conflicts.
            time.sleep(0.05)
            return True
        if getattr(self, "realtime_stt_active", False):
            # RealtimeSTT opens its own PyAudio stream internally.
            time.sleep(0.05)
            return True
        if self.capture_suspend_event.is_set():
            self.capture_suspended_event.set()
            time.sleep(0.05)
            return True
        if self.capture_suspended_event.is_set():
            self.capture_suspended_event.clear()
        if self.is_paused:
            time.sleep(0.2)
            return True
        if self.capture_restart_requested:
            self.capture_restart_requested = False
        return False

    def _capture_should_use_loopback(self, device_label):
        return self.device_types.get(device_label) != "input" or self._is_loopback_label(
            device_label
        )

    def _run_capture_session(self, device_label, device_index, sample_rate, use_loopback=False):
        mic = None
        try:
            mic, source = self._open_microphone_source(device_index, sample_rate)
            self._prepare_capture_session(source, use_loopback)
            self._capture_session_loop(source, device_label, use_loopback)
        finally:
            self._close_microphone_source(mic)

    def _prepare_capture_session(self, source, use_loopback):
        if not use_loopback:
            try:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            except Exception:
                pass
        self.loopback_tail_raw = b""
        self.transcript_context_words = []
        self.transcript_context_updated_at = 0.0
        self.update_status(self.STATUS_LISTENING)

    def _capture_session_loop(self, source, device_label, use_loopback):
        while self.listening and not self.is_paused:
            if self._capture_session_should_stop(device_label):
                return
            audio, loopback_duration, should_break = self._read_capture_audio_chunk(
                source, use_loopback
            )
            if should_break:
                return
            if audio is None:
                continue
            self._audio_callback(
                self.recognizer,
                audio,
                use_loopback=use_loopback,
                chunk_seconds=loopback_duration if use_loopback else None,
            )

    def _capture_session_should_stop(self, device_label):
        if self.capture_suspend_event.is_set():
            return True
        if self.capture_restart_requested:
            self.capture_restart_requested = False
            return True
        return self._get_selected_device_name() != device_label

    def _read_capture_audio_chunk(self, source, use_loopback):
        loopback_duration = None
        try:
            if use_loopback:
                loopback_duration = self._effective_loopback_chunk_seconds()
                audio = self.recognizer.record(source, duration=loopback_duration)
            else:
                audio = self.recognizer.listen(
                    source, timeout=1, phrase_time_limit=self.phrase_time_limit
                )
            return audio, loopback_duration, False
        except sr.WaitTimeoutError:
            self._note_no_speech_timeout()
            return None, None, False
        except OSError as exc:
            self.update_status(f"Audio device error: {exc}")
            return None, None, True
        except Exception as exc:
            self.update_status(f"Audio error: {exc}")
            return None, None, True

    def _note_no_speech_timeout(self):
        self.no_speech_timeout_count += 1
        if self.no_speech_timeout_count >= 2:
            self.transcript_context_words = []
            self.transcript_context_updated_at = 0.0
        if self.no_speech_timeout_count < 6:
            return
        now = time.time()
        if now - self.last_no_speech_notice < 5.0:
            return
        self.last_no_speech_notice = now
        self.update_status("No speech detected. Check mic level or device.")

    def _note_unknown_speech(self):
        self.unknown_speech_count += 1
        if self.unknown_speech_count < 3:
            return
        now = time.time()
        if now - self.last_unknown_notice < 5.0:
            return
        self.last_unknown_notice = now
        self.update_status("Heard audio but could not recognize speech.")

    def _reset_speech_counters(self):
        self.no_speech_timeout_count = 0
        self.unknown_speech_count = 0

    def _recognize_audio(self, audio):
        engine = (self.speech_engine or "").strip().lower()

        # Pre-load faster-whisper model before recognition so the lock in
        # _apply_api_vars is never blocked waiting on model initialization.
        if engine == "faster-whisper":
            try:
                self._get_faster_whisper_model()
            except Exception:
                pass

        # All engines run outside recognition_lock. Each engine snapshots the
        # settings it needs at the top of its call, so mid-call Apply changes
        # take effect cleanly on the next chunk with no UI freeze.
        return self._recognize_audio_unlocked(audio, engine)

    def _recognize_audio_unlocked(self, audio, engine):
        """Run I/O-bound engines outside recognition_lock to keep Apply responsive."""
        try:
            text, out_engine = self._recognize_audio_core(audio)
            return self._finalize_recognized_audio(text, out_engine)
        except sr.UnknownValueError:
            self._note_unknown_speech()
            self._trace_pipeline("stt_unknown_value", "", engine=engine)
            return ""
        except Exception as exc:
            self.update_status(f"Speech error: {exc}")
            self._trace_pipeline("stt_error", "", engine=engine, error=str(exc))
            return ""

    def _recognize_audio_locked(self, audio):
        try:
            text, engine = self._recognize_audio_core(audio)
            return self._finalize_recognized_audio(text, engine)
        except sr.UnknownValueError:
            self._note_unknown_speech()
            self._trace_pipeline(
                "stt_unknown_value",
                "",
                engine=(self.speech_engine or "openai"),
            )
            return ""
        except Exception as exc:
            self.update_status(f"Speech error: {exc}")
            self._trace_pipeline(
                "stt_error",
                "",
                engine=(self.speech_engine or "openai"),
                error=str(exc),
            )
            return ""

    def _recognize_audio_core(self, audio):
        engine = (self.speech_engine or "openai").lower()
        self._reset_recognition_state()
        raw_text, stt_ms = self._run_stt_engine(audio, engine)
        self.last_openai_stt_ms = stt_ms
        if engine in ("faster-whisper", "omnilingual-sidecar", "kroko"):
            # Keep local/cloud-passthrough output raw so we can evaluate baseline behavior.
            self._trace_pipeline("stt_raw", raw_text, engine=engine, stt_openai_ms=stt_ms)
            text = self._coerce_text(raw_text).strip()
            self._trace_pipeline("stt_passthrough", text, engine=engine, stt_openai_ms=stt_ms)
            return text, engine
        text = self._apply_stt_text_policy(
            raw_text,
            engine,
            stt_ms,
        )
        return text, engine

    def _finalize_recognized_audio(self, text, engine):
        if not text:
            self._note_empty_recognition(engine)
            return ""
        if engine == "omnilingual-sidecar":
            return self._finalize_omnilingual_sidecar_text(text, engine)
        if engine == "faster-whisper":
            return self._finalize_faster_whisper_text(text, engine)
        if engine == "kroko":
            return self._finalize_kroko_text(text, engine)
        if self._source_filter_blocks_recognized_text(text, engine=engine):
            return ""
        self._reset_speech_counters()
        return text

    def _finalize_kroko_text(self, text, engine):
        cleaned_text, stripped_noise = self._sanitize_faster_whisper_output(text)
        if not cleaned_text:
            self._trace_pipeline("stt_kroko_noise_suppressed", text)
            self._note_unknown_speech()
            return ""
        if stripped_noise and cleaned_text != text:
            self._trace_pipeline("stt_kroko_noise_trimmed", cleaned_text)
        if self._source_filter_blocks_recognized_text(cleaned_text, engine=engine):
            return ""
        self._log_transcribed_text(
            cleaned_text,
            engine="kroko",
            mode="single_pass_source",
            source_lang=self.kroko_language_code,
            selected=True,
            selected_output_pretranslated=False,
        )
        self._reset_speech_counters()
        return cleaned_text

    def _note_empty_recognition(self, engine):
        if engine in ("faster-whisper", "omnilingual-sidecar", "kroko"):
            self._note_unknown_speech()

    def _finalize_omnilingual_sidecar_text(self, text, engine):
        cleaned_text, stripped_noise = self._sanitize_faster_whisper_output(text)
        if not cleaned_text:
            self._trace_pipeline(
                "stt_omnilingual_sidecar_noise_suppressed",
                text,
            )
            self._note_unknown_speech()
            return ""
        if stripped_noise and cleaned_text != text:
            self._trace_pipeline(
                "stt_omnilingual_sidecar_noise_trimmed",
                cleaned_text,
            )
        if self._source_filter_blocks_recognized_text(cleaned_text, engine=engine):
            return ""
        self._log_transcribed_text(
            cleaned_text,
            engine="omnilingual-sidecar",
            mode="single_pass_source",
            source_lang=(
                self._normalized_omnilingual_sidecar_language_code()
                or self.omnilingual_sidecar_language
            ),
            selected=True,
            selected_output_pretranslated=False,
        )
        self._reset_speech_counters()
        return cleaned_text

    def _finalize_faster_whisper_text(self, text, engine):
        cleaned_text, stripped_noise = self._sanitize_faster_whisper_output(text)
        if not cleaned_text:
            self._trace_pipeline(
                "stt_faster_whisper_noise_suppressed",
                text,
                stt_confidence=self.last_faster_whisper_confidence,
            )
            self._note_unknown_speech()
            return ""
        if stripped_noise and cleaned_text != text:
            self._trace_pipeline(
                "stt_faster_whisper_noise_trimmed",
                cleaned_text,
                stt_confidence=self.last_faster_whisper_confidence,
            )
        if self._is_probable_gratitude_hallucination(cleaned_text):
            self._trace_pipeline(
                "stt_gratitude_hallucination_suppressed",
                cleaned_text,
                stt_confidence=self.last_faster_whisper_confidence,
            )
            self._note_unknown_speech()
            return ""
        if self._source_filter_blocks_recognized_text(cleaned_text, engine=engine):
            return ""
        self._reset_speech_counters()
        return cleaned_text

    def _sanitize_faster_whisper_output(self, text):
        stripped, stripped_noise = self._strip_known_stt_edge_noise(text)
        sanitized = self._sanitize_model_text(stripped)
        return sanitized, stripped_noise

    def _strip_known_stt_edge_noise(self, text):
        cleaned = (text or "").strip()
        if not cleaned:
            return "", False
        stripped_noise = False
        while cleaned:
            prior = cleaned
            for pattern in self.STT_EDGE_NOISE_PREFIX_PATTERNS:
                cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
            for pattern in self.STT_EDGE_NOISE_SUFFIX_PATTERNS:
                cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(self.TRAILING_EDGE_PUNCTUATION_PATTERN, "", cleaned).strip()
            if cleaned == prior:
                break
            stripped_noise = True
        return cleaned, stripped_noise

    def _is_probable_gratitude_hallucination(self, text):
        normalized = re.sub(
            self.NON_WORD_PATTERN,
            " ",
            (text or "").lower(),
            flags=re.UNICODE,
        ).strip()
        if normalized not in self.GRATITUDE_SHORT_PHRASES:
            return False
        try:
            confidence = float(self.last_faster_whisper_confidence)
        except Exception:
            return False
        token_count = len(normalized.split())
        suppress_threshold = 0.80 if token_count <= 2 else 0.70
        return confidence < suppress_threshold

    def _source_filter_blocks_recognized_text(self, text, engine=""):
        filter_text = self._source_filter_candidate_text(text)
        if filter_text:
            self._update_auto_detect_language(filter_text)
        expected = self._source_language_filter_expected(engine)
        if not expected:
            return False
        if self._passes_source_language_filter(filter_text, expected=expected):
            return False
        self._trace_pipeline(
            "stt_source_lang_filtered",
            text,
            source_lang=expected,
            app_source_lang=(self.source_lang or "").strip().lower(),
            speech_engine=(engine or self.speech_engine or "").strip().lower(),
            filter_text=filter_text,
            pretranslated=bool(self.last_stt_pretranslated),
        )
        return True

    def _source_language_filter_expected(self, engine=""):
        if self.last_stt_pretranslated and not (self.last_stt_source_text or "").strip():
            return ""
        engine = (engine or self.speech_engine or "").strip().lower()
        if engine == "omnilingual-sidecar":
            return self._normalized_omnilingual_sidecar_language_code()
        if self.translation_enabled and self._active_text_translation_provider() == "local_nllb":
            nllb_expected = self._nllb_source_language_filter_expected()
            if nllb_expected:
                return nllb_expected
        source = (self.source_lang or "").strip().lower()
        if source == "auto":
            return ""
        if self.auto_switch_translation:
            return ""
        return self._normalized_source_lang_code()

    def _nllb_source_language_filter_expected(self):
        # An explicit (non-auto) Local NLLB source language reflects what the
        # user is actually speaking, which can differ from the legacy
        # es->en-only self.source_lang default and would otherwise cause
        # legitimate speech to be filtered out as a hallucination.
        configured = (self.local_nllb_source_lang or "").strip().lower()
        if configured in (
            "",
            "auto",
            "selected",
            "selected_source",
            self.LOCAL_NLLB_DEFAULT_SOURCE_LANG.lower(),
        ):
            return ""
        try:
            resolved = self._resolve_local_nllb_source_lang()
        except ValueError:
            return ""
        return self._nllb_code_to_two_letter(resolved)

    def _reset_recognition_state(self):
        self.last_openai_translate_ms = None
        self.last_stt_pretranslated = False
        self.last_stt_source_text = ""
        self.last_stt_source_lang = ""
        self.last_stt_source_lang_confidence = None
        self.last_faster_whisper_confidence = None

    def _run_stt_engine(self, audio, engine):
        if engine == "faster-whisper":
            stt_started = time.time()
            raw_text, pretranslated = self.recognize_faster_whisper(audio)
            self.last_stt_pretranslated = bool(pretranslated)
            stt_ms = int((time.time() - stt_started) * 1000)
            return raw_text, stt_ms
        if engine == "omnilingual-sidecar":
            stt_started = time.time()
            raw_text = self.recognize_omnilingual_sidecar(audio)
            self.last_stt_pretranslated = False
            stt_ms = int((time.time() - stt_started) * 1000)
            return raw_text, stt_ms
        if engine == "kroko":
            stt_started = time.time()
            raw_text = self.recognize_kroko(audio)
            self.last_stt_pretranslated = False
            stt_ms = int((time.time() - stt_started) * 1000)
            return raw_text, stt_ms
        if not self.openai_api_key:
            raise ValueError(self.OPENAI_API_KEY_EMPTY_MESSAGE)
        if self._should_use_whisper_direct_translation():
            raw_text, stt_ms = self.recognize_openai_whisper_translation(
                audio, self.openai_api_key
            )
            self.last_stt_pretranslated = True
            return raw_text, stt_ms
        return self.recognize_openai_whisper(audio, self.openai_api_key)

    def _coerce_text(self, value):
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)

    def _apply_stt_text_policy(
        self,
        raw_text,
        engine,
        stt_ms,
    ):
        self._trace_pipeline("stt_raw", raw_text, engine=engine, stt_openai_ms=stt_ms)
        text = self._sanitize_model_text(raw_text)
        self._trace_pipeline(
            "stt_sanitized",
            text,
            engine=engine,
            stt_openai_ms=stt_ms,
        )
        if not self.last_stt_pretranslated:
            self._log_transcribed_text(
                text,
                engine=engine,
                mode="single_pass_source",
                stt_openai_ms=stt_ms,
                source_lang=self._maybe_openai_language(),
                selected=True,
                selected_output_pretranslated=False,
            )
        return text

    def _should_apply_source_language_filter(self):
        if self.last_stt_pretranslated and not (self.last_stt_source_text or "").strip():
            return False
        source = (self.source_lang or "").strip().lower()
        if source == "auto":
            return False
        if self.auto_switch_translation:
            return False
        return True

    def _source_filter_candidate_text(self, text):
        if self.last_stt_pretranslated:
            source_text = (self.last_stt_source_text or "").strip()
            if source_text:
                return source_text
        return (text or "").strip()

    def _set_last_stt_source_language_info(self, info):
        self.last_stt_source_lang = self._normalize_lang_code(
            getattr(info, "language", ""),
            default="",
            allow_auto=False,
        )
        probability = getattr(info, "language_probability", None)
        try:
            probability = float(probability)
        except Exception:
            probability = None
        if probability is not None:
            if not math.isfinite(probability):
                probability = None
            else:
                probability = max(0.0, min(1.0, probability))
        self.last_stt_source_lang_confidence = probability

    def _detected_source_language_from_stt(self, text=""):
        lang = (self.last_stt_source_lang or "").strip().lower()
        if lang not in ("en", "es"):
            return ""
        confidence = self.last_stt_source_lang_confidence
        if confidence is None or confidence >= 0.55:
            return lang
        heuristic = self._detect_language_from_text(text)
        if heuristic == lang:
            return lang
        return ""

