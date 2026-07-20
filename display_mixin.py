from threading import Thread
import time
import re
import pyaudio
import math

# Finalized text is revealed onto the display in small word groups (the
# broadcast-caption "roll-up" style) instead of dumping whole blocks at
# once, so readers can follow along word by word. Pacing is elastic via a
# time budget: with little queued, words paint at a comfortable reading
# cadence (a bit faster than people speak, so the reveal can never fall
# structurally behind); as the queue grows the per-word delay shrinks
# proportionally so everything queued always drains within the budget.
# Past the point where even the floor rate would exceed the budget, the
# overflow is batched into one tick (revealing that fast reads as instant
# anyway). The first group of an idle commit still lands instantly.
_DRIP_WORDS_PER_TICK = 2
_DRIP_BASE_MS_PER_WORD = 300  # ~200 wpm reveal; speech runs ~130-160 wpm
_DRIP_FLOOR_MS_PER_WORD = 70
_DRIP_LAG_BUDGET_MS = 3500  # max time for the whole queue to drain


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

    def _report_display_latency_once(self, meta):
        if not meta or meta.get("display_reported"):
            return
        meta["display_reported"] = True
        self._record_chunk_latency(
            meta.get("queued_at"),
            latency_meta=meta,
            rendered_at=time.time(),
        )

    def _split_display_word_groups(self, text):
        """Split a finalized block into small word groups for the drip
        reveal. Grouping only affects reveal timing - the rolling display
        re-merges everything into one paragraph, so the final text is
        unchanged."""
        words = re.findall(r"\S+", text or "")
        return [
            " ".join(words[i:i + _DRIP_WORDS_PER_TICK])
            for i in range(0, len(words), _DRIP_WORDS_PER_TICK)
        ]

    def _interim_display_active(self):
        """Raw interim partials are source-language text, so they are shown
        only when translation is off; with translation on the live row is
        fed by throttled NLLB translations of stabilized text instead (see
        _translated_interim_active)."""
        return (
            bool(getattr(self, "show_interim_text", False))
            and not self.translation_enabled
        )

    def _translated_interim_active(self):
        """With translation on, the live row shows throttled NLLB
        translations of RealtimeSTT's stabilized text — target-language
        preview text that the translated final then replaces."""
        return (
            bool(getattr(self, "show_interim_text", False))
            and self.translation_enabled
        )

    def _show_translated_interim(self, text):
        """Tk thread: render a translated interim into the live row on the
        same light path as _apply_interim_display (content-only, no font
        refit)."""
        if not self._translated_interim_active():
            return
        self.live_line = (text or "").strip()
        self._update_line_items(self._compose_display_lines())

    def _meter_display_commit(self, text, latency_meta=None, stage="display_commit"):
        if self._interim_display_active() or self._translated_interim_active():
            # Interim mode: the viewer already watched this utterance type
            # out live, so metering the final back out word-by-word would
            # just re-add the latency interim mode exists to remove. Commit
            # the whole final at once, replacing the live interim row.
            self.live_line = ""
            self._commit_display_piece(text, stage)
            self._report_display_latency_once(latency_meta)
            return
        groups = self._split_display_word_groups(text)
        if not groups:
            return
        drip_idle = not self.display_drip_queue and self.display_drip_after_id is None
        if drip_idle:
            # Nothing pending: the first words land instantly so a new
            # utterance is never held back by the reveal cadence.
            self._commit_display_piece(groups[0], stage)
            self._report_display_latency_once(latency_meta)
            groups = groups[1:]
            latency_meta = None
        for group in groups:
            # Latency is reported once, on whichever group renders first.
            self.display_drip_queue.append((group, latency_meta, stage))
            latency_meta = None
        if self.display_drip_queue:
            # Everything queued (including what was already pending, which
            # displays first) must finish painting within the lag budget of
            # this newest arrival.
            self.display_drip_deadline = time.monotonic() + (_DRIP_LAG_BUDGET_MS / 1000.0)
            if self.display_drip_after_id is None:
                self._schedule_display_drip()

    def _commit_display_piece(self, piece, stage):
        filtered_text = self.filter_bad_words(piece)
        if not filtered_text:
            return
        self.translations.append(filtered_text)
        self._append_to_display_page(filtered_text)
        self._trim_translation_history()
        self.render_text()
        self._trace_pipeline(stage, filtered_text)

    def _append_to_display_page(self, text):
        """Roll-up paging: committed text wraps into frozen lines, so
        appending can only extend the bottom line or add new lines below
        it - painted lines never re-wrap while being read. Once the
        display is full, each completed new line clears the top line and
        shifts the frozen lines up one slot (broadcast roll-up), instead
        of continuously re-flowing the whole transcript."""
        text = (text or "").strip()
        if not text:
            return
        width = self._display_wrap_width()
        lines = self.display_page_lines
        tail = lines.pop() if lines else ""
        candidate = self.clean_text_spacing(f"{tail} {text}".strip())
        lines.extend(self._wrap_lines_to_width([candidate], width))
        max_lines = self._effective_max_lines()
        if len(lines) > max_lines:
            del lines[: len(lines) - max_lines]

    def _display_wrap_width(self):
        return max(10, self.text_canvas.winfo_width() - (self.text_padding * 2))

    def _reflow_display_page(self):
        """Re-wrap the frozen page lines after the window or font changed;
        outside of that, lines are never re-wrapped once painted."""
        merged = self.clean_text_spacing(" ".join(self.display_page_lines).strip())
        if not merged:
            return
        wrapped = self._wrap_lines_to_width([merged], self._display_wrap_width())
        self.display_page_lines = wrapped[-self._effective_max_lines():]

    def _drip_pending_words(self):
        return sum(
            len(re.findall(r"\S+", piece))
            for piece, _meta, _stage in self.display_drip_queue
        )

    def _drip_remaining_budget_ms(self):
        return max(0.0, (self.display_drip_deadline - time.monotonic()) * 1000.0)

    def _display_drip_delay_ms(self):
        pending = self._drip_pending_words()
        per_word = max(
            _DRIP_FLOOR_MS_PER_WORD,
            min(
                _DRIP_BASE_MS_PER_WORD,
                self._drip_remaining_budget_ms() / max(1, pending),
            ),
        )
        next_piece = self.display_drip_queue[0][0] if self.display_drip_queue else ""
        next_words = max(1, len(re.findall(r"\S+", next_piece)))
        return self._scaled_display_delay_ms(per_word * next_words, minimum_ms=40)

    def _schedule_display_drip(self):
        self.display_drip_after_id = self.root.after(
            self._display_drip_delay_ms(), self._display_drip_tick
        )

    def _display_drip_tick(self):
        self.display_drip_after_id = None
        if not self.display_drip_queue:
            return
        popped = [self.display_drip_queue.popleft()]
        # Past the point where even floor-rate drainage would blow the lag
        # budget, a word-by-word reveal reads as a fast crawl that still
        # lags the speaker - batch the overflow into this tick instead so
        # the display snaps back inside the budget.
        pending = self._drip_pending_words()
        remaining_budget_ms = self._drip_remaining_budget_ms()
        while (
            self.display_drip_queue
            and pending * _DRIP_FLOOR_MS_PER_WORD > remaining_budget_ms
        ):
            piece, meta, stage = self.display_drip_queue.popleft()
            popped.append((piece, meta, stage))
            pending -= len(re.findall(r"\S+", piece))
        self._commit_display_piece(" ".join(item[0] for item in popped), popped[0][2])
        for _piece, meta, _stage in popped:
            self._report_display_latency_once(meta)
        if self.display_drip_queue:
            self._schedule_display_drip()
    
    def update_text(self, text, latency_meta=None):
        self.root.after(0, lambda: self._update_text_on_ui_thread(text, latency_meta))

    def _update_text_on_ui_thread(self, text, latency_meta=None):
        # Local NLLB (the only translation provider) is already fast/local
        # by the time it reaches display, so finalized text always commits
        # here immediately and is metered out via _meter_display_commit,
        # exactly like the untranslated RealtimeSTT path.
        incoming = self._coerce_incoming_display_text(text)
        if incoming == "":
            return
        self._trace_pipeline("display_update_input", incoming)
        self._meter_display_commit(
            incoming,
            latency_meta=latency_meta,
            stage="display_commit",
        )

    def _coerce_incoming_display_text(self, text):
        return (text or "").strip()

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
        self._reflow_display_page()
        self.render_text()

    def render_text(self):
        self._fit_font_to_lines()
        display_lines = self._compose_display_lines()
        self.text_canvas.itemconfigure(self.text_item, text="", state="hidden")
        for item in self.text_line_items:
            self.text_canvas.itemconfigure(item, state="normal")
        self._update_line_items(display_lines)

    def _compose_display_lines(self):
        """The frozen page lines, plus the live interim line on its own
        reserved bottom row. The live row is never merged into the frozen
        tail line and never wrapped - it shows the tail end of the
        utterance, truncated from the left to fit one row - so interim
        updates can never re-wrap/reflow text that's already painted (the
        exact jerkiness that got the original interim preview removed).
        When the frozen page is already full, making room for the live row
        is one discrete top-line roll-up, the same visual event the display
        already does when a completed line arrives."""
        lines = list(self.display_page_lines)
        if self.live_line:
            max_lines = self._effective_max_lines()
            if len(lines) >= max_lines:
                lines = lines[-(max_lines - 1):] if max_lines > 1 else []
            lines.append(
                self._interim_tail_for_width(self.live_line, self._display_wrap_width())
            )
        return [self._coerce_render_segment(line) for line in lines]

    def _interim_tail_for_width(self, text, max_width):
        """Last words of `text` that fit within max_width on one row."""
        words = (text or "").split()
        if not words:
            return ""
        tail = ""
        for word in reversed(words):
            candidate = f"{word} {tail}".strip()
            if self.text_font.measure(candidate) > max_width:
                break
            tail = candidate
        return tail or words[-1]

    def _queue_interim_display(self, text):
        """Accept a raw RealtimeSTT partial for display. Called from
        RealtimeSTT's worker thread ~10x/sec; coalesces into at most one
        Tk-thread render per 120ms window, applying only the newest text."""
        if not self._interim_display_active():
            return
        self._interim_latest_text = text or ""
        if self._interim_render_scheduled:
            return
        self._interim_render_scheduled = True
        try:
            self.root.after(120, self._apply_interim_display)
        except Exception:
            self._interim_render_scheduled = False

    def _apply_interim_display(self):
        self._interim_render_scheduled = False
        if not self._interim_display_active():
            return
        self.live_line = (self._interim_latest_text or "").strip()
        # Light render path: content-only. Font size depends on canvas size
        # and _effective_max_lines(), not on what text is showing, so
        # skipping _fit_font_to_lines here is safe and keeps the ~8Hz
        # interim tick cheap.
        self._update_line_items(self._compose_display_lines())

    def _coerce_render_segment(self, segment):
        return self.filter_bad_words(segment).strip()

