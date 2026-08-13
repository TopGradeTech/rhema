# Rhema - live speech transcription and translation, run locally.
# Copyright (C) 2026 Zachary Price
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import speech_recognition as sr
import queue
import time
import re
import struct
import math


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
        while self.listening:
            try:
                self._run_listen_iteration()
            except sr.RequestError as e:
                self.update_status(f"API Error: {e}")
            except Exception as e:
                self.update_status(f"Error: {e}")

    def _run_listen_iteration(self):
        self._service_realtime_stt_restart()
        if not getattr(self, "realtime_stt_active", False):
            self._start_realtime_stt()
        if self._pause_if_needed():
            return
        self._flush_sentence_buffer_if_due()
        time.sleep(0.05)

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

    def _pause_if_needed(self):
        if not self.is_paused:
            return False
        self.update_status("Paused")
        time.sleep(0.2)
        return True

    def _get_selected_device_name(self):
        if (
            self.microphone_index is None
            or not self.devices
            or self.microphone_index >= len(self.devices)
        ):
            return None
        return self.devices[self.microphone_index]

    def _suspend_capture_for_device_scan(self):
        # RealtimeSTT owns its own audio stream, so there is no classic
        # capture thread to suspend anymore. Kept as a named seam for the
        # device-refresh flow in Settings.
        return True

    def _resume_capture_after_device_scan(self):
        self._request_audio_level_stream_restart()

    def _request_audio_level_stream_restart(self):
        self.audio_level_restart_requested = True

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
        source = (self.source_lang or "").strip().lower()
        if source == "auto":
            return ""
        if self.auto_switch_translation:
            return ""
        return self._normalized_source_lang_code()

    def _source_filter_candidate_text(self, text):
        if self.last_stt_pretranslated:
            source_text = (self.last_stt_source_text or "").strip()
            if source_text:
                return source_text
        return (text or "").strip()

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

