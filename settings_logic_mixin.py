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

"""Extracted from settings_ui_mixin.py during the pywebview port (Phase 6)
so both TranslationApp (Tk) and WebTranslationApp (pywebview) can share
one copy of the Options/Controller business logic - Apply-flow application
of every settings section, the dirty-tracking machinery, hardware
autodetect, and the entire Local NLLB cache-check/download/verification
worker chain.

This is genuinely NOT a clean "no Tk" split, and pretending otherwise would
have been the exact drift risk this extraction exists to avoid. A close
read (before extraction, not after something broke) found real Tk-widget
touches embedded in what the original architecture plan called "logic":

- _run_hardware_autodetect_from_menu called tkinter.messagebox directly -
  fixed by routing it through a new _show_hardware_autodetect_result(text)
  hook (settings_ui_mixin.py has the real messagebox implementation;
  WebSettingsUIMixin provides its own).
- _run_local_nllb_test_from_vars/_finish_local_nllb_test both take a real
  button widget as a PARAMETER (test_button.config(...)) rather than
  touching self.* directly - already safely swappable: callers pass
  whatever real or stand-in object is appropriate for their own chrome.
- _apply_settings_from_controller/_set_settings_dirty_state read
  dirty_ctx.get("save_button") rather than self.save_button - None-guarded,
  so simply omitting a "save_button" key from dirty_ctx (as
  WebSettingsUIMixin's Options window does) makes these no-op harmlessly
  for the Web side without needing any stand-in object at all.
- _confirm_local_nllb_download (a real, synchronous modal Tk dialog) and
  _refresh_local_nllb_runtime_ui (real button .config() calls, already
  None-guarded) stay in settings_ui_mixin.py as chrome; the NLLB worker
  chain here calls them BY NAME, and WebSettingsUIMixin supplies its own
  same-named implementations (a native message-box confirm for the
  former; already-safe no-op via None-guards for the latter, since this
  app's Web Options window doesn't build real download/test button
  objects).
- _apply_display_vars/_apply_advanced_vars call _apply_ui_theme/
  _rebuild_settings_windows/_fit_font_to_lines/_apply_scaled_fonts by
  name - all four stay real chrome in settings_ui_mixin.py;
  _fit_font_to_lines/_apply_scaled_fonts already work unmodified against
  WebCanvas/WebMeasurer (proved in Phase 3), and theme_var/monitor_var
  simply aren't wired into the Web Options form yet (dark/light theme
  switching and real per-monitor selection are still-open scope, matching
  experiments/web_options.py's own already-reviewed choice to leave them
  unwired for the same reason).

So "logic" here means: every method below is safe to call, unmodified,
from either app - not that every method is itself Tk-import-free. Several
call chrome methods by name, resolved via each concrete class's own MRO
(SettingsUIMixin(SettingsLogicMixin) for Tk, WebSettingsUIMixin(
SettingsLogicMixin) for Web) - the same pattern this whole port already
relies on for _show_error_dialog/_show_info_dialog/_confirm_yes_no
(logging_mixin.py, Phase 1).
"""

import gc
import importlib.util
import os
import re
import time
import tkinter as tk
import webbrowser
from threading import Thread

import speech_recognition as sr

FEATURE_REQUEST_URL = "https://github.com/TopGradeTech/rhema/discussions/categories/ideas"


class SettingsLogicMixin:

    def _check_startup_ready(self):
        if self.app_startup_ready:
            return
        if not (
            self.startup_stt_ready
            and self.startup_translation_ready
            and self.startup_video_scan_ready
        ):
            return
        self.app_startup_ready = True
        self._hide_startup_loading_overlay()


    def _mark_startup_video_scan_ready(self):
        if self.startup_video_scan_ready:
            return
        self.startup_video_scan_ready = True
        if getattr(self, "_start_video_feed_after_startup_scan", False):
            # The scan that just finished is exactly what start_video_feed()
            # was held back for at startup (see main.py) - safe to open the
            # real feed now that it's no longer contending for the camera.
            self._start_video_feed_after_startup_scan = False
            try:
                self.start_video_feed()
            except Exception:
                pass
        try:
            self.root.after(0, self._check_startup_ready)
        except Exception:
            pass


    def _mark_startup_translation_ready(self):
        if self.startup_translation_ready:
            return
        self.startup_translation_ready = True
        try:
            self.root.after(0, self._check_startup_ready)
        except Exception:
            pass


    def _new_settings_dirty_context(self):
        return {
            "dirty_ready": False,
            "applied_snapshot": None,
            "dirty_value": False,
            "tracked_getters": [],
            "save_button": None,
            "apply_style": None,
        }


    def _collect_settings_vars_for_dirty_tracking(self, mapping, dirty_ctx):
        for value in mapping.values():
            if isinstance(value, tk.Variable):
                self._track_settings_var(value, dirty_ctx)
            elif isinstance(value, tk.Text):
                self._track_settings_text(value, dirty_ctx)


    def _track_settings_var(self, var, dirty_ctx):
        dirty_ctx["tracked_getters"].append(lambda var=var: var.get())
        var.trace_add("write", lambda *_args: self._update_settings_dirty_state(dirty_ctx))


    def _track_settings_text(self, widget, dirty_ctx):
        dirty_ctx["tracked_getters"].append(
            lambda widget=widget: widget.get("1.0", "end").strip()
        )

        def on_modified(_event, widget=widget):
            if not widget.edit_modified():
                return
            widget.edit_modified(False)
            self._update_settings_dirty_state(dirty_ctx)

        widget.bind("<<Modified>>", on_modified)
        widget.edit_modified(False)


    def _capture_settings_snapshot(self, dirty_ctx):
        snapshot = []
        for getter in dirty_ctx.get("tracked_getters", []):
            try:
                snapshot.append(getter())
            except Exception:
                snapshot.append(None)
        return snapshot


    def _update_settings_dirty_state(self, dirty_ctx, force=False):
        if not dirty_ctx.get("dirty_ready"):
            return
        snapshot = self._capture_settings_snapshot(dirty_ctx)
        is_dirty = snapshot != dirty_ctx.get("applied_snapshot")
        self._set_settings_dirty_state(dirty_ctx, is_dirty, force=force)


    def _apply_settings_from_controller(
        self,
        display_vars,
        audio_vars,
        transcription_vars,
        translation_vars,
        advanced_vars,
        dirty_ctx,
    ):
        if self.is_applying_settings:
            return
        self.is_applying_settings = True
        save_button = dirty_ctx.get("save_button")
        if save_button is not None:
            save_button.config(state=tk.DISABLED)
        try:
            self._log_status("Apply clicked")
            self._apply_settings_vars(
                display_vars,
                audio_vars,
                transcription_vars,
                translation_vars,
                advanced_vars,
            )
            self._show_apply_success()
            dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(dirty_ctx)
            self._log_status("Apply finished")
        except Exception as exc:
            self._handle_settings_apply_failure(exc)
        finally:
            self.is_applying_settings = False
            self._update_settings_dirty_state(dirty_ctx, force=True)


    def _handle_settings_apply_failure(self, exc):
        try:
            self._log_status(f"Apply failed: {exc}")
        except Exception:
            pass
        try:
            traceback.print_exc()
        except Exception:
            pass
        try:
            self._show_error_dialog("Apply Failed", f"{exc}")
        except Exception:
            pass


    def _apply_settings_vars(
        self,
        display_vars,
        audio_vars,
        transcription_vars,
        translation_vars,
        advanced_vars,
    ):
        self._apply_display_vars(display_vars)
        self._apply_transcription_vars(transcription_vars)
        self._apply_translation_vars(translation_vars)
        self._apply_audio_vars(audio_vars)
        self._apply_advanced_vars(advanced_vars)
        self._refresh_audio_devices()
        self.apply_colors()
        self.update_display()
        self.is_fullscreen = True
        self.enter_fullscreen()
        self.save_settings()


    def _show_apply_success(self):
        self.update_status("Settings applied")
        try:
            self.root.after(2000, self._restore_status_label)
        except Exception:
            pass


    def _restore_status_label(self):
        if self.is_paused:
            self.update_status("Paused")
        else:
            self.update_status(self.STATUS_LISTENING)


    @staticmethod
    def _parse_camera_device_label(label):
        # Labels are "Camera N" or "Camera N: Friendly Name" - only the
        # leading number after the prefix matters for resolving the index.
        prefix = "Camera "
        if not label or not label.startswith(prefix):
            return None
        match = re.match(r"(\d+)", label[len(prefix):])
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None


    def _apply_display_vars(self, display_vars):
        if "theme_var" in display_vars:
            new_theme = "dark" if display_vars["theme_var"].get() == "Dark" else "light"
            if new_theme != self.ui_theme:
                self.ui_theme = new_theme
                self._apply_ui_theme()
                # Deferred rather than done inline here: the Options
                # dialog's own Apply button click is still unwinding
                # through _apply_settings_from_controller/_apply_settings_vars
                # at this point, which touches dirty_ctx/vars-dicts that
                # belong to the very windows this would destroy. Runs on
                # the next event-loop tick once that chain has returned.
                self.root.after(0, self._rebuild_settings_windows)
        self.max_lines = self._coerce_int_range(
            display_vars["lines_var"].get(),
            self.LINES_NO_VIDEO_DEFAULT,
            self.LINES_NO_VIDEO_MIN,
            self.LINES_NO_VIDEO_MAX,
        )
        self.video_max_lines = self._coerce_int_range(
            display_vars["video_lines_var"].get(),
            self.LINES_VIDEO_DEFAULT,
            self.LINES_VIDEO_MIN,
            self.LINES_VIDEO_MAX,
        )
        self.bg_color = display_vars["bg_color_var"].get()
        self.text_color = display_vars["text_color_var"].get()
        if "lock_output_focus_var" in display_vars:
            self.lock_output_focus = bool(display_vars["lock_output_focus_var"].get())
        if "clear_display_on_inactivity_var" in display_vars:
            self.clear_display_on_inactivity = bool(
                display_vars["clear_display_on_inactivity_var"].get()
            )
        if "clear_display_inactivity_seconds_var" in display_vars:
            self.clear_display_inactivity_seconds = self._coerce_int_range(
                display_vars["clear_display_inactivity_seconds_var"].get(),
                self.CLEAR_DISPLAY_INACTIVITY_DEFAULT,
                self.CLEAR_DISPLAY_INACTIVITY_MIN,
                self.CLEAR_DISPLAY_INACTIVITY_MAX,
            )
        # Re-arms (or cancels, if just turned off) immediately rather than
        # waiting for the next line of speech, so toggling this in Apply
        # takes effect right away.
        self._schedule_display_inactivity_clear()
        if "video_feed_enabled_var" in display_vars:
            self.video_feed_enabled = bool(display_vars["video_feed_enabled_var"].get())
        if "video_device_var" in display_vars:
            self.video_device_index = self._parse_camera_device_label(
                display_vars["video_device_var"].get()
            )
        if "video_caption_alpha_var" in display_vars:
            self.video_caption_bar_alpha = display_vars["video_caption_alpha_var"].get() / 100.0
        self.stop_video_feed()
        self.start_video_feed()
        self._apply_scaled_fonts()
        self._fit_font_to_lines()
        monitor_labels = display_vars["monitor_labels"]
        monitor_value = display_vars["monitor_var"].get()
        settings_monitor_value = display_vars["settings_monitor_var"].get()
        if monitor_value in monitor_labels:
            self.monitor_index = monitor_labels.index(monitor_value)
        if settings_monitor_value in monitor_labels:
            self.settings_monitor_index = monitor_labels.index(settings_monitor_value)


    def _apply_advanced_vars(self, advanced_vars):
        previous_start_with_windows = bool(self.start_with_windows)
        previous_cuda_directory = self.cuda_directory
        if "logging_mode_var" in advanced_vars and "logging_mode_map" in advanced_vars:
            selected = advanced_vars["logging_mode_var"].get()
            self.logging_mode = self._normalize_logging_mode(
                advanced_vars["logging_mode_map"].get(selected, self.logging_mode)
            )
            self._apply_logging_mode_flags()
        if "start_with_windows_var" in advanced_vars:
            self.start_with_windows = bool(advanced_vars["start_with_windows_var"].get())
        if "cuda_directory_var" in advanced_vars:
            next_cuda_directory = self._normalize_optional_directory(
                advanced_vars["cuda_directory_var"].get()
            )
            if next_cuda_directory and not os.path.isdir(next_cuda_directory):
                raise ValueError(f"CUDA directory not found: {next_cuda_directory}")
            self.cuda_directory = next_cuda_directory
        if self.start_with_windows != previous_start_with_windows:
            try:
                self._set_windows_startup_enabled(self.start_with_windows)
            except Exception as exc:
                self.start_with_windows = previous_start_with_windows
                raise ValueError(
                    f"Could not update Windows startup setting: {exc}"
                ) from exc
        if self.cuda_directory != previous_cuda_directory:
            self._configure_cuda_dll_search_path()
        self._fit_font_to_lines()
        if "bad_words_en_text" in advanced_vars and "bad_words_es_text" in advanced_vars:
            en_text = advanced_vars["bad_words_en_text"].get("1.0", tk.END).strip()
            es_text = advanced_vars["bad_words_es_text"].get("1.0", tk.END).strip()
            self.bad_words_by_lang["en"] = {
                word.strip().lower() for word in en_text.split(",") if word.strip()
            }
            self.bad_words_by_lang["es"] = {
                word.strip().lower() for word in es_text.split(",") if word.strip()
            }
            for lang in self.bad_words_by_lang.keys():
                self.bad_word_filters_enabled[lang] = True
            self._refresh_bad_words()
        if "custom_vocab_en_text" in advanced_vars and "custom_vocab_es_text" in advanced_vars:
            for lang in self.custom_vocabulary_by_lang.keys():
                self.custom_vocab_langs_enabled[lang] = True
            vocab_en_text = advanced_vars["custom_vocab_en_text"].get("1.0", tk.END).strip()
            vocab_es_text = advanced_vars["custom_vocab_es_text"].get("1.0", tk.END).strip()
            self.custom_vocabulary_by_lang["en"] = [
                v.strip() for v in vocab_en_text.split(",") if v.strip()
            ]
            self.custom_vocabulary_by_lang["es"] = [
                v.strip() for v in vocab_es_text.split(",") if v.strip()
            ]


    def _apply_transcription_vars(self, transcription_vars):
        previous_device = self.stt_device
        previous_final_model = self.realtime_stt_final_model
        previous_realtime_model = self.realtime_stt_realtime_model
        previous_silero_sensitivity = self.realtime_stt_silero_sensitivity
        previous_source_lang = self.source_lang
        self.source_lang = self._optional_mapped_setting(
            transcription_vars,
            "stt_source_lang_var",
            "stt_source_lang_map",
            current_value=self.source_lang,
            mapped_default="auto",
        )
        if "stt_device_var" in transcription_vars:
            self.stt_device = self._normalize_stt_device(
                transcription_vars["stt_device_var"].get()
            )
        self.realtime_stt_final_model = self._optional_mapped_setting(
            transcription_vars,
            "realtime_stt_final_model_var",
            "realtime_stt_final_model_map",
            current_value=self.realtime_stt_final_model,
            mapped_default="large-v3",
        )
        self.realtime_stt_realtime_model = self._optional_mapped_setting(
            transcription_vars,
            "realtime_stt_realtime_model_var",
            "realtime_stt_realtime_model_map",
            current_value=self.realtime_stt_realtime_model,
            mapped_default="tiny",
        )
        self.realtime_stt_silero_sensitivity = self._coerce_float_range(
            transcription_vars["realtime_stt_silero_var"].get()
            if "realtime_stt_silero_var" in transcription_vars
            else self.realtime_stt_silero_sensitivity,
            self.realtime_stt_silero_sensitivity, 0.1, 0.9,
        )
        if "show_interim_text_var" in transcription_vars:
            previously_shown = self.show_interim_text
            self.show_interim_text = bool(
                transcription_vars["show_interim_text_var"].get()
            )
            # No recorder rebuild needed - partials always flow (silence
            # adjustment consumes them) and the display hook checks this
            # flag live. Just clear a leftover interim row when turning off.
            if previously_shown and not self.show_interim_text:
                self.live_line = ""
                self.render_text()
        # device/model/sensitivity/language are only read when RealtimeSTT
        # constructs its recorder, so a live rebuild is needed for the
        # change to apply.
        if (
            self.stt_device != previous_device
            or self.realtime_stt_final_model != previous_final_model
            or self.realtime_stt_realtime_model != previous_realtime_model
            or self.realtime_stt_silero_sensitivity != previous_silero_sensitivity
            or self.source_lang != previous_source_lang
        ):
            self._request_capture_restart()


    def _optional_mapped_setting(
        self,
        settings_vars,
        var_key,
        map_key,
        current_value,
        mapped_default,
    ):
        if var_key not in settings_vars or map_key not in settings_vars:
            return current_value
        selected = settings_vars[var_key].get()
        mapping = settings_vars[map_key]
        return mapping.get(selected, mapped_default)


    def _apply_translation_vars(self, translation_vars):
        previous_nllb_config = (
            self.local_nllb_model_name,
            self.local_nllb_device,
        )
        was_translation_enabled = bool(self.translation_enabled)
        new_translation_enabled = was_translation_enabled
        if "enable_translation_var" in translation_vars:
            new_translation_enabled = self._coerce_bool(
                translation_vars["enable_translation_var"].get(),
                default=False,
            )
        self.translation_enabled = new_translation_enabled
        if self.translation_enabled:
            # Translation wins the conflict with live interim text (see
            # sync_interim_with_translation in _build_settings_sections) -
            # enforced again here so it holds even if this is ever called
            # without going through that UI trace.
            self.show_interim_text = False
        if self.translation_enabled != was_translation_enabled:
            self._apply_translation_mode_defaults()
            # Toggling translation changes what feeds the live row (raw
            # partials vs translated stabilized text), so drop any stale
            # source-language text left over from before the toggle.
            if self.translation_enabled and self.live_line:
                self.live_line = ""
                self.render_text()
        else:
            self._normalize_translation_settings()
        self.local_nllb_model_name = self._optional_mapped_setting(
            translation_vars,
            "local_nllb_model_name_var",
            "local_nllb_model_name_map",
            current_value=self.local_nllb_model_name,
            mapped_default=self.LOCAL_NLLB_DEFAULT_MODEL_NAME,
        )
        if "local_nllb_device_var" in translation_vars:
            self.local_nllb_device = self._normalize_local_nllb_device(
                translation_vars["local_nllb_device_var"].get()
            )
        self.local_nllb_target_lang = self._optional_mapped_setting(
            translation_vars,
            "local_nllb_target_lang_var",
            "local_nllb_target_lang_map",
            current_value=self.local_nllb_target_lang,
            mapped_default=self.LOCAL_NLLB_DEFAULT_TARGET_LANG,
        )
        self.local_nllb_max_chars = self._coerce_int_range(
            translation_vars.get("local_nllb_max_chars_var", None).get()
            if "local_nllb_max_chars_var" in translation_vars
            else self.local_nllb_max_chars,
            self.LOCAL_NLLB_DEFAULT_MAX_CHARS,
            250,
            20000,
        )
        next_nllb_config = (
            self.local_nllb_model_name,
            self.local_nllb_device,
        )
        just_disabled = was_translation_enabled and not self.translation_enabled
        if next_nllb_config != previous_nllb_config or just_disabled:
            self._unload_local_nllb_model()
        self._trace_pipeline(
            "translation_toggle_applied",
            "",
            translation_enabled=self.translation_enabled,
            text_translation_provider=self.text_translation_provider,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            local_nllb_model=self.local_nllb_model_name,
            local_nllb_device=self.local_nllb_device,
            local_nllb_target_lang=self.local_nllb_target_lang,
        )
        if just_disabled:
            self._clear_translation_backlog_after_disable()
            self._set_local_nllb_status(
                "Not selected",
                "Translation is off. Enable it above to check or download the Local NLLB model.",
            )
        # Translation is opt-in: don't check the cache, prompt for a
        # ~2.5 GB download, or load the model into memory unless
        # translation is actually enabled (mirrors maybe_start_nllb_prewarm
        # in _build_translation_section).
        if self.translation_enabled:
            self._start_local_nllb_cache_check(
                self._local_nllb_runtime_config(),
                prompt_if_missing=True,
            )


    def _unload_local_nllb_model(self):
        """Release the loaded NLLB tokenizer/model (and its GPU/CPU memory)
        so it isn't held onto once it's no longer needed - either because
        the model/device/cache config changed, or translation was turned
        off entirely."""
        was_cuda = self.local_nllb_resolved_device == "cuda"
        with self.local_nllb_lock:
            self.local_nllb_tokenizer = None
            self.local_nllb_model = None
            self.local_nllb_model_config = None
            self.local_nllb_resolved_device = ""
        self.nllb_model_loaded = False
        self.nllb_ready_config = None
        self.nllb_last_error = ""
        gc.collect()
        if was_cuda:
            # Dropping the last reference frees the Python objects, but
            # PyTorch's CUDA caching allocator holds onto that VRAM for
            # reuse rather than returning it to the driver - empty_cache()
            # actually gives it back, which matters since RealtimeSTT's
            # own models may be competing for the same GPU.
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass


    def _apply_audio_vars(self, audio_vars):
        # Audio device changes are applied immediately by the device-menu callback.
        _ = audio_vars
        return None


    def _settings_window_requires_reposition(self, geometry_monitor_index):
        if not self.settings_geometry:
            return True
        if geometry_monitor_index is None:
            return True
        return geometry_monitor_index != self.settings_monitor_index


    def _open_feature_request_page(self):
        try:
            webbrowser.open(FEATURE_REQUEST_URL)
        except Exception:
            pass


    def _settings_palette(self):
        if self.ui_theme == "dark":
            return {
                "window_bg": "#1E2228",
                "section_bg": "#262A33",
                "text": "#E5E7EB",
                "muted_text": "#9CA3AF",
                "border": "#3A3F4B",
                "input_bg": "#1B1E24",
                "accent": "#5B8FF7",
                "accent_hover": "#6EA0FF",
                "accent_soft": "#22304A",
            }
        return {
            "window_bg": "#C6CAD1",
            "section_bg": "#FFFFFF",
            "text": "#0F172A",
            "muted_text": "#6B7280",
            "border": "#E5E7EB",
            "input_bg": "#F9FAFB",
            "accent": "#5B8FF7",
            "accent_hover": "#4A7FEA",
            "accent_soft": "#EEF4FF",
        }


    def _surface_bootstyle(self, container_bg):
        """bootstyle naming the exact background a ttk widget sits on.

        ttkbootstrap 2.x composites a widget's rounded, anti-aliased raster
        assets against its "surface" - which defaults to the *theme's* own
        background (#ffffff light / #212529 dark), not the background of the
        tk container the widget was actually placed in. This app colors its
        raw tk containers from _settings_palette() instead, and light-mode
        window_bg (#C6CAD1) is far darker than white, so a Progressbar
        dropped on it drew its edge pixels blended toward white - a bright
        square halo around the track. Dark mode looked clean only by
        coincidence: #1E2228 happens to sit within ~4/channel of #212529.

        Naming the real container color as an @#hex surface makes the edges
        blend into it in both themes, and lets the recessed trough shade
        derive from it rather than from a white it isn't sitting on.
        """
        return "@%s" % str(container_bg or "").strip().lower()


    def _font_size_bounds_for_canvas(self, available_height, lines, max_size=None):
        approx = max(12, int(available_height / max(1, lines)))
        resolved_max = int(max_size or min(320, int(approx * 1.6)))
        return 12, resolved_max


    def _binary_search_font_size(self, min_size, max_size, fits_fn):
        lo, hi = int(min_size), int(max_size)
        best = int(min_size)
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits_fn(mid):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best


    def _wrap_lines_to_width(self, lines, max_width):
        if max_width <= 1:
            return lines
        wrapped = []
        for line in lines:
            wrapped.extend(self._wrap_single_line_to_width(line, max_width))
        return wrapped


    def _wrap_single_line_to_width(self, line, max_width):
        words = re.findall(r"\S+|\s+", line)
        current = ""
        wrapped = []
        for token in words:
            current, emitted = self._wrap_token_to_width(current, token, max_width)
            wrapped.extend(emitted)
        if current or not line:
            wrapped.append(current.rstrip())
        return wrapped


    # ------------------------------------------------------------------ #
    # Hardware autodetect                                                   #
    # ------------------------------------------------------------------ #

    # (min_vram_gb, final_model, nllb_model) — tiers assume the final STT
    # model and NLLB model may be resident in VRAM at the same time, so the
    # combined footprint (see the VRAM notes on each dropdown option) is what
    # each threshold is sized against, not either model alone.
    _HARDWARE_VRAM_TIERS = (
        (20.0, "large-v3", "facebook/nllb-200-3.3B"),
        (14.0, "large-v3", "facebook/nllb-200-1.3B"),
        (10.0, "large-v3", "facebook/nllb-200-distilled-1.3B"),
        (8.0, "distil-large-v3", "facebook/nllb-200-distilled-600M"),
        (6.0, "medium", "facebook/nllb-200-distilled-600M"),
        (4.0, "small", "facebook/nllb-200-distilled-600M"),
    )


    def _detect_hardware_vram(self):
        """Returns (cuda_available, vram_gb, gpu_name).

        vram_gb/gpu_name are None when CUDA isn't available (or torch isn't
        installed) so callers fall back to the CPU-only recommendation.
        """
        try:
            import torch
        except Exception:
            return False, None, None
        try:
            if not torch.cuda.is_available():
                return False, None, None
            props = torch.cuda.get_device_properties(0)
            return True, props.total_memory / (1024 ** 3), props.name
        except Exception:
            return False, None, None


    def _recommend_models_for_vram(self, cuda_available, vram_gb):
        if cuda_available and vram_gb:
            for min_vram, final_model, nllb_model in self._HARDWARE_VRAM_TIERS:
                if vram_gb >= min_vram:
                    return {
                        "final_model": final_model,
                        "realtime_model": "tiny",
                        "nllb_model": nllb_model,
                    }
        # No CUDA, or CUDA with less VRAM than the smallest tier: favor
        # light models that stay usable on CPU or a low-VRAM GPU.
        return {
            "final_model": "small" if cuda_available else "base",
            "realtime_model": "tiny",
            "nllb_model": "facebook/nllb-200-distilled-600M",
        }


    def _hardware_recommendation_differs(self):
        """Compares the current autodetect recommendation directly
        against self.* (not any UI var), so this can be checked before
        touching anything - used to gate the launch-time auto-trigger so
        it only actually runs when something would change (or on the
        very first run). Mirrors _autodetect_hardware_models's own
        "only touch device if CUDA is available" behavior, since a
        recommendation that doesn't touch device at all can't "differ"
        on that front either.
        """
        cuda_available, vram_gb, _gpu_name = self._detect_hardware_vram()
        recommendation = self._recommend_models_for_vram(cuda_available, vram_gb)
        if recommendation["final_model"] != self.realtime_stt_final_model:
            return True
        if recommendation["realtime_model"] != self.realtime_stt_realtime_model:
            return True
        if recommendation["nllb_model"] != self.local_nllb_model_name:
            return True
        if cuda_available:
            if self._normalize_stt_device(self.stt_device) != "cuda":
                return True
            if self._normalize_local_nllb_device(self.local_nllb_device) != "cuda":
                return True
        return False


    def _run_hardware_autodetect_from_menu(self, transcription_vars, translation_vars):
        # Reuses _autodetect_hardware_models as-is (same dropdown updates,
        # same result text) - only the display surface differs from the
        # old inline-button version, since there's no inline label next
        # to a menu item to show the result in. Unlike a normal Options
        # Apply, this applies immediately rather than waiting for the
        # user to also click Apply in Options - it's a menu action, not
        # a settings-page edit, and only ever touches the transcription/
        # translation model vars autodetect itself just set, not
        # whatever else may be sitting edited-but-unapplied elsewhere in
        # the still-open Options dialog. Deliberately doesn't touch
        # Options' own dirty_ctx/Apply button afterward: its dirty check
        # is one combined snapshot across all five vars-dicts, so
        # resetting it here would also silently mark any *unrelated*
        # pending edit elsewhere in Options as "already applied" - a
        # real risk of losing that edit, worse than the cosmetic
        # side effect of Apply staying enabled when it doesn't need to.
        result_var = tk.StringVar(value="")
        self._autodetect_hardware_models(transcription_vars, translation_vars, result_var)
        self._apply_transcription_vars(transcription_vars)
        self._apply_translation_vars(translation_vars)
        self.save_settings()
        self._show_hardware_autodetect_result(result_var.get())


    def _autodetect_hardware_models(self, transcription_vars, translation_vars, result_var):
        cuda_available, vram_gb, gpu_name = self._detect_hardware_vram()
        recommendation = self._recommend_models_for_vram(cuda_available, vram_gb)

        final_rev_map = transcription_vars.get("realtime_stt_final_model_rev_map", {})
        realtime_rev_map = transcription_vars.get("realtime_stt_realtime_model_rev_map", {})
        nllb_rev_map = translation_vars.get("local_nllb_model_name_rev_map", {})

        final_display = final_rev_map.get(recommendation["final_model"])
        realtime_display = realtime_rev_map.get(recommendation["realtime_model"])
        nllb_display = nllb_rev_map.get(recommendation["nllb_model"])

        if final_display:
            transcription_vars["realtime_stt_final_model_var"].set(final_display)
        if realtime_display:
            transcription_vars["realtime_stt_realtime_model_var"].set(realtime_display)
        if nllb_display:
            translation_vars["local_nllb_model_name_var"].set(nllb_display)

        # "Auto" already resolves to CUDA at runtime when it's available
        # (_resolve_stt_device/_resolve_local_nllb_device), but setting it
        # explicitly here makes the recommendation self-documenting - the
        # Device dropdowns visibly show what autodetect found, rather than
        # leaving the user to wonder whether "Auto" actually means CUDA.
        if cuda_available:
            if "stt_device_var" in transcription_vars:
                transcription_vars["stt_device_var"].set("CUDA")
            if "local_nllb_device_var" in translation_vars:
                translation_vars["local_nllb_device_var"].set("CUDA")

        if cuda_available and vram_gb:
            hardware_desc = f"{gpu_name or 'NVIDIA GPU'} ({vram_gb:.1f} GB VRAM)"
        else:
            hardware_desc = "No CUDA GPU detected (CPU only)"
        device_line = "Device: CUDA\n" if cuda_available else ""
        result_var.set(
            f"Detected: {hardware_desc}\n"
            "\n"
            f"Final model: {final_display or recommendation['final_model']}\n"
            f"Realtime model: {realtime_display or recommendation['realtime_model']}\n"
            f"NLLB model: {nllb_display or recommendation['nllb_model']}\n"
            f"{device_line}"
            "\n"
            "These settings have been applied automatically."
        )


    def _run_local_nllb_test_from_vars(
        self,
        model_name_var,
        device_var,
        max_chars_var,
        test_button,
        test_status_var,
        model_name_map=None,
    ):
        config = self._local_nllb_config_from_vars(
            model_name_var,
            device_var,
            max_chars_var,
            model_name_map=model_name_map,
        )
        try:
            test_button.config(state=tk.DISABLED)
            test_status_var.set("Testing Local NLLB...")
        except Exception:
            pass
        self._set_local_nllb_status(
            "Loading",
            "Testing Local NLLB from the local cache.",
        )
        self.update_status("Testing Local NLLB translation...")
        Thread(
            target=lambda: self._run_local_nllb_test_worker(
                config,
                test_button,
                test_status_var,
            ),
            daemon=True,
        ).start()


    def _local_nllb_config_from_vars(
        self,
        model_name_var,
        device_var,
        max_chars_var,
        model_name_map=None,
    ):
        selected_model_name = model_name_var.get().strip()
        if model_name_map:
            selected_model_name = model_name_map.get(selected_model_name, selected_model_name)
        return {
            "model_name": selected_model_name or self.LOCAL_NLLB_DEFAULT_MODEL_NAME,
            "device": self._normalize_local_nllb_device(device_var.get()),
            "max_chars": self._coerce_int_range(
                max_chars_var.get(),
                self.LOCAL_NLLB_DEFAULT_MAX_CHARS,
                250,
                20000,
            ),
        }


    def _run_local_nllb_test_worker(self, config, test_button, test_status_var):
        message, status = self._execute_local_nllb_test(config)
        self._finish_local_nllb_test(test_button, test_status_var, message, status)


    def _execute_local_nllb_test(self, config, announce_result=True):
        """Load Local NLLB and run a sample translation to confirm it works.

        `announce_result` controls whether the sample translation/timing is
        surfaced as the persistent status detail. Automatic verification
        (every time the settings panel opens and finds the model already
        cached) passes False so it silently marks the model ready without
        showing "Success: <sample translation>" on every startup; the
        explicit "Test Local NLLB" button passes True so the user sees the
        result they asked for.
        """
        sample = "En el principio cri\u00f3 Dios los cielos y la tierra."
        try:
            translated, elapsed_ms = self._translate_with_local_nllb(
                sample,
                model_name=config["model_name"],
                device=config["device"],
                source_lang="spa_Latn",
                target_lang="eng_Latn",
                max_chars=config["max_chars"],
            )
            if not (translated or "").strip():
                raise sr.RequestError(self.LOCAL_NLLB_FAILED_MESSAGE)
            message = (
                "Success: "
                f"{self._short_response_preview(translated, limit=120)} "
                f"({elapsed_ms} ms)"
            )
            self.nllb_model_loaded = True
            self.nllb_ready_config = self._local_nllb_config_tuple_from_config(config)
            self.nllb_last_error = ""
            self._set_local_nllb_status(
                "Ready",
                message if announce_result else "",
            )
            return (
                message,
                "Local NLLB test succeeded",
            )
        except Exception as exc:
            message = self._local_nllb_exception_message(exc, phase="verification")
            self.nllb_model_loaded = False
            self.nllb_ready_config = None
            self.nllb_last_error = message
            self._set_local_nllb_status("Error", message)
            return f"Failed: {message}", f"Local NLLB test failed: {message}"


    def _finish_local_nllb_test(
        self,
        test_button,
        test_status_var,
        message,
        status,
    ):
        def finish():
            try:
                test_status_var.set(message)
                test_button.config(state=tk.NORMAL)
            except Exception:
                pass
            self._refresh_local_nllb_runtime_ui()
            self.update_status(status)

        try:
            self.root.after(0, finish)
        except Exception:
            finish()


    def _local_nllb_runtime_config(self):
        return {
            "model_name": (self.local_nllb_model_name or "").strip()
            or self.LOCAL_NLLB_DEFAULT_MODEL_NAME,
            "device": self._normalize_local_nllb_device(self.local_nllb_device),
            "max_chars": self._coerce_int_range(
                self.local_nllb_max_chars,
                self.LOCAL_NLLB_DEFAULT_MAX_CHARS,
                250,
                20000,
            ),
        }


    def _local_nllb_config_tuple_from_config(self, config):
        config = config or {}
        return (
            str(config.get("model_name") or self.LOCAL_NLLB_DEFAULT_MODEL_NAME).strip()
            or self.LOCAL_NLLB_DEFAULT_MODEL_NAME,
            self._normalize_local_nllb_device(config.get("device", "auto")),
        )


    def _local_nllb_status_message(self):
        if self.nllb_status_detail:
            return self.nllb_status_detail
        model_name = (self.local_nllb_model_name or "").strip() or self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        messages = {
            "Not selected": "Select Local NLLB to check the model cache.",
            "Checking": f"Checking whether {model_name} is already cached.",
            "Not downloaded": (
                "Model not downloaded. Click Download Local NLLB model to enable."
            ),
            "Downloading": (
                f"Downloading {model_name}. This only needs to happen once."
            ),
            "Downloaded": "Model files are downloaded. Verifying Local NLLB.",
            "Loading": "Loading Local NLLB and running a test translation.",
            "Ready": "Local NLLB verified and ready.",
            "Error": self.nllb_last_error or "Local NLLB setup needs attention.",
        }
        return messages.get(self.nllb_status, "")


    def _set_local_nllb_status(self, status, message="", error=""):
        self.nllb_status = str(status or "").strip() or "Not selected"
        self.nllb_status_detail = str(message or "").strip()
        if self.nllb_status == "Error":
            self.nllb_last_error = str(error or message or "").strip()
            self.nllb_model_loaded = False
            self.nllb_ready_config = None
        elif self.nllb_status in ("Checking", "Downloading", "Downloaded", "Loading"):
            self.nllb_last_error = ""
            self.nllb_model_loaded = False
        elif self.nllb_status == "Ready":
            self.nllb_last_error = ""
            self.local_nllb_last_unready_notice = 0.0
        elif self.nllb_status == "Not selected":
            self.nllb_last_error = ""
        if self.nllb_status != "Ready":
            self.local_nllb_last_unready_notice = 0.0

        def update():
            try:
                if self.local_nllb_status_var is not None:
                    self.local_nllb_status_var.set(self.nllb_status)
                if self.local_nllb_message_var is not None:
                    self.local_nllb_message_var.set(self._local_nllb_status_message())
            except Exception:
                pass
            self._sync_local_nllb_progress_popup()
            self._refresh_local_nllb_runtime_ui()

        try:
            self.root.after(0, update)
        except Exception:
            update()


    def _download_or_check_local_nllb_from_vars(
        self,
        model_name_var,
        device_var,
        max_chars_var,
        model_name_map=None,
    ):
        """Single button's action: download when the model isn't on disk
        yet, or re-check the Hub for updates once it's already Ready (see
        _refresh_local_nllb_runtime_ui, which relabels the same button
        between the two)."""
        config = self._local_nllb_config_from_vars(
            model_name_var,
            device_var,
            max_chars_var,
            model_name_map=model_name_map,
        )
        if self.nllb_status == "Ready":
            self._start_local_nllb_update_check(config)
        else:
            self._start_local_nllb_download(config, prompt=True)


    def _start_local_nllb_cache_check(
        self,
        config,
        prompt_if_missing=False,
    ):
        config = dict(config or self._local_nllb_runtime_config())
        config_tuple = self._local_nllb_config_tuple_from_config(config)
        if (
            self.nllb_status == "Ready"
            and self.nllb_model_loaded
            and self.nllb_ready_config == config_tuple
        ):
            self._refresh_local_nllb_runtime_ui()
            return
        if self.nllb_download_in_progress:
            self._set_local_nllb_status(
                "Downloading",
                "Local NLLB model is still downloading.",
            )
            return
        if self.nllb_check_in_progress:
            return
        self.nllb_check_in_progress = True
        self.nllb_model_loaded = False
        self.nllb_ready_config = None
        self._set_local_nllb_status(
            "Checking",
            f"Checking whether {config['model_name']} is already cached.",
        )
        Thread(
            target=lambda: self._run_local_nllb_cache_check_worker(
                config,
                prompt_if_missing,
            ),
            daemon=True,
        ).start()


    def _run_local_nllb_cache_check_worker(
        self,
        config,
        prompt_if_missing,
    ):
        result = {"cached": False, "error": ""}
        try:
            dependency_error = self._local_nllb_dependencies_error()
            if dependency_error:
                result["error"] = dependency_error
            else:
                result["cached"] = self._is_local_nllb_model_cached(config)
        except Exception as exc:
            result["error"] = self._local_nllb_exception_message(
                exc,
                phase="cache_check",
            )

        def finish():
            self._finish_local_nllb_cache_check(
                config,
                prompt_if_missing,
                result,
            )

        try:
            self.root.after(0, finish)
        except Exception:
            finish()


    def _finish_local_nllb_cache_check(
        self,
        config,
        prompt_if_missing,
        result,
    ):
        self.nllb_check_in_progress = False
        if result.get("error"):
            self._set_local_nllb_status("Error", result["error"])
            self.update_status(result["error"])
            self._mark_startup_translation_ready()
            return
        if result.get("cached"):
            self._set_local_nllb_status(
                "Downloaded",
                "Local NLLB model files are already cached.",
            )
            self._start_local_nllb_verification(config)
            return
        self._set_local_nllb_status(
            "Not downloaded",
            "Model not downloaded. Click Download Local NLLB model to enable.",
        )
        if not prompt_if_missing:
            self._mark_startup_translation_ready()
            return
        if self._confirm_local_nllb_download(config.get("model_name", "")):
            self._start_local_nllb_download(config, prompt=False)
            return
        self._set_local_nllb_status(
            "Not downloaded",
            self.LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE,
        )
        self.update_status(self.LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE)
        self._mark_startup_translation_ready()


    def _start_local_nllb_verification(self, config):
        self._set_local_nllb_status(
            "Loading",
            "Loading Local NLLB and running a test translation.",
        )
        self.update_status(f"Loading Local NLLB weights ({config['model_name']})...")
        Thread(
            target=lambda: self._run_local_nllb_verification_worker(config),
            daemon=True,
        ).start()


    def _run_local_nllb_verification_worker(self, config):
        _message, status = self._execute_local_nllb_test(config, announce_result=False)

        def finish():
            self._refresh_local_nllb_runtime_ui()
            self.update_status(status)
            self._mark_startup_translation_ready()

        try:
            self.root.after(0, finish)
        except Exception:
            finish()


    def _start_local_nllb_download(self, config, prompt=True):
        config = dict(config or self._local_nllb_runtime_config())
        if self.nllb_download_in_progress:
            self._set_local_nllb_status(
                "Downloading",
                "Local NLLB model is still downloading.",
            )
            return
        dependency_error = self._local_nllb_dependencies_error()
        if dependency_error:
            self._set_local_nllb_status("Error", dependency_error)
            self.update_status(dependency_error)
            return
        if prompt and not self._confirm_local_nllb_download(config.get("model_name", "")):
            self._set_local_nllb_status(
                "Not downloaded",
                self.LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE,
            )
            self.update_status(self.LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE)
            return
        self.nllb_download_in_progress = True
        self.nllb_model_loaded = False
        self.nllb_ready_config = None
        with self.local_nllb_lock:
            self.local_nllb_tokenizer = None
            self.local_nllb_model = None
            self.local_nllb_model_config = None
            self.local_nllb_resolved_device = ""
        self._set_local_nllb_status(
            "Downloading",
            f"Downloading {config['model_name']}. This only needs to happen once.",
        )
        self.update_status(f"Downloading Local NLLB model ({config['model_name']})...")
        Thread(
            target=lambda: self._run_local_nllb_download_worker(config),
            daemon=True,
        ).start()


    def _run_local_nllb_download_worker(self, config):
        try:
            if self._is_local_nllb_model_cached(config):
                self._set_local_nllb_status(
                    "Downloaded",
                    "Local NLLB model files are already cached.",
                )
                self._set_local_nllb_status(
                    "Loading",
                    "Loading Local NLLB and running a test translation.",
                )
                self._run_local_nllb_verification_worker(config)
                return
            self._download_local_nllb_model_files(config)
            self._set_local_nllb_status(
                "Downloaded",
                "Local NLLB model downloaded. Verifying the local cache.",
            )
            self._set_local_nllb_status(
                "Loading",
                "Loading Local NLLB and running a test translation.",
            )
            self._run_local_nllb_verification_worker(config)
        except Exception as exc:
            message = self._local_nllb_exception_message(exc, phase="download")
            self.nllb_model_loaded = False
            self.nllb_ready_config = None
            self._set_local_nllb_status("Error", message)
            self.update_status(message)
            self._mark_startup_translation_ready()
        finally:
            self.nllb_download_in_progress = False
            try:
                self.root.after(0, self._refresh_local_nllb_runtime_ui)
            except Exception:
                self._refresh_local_nllb_runtime_ui()


    def _start_local_nllb_update_check(self, config):
        """"Check for Updates": unlike _start_local_nllb_download, this
        always re-fetches from the Hub (skipping the already-cached
        early-exit in _run_local_nllb_download_worker) so a newer revision
        actually gets pulled. Only reachable once nllb_status is already
        "Ready" (see _download_or_check_local_nllb_from_vars), so no
        confirmation prompt - the model is already on disk either way."""
        config = dict(config or self._local_nllb_runtime_config())
        if self.nllb_download_in_progress:
            return
        dependency_error = self._local_nllb_dependencies_error()
        if dependency_error:
            self._set_local_nllb_status("Error", dependency_error)
            self.update_status(dependency_error)
            return
        self.nllb_download_in_progress = True
        self.nllb_model_loaded = False
        self.nllb_ready_config = None
        with self.local_nllb_lock:
            self.local_nllb_tokenizer = None
            self.local_nllb_model = None
            self.local_nllb_model_config = None
            self.local_nllb_resolved_device = ""
        self._set_local_nllb_status(
            "Downloading",
            f"Checking Hugging Face for updates to {config['model_name']}.",
        )
        self.update_status(f"Checking for Local NLLB updates ({config['model_name']})...")
        Thread(
            target=lambda: self._run_local_nllb_update_check_worker(config),
            daemon=True,
        ).start()


    def _run_local_nllb_update_check_worker(self, config):
        try:
            self._download_local_nllb_model_files(config)
            self._set_local_nllb_status(
                "Downloaded",
                "Local NLLB is up to date. Verifying the local cache.",
            )
            self._set_local_nllb_status(
                "Loading",
                "Loading Local NLLB and running a test translation.",
            )
            self._run_local_nllb_verification_worker(config)
        except Exception as exc:
            message = self._local_nllb_exception_message(exc, phase="download")
            self.nllb_model_loaded = False
            self.nllb_ready_config = None
            self._set_local_nllb_status("Error", message)
            self.update_status(message)
        finally:
            self.nllb_download_in_progress = False
            try:
                self.root.after(0, self._refresh_local_nllb_runtime_ui)
            except Exception:
                self._refresh_local_nllb_runtime_ui()


    def _local_nllb_dependencies_error(self):
        missing = []
        for module_name, package_name in (
            ("transformers", "transformers"),
            ("sentencepiece", "sentencepiece"),
            ("torch", "torch"),
        ):
            try:
                if importlib.util.find_spec(module_name) is None:
                    missing.append(package_name)
            except Exception:
                missing.append(package_name)
        if missing:
            return self.LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE
        return ""


    def _import_local_nllb_dependencies(self):
        dependency_error = self._local_nllb_dependencies_error()
        if dependency_error:
            raise sr.RequestError(dependency_error)
        try:
            import torch as torch_module
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            from transformers.utils import logging as hf_logging
            # transformers prints its own "Loading weights" tqdm bar (and
            # huggingface_hub its own download bars) straight to the
            # terminal. Silence both — the app already reflects the
            # equivalent Loading/Downloading phases via update_status/
            # _set_local_nllb_status.
            hf_logging.disable_progress_bar()
        except Exception as exc:
            raise sr.RequestError(self.LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE) from exc
        return torch_module, AutoModelForSeq2SeqLM, AutoTokenizer


    def _local_nllb_model_kwargs(self, local_files_only=True):
        return {"local_files_only": bool(local_files_only)}


    def _is_local_nllb_model_cached(self, config):
        _torch_module, AutoModelForSeq2SeqLM, AutoTokenizer = self._import_local_nllb_dependencies()
        kwargs = self._local_nllb_model_kwargs(local_files_only=True)
        model_name = config.get("model_name") or self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                src_lang="spa_Latn",
                **kwargs,
            )
            model = AutoModelForSeq2SeqLM.from_pretrained(model_name, **kwargs)
            del tokenizer
            del model
            gc.collect()
            return True
        except Exception:
            return False


    def _download_local_nllb_model_files(self, config):
        _torch_module, AutoModelForSeq2SeqLM, AutoTokenizer = self._import_local_nllb_dependencies()
        kwargs = self._local_nllb_model_kwargs(local_files_only=False)
        model_name = config.get("model_name") or self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            src_lang="spa_Latn",
            **kwargs,
        )
        model = AutoModelForSeq2SeqLM.from_pretrained(model_name, **kwargs)
        del tokenizer
        del model
        gc.collect()


    def _local_nllb_exception_message(self, exc, phase="translation"):
        text = str(exc or "")
        lower = text.lower()
        known_messages = (
            self.LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE,
            self.LOCAL_NLLB_CACHE_ERROR_MESSAGE,
            self.LOCAL_NLLB_NOT_READY_MESSAGE,
            self.LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE,
            self.LOCAL_NLLB_DOWNLOAD_FAILED_MESSAGE,
            self.LOCAL_NLLB_CUDA_OOM_MESSAGE,
            self.LOCAL_NLLB_VERIFICATION_CUDA_OOM_MESSAGE,
        )
        for message in known_messages:
            if message and message in text:
                if phase == "verification" and message == self.LOCAL_NLLB_CUDA_OOM_MESSAGE:
                    return self.LOCAL_NLLB_VERIFICATION_CUDA_OOM_MESSAGE
                return message
        if self._is_local_nllb_cuda_oom_exception(exc):
            if phase == "verification":
                return self.LOCAL_NLLB_VERIFICATION_CUDA_OOM_MESSAGE
            return self.LOCAL_NLLB_CUDA_OOM_MESSAGE
        if any(token in lower for token in ("permission", "access is denied", "no space", "disk", "quota", "read-only")):
            return self.LOCAL_NLLB_CACHE_ERROR_MESSAGE
        if phase == "download":
            return self.LOCAL_NLLB_DOWNLOAD_FAILED_MESSAGE
        if phase == "cache_check":
            return self.LOCAL_NLLB_NOT_READY_MESSAGE
        if phase == "verification":
            return self.LOCAL_NLLB_FAILED_MESSAGE
        return self.LOCAL_NLLB_FAILED_MESSAGE


    def _local_nllb_ready_for_translation(self):
        if self._active_text_translation_provider() != "local_nllb":
            return True
        if self.nllb_download_in_progress or self.nllb_check_in_progress:
            return False
        if self.nllb_status != "Ready" or not self.nllb_model_loaded:
            return False
        return self.nllb_ready_config == self._local_nllb_config_tuple_from_config(
            self._local_nllb_runtime_config()
        )


    def _maybe_report_local_nllb_not_ready(self, force=False):
        now = time.time()
        if not force and now - self.local_nllb_last_unready_notice < 12.0:
            return
        self.local_nllb_last_unready_notice = now
        message = self.LOCAL_NLLB_NOT_READY_MESSAGE
        if self.nllb_download_in_progress:
            message = "Local NLLB model is still downloading."
        elif self.nllb_status == "Not downloaded" and self.nllb_status_detail:
            message = self.nllb_status_detail
        elif self.nllb_status == "Error" and self.nllb_last_error:
            message = self.nllb_last_error
        self.update_status(message)


    def _sync_translation_toggle_runtime(self, enable_translation_var):
        enabled = self._coerce_bool(enable_translation_var.get(), default=False)
        previous = bool(self.translation_enabled)
        if enabled == previous:
            return
        self.translation_enabled = enabled
        self._apply_translation_mode_defaults()
        if enabled and self.live_line:
            self.live_line = ""
            self.render_text()
        if previous and not enabled:
            self._clear_translation_backlog_after_disable()
        self._trace_pipeline(
            "translation_toggle_runtime",
            "",
            translation_enabled=self.translation_enabled,
            text_translation_provider=self.text_translation_provider,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
        )
