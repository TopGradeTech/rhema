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

# Metering for large finalized blocks: a long uninterrupted stretch of
# speech can finalize as one multi-sentence block, which used to dump onto
# the display all at once as a wall of text. Committed finals are split at
# sentence boundaries; the first sentence still commits instantly (so the
# normal one-sentence final keeps RealtimeSTT's native instant feel), and
# the rest drip in at roughly reading pace. Under backlog the drip speeds
# up so the display never falls far behind the speaker.
_DRIP_MS_PER_WORD = 220
_DRIP_MIN_MS = 350
_DRIP_MAX_MS = 1500
_DRIP_BACKLOG_MODERATE = 4
_DRIP_MODERATE_CAP_MS = 700
_DRIP_BACKLOG_HEAVY = 8
_DRIP_HEAVY_CAP_MS = 300
# Sentences shorter than this merge into their neighbor so bursts of tiny
# exclamations don't each spend a full drip tick.
_DRIP_MIN_PIECE_CHARS = 12


class DisplayMixin:
    def _unpack_sentence_payload(self, payload):
        if isinstance(payload, dict):
            return payload.get("text", ""), payload.get("queued_at"), payload
        if isinstance(payload, tuple):
            if len(payload) == 3:
                return payload[0], payload[1], payload[2] if isinstance(payload[2], dict) else {}
            if len(payload) == 2:
                return payload[0], payload[1], {}
        return payload, None, {}

    def _record_chunk_latency(self, started_at, latency_meta=None, rendered_at=None):
        if not started_at:
            return
        end_ts = rendered_at if rendered_at is not None else time.time()
        elapsed_ms = int((end_ts - started_at) * 1000)
        if elapsed_ms < 0:
            return
        self.latency_samples.append(elapsed_ms)
        avg_ms = int(sum(self.latency_samples) / max(1, len(self.latency_samples)))
        detail_parts = self._chunk_latency_detail_parts(
            elapsed_ms,
            avg_ms,
            latency_meta=latency_meta,
        )
        label_text = "Latency: " + " | ".join(detail_parts)
        self.root.after(0, lambda: self._set_chunk_latency_label_text(label_text))

    def _chunk_latency_detail_parts(self, elapsed_ms, avg_ms, latency_meta=None):
        meta = latency_meta or {}
        translate_ms = meta.get("translate_openai_ms")
        stt_confidence = meta.get("stt_confidence")
        chunk_seconds = meta.get("chunk_seconds")
        overlap_words = meta.get("overlap_words")
        detail_parts = [f"Total {elapsed_ms} ms (queue->display)", f"Avg {avg_ms} ms"]
        if isinstance(translate_ms, (int, float)) and translate_ms >= 0:
            detail_parts.append(f"Translate {int(translate_ms)} ms")
        if isinstance(chunk_seconds, (int, float)) and chunk_seconds > 0:
            detail_parts.append(f"Chunk {float(chunk_seconds):.2f}s")
        if isinstance(stt_confidence, (int, float)):
            detail_parts.append(f"Conf {max(0.0, min(1.0, float(stt_confidence))):.2f}")
        if isinstance(overlap_words, (int, float)) and int(overlap_words) > 0:
            detail_parts.append(f"Overlap {int(overlap_words)}w")
        return detail_parts

    def _set_chunk_latency_label_text(self, label_text):
        if not self.chunk_latency_label or not self.chunk_latency_label.winfo_exists():
            return
        self.chunk_latency_label.config(text=label_text)

    def _display_should_fast_path(self, text, latency_meta=None):
        if not self._display_fast_path_enabled():
            return False
        now = time.time()
        if now < float(getattr(self, "fast_display_until", 0.0)):
            return True
        pressure = self._display_fast_path_pressure(text, latency_meta)
        if not pressure["enabled"]:
            return False
        hold_sec = max(0.2, float(getattr(self, "fast_display_hold_sec", 2.0)))
        self.fast_display_until = now + hold_sec
        self._trace_pipeline(
            "display_fast_mode_on",
            "",
            sentence_fill_ratio=round(pressure["sentence_fill"], 3),
            reveal_queue=len(self.word_reveal_queue),
            text_queue=len(self.text_queue),
            translate_openai_ms=pressure["translate_ms"],
        )
        return True

    def _display_fast_path_enabled(self):
        if not self.word_by_word:
            return False
        return bool(getattr(self, "dynamic_fast_display_enabled", True))

    def _display_fast_path_pressure(self, text, latency_meta=None):
        _size, _maxsize, sentence_fill = self._queue_fill_ratio(self.sentence_queue)
        pressure = sentence_fill >= float(
            getattr(self, "fast_display_sentence_queue_ratio", 0.25)
        )
        pressure = pressure or len(self.word_reveal_queue) >= int(
            getattr(self, "fast_display_reveal_queue_items", 2)
        )
        pressure = pressure or (self.is_revealing_words and len(self.word_reveal_queue) >= 1)
        pressure = pressure or len(self.text_queue) >= 2
        if self.translation_enabled and len(re.findall(r"\S+", text or "")) >= 9:
            pressure = True
        meta = latency_meta or {}
        translate_ms = meta.get("translate_openai_ms")
        if isinstance(translate_ms, (int, float)) and translate_ms >= int(
            getattr(self, "fast_display_translate_ms", 1200)
        ):
            pressure = True
        return {
            "enabled": pressure,
            "sentence_fill": sentence_fill,
            "translate_ms": translate_ms,
        }

    def _drain_display_queues_immediately(self):
        drained_items, drained = self._collect_immediate_display_drain_items()
        self.current_reveal_words = []
        self.current_reveal_text = ""
        self.current_reveal_latency_meta = None
        self.is_revealing_words = False
        self.live_line = ""
        drained += self._drain_display_payload_queue(self.word_reveal_queue, drained_items)
        drained += self._drain_display_payload_queue(self.text_queue, drained_items)
        self.is_flushing_queue = False
        appended = self._append_drained_display_items(drained_items)
        if appended > 0:
            self._trim_translation_history()
            self.render_text()
        if drained > 0:
            self._trace_pipeline(
                "display_fast_mode_drain",
                "",
                drained_count=drained,
                appended_count=appended,
            )

    def _collect_immediate_display_drain_items(self):
        drained_items = []
        drained = 0
        if self.current_reveal_text:
            drained_items.append(
                (
                    self.current_reveal_text,
                    dict(self.current_reveal_latency_meta or {}),
                )
            )
            drained = 1
        return drained_items, drained

    def _drain_display_payload_queue(self, payload_queue, drained_items):
        drained = 0
        while payload_queue:
            sentence, chunk_meta = self._unpack_display_payload(payload_queue.popleft())
            if not sentence:
                continue
            drained_items.append((sentence, dict(chunk_meta or {})))
            drained += 1
        return drained

    def _append_drained_display_items(self, drained_items):
        appended = 0
        for sentence, meta in drained_items:
            filtered = self.filter_bad_words(sentence)
            if not filtered:
                continue
            self.translations.append(filtered)
            appended += 1
            self._report_display_latency_once(meta)
        return appended

    def _report_display_latency_once(self, meta):
        if not meta or meta.get("display_reported"):
            return
        meta["display_reported"] = True
        self._record_chunk_latency(
            meta.get("queued_at"),
            latency_meta=meta,
            rendered_at=time.time(),
        )

    def _append_display_text_immediate(self, text, latency_meta=None, stage="display_fast_path"):
        self._meter_display_commit(text, latency_meta=latency_meta, stage=stage)

    def _split_display_sentences(self, text):
        """Split a finalized block at sentence boundaries for drip commits.
        A wrong split is harmless - the rolling display re-merges everything
        into one paragraph, so splitting only affects drip timing, never the
        final text."""
        pieces = re.findall(r"[^.!?]+(?:[.!?]+[\"')\]]*)?", text or "")
        pieces = [piece.strip() for piece in pieces if piece and piece.strip()]
        if not pieces:
            return []
        merged = []
        for piece in pieces:
            if merged and len(piece) < _DRIP_MIN_PIECE_CHARS:
                merged[-1] = f"{merged[-1]} {piece}"
            else:
                merged.append(piece)
        chunked = []
        for piece in merged:
            # A very long run-on with no punctuation would still dump at
            # once - reuse the existing chunker to keep pieces bounded.
            chunked.extend(self.chunk_text(piece, max(60, int(self.chunk_size))))
        return chunked

    def _meter_display_commit(self, text, latency_meta=None, stage="display_commit"):
        pieces = self._split_display_sentences(text)
        if not pieces:
            return
        drip_idle = not self.display_drip_queue and self.display_drip_after_id is None
        if drip_idle:
            # Nothing pending: the first sentence commits instantly, exactly
            # like the pre-metering behavior for a normal short final.
            self._commit_display_piece(pieces[0], latency_meta, stage)
            pieces = pieces[1:]
            latency_meta = None
        for piece in pieces:
            # Latency is reported once, on whichever piece renders first.
            self.display_drip_queue.append((piece, latency_meta, stage))
            latency_meta = None
        if self.display_drip_queue and self.display_drip_after_id is None:
            self._schedule_display_drip()

    def _commit_display_piece(self, piece, latency_meta, stage):
        filtered_text = self.filter_bad_words(piece)
        if not filtered_text:
            return
        self.translations.append(filtered_text)
        self._trim_translation_history()
        self.render_text()
        self._trace_pipeline(stage, filtered_text)
        self._report_display_latency_once(latency_meta)

    def _display_drip_delay_ms(self):
        piece = self.display_drip_queue[0][0] if self.display_drip_queue else ""
        words = len(re.findall(r"\S+", piece))
        base = max(_DRIP_MIN_MS, min(_DRIP_MAX_MS, words * _DRIP_MS_PER_WORD))
        backlog = len(self.display_drip_queue)
        if backlog >= _DRIP_BACKLOG_HEAVY:
            base = min(base, _DRIP_HEAVY_CAP_MS)
        elif backlog >= _DRIP_BACKLOG_MODERATE:
            base = min(base, _DRIP_MODERATE_CAP_MS)
        return self._scaled_display_delay_ms(base, minimum_ms=100)

    def _schedule_display_drip(self):
        self.display_drip_after_id = self.root.after(
            self._display_drip_delay_ms(), self._display_drip_tick
        )

    def _display_drip_tick(self):
        self.display_drip_after_id = None
        if not self.display_drip_queue:
            return
        piece, latency_meta, stage = self.display_drip_queue.popleft()
        self._commit_display_piece(piece, latency_meta, stage)
        if self.display_drip_queue:
            self._schedule_display_drip()
    
    def update_text(self, text, latency_meta=None):
        self.root.after(0, lambda: self._update_text_on_ui_thread(text, latency_meta))

    def _update_text_on_ui_thread(self, text, latency_meta=None):
        incoming = self._coerce_incoming_display_text(text)
        if incoming == "":
            return
        self._trace_pipeline(
            "display_update_input",
            incoming,
            word_by_word=self.word_by_word,
        )
        if self._is_local_nllb_output(latency_meta) or self._display_should_fast_path(
            incoming, latency_meta
        ):
            self._drain_display_queues_immediately()
            self._append_display_text_immediate(
                incoming,
                latency_meta=latency_meta,
                stage="display_fast_path_render",
            )
            return
        if self.word_by_word:
            self.enqueue_text(incoming, latency_meta=latency_meta)
            return
        self._queue_pending_display_text(incoming, latency_meta=latency_meta)

    def _is_local_nllb_output(self, latency_meta):
        # Local NLLB translation is already fast/local by the time it reaches
        # display, so it should commit instantly like the untranslated
        # RealtimeSTT path instead of trickling out through the word-by-word
        # reveal queue.
        return bool(latency_meta) and latency_meta.get("text_translation_provider") == "local_nllb"

    def _coerce_incoming_display_text(self, text):
        return (text or "").strip()

    def _queue_pending_display_text(self, incoming, latency_meta=None):
        if self.pending_text:
            self.pending_text = f"{self.pending_text} {incoming}"
        else:
            self.pending_text = incoming
            self.pending_latency_meta = latency_meta
        if len(self.pending_text) >= self.chunk_size:
            self.enqueue_text(self.pending_text, latency_meta=self.pending_latency_meta)
            self.pending_text = ""
            self.pending_latency_meta = None
        self._schedule_pending_text_flush()

    def _schedule_pending_text_flush(self):
        if self.flush_after_id is not None:
            self.root.after_cancel(self.flush_after_id)
        flush_delay_ms = self._scaled_display_delay_ms(
            self.flush_timeout_ms,
            minimum_ms=150,
        )
        self.flush_after_id = self.root.after(flush_delay_ms, self.flush_pending_text)

    def _unpack_display_payload(self, payload):
        if isinstance(payload, tuple) and len(payload) == 2:
            return payload[0], payload[1] if isinstance(payload[1], dict) else None
        return payload, None

    def _effective_display_speed(self):
        try:
            speed = float(self.display_speed_factor)
        except Exception:
            speed = 1.0
        return max(0.5, min(speed, 2.5))

    def _scaled_display_delay_ms(self, base_ms, minimum_ms=20):
        try:
            base = float(base_ms)
        except Exception:
            base = 0.0
        speed = self._effective_display_speed()
        return max(int(minimum_ms), int(round(max(0.0, base) / speed)))

    def _effective_max_lines(self):
        if getattr(self, "video_feed_enabled", False):
            return 2
        return max(1, int(self.max_lines))

    def _trim_translation_history(self):
        max_entries = max(50, self._effective_max_lines() * 12)
        if len(self.translations) > max_entries:
            self.translations = self.translations[-max_entries:]

    def enqueue_text(self, text, latency_meta=None):
        if self.word_by_word:
            chunks = self.chunk_text(text, self.chunk_size)
            for idx, chunk in enumerate(chunks):
                chunk_meta = dict(latency_meta) if latency_meta and idx == 0 else None
                self.word_reveal_queue.append((chunk, chunk_meta))
                self._trace_pipeline(
                    "display_chunk_enqueued",
                    chunk,
                    chunk_index=idx,
                    chunk_count=len(chunks),
                    word_by_word=True,
                )
            if not self.is_revealing_words:
                self.start_word_reveal()
            return
        chunks = self.chunk_text(text, self.chunk_size)
        for idx, chunk in enumerate(chunks):
            chunk_meta = dict(latency_meta) if latency_meta and idx == 0 else None
            self.text_queue.append((chunk, chunk_meta))
            self._trace_pipeline(
                "display_chunk_enqueued",
                chunk,
                chunk_index=idx,
                chunk_count=len(chunks),
                word_by_word=False,
            )
        if not self.is_flushing_queue:
            self.flush_text_queue()

    def start_word_reveal(self):
        if self.is_revealing_words:
            return
        self.is_revealing_words = True
        self.current_reveal_words = []
        self.current_reveal_text = ""
        self.reveal_next_word()

    def reveal_next_word(self):
        if not self.is_revealing_words:
            return
        if not self.current_reveal_words:
            if self.current_reveal_text:
                self.translations.append(self.current_reveal_text)
                self._trace_pipeline("display_word_reveal_complete", self.current_reveal_text)
                self._trim_translation_history()
                self.current_reveal_text = ""
                self.current_reveal_latency_meta = None
            if not self.word_reveal_queue:
                self.is_revealing_words = False
                self.live_line = ""
                self.render_text()
                return
            sentence, self.current_reveal_latency_meta = self._unpack_display_payload(
                self.word_reveal_queue.popleft()
            )
            self.current_reveal_words = re.findall(r"\S+", sentence)

        next_word = self.current_reveal_words.pop(0)
        if self.current_reveal_text:
            self.current_reveal_text = f"{self.current_reveal_text} {next_word}"
        else:
            self.current_reveal_text = next_word
        self.live_line = self.current_reveal_text
        self.render_text()
        if self.current_reveal_latency_meta and not self.current_reveal_latency_meta.get("display_reported"):
            self.current_reveal_latency_meta["display_reported"] = True
            self._record_chunk_latency(
                self.current_reveal_latency_meta.get("queued_at"),
                latency_meta=self.current_reveal_latency_meta,
                rendered_at=time.time(),
            )
        self.root.after(
            self._scaled_display_delay_ms(self.chunk_delay_ms, minimum_ms=20),
            self.reveal_next_word,
        )

    def flush_pending_text(self):
        self.flush_after_id = None
        if not self.pending_text:
            return
        self._trace_pipeline("display_pending_flush", self.pending_text)
        self.enqueue_text(self.pending_text, latency_meta=self.pending_latency_meta)
        self.pending_text = ""
        self.pending_latency_meta = None

    def flush_text_queue(self):
        if not self.text_queue:
            self.is_flushing_queue = False
            return

        self.is_flushing_queue = True
        chunk, chunk_meta = self._unpack_display_payload(self.text_queue.popleft())
        filtered_text = self.filter_bad_words(chunk)
        self._trace_pipeline(
            "display_chunk_rendered",
            filtered_text,
            filtered_changed=(filtered_text != chunk),
        )
        self.translations.append(filtered_text)
        self._trim_translation_history()
        self.render_text()
        if chunk_meta and not chunk_meta.get("display_reported"):
            chunk_meta["display_reported"] = True
            self._record_chunk_latency(
                chunk_meta.get("queued_at"),
                latency_meta=chunk_meta,
                rendered_at=time.time(),
            )
        self.root.after(
            self._scaled_display_delay_ms(self.chunk_delay_ms, minimum_ms=20),
            self.flush_text_queue,
        )

    def chunk_text(self, text, max_len):
        if len(text) <= max_len:
            return [text]

        chunks = []
        remaining = text.strip()
        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break
            split_at = remaining.rfind(" ", 0, max_len + 1)
            if split_at == -1 or split_at < max_len // 2:
                split_at = max_len
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        return chunks
    
    def update_display(self):
        def update():
            self.render_text()
        self.root.after(0, update)
    
    def update_status(self, msg):
        if msg == self.STATUS_LISTENING or msg.startswith("Listening"):
            msg = self._listening_status_message()
        self._log_status(msg)
        def update():
            self.status_label.config(text=f"Status: {msg}")
        self.root.after(0, update)

    def _capture_audio_level(self, audio):
        try:
            raw = audio.get_raw_data()
            if not raw:
                return
            sample_width = int(getattr(audio, "sample_width", 2) or 2)
            sample_width = max(1, sample_width)
            self._capture_audio_level_from_raw(raw, sample_width)
        except Exception:
            pass

    def _capture_audio_level_from_raw(self, raw, sample_width):
        if not raw:
            return
        sample_width = max(1, int(sample_width))
        rms = float(self._audio_rms(raw, sample_width))
        peak = float(self._audio_max(raw, sample_width))
        full_scale = float((1 << ((sample_width * 8) - 1)) - 1)
        if full_scale <= 0:
            return
        min_ratio = 1.0 / full_scale
        rms_ratio = max(min_ratio, rms / full_scale)
        peak_ratio = max(min_ratio, peak / full_scale)
        db_rms = 20.0 * math.log10(rms_ratio)
        db_peak = 20.0 * math.log10(peak_ratio)
        # Blend toward peak so brief transients are visible, while RMS keeps
        # the meter stable on sustained speech.
        db_effective = max(db_peak, db_rms + 3.0)
        level = self._meter_level_from_dbfs(db_effective)
        self.audio_level_target = max(0.0, min(100.0, level))
        self.audio_level_last_update = time.time()

    def _meter_level_from_dbfs(self, db_value):
        floor_db = float(self.audio_level_floor_db)
        norm = max(0.0, min(1.0, (float(db_value) - floor_db) / (0.0 - floor_db)))
        # Perceptual curve for a Windows-mixer-like visual response.
        return (norm ** 0.62) * 100.0

    def _resolve_audio_level_device_index(self):
        device_name = self._get_selected_device_name()
        if not device_name:
            return None
        if self.device_types.get(device_name) != "input":
            if not self.allow_loopback:
                return None
            return self.loopback_output_map.get(device_name)
        return self.device_indices.get(device_name)

    def _start_audio_level_stream_thread(self):
        if self.audio_level_thread is not None and self.audio_level_thread.is_alive():
            return
        self.audio_level_thread = Thread(target=self._audio_level_stream_loop, daemon=True)
        self.audio_level_thread.start()

    def _open_audio_level_stream(self, pa, device_index, sample_rate, frames_per_buffer):
        last_exc = None
        for channels in (1, 2):
            try:
                with self.portaudio_admin_lock:
                    return pa.open(
                        format=pyaudio.paInt16,
                        channels=channels,
                        rate=sample_rate,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=frames_per_buffer,
                    )
            except Exception as exc:
                last_exc = exc
        if last_exc:
            raise last_exc
        raise ValueError("Unable to open audio level stream")

    def _audio_level_stream_loop(self):
        pa = None
        stream = None
        current_key = None
        frames_per_buffer = 1024
        try:
            while self.listening:
                # Suspend while RealtimeSTT has its own PyAudio input stream
                # open — two simultaneous mic streams on the same device
                # causes audio contention and slows transcription.
                if getattr(self, "realtime_stt_active", False):
                    if stream is not None:
                        stream = self._close_audio_level_stream_handle(stream)
                        pa = self._close_audio_level_pyaudio(pa)
                        current_key = None
                    self.audio_level_target = 0.0
                    time.sleep(0.1)
                    continue
                device_index = self._resolve_audio_level_device_index()
                if device_index is None:
                    current_key = self._handle_missing_audio_level_device()
                    continue
                sample_rate = int(
                    self.device_sample_rates_by_index.get(device_index, 16000) or 16000
                )
                next_key = (device_index, sample_rate)
                pa, stream, current_key, ready = self._ensure_audio_level_stream_ready(
                    pa,
                    stream,
                    current_key,
                    next_key,
                    device_index,
                    sample_rate,
                    frames_per_buffer,
                )
                if not ready:
                    continue
                if not self._read_audio_level_stream_frame(stream, frames_per_buffer):
                    current_key = None
                    stream = self._close_audio_level_stream_handle(stream)
                    time.sleep(0.2)
        finally:
            stream = self._close_audio_level_stream_handle(stream)
            pa = self._close_audio_level_pyaudio(pa)

    def _handle_missing_audio_level_device(self):
        self.audio_level_target = 0.0
        time.sleep(0.2)
        return None

    def _ensure_audio_level_stream_ready(
        self,
        pa,
        stream,
        current_key,
        next_key,
        device_index,
        sample_rate,
        frames_per_buffer,
    ):
        if not self._audio_level_stream_needs_reopen(current_key, next_key, stream):
            return pa, stream, current_key, True
        self.audio_level_restart_requested = False
        stream = self._close_audio_level_stream_handle(stream)
        pa = self._close_audio_level_pyaudio(pa)
        try:
            pa = self._create_pyaudio()
            stream = self._open_audio_level_stream(
                pa,
                device_index,
                sample_rate,
                frames_per_buffer,
            )
            return pa, stream, next_key, True
        except Exception:
            stream = self._close_audio_level_stream_handle(stream)
            pa = self._close_audio_level_pyaudio(pa)
            self._note_audio_level_stream_unavailable()
            self.audio_level_target = 0.0
            time.sleep(0.5)
            return None, None, None, False

    def _audio_level_stream_needs_reopen(self, current_key, next_key, stream):
        if self.audio_level_restart_requested:
            return True
        if current_key != next_key:
            return True
        return stream is None

    def _read_audio_level_stream_frame(self, stream, frames_per_buffer):
        try:
            raw = stream.read(frames_per_buffer, exception_on_overflow=False)
            self._capture_audio_level_from_raw(raw, 2)
            return True
        except Exception:
            return False

    def _note_audio_level_stream_unavailable(self):
        now = time.time()
        if now - self._audio_level_last_error_log <= 8.0:
            return
        self._audio_level_last_error_log = now
        self._log_status("Audio level stream unavailable; using chunk meter fallback")

    def _close_audio_level_stream_handle(self, stream):
        if stream is None:
            return None
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        return None

    def _close_audio_level_pyaudio(self, pa):
        if pa is None:
            return None
        try:
            self._terminate_pyaudio(pa)
        except Exception:
            pass
        return None

    def _start_audio_level_updates(self):
        if self.audio_level_after_id is not None:
            return
        try:
            self.audio_level_after_id = self.root.after(self.audio_level_tick_ms, self._update_audio_level_meter)
        except Exception:
            self.audio_level_after_id = None

    def _update_audio_level_meter(self):
        self.audio_level_after_id = None
        now = time.time()
        last_meter = float(self.audio_level_last_meter_update or now)
        dt = max(0.0, now - last_meter)
        self.audio_level_last_meter_update = now
        target = float(self.audio_level_target)
        if now - float(self.audio_level_last_update or 0.0) > 0.2:
            target = 0.0
            self.audio_level_target = 0.0
        level = float(self.audio_level_value)
        if target > level:
            level = min(target, level + (dt * self.audio_level_attack_per_second))
        else:
            level = max(target, level - (dt * self.audio_level_release_per_second))
        self.audio_level_value = level
        self._render_audio_level_meter(level)
        self._start_audio_level_updates()

    def _render_audio_level_meter(self, level):
        if self.audio_level_bar is None or not self.audio_level_bar.winfo_exists():
            return
        try:
            width = max(1.0, float(self.audio_level_bar.winfo_width()))
            height = max(1.0, float(self.audio_level_bar.winfo_height()))
            fill_width = width * max(0.0, min(1.0, level / 100.0))
            if self.audio_level_fill_item is not None:
                self.audio_level_bar.coords(
                    self.audio_level_fill_item,
                    0,
                    0,
                    fill_width,
                    height,
                )
        except Exception:
            pass

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self.pause_button.config(text="Resume" if self.is_paused else "Pause")
        self.update_status("Paused" if self.is_paused else self.STATUS_LISTENING)

    def on_canvas_resize(self, event):
        self.text_canvas.itemconfigure(self.text_item, width=0, font=self.text_font)
        self._fit_font_to_lines()
        if self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.root.after(60, self._finish_resize)

    def _finish_resize(self):
        self._resize_after_id = None
        self.render_text()

    def render_text(self):
        self._fit_font_to_lines()
        base_parts = self._collect_render_parts()
        merged_text = self._merge_render_parts(base_parts)
        width = max(10, self.text_canvas.winfo_width() - (self.text_padding * 2))
        wrapped_lines = self._wrap_lines_to_width(
            [merged_text],
            width,
        )
        display_lines = wrapped_lines[-self._effective_max_lines():]
        self.last_display_line_count = len(display_lines)
        display_text = "\n".join(display_lines)
        self.text_canvas.itemconfigure(self.text_item, text="", state="hidden")
        for item in self.text_line_items:
            self.text_canvas.itemconfigure(item, state="normal")
        self._update_line_items(display_lines)
        self.update_preview(display_text)
        self.update_text_metrics()

    def _collect_render_parts(self):
        parts = []
        for segment in self.translations:
            cleaned = self._coerce_render_segment(segment)
            if cleaned:
                parts.append(cleaned)
        if self.live_line:
            live_cleaned = self._coerce_render_segment(self.live_line)
            if live_cleaned:
                parts.append(live_cleaned)
        return parts

    def _coerce_render_segment(self, segment):
        return self.filter_bad_words(segment).strip()

    def _merge_render_parts(self, base_parts):
        if not base_parts:
            return ""
        joined = " ".join(base_parts)
        return self.clean_text_spacing(joined)

    def update_preview(self, text):
        if not self.preview_widget:
            return

        def update():
            widget = self.preview_widget
            if not widget or not widget.winfo_exists():
                return
            widget.config(text=text if text else self.preview_placeholder)
            self._sync_preview_colors()

        self.root.after(0, update)

    def update_text_metrics(self):
        line_height = self.text_font.metrics("linespace") or 1
        self.text_bbox_height = line_height * max(1, self.last_display_line_count)


if __name__ == "__main__":
    app = TranslationApp()
