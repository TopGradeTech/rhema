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

import queue
import time
import re

from languages import WHISPER_LANGUAGE_NAMES


class TranscriptionMixin:
    def _enqueue_flushed_sentences_from_buffer(self, text, capture_meta=None, overlap_words=0):
        flushed = self._append_sentence_buffer(text)
        if not flushed:
            return
        for sentence_payload in flushed:
            sentence, pretranslated, source_text = self._unpack_buffered_sentence_payload(
                sentence_payload
            )
            self._enqueue_sentence(
                sentence,
                pretranslated=pretranslated,
                source_text=source_text,
                capture_meta=capture_meta,
                overlap_words=overlap_words,
            )

    def _unpack_buffered_sentence_payload(self, payload):
        if isinstance(payload, tuple):
            return (
                payload[0],
                bool(payload[1]) if len(payload) > 1 else False,
                payload[2] if len(payload) > 2 else "",
            )
        return payload, False, ""

    def _auto_detect_enabled(self):
        if self.auto_switch_translation:
            return True
        return (self.source_lang or "").strip().lower() == "auto"

    def _normalized_source_lang_code(self):
        lang = (self.source_lang or "").strip().lower()
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if "_" in lang:
            lang = lang.split("_", 1)[0]
        return lang

    def _passes_source_language_filter(self, text, expected=None):
        if not text:
            return False
        expected = expected or self._normalized_source_lang_code()
        detected_from_stt = self._detected_source_language_from_stt(text)
        if expected in ("en", "es") and detected_from_stt in ("en", "es"):
            return detected_from_stt == expected
        locked_lang = self._locked_auto_detect_language()
        if locked_lang:
            return self._passes_locked_auto_detect_filter(text, locked_lang)
        # Strict filtering is currently implemented only for EN/ES where we
        # have dedicated lightweight detection heuristics.
        return self._passes_expected_source_language(text, expected)

    def _locked_auto_detect_language(self):
        if not self._auto_detect_enabled():
            return ""
        locked = (self.auto_detect_lang or "").strip().lower()
        if locked in ("en", "es"):
            return locked
        return ""

    def _passes_locked_auto_detect_filter(self, text, locked_lang):
        detected = self._detect_language_from_text(text)
        if detected and detected in self.auto_detect_langs:
            return detected == locked_lang
        tokens = re.findall(self.UNICODE_WORD_PATTERN, text.lower(), flags=re.UNICODE)
        if not tokens:
            return True
        return self._passes_locked_language_heuristics(text, tokens, locked_lang)

    def _passes_locked_language_heuristics(self, text, tokens, locked_lang):
        token_set = set(tokens)
        if locked_lang == "es":
            return self._passes_locked_spanish_heuristics(text, tokens, token_set)
        if locked_lang == "en":
            return self._passes_locked_english_heuristics(text, tokens, token_set)
        return True

    def _passes_locked_spanish_heuristics(self, text, tokens, token_set):
        if re.search(self.SPANISH_DIACRITIC_PATTERN, text.lower()):
            return True
        if token_set & self.spanish_common_words:
            return True
        if token_set & self.english_common_words:
            return False
        return len(tokens) < 3

    def _passes_locked_english_heuristics(self, text, tokens, token_set):
        if re.search(self.SPANISH_DIACRITIC_PATTERN, text.lower()):
            return False
        if token_set & self.english_common_words:
            return True
        if token_set & self.spanish_common_words:
            return False
        return len(tokens) < 3

    def _passes_expected_source_language(self, text, expected):
        if expected not in ("en", "es"):
            return True
        detected = self._detect_language_from_text(text)
        if not detected:
            return True
        return detected == expected

    def _detect_language_from_text(self, text):
        if not text:
            return None
        sample = text.lower()
        tokens = re.findall(self.SPANISH_WORD_PATTERN, sample)
        if not tokens:
            return None
        es_score = sum(1 for token in tokens if token in self.spanish_common_words)
        en_score = sum(1 for token in tokens if token in self.english_common_words)
        if re.search(self.SPANISH_DIACRITIC_PATTERN, sample):
            es_score += 2
        if es_score >= en_score + 2 and es_score >= 2:
            return "es"
        if en_score >= es_score + 2 and en_score >= 2:
            return "en"
        return None

    def _update_auto_detect_language(self, text):
        if not self._auto_detect_enabled():
            return
        detected = self._detect_language_from_text(text)
        if not detected or detected not in self.auto_detect_langs:
            return
        if detected == self.auto_detect_streak_lang:
            self.auto_detect_streak_count += 1
        else:
            self.auto_detect_streak_lang = detected
            self.auto_detect_streak_count = 1
        if self.auto_detect_streak_count >= 2 and detected != self.auto_detect_lang:
            self.auto_detect_lang = detected

    def _contains_url_like_text(self, text):
        sample = (text or "").strip().lower()
        if not sample:
            return False
        if re.search(self.URL_SCHEME_PATTERN, sample):
            return True
        # Bare domains (e.g., example.com) without scheme.
        return any(
            match.group(0).rsplit(".", 1)[-1] in self.COMMON_DOMAIN_SUFFIXES
            for match in re.finditer(self.BARE_DOMAIN_PATTERN, sample)
        )

    def _effective_sentence_max_chars(self):
        # Smaller cap when the live interim row is off: without it, a
        # finalized chunk is the only feedback the viewer gets, so run-on
        # speech without punctuation shouldn't sit in the buffer as long
        # before something appears (see sentence_max_chars_no_interim).
        if getattr(self, "show_interim_text", False):
            return self.sentence_max_chars
        return self.sentence_max_chars_no_interim

    def _append_sentence_buffer(self, text):
        text = text.strip()
        if not text:
            return []
        incoming_pretranslated = bool(self.last_stt_pretranslated)
        incoming_source_text = self._current_sentence_source_text(
            text,
            pretranslated=incoming_pretranslated,
        )
        with self.sentence_lock:
            if self.sentence_buffer:
                self.sentence_buffer = f"{self.sentence_buffer} {text}"
                self.sentence_buffer_source_text = self._append_text_fragment(
                    self.sentence_buffer_source_text,
                    incoming_source_text,
                )
                self.sentence_buffer_pretranslated = (
                    bool(self.sentence_buffer_pretranslated) and incoming_pretranslated
                )
            else:
                self.sentence_buffer = text
                self.sentence_buffer_pretranslated = incoming_pretranslated
                self.sentence_buffer_source_text = incoming_source_text
            self.sentence_last_update = time.time()
            buffer_text = self.sentence_buffer.strip()
            has_terminal_punctuation = bool(
                re.search(self.TERMINAL_PUNCTUATION_PATTERN, buffer_text)
            )
            if (
                len(buffer_text) >= self._effective_sentence_max_chars()
                or (
                    has_terminal_punctuation
                    and not self._is_likely_sentence_fragment(buffer_text)
                )
            ):
                flush_reason = "max_chars"
                if has_terminal_punctuation:
                    flush_reason = "terminal_punctuation"
                self._trace_pipeline(
                    "sentence_buffer_flush",
                    buffer_text,
                    reason=flush_reason,
                    chars=len(buffer_text),
                )
                pretranslated = bool(self.sentence_buffer_pretranslated)
                source_text = self.sentence_buffer_source_text.strip()
                self.sentence_buffer = ""
                self.sentence_buffer_pretranslated = False
                self.sentence_buffer_source_text = ""
                return [(buffer_text, pretranslated, source_text)]
        return []

    def _current_sentence_source_text(self, text, pretranslated=False):
        source_text = (self.last_stt_source_text or "").strip()
        if source_text:
            return source_text
        if not bool(pretranslated):
            return (text or "").strip()
        return ""

    def _append_text_fragment(self, existing, fragment):
        existing = (existing or "").strip()
        fragment = (fragment or "").strip()
        if existing and fragment:
            return f"{existing} {fragment}"
        return existing or fragment

    def _is_likely_sentence_fragment(self, text):
        text = (text or "").strip()
        if not text:
            return False
        words = re.findall(self.UNICODE_WORD_PATTERN, text, flags=re.UNICODE)
        if not words:
            return False
        word_count = len(words)
        has_terminal = bool(re.search(self.TERMINAL_PUNCTUATION_PATTERN, text))
        if re.search(r"[,;:][\"')\\]]*$", text):
            return True
        first_letter = re.search(self.UNICODE_LETTER_PATTERN, text, flags=re.UNICODE)
        starts_lower = bool(first_letter and first_letter.group(0).islower())
        if has_terminal and starts_lower and word_count <= 6:
            return True
        # Treat only very short lowercase snippets as likely fragments.
        # This keeps noun phrases like "Hombre llamado George" responsive.
        if not has_terminal and starts_lower and word_count <= 5:
            return True
        if not has_terminal and word_count <= 2:
            return True
        return False

    def _flush_sentence_buffer_if_due(self):
        if not self.sentence_buffer:
            return
        age_ms = (time.time() - self.sentence_last_update) * 1000
        if age_ms < self.sentence_flush_ms:
            return
        fragment_grace_ms = max(0, int(self.sentence_fragment_grace_ms))
        min_timeout_words = max(1, int(self.sentence_timeout_min_words))
        if self._queue_is_hot(self.sentence_queue, 0.35):
            # Under capture/translation pressure, flush partials sooner to reduce visible lag.
            fragment_grace_ms = min(fragment_grace_ms, 120)
            min_timeout_words = min(min_timeout_words, 2)
        with self.sentence_lock:
            if not self.sentence_buffer:
                return
            buffer_text = self.sentence_buffer.strip()
            buffer_pretranslated = bool(self.sentence_buffer_pretranslated)
            buffer_source_text = self.sentence_buffer_source_text.strip()
            words = re.findall(self.UNICODE_WORD_PATTERN, buffer_text, flags=re.UNICODE)
            word_count = len(words)
            has_terminal = bool(
                re.search(self.TERMINAL_PUNCTUATION_PATTERN, buffer_text)
            )
            if (
                self._is_likely_sentence_fragment(buffer_text)
                and age_ms < (self.sentence_flush_ms + fragment_grace_ms)
            ):
                self._trace_pipeline(
                    "sentence_buffer_timeout_defer",
                    buffer_text,
                    wait_ms=self.sentence_flush_ms,
                    age_ms=int(age_ms),
                    grace_ms=fragment_grace_ms,
                    reason="likely_fragment",
                )
                return
            if (
                word_count < min_timeout_words
                and not has_terminal
                and age_ms < (self.sentence_flush_ms + fragment_grace_ms)
            ):
                self._trace_pipeline(
                    "sentence_buffer_timeout_defer",
                    buffer_text,
                    wait_ms=self.sentence_flush_ms,
                    age_ms=int(age_ms),
                    grace_ms=fragment_grace_ms,
                    min_words=min_timeout_words,
                    word_count=word_count,
                    reason="too_short",
                )
                return
            self.sentence_buffer = ""
            self.sentence_buffer_pretranslated = False
            self.sentence_buffer_source_text = ""
        self._trace_pipeline(
            "sentence_buffer_timeout_flush",
            buffer_text,
            wait_ms=self.sentence_flush_ms,
        )
        self._enqueue_sentence(
            buffer_text,
            pretranslated=buffer_pretranslated,
            source_text=buffer_source_text,
        )

    def _enqueue_sentence(
        self,
        text,
        pretranslated=False,
        source_text="",
        capture_meta=None,
        overlap_words=0,
    ):
        if not text:
            return
        payload = self._build_sentence_payload(
            text,
            pretranslated=pretranslated,
            source_text=source_text,
            capture_meta=capture_meta,
            overlap_words=overlap_words,
        )
        if self._queue_is_hot(self.sentence_queue, self.sentence_queue_high_water_ratio):
            self._maybe_report_queue_backpressure(
                "sentence", self.sentence_queue, action="translation backlog"
            )
        if self._enqueue_sentence_payload(
            payload,
            text,
            pretranslated=pretranslated,
            stage="sentence_enqueued",
        ):
            return
        dropped = self._drop_sentence_queue_items_for_retry()
        if self._enqueue_sentence_payload(
            payload,
            text,
            pretranslated=pretranslated,
            stage="sentence_enqueued_after_drop",
            dropped_count=dropped,
        ):
            self._maybe_report_queue_backpressure(
                "sentence", self.sentence_queue, action=f"overflow fallback drop {dropped}"
            )

    def _build_sentence_payload(
        self,
        text,
        pretranslated=False,
        source_text="",
        capture_meta=None,
        overlap_words=0,
    ):
        payload = {
            "text": text,
            "queued_at": time.time(),
            "pretranslated": bool(pretranslated),
            "stt_confidence": self.last_faster_whisper_confidence,
            "overlap_words": max(0, int(overlap_words or 0)),
        }
        source_text = (source_text or "").strip()
        if source_text:
            payload["source_text"] = source_text
        try:
            chunk_seconds = float((capture_meta or {}).get("chunk_seconds") or 0.0)
        except Exception:
            chunk_seconds = 0.0
        if chunk_seconds > 0.0:
            payload["chunk_seconds"] = round(chunk_seconds, 3)
        return payload

    def _enqueue_sentence_payload(
        self,
        payload,
        text,
        pretranslated=False,
        stage="sentence_enqueued",
        dropped_count=None,
    ):
        try:
            self.sentence_queue.put_nowait(payload)
        except queue.Full:
            return False
        trace_meta = {
            "queue_size": self.sentence_queue.qsize(),
            "pretranslated": bool(pretranslated),
            "stt_confidence": self.last_faster_whisper_confidence,
            "chunk_seconds": payload.get("chunk_seconds"),
            "overlap_words": payload.get("overlap_words"),
        }
        if dropped_count is not None:
            trace_meta["dropped_count"] = dropped_count
        self._trace_pipeline(stage, text, **trace_meta)
        return True

    def _drop_sentence_queue_items_for_retry(self):
        dropped = self._trim_queue_to_fill_ratio(
            self.sentence_queue, self.sentence_queue_relief_ratio
        )
        if dropped > 0:
            return dropped
        try:
            self.sentence_queue.get_nowait()
            return 1
        except queue.Empty:
            return 0

    def _collect_translation_batch(self, sentence, started_at, latency_meta):
        sentence = (sentence or "").strip()
        if not sentence:
            return "", started_at, latency_meta
        if not self.translation_enabled:
            # Keep disabled mode strictly one-in/one-out so no mixed-state batch
            # can leak translated payloads around toggle transitions.
            return sentence, started_at, latency_meta
        merged_items = self._gather_translation_batch_items(
            sentence,
            started_at,
            latency_meta,
        )
        if len(merged_items) == 1:
            return sentence, started_at, latency_meta
        merged_text = " ".join(item[0] for item in merged_items if item[0]).strip()
        merged_started_at = self._earliest_batch_started_at(merged_items, started_at)
        merged_meta = self._aggregate_translation_batch_meta(merged_items, latency_meta)
        self._trace_pipeline(
            "sentence_batch_merge",
            merged_text,
            batch_items=len(merged_items),
            queue_size=self.sentence_queue.qsize(),
        )
        self._maybe_report_queue_backpressure(
            "sentence", self.sentence_queue, action=f"batched {len(merged_items)} items"
        )
        return merged_text, merged_started_at, merged_meta

    def _gather_translation_batch_items(self, sentence, started_at, latency_meta):
        merged_items = [(sentence, started_at, dict(latency_meta or {}))]
        if not self._queue_is_hot(self.sentence_queue, self.sentence_queue_high_water_ratio):
            return merged_items
        max_batch = max(2, int(self.translation_backlog_batch_max))
        while len(merged_items) < max_batch:
            if not self._queue_is_hot(self.sentence_queue, self.sentence_queue_relief_ratio):
                break
            next_item = self._dequeue_translation_batch_item()
            if next_item is None:
                break
            merged_items.append(next_item)
        return merged_items

    def _dequeue_translation_batch_item(self):
        try:
            payload = self.sentence_queue.get_nowait()
        except queue.Empty:
            return None
        next_sentence, next_started, next_meta = self._unpack_sentence_payload(payload)
        next_sentence = (next_sentence or "").strip()
        if not next_sentence:
            return None
        return next_sentence, next_started, dict(next_meta or {})

    def _earliest_batch_started_at(self, merged_items, default_started_at):
        started_candidates = [
            item[1] for item in merged_items if isinstance(item[1], (int, float))
        ]
        if started_candidates:
            return min(started_candidates)
        return default_started_at

    def _aggregate_translation_batch_meta(self, merged_items, latency_meta):
        merged_meta = dict(latency_meta or {})
        merged_meta["batched_items"] = len(merged_items)
        item_meta = [meta for _text, _started, meta in merged_items]
        pretranslated_values = [bool(meta.get("pretranslated")) for meta in item_meta]
        translate_values = [
            int(meta.get("translate_nllb_ms"))
            for meta in item_meta
            if isinstance(meta.get("translate_nllb_ms"), (int, float))
            and meta.get("translate_nllb_ms") >= 0
        ]
        confidence_values = [
            float(meta.get("stt_confidence"))
            for meta in item_meta
            if isinstance(meta.get("stt_confidence"), (int, float))
        ]
        chunk_seconds_values = [
            float(meta.get("chunk_seconds"))
            for meta in item_meta
            if isinstance(meta.get("chunk_seconds"), (int, float))
        ]
        overlap_values = [
            int(meta.get("overlap_words"))
            for meta in item_meta
            if isinstance(meta.get("overlap_words"), (int, float))
        ]
        if pretranslated_values:
            merged_meta["pretranslated"] = all(pretranslated_values)
        if translate_values:
            merged_meta["translate_nllb_ms"] = max(translate_values)
        if confidence_values:
            merged_meta["stt_confidence"] = sum(confidence_values) / len(confidence_values)
        source_text_values = [
            str(meta.get("source_text")).strip()
            for meta in item_meta
            if str(meta.get("source_text") or "").strip()
        ]
        if source_text_values:
            merged_meta["source_text"] = " ".join(source_text_values)
        if chunk_seconds_values:
            merged_meta["chunk_seconds"] = max(chunk_seconds_values)
        if overlap_values:
            merged_meta["overlap_words"] = max(overlap_values)
        return merged_meta

    def _enqueue_finalized_output(self, text, latency_meta=None):
        output_text = (text or "").strip()
        if not output_text:
            return
        output_meta = dict(latency_meta or {})
        output_meta.setdefault("stt_confidence", self.last_faster_whisper_confidence)
        payload = {
            "text": output_text,
            "latency_meta": output_meta,
        }
        self._log_finalized_sentence(
            output_text,
            translation_enabled=bool(self.translation_enabled),
            translate_nllb_ms=output_meta.get("translate_nllb_ms"),
            stt_confidence=output_meta.get("stt_confidence"),
            pretranslated=bool(output_meta.get("pretranslated")),
        )
        if self._queue_is_hot(
            self.finalized_output_queue, self.finalized_output_queue_high_water_ratio
        ):
            self._maybe_report_queue_backpressure(
                "finalized_output",
                self.finalized_output_queue,
                action="display backlog",
            )
        try:
            self.finalized_output_queue.put_nowait(payload)
            self._trace_pipeline(
                "finalized_output_enqueued",
                output_text,
                queue_size=self.finalized_output_queue.qsize(),
            )
            return
        except queue.Full:
            dropped = self._trim_queue_to_fill_ratio(
                self.finalized_output_queue,
                self.finalized_output_queue_relief_ratio,
            )
            if dropped <= 0:
                try:
                    self.finalized_output_queue.get_nowait()
                    dropped = 1
                except queue.Empty:
                    dropped = 0
        try:
            self.finalized_output_queue.put_nowait(payload)
            self._trace_pipeline(
                "finalized_output_enqueued_after_drop",
                output_text,
                queue_size=self.finalized_output_queue.qsize(),
                dropped_count=dropped,
            )
            self._maybe_report_queue_backpressure(
                "finalized_output",
                self.finalized_output_queue,
                action=f"overflow fallback drop {dropped}",
            )
        except Exception:
            pass

    def _unpack_finalized_output_payload(self, payload):
        if isinstance(payload, dict):
            return payload.get("text", ""), payload.get("latency_meta", {})
        if isinstance(payload, tuple):
            if len(payload) == 2:
                text, meta = payload
                return text, meta if isinstance(meta, dict) else {}
        return payload, {}

    def _display_worker(self):
        while self.listening:
            try:
                payload = self.finalized_output_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            text, latency_meta = self._unpack_finalized_output_payload(payload)
            if not text:
                continue
            try:
                self.update_text(text, latency_meta=latency_meta)
            except Exception:
                pass

    def _clear_translation_backlog_after_disable(self):
        dropped_sentences = 0
        with self.sentence_lock:
            self.sentence_buffer = ""
            self.sentence_buffer_pretranslated = False
            self.sentence_buffer_source_text = ""
            self.sentence_last_update = 0.0
        while True:
            try:
                self.sentence_queue.get_nowait()
                dropped_sentences += 1
            except queue.Empty:
                break
            except Exception:
                break
        dropped_display = len(self.display_drip_queue)
        self.display_drip_queue.clear()
        if self.display_drip_after_id is not None:
            try:
                self.root.after_cancel(self.display_drip_after_id)
            except Exception:
                pass
            self.display_drip_after_id = None
        dropped_finalized = 0
        while True:
            try:
                self.finalized_output_queue.get_nowait()
                dropped_finalized += 1
            except queue.Empty:
                break
            except Exception:
                break
        self.live_line = ""
        self.last_stt_pretranslated = False
        self._trace_pipeline(
            "translation_disabled_backlog_cleared",
            "",
            dropped_sentences=dropped_sentences,
            dropped_display=dropped_display,
            dropped_finalized=dropped_finalized,
        )

    def _translation_worker(self):
        while self.listening:
            try:
                payload = self.sentence_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            sentence, started_at, latency_meta = self._unpack_sentence_payload(payload)
            if not sentence:
                continue
            sentence, started_at, latency_meta = self._collect_translation_batch(
                sentence, started_at, latency_meta
            )
            if not sentence:
                continue
            self._translate_and_display(sentence, started_at, latency_meta)

    def _effective_source_lang(self):
        lang = (self.source_lang or "").strip().lower()
        if self._auto_detect_enabled():
            auto_lang = (self.auto_detect_lang or "").strip().lower()
            if auto_lang in self.auto_detect_langs:
                return auto_lang
            return "auto"
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if "_" in lang:
            lang = lang.split("_", 1)[0]
        return lang

    def _effective_target_lang(self):
        target = (self.target_lang or "").strip().lower()
        if "-" in target:
            target = target.split("-", 1)[0]
        if "_" in target:
            target = target.split("_", 1)[0]
        if self.auto_switch_translation and target in ("en", "es"):
            auto_lang = (self.auto_detect_lang or "").strip().lower()
            if auto_lang == "en":
                return "es"
            if auto_lang == "es":
                return "en"
        return target or "en"

    def _language_label(self, code):
        code = (code or "").strip().lower()
        if not code:
            return ""
        return WHISPER_LANGUAGE_NAMES.get(code, code)

    def _listening_status_message(self):
        source = (self.source_lang or "").strip().lower()
        if source == "auto" or self.auto_switch_translation:
            detected = (self.auto_detect_lang or "").strip().lower()
            if detected:
                return f"Listening (Detected: {self._language_label(detected)})"
            choices = [self._language_label(c) for c in self.auto_detect_langs]
            choices = [c for c in choices if c]
            if choices:
                return f"Listening (Detecting: {'/'.join(choices)})"
            return "Listening (Detecting...)"
        label = self._language_label(source)
        if label:
            return f"Listening ({label})"
        return self.STATUS_LISTENING

