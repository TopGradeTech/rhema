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
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from tkinter import colorchooser
from tkinter import filedialog
from threading import Thread
import time
import re
import os
import sys
import traceback
import math
import gc
import importlib.util
import webbrowser
import ttkbootstrap as ttkb
from ttkbootstrap.constants import PRIMARY
from PIL import Image, ImageGrab, ImageTk

from languages import whisper_language_options, nllb_language_options
from tooltip import Tooltip

DONATE_URL = "https://www.paypal.com/donate/?hosted_button_id=N36X2WBFVU9U8"
# GitHub Discussions "Ideas" rather than a mailto: requests land somewhere
# public, searchable and upvotable instead of in one inbox, and the app stops
# shipping a personal address to every install. Requires the repo to be
# public - the link just 404s for anyone not signed in with access otherwise.
FEATURE_REQUEST_URL = "https://github.com/TopGradeTech/rhema/discussions/categories/ideas"

# Output window snapshot (replaces the old live text preview): a periodic
# screenshot thumbnail instead of a per-render text mirror, so the
# Controller window doesn't need to reformat/rewrap text on every single
# render tick just to keep a duplicate preview in sync.
_OUTPUT_SNAPSHOT_INTERVAL_MS = 15_000  # 15 seconds between snapshots
_OUTPUT_SNAPSHOT_WIDTH = 640  # thumbnail width in px; height follows the output window's aspect ratio


class SettingsUIMixin:
    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return

        settings_window = tk.Toplevel(self.root)
        self.settings_window = settings_window
        settings_window.title("Rhema Controller")
        self.apply_dark_title_bar(settings_window, dark=(self.ui_theme == "dark"))
        self._apply_settings_geometry(settings_window)
        palette = self._settings_palette()
        self._ui_palette = palette
        settings_bg = palette["window_bg"]
        section_bg = palette["section_bg"]
        settings_fg = palette["text"]
        settings_window.configure(bg=settings_bg)
        label_font = (self.ui_font_family, 10)
        label_opts = {"bg": section_bg, "fg": settings_fg, "font": label_font}
        section_font = (self.ui_font_family, 12, "bold")

        settings_window.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Built eagerly but hidden (see _build_options_dialog's own
        # docstring): Display/Audio/Transcription/Translation/Advanced
        # all live there now, but the Translation section's build-time
        # side effects (NLLB cache-check/prewarm kickoff, marking startup
        # translation readiness) still need to fire unconditionally at
        # launch, exactly as when this content lived directly in this
        # window - only visibility is deferred to File > Options.
        transcription_vars, translation_vars = self._build_options_dialog(
            settings_window, palette
        )

        # Needs transcription_vars/translation_vars for the Hardware
        # Autodetect menu item, which is why this runs after the call
        # above rather than before it.
        self._build_menu_bar(settings_window, transcription_vars, translation_vars)

        self._build_preview_section(
            settings_window, label_opts, section_bg, settings_fg, section_font
        )

        button_frame = tk.Frame(settings_window, bg=settings_bg)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(8, 12))

        # Packed before status_section (below) even though it's drawn on
        # the opposite side: pack carves cavity space in packing order, so
        # a fixed-size side=RIGHT widget packed *after* an expand=True
        # side=LEFT one can get squeezed down to a sliver if that first
        # widget's own request leaves little cavity left over. Packing
        # this fixed-size button first guarantees it always gets its full
        # natural size before status_section claims (and grows into) the
        # remainder.
        self.toggle_fullscreen_button = self._make_button(
            button_frame,
            "Toggle Fullscreen",
            command=self.toggle_fullscreen,
            primary=True,
        )
        self.toggle_fullscreen_button.pack(side=tk.RIGHT, padx=(0, 10), pady=10)

        status_section = tk.LabelFrame(
            button_frame,
            text="Status",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        status_section.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10), pady=6)

        self.status_label = tk.Label(
            status_section,
            text="Status: ",
            anchor="w",
            justify=tk.LEFT,
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 10),
            bd=0,
            highlightthickness=0,
        )
        self.status_label.pack(fill=tk.X)
        self._bind_responsive_wraplength(self.status_label)

        self.chunk_latency_label = tk.Label(
            status_section,
            text="Latency: --",
            anchor="w",
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9),
            bd=0,
            highlightthickness=0,
        )
        self.chunk_latency_label.pack(fill=tk.X, pady=(4, 0))
        self.audio_level_label = tk.Label(
            status_section,
            text="Audio level",
            anchor="w",
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9),
            bd=0,
            highlightthickness=0,
        )
        self.audio_level_label.pack(fill=tk.X, pady=(4, 0))
        self.audio_level_bar = tk.Canvas(
            status_section,
            height=12,
            bg="#1A1A1A",
            highlightthickness=1,
            highlightbackground="#3A3A3A",
            bd=0,
        )
        self.audio_level_bar.pack(fill=tk.X, pady=(2, 0))
        self.audio_level_fill_item = self.audio_level_bar.create_rectangle(
            0, 0, 0, 12, fill="#5B8FF7", outline=""
        )
        self.pause_button = self._make_button(
            status_section,
            "Pause",
            command=self.toggle_pause,
            primary=True,
        )
        self.pause_button.pack(anchor="w", pady=(8, 0))

        self._start_audio_level_updates()
        if not self.app_startup_ready:
            self._show_startup_loading_overlay(settings_window, settings_bg, settings_fg)

    def _build_options_dialog(self, controller_window, palette):
        """Builds the Options dialog (Display/Audio/Transcription/
        Translation/Advanced) once, hidden, during Controller startup -
        see open_settings for why this can't be deferred to first click.
        Returns (transcription_vars, translation_vars), the two dicts
        the Controller's menu bar needs for Hardware Autodetect.
        """
        options_window = tk.Toplevel(controller_window)
        self.options_window = options_window
        options_window.title("Rhema Options")
        self.apply_dark_title_bar(options_window, dark=(self.ui_theme == "dark"))
        self._apply_options_geometry(options_window)
        settings_bg = palette["window_bg"]
        section_bg = palette["section_bg"]
        settings_fg = palette["text"]
        options_window.configure(bg=settings_bg)
        label_opts = {"bg": section_bg, "fg": settings_fg, "font": (self.ui_font_family, 10)}
        section_font = (self.ui_font_family, 12, "bold")

        # Hidden rather than destroyed on close, so reopening via
        # File > Options doesn't need to rebuild all this state (and
        # doesn't re-trigger the Translation section's cache-check/
        # prewarm side effect a second time).
        options_window.protocol("WM_DELETE_WINDOW", options_window.withdraw)

        content = self._build_settings_canvas(options_window, settings_bg)
        display_vars, audio_vars, transcription_vars, translation_vars, advanced_vars = (
            self._build_settings_sections(
                content,
                options_window,
                label_opts,
                section_bg,
                settings_fg,
                section_font,
            )
        )

        dirty_ctx = self._new_settings_dirty_context()

        button_frame = tk.Frame(options_window, bg=settings_bg)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(8, 12))

        save_button = self._make_button(
            button_frame,
            "Apply",
            command=lambda: self._apply_settings_from_controller(
                display_vars,
                audio_vars,
                transcription_vars,
                translation_vars,
                advanced_vars,
                dirty_ctx,
            ),
            primary=True,
        )
        save_button.pack(side=tk.RIGHT, padx=10, pady=10)

        apply_style = "apply.primary.TButton"
        try:
            self.style.configure(
                apply_style,
                background="#5B8FF7",
                foreground="#FFFFFF",
                font=(self.ui_font_family, 10, "bold"),
                padding=(12, 6),
            )
            self.style.map(
                apply_style,
                background=[
                    ("disabled", "#788192"),
                    ("active", "#4A7FEA"),
                    ("pressed", "#4A7FEA"),
                ],
                foreground=[("disabled", "#F2F4F8")],
            )
            save_button.configure(style=apply_style)
        except Exception:
            pass
        dirty_ctx["save_button"] = save_button
        dirty_ctx["apply_style"] = apply_style

        self._collect_settings_vars_for_dirty_tracking(display_vars, dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(audio_vars, dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(transcription_vars, dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(translation_vars, dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(advanced_vars, dirty_ctx)
        dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(dirty_ctx)
        dirty_ctx["dirty_ready"] = True
        self._set_settings_dirty_state(dirty_ctx, False, force=True)

        options_window.withdraw()
        # Stashed so _hide_startup_loading_overlay can trigger Hardware
        # Autodetect automatically once startup finishes, without
        # threading these two dicts through more function signatures.
        self._autodetect_transcription_vars = transcription_vars
        self._autodetect_translation_vars = translation_vars
        return transcription_vars, translation_vars

    def _show_options_dialog(self):
        if self.options_window is not None and self.options_window.winfo_exists():
            self.options_window.deiconify()
            self.options_window.focus_force()

    def _show_startup_loading_overlay(self, settings_window, settings_bg, settings_fg):
        """Block settings interaction behind a full-window overlay until
        RealtimeSTT, Local NLLB, and (if the video overlay was left on) the
        camera scan have all finished their initial load/verify pass, so
        the user can't change device/model settings out from under a load
        already in progress. Also uses the wait to silently rescan camera
        devices so the video device dropdown reflects last run's saved
        selection instead of the "(click Refresh)" placeholder. Shown once
        per app run; see _check_startup_ready/_mark_startup_stt_ready/
        _mark_startup_translation_ready/_mark_startup_video_scan_ready.

        Deliberately just a plain message and spinner - all readiness
        flags are marked "ready" on failure as well as success (a
        terminal-state gate, not a success gate), so a real error hides
        this overlay almost immediately rather than sitting behind it, and
        surfaces via the ordinary status_label underneath (update_status)
        instead of needing its own error text here.
        """
        overlay = tk.Frame(settings_window, bg=settings_bg)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
        overlay.bind("<Button-1>", lambda _e: "break")

        center = tk.Frame(overlay, bg=settings_bg)
        center.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(
            center,
            text="Loading...",
            bg=settings_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 14, "bold"),
        ).pack(pady=(0, 14))
        progress = ttkb.Progressbar(
            center,
            mode="indeterminate",
            length=320,
            bootstyle=self._surface_bootstyle(settings_bg),
        )
        progress.pack()
        progress.start(15)

        self._startup_loading_overlay = overlay
        self._startup_loading_progress = progress
        # Rescan camera devices behind the overlay so the video device
        # dropdown reflects the previously saved selection (video_device_index)
        # instead of sitting on the "(click Refresh)" placeholder until the
        # user manually clicks Refresh - only relevant if the video overlay
        # was actually in use last run.
        if self.video_feed_enabled:
            self._refresh_video_devices(show_popup=False)
        else:
            self._mark_startup_video_scan_ready()
        self._poll_startup_overlay_status()

    def _poll_startup_overlay_status(self):
        if self._startup_loading_overlay is None:
            return
        self._check_startup_ready()
        if self._startup_loading_overlay is not None:
            self.root.after(500, self._poll_startup_overlay_status)

    def _hide_startup_loading_overlay(self):
        progress = self._startup_loading_progress
        if progress is not None:
            try:
                progress.stop()
            except Exception:
                pass
        overlay = self._startup_loading_overlay
        if overlay is not None:
            try:
                overlay.destroy()
            except Exception:
                pass
        self._startup_loading_overlay = None
        self._startup_loading_progress = None
        if self.settings_window is not None and self._settings_menu_bar is not None:
            try:
                self.settings_window.config(menu=self._settings_menu_bar)
            except Exception:
                pass
        # Runs the same Hardware Autodetect as File > Hardware Autodetect,
        # but only on the very first run (nothing configured yet) or
        # when the recommendation would actually change something -
        # otherwise this would show its result popup on every single
        # launch even once already sitting on the right models. A short
        # delay avoids popping the result dialog in the same instant the
        # loading overlay disappears.
        if (
            self._autodetect_transcription_vars is not None
            and self._autodetect_translation_vars is not None
            and (self.is_first_run or self._hardware_recommendation_differs())
        ):
            self.root.after(
                300,
                lambda: self._run_hardware_autodetect_from_menu(
                    self._autodetect_transcription_vars,
                    self._autodetect_translation_vars,
                ),
            )

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

    def _set_settings_dirty_state(self, dirty_ctx, is_dirty, force=False):
        if not force and is_dirty == bool(dirty_ctx.get("dirty_value")):
            return
        dirty_ctx["dirty_value"] = bool(is_dirty)
        save_button = dirty_ctx.get("save_button")
        if save_button is None:
            return
        try:
            save_button.config(style=dirty_ctx.get("apply_style"))
            if is_dirty and not self.is_applying_settings:
                save_button.config(state=tk.NORMAL)
            else:
                save_button.config(state=tk.DISABLED)
        except Exception:
            pass

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

    def _refresh_audio_devices(self):
        from threading import Thread

        def _worker():
            self._suspend_capture_for_device_scan()
            self.device_refresh_in_progress = True
            try:
                devices = self.get_audio_devices()
            finally:
                self.device_refresh_in_progress = False
                self._resume_capture_after_device_scan()

            def _update_ui():
                self.devices = devices
                if self.device_menu is not None:
                    menu = self.device_menu["menu"]
                    menu.delete(0, "end")
                    for device in devices:
                        menu.add_command(
                            label=device,
                            command=tk._setit(self.device_var, device),
                        )
                preferred_label = self._resolve_preferred_device_label(
                    self.preferred_device_label
                )
                if preferred_label:
                    self.device_var.set(preferred_label)
                elif self.device_var.get() not in devices:
                    self.device_var.set(devices[0] if devices else "No devices")
                if self.device_var.get() in self.device_indices:
                    self.microphone_index = devices.index(self.device_var.get())
                else:
                    self.microphone_index = None

            try:
                self.root.after(0, _update_ui)
            except Exception:
                pass

        Thread(target=_worker, daemon=True).start()

    def _refresh_video_devices(self, show_popup=True):
        # cv2's DirectShow backend is not safe to probe from two threads at
        # once (concurrent enumerate_video_devices calls have crashed the
        # process with a native heap-corruption fault), so ignore Refresh
        # clicks while a scan is already running.
        if getattr(self, "_video_scan_in_progress", False):
            return
        self._video_scan_in_progress = True
        refresh_button = getattr(self, "video_refresh_button", None)
        if refresh_button is not None:
            try:
                refresh_button.config(state="disabled")
            except Exception:
                pass
        max_probe = 5
        # show_popup=False for the automatic startup scan (see
        # _show_startup_loading_overlay) - it already runs behind the
        # startup loading overlay, so a second modal popup on top of it
        # would be redundant.
        if show_popup:
            self._show_video_scan_progress_popup(max_probe)

        def _on_progress(completed, total):
            try:
                self.root.after(0, lambda: self._update_video_scan_progress_popup(completed, total))
            except Exception:
                pass

        def _worker():
            try:
                devices = self.enumerate_video_devices(max_probe=max_probe, on_progress=_on_progress)
            except Exception:
                devices = self.video_devices

            def _update_ui():
                self.video_devices = devices
                labels = [self._video_device_label(i) for i in devices] or ["(click Refresh)"]
                if self.video_device_menu is not None:
                    menu = self.video_device_menu["menu"]
                    menu.delete(0, "end")
                    for label in labels:
                        menu.add_command(
                            label=label,
                            command=tk._setit(self.video_device_var, label),
                        )
                if self.video_device_var is not None:
                    current_label = self._video_device_label(self.video_device_index)
                    if devices and current_label in labels:
                        self.video_device_var.set(current_label)
                    else:
                        self.video_device_var.set(labels[0])
                self._video_scan_in_progress = False
                if refresh_button is not None:
                    try:
                        refresh_button.config(state="normal")
                    except Exception:
                        pass
                self._close_video_scan_progress_popup()
                self._mark_startup_video_scan_ready()

            try:
                self.root.after(0, _update_ui)
            except Exception:
                self._video_scan_in_progress = False
                self._close_video_scan_progress_popup()
                self._mark_startup_video_scan_ready()

        Thread(target=_worker, daemon=True).start()

    def _show_video_scan_progress_popup(self, max_probe):
        parent = (
            self.settings_window
            if self.settings_window is not None and self.settings_window.winfo_exists()
            else self.root
        )
        palette = self._settings_palette()
        popup = tk.Toplevel(parent)
        popup.title("Scanning Cameras")
        popup.configure(bg=palette["section_bg"])
        popup.resizable(False, False)
        popup.transient(parent)
        # The underlying cv2 probe can't be interrupted mid-call, so there's
        # nothing a Cancel/close action could actually do - block it instead
        # of leaving a close button that appears to hang.
        popup.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = tk.Frame(popup, bg=palette["section_bg"], padx=24, pady=18)
        frame.pack()
        tk.Label(
            frame,
            text="Scanning camera devices...",
            bg=palette["section_bg"],
            fg=palette["text"],
            font=(self.ui_font_family, 11, "bold"),
        ).pack(pady=(0, 10))
        # Indeterminate (bouncing) bar, matching the startup loading overlay -
        # a determinate fill bar here visibly stalls between per-device probe
        # callbacks (each cv2 probe can take a while), which reads as frozen.
        progress = ttkb.Progressbar(
            frame,
            mode="indeterminate",
            length=280,
            bootstyle=self._surface_bootstyle(palette["section_bg"]),
        )
        progress.pack()
        progress.start(15)
        # No "0 of N" here: the real probe count isn't known until the scan's
        # first progress callback (enumerate_video_devices bounds it by the
        # pygrabber device count, which is itself the first thing the scan
        # resolves), so an upfront number would just be max_probe and wrong.
        status_var = tk.StringVar(value="Please wait...")
        tk.Label(
            frame,
            textvariable=status_var,
            bg=palette["section_bg"],
            fg=palette["muted_text"],
            font=(self.ui_font_family, 9),
        ).pack(pady=(8, 0))

        popup.update_idletasks()
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - popup.winfo_width()) // 2
            py = parent.winfo_rooty() + (parent.winfo_height() - popup.winfo_height()) // 2
            popup.geometry(f"+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass
        try:
            popup.grab_set()
        except Exception:
            pass

        self._video_scan_popup = popup
        self._video_scan_popup_progress = progress
        self._video_scan_popup_status_var = status_var

    def _update_video_scan_progress_popup(self, completed, total):
        # Bar itself is indeterminate now (see _show_video_scan_progress_popup),
        # so only the status text tracks real progress.
        status_var = getattr(self, "_video_scan_popup_status_var", None)
        if status_var is not None:
            try:
                status_var.set(f"Device {completed} of {total}")
            except Exception:
                pass

    def _close_video_scan_progress_popup(self):
        popup = getattr(self, "_video_scan_popup", None)
        progress = getattr(self, "_video_scan_popup_progress", None)
        if progress is not None:
            try:
                progress.stop()
            except Exception:
                pass
        if popup is not None:
            try:
                popup.grab_release()
            except Exception:
                pass
            try:
                popup.destroy()
            except Exception:
                pass
        self._video_scan_popup = None
        self._video_scan_popup_progress = None
        self._video_scan_popup_status_var = None

    def _apply_settings_geometry(self, settings_window):
        # Small and non-maximized only as a *default*: the Controller shows
        # just Preview + status now (everything else lives in the Options
        # dialog - see _apply_options_geometry), so it doesn't need the
        # tall, maximized footprint the old single-window layout did. A
        # size or maximized state the user chose themselves still wins over
        # that default - same treatment Options gets.
        settings_window.geometry(self.settings_geometry or "700x820")
        settings_window.minsize(520, 620)
        settings_window.update_idletasks()
        geometry_monitor_index = self._monitor_index_from_saved_settings_geometry(
            settings_window
        )
        if self._settings_window_requires_reposition(geometry_monitor_index):
            self._position_settings_window(settings_window)
        self._move_settings_window_to_selected_monitor()
        if self.settings_maximized:
            # Last on purpose: both helpers above set an explicit geometry,
            # which would knock the window straight back out of zoomed.
            self._maximize_settings_window(settings_window)

    def _apply_options_geometry(self, options_window):
        # Restores the last real (non-maximized) size/position saved
        # from save_settings() if there is one, falling back to the
        # original tall default for a first-ever launch. Deliberately
        # skips the monitor-index persistence chain above -
        # _move_settings_window_to_monitor (monitor_mixin.py) is
        # hardwired to self.settings_window, and a secondary,
        # occasionally-opened dialog doesn't need its own remembered
        # monitor; transient(parent) already gets it placed sensibly,
        # and a plain geometry string restores the right monitor anyway
        # as long as that monitor is still connected.
        options_window.geometry(self.options_geometry or "960x1280")
        # Relaxed from the old fixed 960x1280 (which forced this exact
        # size at minimum, fighting any attempt to restore or manually
        # resize smaller) now that shrinking just means the existing
        # scrollable canvas shows a scrollbar - the intended behavior of
        # that scrolling in the first place, not something to prevent.
        options_window.minsize(640, 480)
        options_window.update_idletasks()
        if self.options_maximized:
            self._maximize_settings_window(options_window)

    def _maximize_settings_window(self, settings_window):
        try:
            if os.name == "nt":
                settings_window.state("zoomed")
            else:
                settings_window.attributes("-zoomed", True)
        except Exception:
            pass

    def _monitor_index_from_saved_settings_geometry(self, settings_window):
        if not self.settings_geometry:
            return None
        try:
            settings_window.geometry(self.settings_geometry)
        except Exception:
            self.settings_geometry = None
            return None
        parsed = self._parse_geometry(self.settings_geometry)
        if not parsed:
            return None
        width, height, x, y = parsed
        if width and height:
            x = x + width / 2
            y = y + height / 2
        return self._find_monitor_index_for_point(x, y)

    def _settings_window_requires_reposition(self, geometry_monitor_index):
        if not self.settings_geometry:
            return True
        if geometry_monitor_index is None:
            return True
        return geometry_monitor_index != self.settings_monitor_index

    def _move_settings_window_to_selected_monitor(self):
        try:
            self._move_settings_window_to_monitor(self.settings_monitor_index)
        except Exception:
            pass

    def _position_settings_window(self, settings_window):
        if self.monitors:
            idx = max(0, min(self.settings_monitor_index, len(self.monitors) - 1))
            monitor = self.monitors[idx]
            width = settings_window.winfo_width()
            height = settings_window.winfo_height()
            x = monitor["left"] + max(0, (monitor["right"] - monitor["left"] - width) // 2)
            y = monitor["top"] + max(0, (monitor["bottom"] - monitor["top"] - height) // 2)
            settings_window.geometry(f"+{x}+{y}")
            return
        x = self.root.winfo_rootx() + (self.root.winfo_width() - settings_window.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - settings_window.winfo_height()) // 2
        settings_window.geometry(f"+{x}+{y}")

    def _create_help_icon(self, parent, help_text, bg, fg):
        icon = tk.Label(
            parent,
            text="?",
            bg=bg,
            fg=fg,
            font=(self.ui_font_family, 10, "bold"),
            cursor="question_arrow",
        )
        icon.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(icon, help_text)
        return icon

    def _create_doc_link(self, parent, text, doc_filename, bg):
        link = tk.Label(
            parent,
            text=text,
            bg=bg,
            fg="#5B8FF7",
            font=(self.ui_font_family, 9, "underline"),
            cursor="hand2",
        )
        link.pack(side=tk.LEFT, padx=(6, 0))
        link.bind("<Button-1>", lambda _event: self._open_doc(doc_filename))
        return link

    def _open_doc(self, doc_filename):
        # sys._MEIPASS (not the exe's own directory) is where PyInstaller
        # actually places bundled datas - for onedir builds (this app,
        # since main.spec) that's the _internal subfolder, not the top-level
        # folder next to the exe, so this has to match main.spec's datas
        # dest ('.') via _MEIPASS rather than assuming exe_dir/doc_filename.
        if getattr(sys, "frozen", False):
            base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        doc_path = os.path.join(base_dir, doc_filename)
        try:
            os.startfile(doc_path)
        except Exception:
            messagebox.showerror(
                "Can't open guide",
                f"Couldn't open the setup guide:\n{doc_path}",
            )

    def _build_menu_bar(self, settings_window, transcription_vars, translation_vars):
        # A real native menu bar (docked top-left by Windows itself)
        # instead of a Menubutton+Menu floating dropdown - the latter's
        # posted menu is a native Win32 popup outside Tk's own tracking,
        # and this window's global click-outside handler (see
        # on_click_outside/_build_settings_canvas) was dismissing it on
        # the very click that opened it, before it ever became visible.
        # A menu bar is handled by the window frame itself, sidestepping
        # that entirely.
        menu_bar = tk.Menu(settings_window)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(
            label="Hardware Autodetect",
            command=lambda: self._run_hardware_autodetect_from_menu(
                transcription_vars, translation_vars
            ),
        )
        file_menu.add_command(label="Options", command=self._show_options_dialog)
        menu_bar.add_cascade(label="File", menu=file_menu)

        about_menu = tk.Menu(menu_bar, tearoff=0)
        about_menu.add_command(label="About Rhema", command=self._show_about_popup)
        about_menu.add_separator()
        about_menu.add_command(
            label="Check for Updates",
            command=lambda: self.check_for_updates(manual=True),
        )
        about_menu.add_command(label="Donate", command=self._show_donate_popup)
        about_menu.add_command(
            label="Feature Request", command=self._open_feature_request_page
        )
        menu_bar.add_cascade(label="About", menu=about_menu)

        # Every item here changes device/model settings or reveals a
        # dialog that does, so the whole menu bar stays off the window -
        # not just its items grayed out - until startup finishes, same
        # intent as the loading overlay itself. _hide_startup_loading_overlay
        # attaches this once ready.
        self._settings_menu_bar = menu_bar
        if self.app_startup_ready:
            settings_window.config(menu=menu_bar)

    def _show_about_popup(self):
        parent = self.settings_window if self.settings_window is not None else self.root
        palette = self._settings_palette()
        dialog = tk.Toplevel(parent)
        dialog.title("About Rhema")
        dialog.configure(bg=palette["section_bg"])
        dialog.resizable(False, False)
        try:
            dialog.transient(parent)
            dialog.grab_set()
        except Exception:
            pass
        frame = tk.Frame(dialog, bg=palette["section_bg"], padx=20, pady=18)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            frame,
            text="Rhema",
            bg=palette["section_bg"],
            fg=palette["text"],
            font=(self.ui_font_family, 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            frame,
            text="ῥῆμα — pronounced REE-mah",
            bg=palette["section_bg"],
            fg=palette["muted_text"],
            font=(self.ui_font_family, 11),
        ).pack(anchor="w", pady=(2, 12))
        message = (
            "Greek for \"spoken word\" or \"utterance\" - distinct from "
            "logos (λόγος), the broader word for "
            "\"word\" or \"reason.\" Rhema is the word spoken aloud in the "
            "moment, which is what this app carries across languages in "
            "real time."
        )
        tk.Label(
            frame,
            text=message,
            bg=palette["section_bg"],
            fg=palette["text"],
            justify="left",
            wraplength=380,
            font=(self.ui_font_family, 10),
        ).pack(anchor="w", fill=tk.X)

        button_row = tk.Frame(frame, bg=palette["section_bg"])
        button_row.pack(anchor="e", fill=tk.X, pady=(16, 0))
        close_button = self._make_button(button_row, "Close", command=dialog.destroy)
        close_button.pack(side=tk.RIGHT)

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        try:
            dialog.update_idletasks()
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            parent_w = max(1, parent.winfo_width())
            parent_h = max(1, parent.winfo_height())
            dialog_w = dialog.winfo_width()
            dialog_h = dialog.winfo_height()
            x = parent_x + max(0, (parent_w - dialog_w) // 2)
            y = parent_y + max(0, (parent_h - dialog_h) // 2)
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _open_feature_request_page(self):
        try:
            webbrowser.open(FEATURE_REQUEST_URL)
        except Exception:
            pass

    def _show_donate_popup(self):
        parent = self.settings_window if self.settings_window is not None else self.root
        palette = self._settings_palette()
        dialog = tk.Toplevel(parent)
        dialog.title("Support Rhema")
        dialog.configure(bg=palette["section_bg"])
        dialog.resizable(False, False)
        try:
            dialog.transient(parent)
            dialog.grab_set()
        except Exception:
            pass
        frame = tk.Frame(dialog, bg=palette["section_bg"], padx=20, pady=18)
        frame.pack(fill=tk.BOTH, expand=True)
        verse = (
            "“So faith comes from hearing, and hearing through the "
            "word (rhema) of Christ.” (Romans 10:17, ESV)"
        )
        tk.Label(
            frame,
            text=verse,
            bg=palette["section_bg"],
            fg=palette["muted_text"],
            justify="left",
            wraplength=420,
            font=(self.ui_font_family, 10, "italic"),
        ).pack(anchor="w", fill=tk.X, pady=(0, 12))
        message = (
            "If Rhema has helped carry that spoken word across a language "
            "barrier - a sermon, a Bible study, a testimony someone could "
            "finally understand - I'd be grateful if you'd consider "
            "supporting its continued development.\n\n"
            "This app is built and maintained by one person in their free "
            "time. Every contribution, whatever the amount, directly funds "
            "the time it takes to keep improving it and to keep it "
            "available, free of charge, to churches and ministries who "
            "need it.\n\n"
            "Please note that financial contributions are not tax "
            "deductible."
        )
        tk.Label(
            frame,
            text=message,
            bg=palette["section_bg"],
            fg=palette["text"],
            justify="left",
            wraplength=420,
            font=(self.ui_font_family, 10),
        ).pack(anchor="w", fill=tk.X)

        button_row = tk.Frame(frame, bg=palette["section_bg"])
        button_row.pack(anchor="e", fill=tk.X, pady=(16, 0))

        def open_donate():
            try:
                webbrowser.open(DONATE_URL)
            except Exception:
                messagebox.showerror("Can't open link", f"Couldn't open:\n{DONATE_URL}")

        close_button = self._make_button(button_row, "Close", command=dialog.destroy)
        close_button.pack(side=tk.RIGHT)
        donate_button = self._make_button(
            button_row, "Donate", command=open_donate, primary=True
        )
        donate_button.pack(side=tk.RIGHT, padx=(0, 8))

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        try:
            dialog.update_idletasks()
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            parent_w = max(1, parent.winfo_width())
            parent_h = max(1, parent.winfo_height())
            dialog_w = dialog.winfo_width()
            dialog_h = dialog.winfo_height()
            x = parent_x + max(0, (parent_w - dialog_w) // 2)
            y = parent_y + max(0, (parent_h - dialog_h) // 2)
            dialog.geometry(f"+{x}+{y}")
            donate_button.focus_set()
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

    def _apply_ui_theme(self):
        """Applies self.ui_theme ("light"/"dark") to the ttk theme and to
        any already-open windows' title bars. Does NOT rebuild the
        Controller/Options windows themselves - their raw tk widgets
        (Frame/Label/etc.) are colored once at build time from
        _settings_palette() and don't live-recolor, so a theme change
        made through Options is applied by _apply_display_vars tearing
        those windows down and reopening them, not by this method.
        """
        dark = self.ui_theme == "dark"
        try:
            self.style.theme_use("bootstrap-dark" if dark else "bootstrap-light")
        except Exception:
            pass
        self.apply_dark_title_bar(self.root, dark=dark)
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.apply_dark_title_bar(self.settings_window, dark=dark)
        if self.options_window is not None and self.options_window.winfo_exists():
            self.apply_dark_title_bar(self.options_window, dark=dark)

    def _rebuild_settings_windows(self):
        """Tears down and reopens the Controller/Options windows so their
        raw tk widgets pick up the new theme's _settings_palette() -
        scheduled via _apply_display_vars after a theme change, since
        those widgets are colored once at build time and don't
        live-recolor. Re-running the section builders this way is safe:
        _start_local_nllb_cache_check/maybe_start_nllb_prewarm already
        short-circuit when the model's already loaded with a matching
        config, and _start_audio_level_updates no-ops if its polling
        loop is already running.
        """
        if self.options_window is not None:
            try:
                self.options_window.destroy()
            except Exception:
                pass
            self.options_window = None
        if self.settings_window is not None:
            try:
                self.settings_window.destroy()
            except Exception:
                pass
            self.settings_window = None
        self.open_settings()

    def _make_button(self, parent, text, command=None, primary=False):
        bootstyle = PRIMARY if primary else None
        button = ttkb.Button(parent, text=text, command=command, bootstyle=bootstyle)
        return button

    def _apply_input_style(self, widget):
        palette = getattr(self, "_ui_palette", self._settings_palette())
        widget.configure(
            bg=palette["input_bg"],
            fg=palette["text"],
            insertbackground=palette["text"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )

    def _apply_option_menu_style(self, menu, var=None):
        palette = getattr(self, "_ui_palette", self._settings_palette())
        menu.configure(
            bg=palette["input_bg"],
            fg=palette["text"],
            activebackground=palette["accent_soft"],
            activeforeground=palette["text"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
            # tk.OptionMenu's own Tk-level default is takefocus=0 (unlike
            # a Button's ""), which silently excludes it from Tab
            # traversal - not something this app set, just Tk's built-in
            # default for Menubutton-class widgets.
            takefocus=1,
        )
        try:
            menu["menu"].configure(
                bg=palette["section_bg"],
                fg=palette["text"],
                activebackground=palette["accent_soft"],
                activeforeground=palette["text"],
                bd=0,
            )
        except Exception:
            pass
        if var is not None:
            self._bind_option_menu_arrow_keys(menu, var)

    def _bind_option_menu_arrow_keys(self, option_menu, var):
        # Confirmed by direct testing that a focused OptionMenu doesn't
        # reliably cycle its value on bare Up/Down/Return the way a real
        # combobox does - Menubutton's native keyboard handling is
        # posting-the-menu-only, not value-cycling. Reads the option
        # list straight from the attached Menu widget's own entries
        # (rather than needing it passed in separately), so this works
        # generically for every OptionMenu without extra plumbing.
        def get_options():
            sub_menu = option_menu["menu"]
            try:
                end_index = sub_menu.index("end")
            except Exception:
                return []
            if end_index is None:
                return []
            options = []
            for i in range(end_index + 1):
                try:
                    options.append(sub_menu.entrycget(i, "label"))
                except Exception:
                    pass
            return options

        def cycle(delta):
            options = get_options()
            if not options:
                return "break"
            try:
                idx = options.index(var.get())
            except ValueError:
                idx = 0
            var.set(options[(idx + delta) % len(options)])
            return "break"

        option_menu.bind("<Down>", lambda _e: cycle(1))
        option_menu.bind("<Right>", lambda _e: cycle(1))
        option_menu.bind("<Up>", lambda _e: cycle(-1))
        option_menu.bind("<Left>", lambda _e: cycle(-1))

    def _apply_combobox_style(self, combobox):
        palette = getattr(self, "_ui_palette", self._settings_palette())
        style_name = f"Lang{id(combobox)}.TCombobox"
        try:
            style = ttk.Style(combobox)
            style.configure(
                style_name,
                fieldbackground=palette["input_bg"],
                background=palette["input_bg"],
                foreground=palette["text"],
            )
            combobox.configure(style=style_name)
        except Exception:
            pass

    def _build_searchable_language_combobox(
        self, parent, options, current_code, default_display=None, width=42
    ):
        """A ttk.Combobox that filters its dropdown as the user types, for
        language pickers with 100-200 options where a plain OptionMenu
        would be unusable. `options` is a list of (display_name, code)
        pairs. Returns (combobox, string_var, name_to_code_map) - the var
        and map are exactly what _optional_mapped_setting expects for
        its "var_key"/"map_key" pair, so callers just add both to the
        returned settings-vars dict under those two keys.

        Typing anything not matching an option is harmless: Apply falls
        back to that field's existing mapped_default via
        _optional_mapped_setting, it doesn't raise or crash.
        """
        all_display = [name for name, _code in options]
        name_to_code = dict(options)
        code_to_name = {code: name for name, code in options}
        initial = code_to_name.get(
            current_code, default_display or (all_display[0] if all_display else "")
        )
        var = tk.StringVar(value=initial)
        combobox = ttk.Combobox(parent, textvariable=var, values=all_display, width=width)
        self._apply_combobox_style(combobox)

        def popdown_path():
            return f"{combobox}.popdown"

        # ttk's popdown listbox force-focuses itself the instant it's first
        # mapped (Tk's own combobox.tcl: `bind ComboboxListbox <Map>
        # {focus -force %W}`). Left alone, opening the dropdown while
        # typing steals focus away from this entry mid-keystroke - further
        # characters go to the listbox instead of continuing to filter,
        # and the resulting <FocusOut> on the entry looks identical to the
        # user actually leaving the field. Clearing just this one class
        # binding (application-wide, but this is the only combobox flavor
        # in the app) leaves the sibling ComboboxPopdown <Map> binding
        # (pressed-state + input grab) untouched, so the dropdown still
        # opens and behaves normally - it just no longer steals the entry's
        # keyboard focus.
        try:
            combobox.tk.call("bind", "ComboboxListbox", "<Map>", "")
        except Exception:
            pass

        def filter_options(_event=None):
            typed = var.get().strip().lower()
            if not typed:
                combobox["values"] = all_display
            else:
                filtered = [name for name in all_display if typed in name.lower()]
                combobox["values"] = filtered or all_display
            try:
                combobox.tk.call("ttk::combobox::Post", combobox)
            except Exception:
                pass

        def restore_full_list(_event=None):
            combobox["values"] = all_display

        def commit_typed_selection(_event=None):
            # Typing narrows the dropdown (filter_options) but doesn't by
            # itself turn "Eng" into "English" - this snaps whatever's
            # typed to its best (first) filtered match whenever the field
            # is committed (Enter) or left (focus-out, including the
            # click-outside handler in _build_settings_canvas), so keyboard
            # users don't have to reach for the mouse to pick a match.
            #
            # Defensive: the popdown listbox's own <Map> binding used to
            # force-focus itself the instant the dropdown opened (cleared
            # above), which fired a <FocusOut> on this entry that looked
            # identical to the user actually leaving the field. That's
            # neutralized now, but skip committing if focus is still ever
            # found inside this combobox's own popdown, just in case.
            try:
                focus_target = str(combobox.tk.call("focus"))
            except Exception:
                focus_target = ""
            if focus_target.startswith(popdown_path()):
                return
            typed = var.get().strip()
            if typed and typed not in name_to_code:
                typed_lower = typed.lower()
                matches = [name for name in all_display if typed_lower in name.lower()]
                if matches:
                    var.set(matches[0])
            restore_full_list()
            try:
                combobox.tk.call("ttk::combobox::Unpost", combobox)
            except Exception:
                pass
            # Without this, ttk's own class-level <Return> binding (posted-
            # listbox selection) still runs after ours and can re-insert
            # text on top of what we just set, e.g. "English" + a leftover
            # "ng" from the original typed text -> "Englishng".
            return "break"

        def select_all_on_focus(_event=None):
            # Entry's own <Button-1> binding (which places the cursor at the
            # click position and clears any selection) runs before the
            # resulting <FocusIn> is delivered, but scheduling this for the
            # very next idle tick - rather than selecting immediately -
            # guards against that ordering in case some Tk build/widget
            # state delivers them the other way around. With the whole
            # field selected, the first typed character replaces it
            # (ttk::entry's own Insert procedure deletes the selection
            # before inserting) instead of landing next to the existing
            # text, so typing over "Auto-detect"/"English"/whatever's
            # already there starts clean instead of appending to it.
            def _select():
                try:
                    combobox.select_range(0, "end")
                    combobox.icursor("end")
                except Exception:
                    pass

            combobox.after(1, _select)

        combobox.bind(
            "<KeyRelease>",
            lambda e: None
            if e.keysym in ("Up", "Down", "Return", "Escape", "Tab")
            else filter_options(),
        )
        combobox.bind("<Return>", commit_typed_selection)
        combobox.bind("<<ComboboxSelected>>", restore_full_list)
        combobox.bind("<FocusOut>", commit_typed_selection)
        combobox.bind("<FocusIn>", select_all_on_focus)
        return combobox, var, name_to_code

    def _apply_scaled_fonts(self):
        self._fit_font_to_lines()

    def _get_pixels_per_inch(self):
        try:
            return float(self.root.winfo_fpixels("1i"))
        except Exception:
            return 96.0

    def _target_line_height_px(self):
        distance_ft = 10.0
        distance_in = distance_ft * 12.0
        angle_deg = 0.40
        angle_rad = math.radians(angle_deg)
        height_in = 2.0 * distance_in * math.tan(angle_rad / 2.0)
        return height_in * self._get_pixels_per_inch()

    def _font_size_for_line_height(self, target_px, min_size=8, max_size=320):
        if not target_px or target_px <= 0:
            return None
        lo, hi = min_size, max_size
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            self.text_font.configure(size=mid)
            linespace = self.text_font.metrics("linespace") or 1
            if linespace >= target_px:
                best = mid
                hi = mid - 1
            else:
                lo = mid + 1
        return best if best is not None else max_size

    def _fit_font_to_lines(self, max_size=None):
        height = self.text_canvas.winfo_height()
        width = self.text_canvas.winfo_width()
        if height <= 1:
            return
        available_height = max(1, height - (self.text_padding * 2))
        available_width = max(1, width - (self.text_padding * 2))
        lines = self._effective_max_lines()
        min_size, max_size = self._font_size_bounds_for_canvas(
            available_height,
            lines,
            max_size=max_size,
        )
        best_height = self._max_font_size_for_height(
            lines,
            available_height,
            min_size=min_size,
            max_size=max_size,
        )
        best = best_height
        target_chars = min(self.chunk_size, self.min_chars_per_line)
        if available_width > 1 and target_chars > 0:
            sample = "x" * target_chars
            best_width = self._max_font_size_for_sample_width(
                sample,
                available_width,
                min_size=min_size,
                max_size=best_height,
            )
            best = min(best_height, best_width)

        target_px = self._target_line_height_px()
        target_size = self._font_size_for_line_height(target_px, min_size=min_size, max_size=best)
        if target_size:
            best = min(best, target_size)

        self.text_font.configure(size=best)

    def _font_size_bounds_for_canvas(self, available_height, lines, max_size=None):
        approx = max(12, int(available_height / max(1, lines)))
        resolved_max = int(max_size or min(320, int(approx * 1.6)))
        return 12, resolved_max

    def _max_font_size_for_height(self, lines, available_height, min_size=12, max_size=320):
        return self._binary_search_font_size(
            min_size,
            max_size,
            lambda size: self._font_height_fits(size, lines, available_height),
        )

    def _font_height_fits(self, size, lines, available_height):
        self.text_font.configure(size=size)
        line_height = self.text_font.metrics("linespace") or 1
        return (line_height * lines) <= available_height

    def _max_font_size_for_sample_width(self, sample, available_width, min_size=12, max_size=320):
        return self._binary_search_font_size(
            min_size,
            max_size,
            lambda size: self._font_sample_width_fits(size, sample, available_width),
        )

    def _font_sample_width_fits(self, size, sample, available_width):
        self.text_font.configure(size=size)
        return self.text_font.measure(sample) <= available_width

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

    def _wrap_token_to_width(self, current, token, max_width):
        if token.isspace():
            return current + token, []
        candidate = f"{current}{token}" if current else token
        if self.text_font.measure(candidate) <= max_width:
            return candidate, []
        emitted = []
        if current:
            emitted.append(current.rstrip())
        if self.text_font.measure(token) <= max_width:
            return token, emitted
        chunks = self._split_token_to_width(token, max_width)
        if not chunks:
            return "", emitted
        emitted.extend(chunks[:-1])
        return chunks[-1], emitted

    def _split_token_to_width(self, token, max_width):
        chunks = []
        chunk = ""
        for ch in token:
            test = f"{chunk}{ch}"
            if self.text_font.measure(test) <= max_width:
                chunk = test
                continue
            if chunk:
                chunks.append(chunk)
            chunk = ch
        if chunk:
            chunks.append(chunk)
        return chunks

    def _ensure_line_items(self, count):
        while len(self.text_line_items) < count:
            item = self.text_canvas.create_text(
                self.text_padding,
                self.text_padding,
                anchor="nw",
                text="",
                fill=self.text_color,
                font=self.text_font,
            )
            self.text_line_items.append(item)
        while len(self.text_line_items) > count:
            item = self.text_line_items.pop()
            try:
                self.text_canvas.delete(item)
            except Exception:
                pass

    def _update_line_items(self, display_lines):
        height = self.text_canvas.winfo_height()
        lines = self._effective_max_lines()
        self._ensure_line_items(lines)
        line_height = self.text_font.metrics("linespace") or 1

        if getattr(self, "video_feed_enabled", False):
            # Captions live inside the fixed gray bar docked to the bottom
            # of the video, not spread across the whole canvas.
            bar_height = self._video_caption_bar_height(height, line_height, lines)
            top = height - bar_height + self.text_padding
            available = max(1, bar_height - (self.text_padding * 2))
        else:
            top = self.text_padding
            available = max(1, height - (self.text_padding * 2))

        if lines > 1 and available > line_height:
            step = (available - line_height) / (lines - 1)
        else:
            step = 0

        # Top-fill: line 1 paints first and stays put as later lines fill
        # in below it (paint-on paging), rather than the old bottom-fill
        # that shifted every line upward as new ones arrived.
        slots = [""] * lines
        for i, line in enumerate(display_lines[:lines]):
            slots[i] = line

        for idx, line in enumerate(slots):
            y = top + (idx * step)
            self.text_canvas.coords(self.text_line_items[idx], self.text_padding, y)
            self.text_canvas.itemconfigure(
                self.text_line_items[idx],
                text=line,
                font=self.text_font,
                state="normal",
            )

    def _add_setting_label(self, parent, text, help_text, label_opts, pady=(0, 4)):
        row = tk.Frame(parent, bg=label_opts["bg"])
        row.pack(fill=tk.X, pady=pady)
        label = tk.Label(row, text=text, **label_opts)
        label.pack(side=tk.LEFT)
        if help_text:
            self._create_help_icon(row, help_text, label_opts["bg"], label_opts["fg"])
        return row

    def _bind_responsive_wraplength(self, label, padding=24, min_width=160):
        # Ties wraplength to the label's own container width instead of a
        # fixed pixel constant, so paragraph text reflows as the
        # Controller/Options windows are resized rather than wrapping at
        # one width regardless of how much room is actually available.
        def update_wrap(_event=None):
            width = label.master.winfo_width()
            if width <= 1:
                return
            try:
                label.configure(wraplength=max(min_width, width - padding))
            except Exception:
                pass

        label.master.bind(self.CONFIGURE_EVENT, update_wrap, add="+")
        label.after(0, update_wrap)
        return update_wrap

    def _build_preview_section(self, content, label_opts, section_bg, settings_fg, section_font):
        preview_section = tk.LabelFrame(
            content,
            text="Preview",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        # Controller's only remaining section now (Display/Audio/etc.
        # moved to the Options dialog), so it fills whatever space the
        # window gives it instead of just wrapping its content.
        preview_section.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 0))
        # Without this, the thumbnail image's own size feeds back into a
        # layout loop: a bigger image grows preview_section's requested
        # size, which shrinks status_section/toggle_fullscreen_button to
        # compensate, which changes preview_section's allotted size again,
        # re-triggering _render_output_snapshot_thumbnail - each pass
        # nudging everything smaller. Disabling propagation makes this
        # section's size strictly top-down (from settings_window's pack
        # allocation only), so the image inside it can never influence it.
        preview_section.pack_propagate(False)

        if _OUTPUT_SNAPSHOT_INTERVAL_MS % 60_000 == 0:
            interval_text = f"{_OUTPUT_SNAPSHOT_INTERVAL_MS // 60_000} min"
        else:
            interval_text = f"{_OUTPUT_SNAPSHOT_INTERVAL_MS // 1000} seconds"
        preview_caption_label = tk.Label(
            preview_section,
            text=f"Periodic screenshot of the output window (refreshes every {interval_text}).",
            justify="left",
            **label_opts,
        )
        preview_caption_label.pack(anchor="w", fill=tk.X, pady=(0, 4))
        self._bind_responsive_wraplength(preview_caption_label)
        self.preview_widget = tk.Label(
            preview_section,
            text="Capturing snapshot...",
            bg=section_bg,
            fg=settings_fg,
            anchor="center",
            relief="solid",
            borderwidth=1,
        )
        self.preview_widget.pack(expand=True, fill=tk.BOTH)

        # Re-render (not re-capture) the thumbnail from the last raw
        # screenshot whenever the section resizes, so dragging the
        # Controller window's edge rescales the preview live instead of
        # waiting for the next periodic snapshot tick.
        preview_section.bind(
            self.CONFIGURE_EVENT, lambda _event: self._render_output_snapshot_thumbnail()
        )

        # Give the output window a moment to finish its own initial
        # layout/paint before grabbing the first screenshot.
        self.root.after(300, self._capture_output_snapshot)
        self._schedule_output_snapshot()

    def _capture_output_snapshot(self):
        widget = self.preview_widget
        if widget is None or not widget.winfo_exists():
            return
        try:
            # Capture by window handle (PrintWindow under the hood on
            # Windows) instead of a screen-coordinate bbox (plain BitBlt).
            # The video overlay draws into the canvas via fast, frequent
            # PhotoImage updates, which DWM can end up presenting through a
            # hardware-accelerated path that BitBlt reads back as solid
            # black - PrintWindow asks the window to render itself and
            # sees that content correctly. Also immune to whatever screen
            # coordinates the window happens to be at.
            shot = ImageGrab.grab(window=self.root.winfo_id())
            width, height = shot.size
            if width <= 1 or height <= 1:
                return
        except Exception:
            return
        # Kept at full resolution (not downscaled here) so the section can
        # be resized afterward and re-rendered at whatever size fits then,
        # without re-grabbing the screen just to change the thumbnail size.
        self._output_snapshot_raw_image = shot
        self._render_output_snapshot_thumbnail()

    def _render_output_snapshot_thumbnail(self):
        widget = self.preview_widget
        shot = self._output_snapshot_raw_image
        if widget is None or shot is None or not widget.winfo_exists():
            return
        width, height = shot.size
        if width <= 1 or height <= 1:
            return
        # Percentage of the Preview section's own current size, not a
        # fixed pixel constant - the thumbnail grows and shrinks with the
        # Controller window instead of sitting at one size regardless of
        # how much room is actually available. Never scales past 1.0x
        # native resolution (only down), so enlarging the window past the
        # output window's own resolution stops growing the thumbnail
        # further rather than blurring it out upscaling.
        container = widget.master
        available_w = container.winfo_width()
        available_h = container.winfo_height()
        if available_w <= 1 or available_h <= 1:
            available_w, available_h = _OUTPUT_SNAPSHOT_WIDTH, _OUTPUT_SNAPSHOT_WIDTH
        target_w = max(1, int(available_w * 0.92))
        target_h = max(1, int(available_h * 0.92))
        scale = min(1.0, target_w / width, target_h / height)
        thumb_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        try:
            thumb = shot.resize(thumb_size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
        except Exception:
            return
        # Keep a reference - Tk drops the image if the last Python reference
        # to a PhotoImage is garbage collected.
        self._output_snapshot_photo = photo
        try:
            widget.config(image=photo, text="")
        except Exception:
            pass

    def _schedule_output_snapshot(self):
        # Cancel any pending tick so rebuilding the preview section can never
        # stack a second permanent snapshot loop.
        if self._output_snapshot_after_id is not None:
            try:
                self.root.after_cancel(self._output_snapshot_after_id)
            except Exception:
                pass
        self._output_snapshot_after_id = self.root.after(
            _OUTPUT_SNAPSHOT_INTERVAL_MS, self._run_output_snapshot_tick
        )

    def _run_output_snapshot_tick(self):
        self._output_snapshot_after_id = None
        self._capture_output_snapshot()
        self._schedule_output_snapshot()

    def _build_settings_sections(
        self,
        content,
        settings_window,
        label_opts,
        section_bg,
        settings_fg,
        section_font,
    ):
        display_section = tk.LabelFrame(
            content,
            text="Display",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        display_section.pack(fill=tk.X, pady=(0, 10))

        display_vars = self._build_display_controls(
            display_section,
            label_opts,
            section_bg,
            settings_fg,
            settings_window,
        )

        audio_section = tk.LabelFrame(
            content,
            text="Audio",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        audio_section.pack(fill=tk.X, pady=(0, 10))
        audio_vars = self._build_audio_section(audio_section, label_opts)

        transcription_section = tk.LabelFrame(
            content,
            text="Transcription",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        transcription_section.pack(fill=tk.X)
        transcription_vars = self._build_transcription_section(transcription_section, label_opts)

        translation_section = tk.LabelFrame(
            content,
            text="Translation",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        translation_section.pack(fill=tk.X, pady=(10, 0))
        translation_vars = self._build_translation_section(
            translation_section, label_opts, transcription_vars["stt_source_lang_var"]
        )

        # Translating a partial, still-changing sentence produces reordered,
        # inconsistent output compared to the eventual finalized translation
        # (NLLB reasons about whole-sentence context, so a growing fragment
        # gets restructured turn by turn) - translation wins the conflict,
        # so live interim text is force-unchecked and disabled whenever
        # translation is on, not the other way around.
        def sync_interim_with_translation(*_args):
            enabled = self._coerce_bool(
                translation_vars["enable_translation_var"].get(), default=False
            )
            interim_var = transcription_vars.get("show_interim_text_var")
            interim_check = transcription_vars.get("show_interim_check")
            if enabled and interim_var is not None:
                interim_var.set(False)
            if interim_check is not None:
                interim_check.config(state=tk.DISABLED if enabled else tk.NORMAL)

        translation_vars["enable_translation_var"].trace_add(
            "write", sync_interim_with_translation
        )
        sync_interim_with_translation()

        advanced_vars = self._build_advanced_section(
            content,
            label_opts,
            section_bg,
            settings_fg,
            section_font,
        )

        return display_vars, audio_vars, transcription_vars, translation_vars, advanced_vars

    def _build_settings_canvas(self, settings_window, settings_bg):
        scroll_frame = tk.Frame(settings_window, bg=settings_bg)
        scroll_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(scroll_frame, bg=settings_bg, highlightthickness=0)
        scrollbar = tk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        content = tk.Frame(canvas, bg=settings_bg)
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def on_canvas_configure(event):
            canvas.itemconfigure(canvas_window, width=event.width)

        def on_content_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind(self.CONFIGURE_EVENT, on_canvas_configure)
        content.bind(self.CONFIGURE_EVENT, on_content_configure)

        def widget_class_of(widget):
            # event.widget is a plain Tk pathname string, not a bound Python
            # widget object, when the event comes from a widget Tcl created
            # without a Python-side wrapper - true of a combobox popdown's
            # internal listbox (built by ttk::combobox::PopdownWindow at the
            # Tcl level). winfo_class() only exists on the wrapper, so it
            # has to be queried directly via Tcl for the string case.
            if isinstance(widget, str):
                try:
                    return canvas.tk.call("winfo", "class", widget)
                except tk.TclError:
                    return ""
            return widget.winfo_class()

        def on_mousewheel(event):
            # bind_all below is the only way to get wheel events without a
            # dedicated binding on every single settings widget, but it also
            # fires for a searchable-language combobox's open dropdown (an
            # internal Listbox) - that widget already has its own default
            # Tk scroll binding, so without this guard the wheel scrolled
            # both the dropdown *and* the settings page underneath it at
            # once. Letting the Listbox's own binding be the only one that
            # runs keeps the wheel scoped to whichever one the pointer is
            # actually over.
            if widget_class_of(event.widget) == "Listbox":
                return
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")

        for event_name in self.SCROLL_EVENTS:
            settings_window.bind_all(event_name, on_mousewheel)

        # Click-to-defocus: a field left focused after a click elsewhere
        # (e.g. blank space, which unlike other widgets doesn't claim focus
        # on click by itself) can go on eating mouse-wheel scrolls instead
        # of the page scrolling - on Windows, wheel events are delivered to
        # the focused window, not whatever's under the pointer, so a
        # still-focused language combobox changes its own selection instead
        # of the settings page scrolling. Any click outside a text-entry
        # widget releases focus to the canvas so the page always scrolls.
        text_entry_classes = {"TCombobox", "Entry", "Spinbox", "Text", "Listbox"}

        def on_click_outside(event):
            if widget_class_of(event.widget) in text_entry_classes:
                return
            canvas.focus_set()

        settings_window.bind_all("<Button-1>", on_click_outside)

        content.configure(padx=12, pady=12)
        return content

    def _build_display_controls(
        self,
        display_section,
        label_opts,
        section_bg,
        settings_fg,
        settings_window,
    ):
        self._add_setting_label(
            display_section,
            "Theme:",
            "Switches the Controller and Options windows between light "
            "and dark. Applying this closes and reopens both windows.",
            label_opts,
            pady=(0, 4),
        )
        theme_display_options = ["Light", "Dark"]
        theme_var = tk.StringVar(
            value="Dark" if self.ui_theme == "dark" else "Light"
        )
        theme_menu = tk.OptionMenu(display_section, theme_var, *theme_display_options)
        self._apply_option_menu_style(theme_menu, theme_var)
        theme_menu.pack(anchor="w", pady=(0, 8))

        video_feed_row = tk.Frame(display_section, bg=section_bg)
        video_feed_row.pack(anchor="w", fill=tk.X, pady=(10, 0))
        video_feed_enabled_var = tk.BooleanVar(value=self.video_feed_enabled)
        video_feed_check = tk.Checkbutton(
            video_feed_row,
            text="Show video feed behind captions",
            variable=video_feed_enabled_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        video_feed_check.pack(side=tk.LEFT)
        self._create_help_icon(
            video_feed_row,
            "Shows the OBS Virtual Camera behind captions. Start OBS's Virtual "
            "Camera first. To guarantee this matches what's live on "
            "YouTube/Facebook, make sure OBS's Virtual Camera Output Type is "
            "set to \"Program\" (its default), not Preview or a specific "
            "Scene/Source.",
            section_bg,
            settings_fg,
        )
        self._create_doc_link(
            video_feed_row,
            "Setup guide",
            "README.md",
            section_bg,
        )

        video_feed_options_frame = tk.Frame(display_section, bg=section_bg)

        self._add_setting_label(
            video_feed_options_frame,
            "Camera Device:",
            "Camera index for the OBS Virtual Camera. Click Refresh after "
            "starting OBS's Virtual Camera if it isn't listed yet.",
            label_opts,
            pady=(10, 4),
        )
        video_device_map = {self._video_device_label(i): i for i in self.video_devices}
        video_device_labels = list(video_device_map.keys()) or ["(click Refresh)"]
        selected_video_label = next(
            (label for label, idx in video_device_map.items() if idx == self.video_device_index),
            video_device_labels[0],
        )
        video_device_var = tk.StringVar(value=selected_video_label)
        self.video_device_var = video_device_var
        video_device_row = tk.Frame(video_feed_options_frame, bg=section_bg)
        video_device_row.pack(anchor="w", fill=tk.X)
        self.video_device_menu = tk.OptionMenu(video_device_row, video_device_var, *video_device_labels)
        self._apply_option_menu_style(self.video_device_menu, video_device_var)
        self.video_device_menu.pack(side=tk.LEFT)
        video_refresh_button = self._make_button(
            video_device_row,
            "Refresh",
            command=self._refresh_video_devices,
            primary=True,
        )
        video_refresh_button.pack(side=tk.LEFT, padx=(8, 0))
        self.video_refresh_button = video_refresh_button

        self.video_status_var = tk.StringVar(value=self.video_status)
        video_status_label = tk.Label(
            video_feed_options_frame,
            textvariable=self.video_status_var,
            bg=section_bg,
            fg=settings_fg,
        )
        video_status_label.pack(anchor="w", pady=(6, 0))

        self._add_setting_label(
            video_feed_options_frame,
            "Number of lines to show:",
            "Maximum number of translated lines kept on screen. Kept lower "
            "than the non-video default to leave more of the video visible.",
            label_opts,
            pady=(10, 4),
        )
        video_lines_var = tk.IntVar(value=self.video_max_lines)
        video_lines_spinbox = tk.Spinbox(
            video_feed_options_frame,
            from_=self.LINES_VIDEO_MIN,
            to=self.LINES_VIDEO_MAX,
            textvariable=video_lines_var,
        )
        self._apply_input_style(video_lines_spinbox)
        video_lines_spinbox.pack(anchor="w")

        self._add_setting_label(
            video_feed_options_frame,
            "Caption Bar Opacity:",
            "How solid the bar behind the caption lines looks, using the "
            "Background Color below. 0% is fully see-through, 100% is a "
            "solid bar.",
            label_opts,
            pady=(10, 0),
        )
        video_caption_alpha_var = tk.IntVar(
            value=int(round(self.video_caption_bar_alpha * 100))
        )
        palette = getattr(self, "_ui_palette", self._settings_palette())
        video_caption_alpha_scale = tk.Scale(
            video_feed_options_frame,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=video_caption_alpha_var,
            bg=section_bg,
            fg=settings_fg,
            troughcolor=palette["input_bg"],
            highlightthickness=0,
            activebackground=palette["accent"],
            showvalue=True,
        )
        video_caption_alpha_scale.pack(anchor="w", fill=tk.X)

        bg_color_label_row = self._add_setting_label(
            display_section,
            "Background Color:",
            "Background color for the output overlay and preview. Also tints "
            "the caption bar behind the video overlay, if enabled.",
            label_opts,
            pady=(10, 4),
        )
        bg_frame = tk.Frame(display_section, bg=section_bg)
        bg_frame.pack(fill=tk.X)
        bg_color_var = tk.StringVar(value=self.bg_color)
        bg_entry = tk.Entry(bg_frame, textvariable=bg_color_var, width=20)
        self._apply_input_style(bg_entry)
        bg_entry.pack(side=tk.LEFT)
        bg_button = self._make_button(
            bg_frame,
            "Choose",
            command=lambda: self.choose_color(bg_color_var, "background", settings_window),
            primary=True,
        )
        bg_button.pack(side=tk.LEFT, padx=(8, 0))

        # Positioned just above Background Color (not next to the checkbox)
        # so toggling "Show video feed" only ever swaps the block between the
        # checkbox and Background Color - this row and Background Color
        # itself stay put instead of the whole area reshuffling.
        lines_no_video_frame = tk.Frame(display_section, bg=section_bg)
        self._add_setting_label(
            lines_no_video_frame,
            "Number of lines to show:",
            "Maximum number of translated lines kept on screen.",
            label_opts,
            pady=(0, 4),
        )
        lines_var = tk.IntVar(value=self.max_lines)
        lines_spinbox = tk.Spinbox(
            lines_no_video_frame,
            from_=self.LINES_NO_VIDEO_MIN,
            to=self.LINES_NO_VIDEO_MAX,
            textvariable=lines_var,
        )
        self._apply_input_style(lines_spinbox)
        lines_spinbox.pack(anchor="w")

        def on_video_feed_toggle(*_args):
            if video_feed_enabled_var.get():
                # pack_forget() below drops the frame out of display_section's
                # packing order entirely, so a later plain pack() call would
                # re-add it at the end (after every other Display setting)
                # instead of back in place. Pin it explicitly with after=/
                # before= so its position is stable no matter how many times
                # it's toggled or when in the build order that happens.
                lines_no_video_frame.pack_forget()
                video_feed_options_frame.pack(fill=tk.X, pady=(4, 0), after=video_feed_row)
            else:
                video_feed_options_frame.pack_forget()
                lines_no_video_frame.pack(fill=tk.X, pady=(10, 4), before=bg_color_label_row)

        video_feed_enabled_var.trace_add("write", lambda *_args: on_video_feed_toggle())
        on_video_feed_toggle()
        if video_feed_enabled_var.get():
            # The options frame (device dropdown + Refresh button) is being
            # packed before the settings window's first geometry pass, which
            # can leave it laid out with a stale/zero size on relaunch when
            # the feature was already enabled at startup. Re-running the
            # same pack call shortly after the window is realized fixes it
            # (this mirrors the manual uncheck/recheck workaround).
            settings_window.after(150, on_video_feed_toggle)

        self._add_setting_label(
            display_section,
            "Text Color:",
            "Text color for the output overlay and preview.",
            label_opts,
            pady=(10, 4),
        )
        text_frame = tk.Frame(display_section, bg=section_bg)
        text_frame.pack(fill=tk.X)
        text_color_var = tk.StringVar(value=self.text_color)
        text_entry = tk.Entry(text_frame, textvariable=text_color_var, width=20)
        self._apply_input_style(text_entry)
        text_entry.pack(side=tk.LEFT)
        text_button = self._make_button(
            text_frame,
            "Choose",
            command=lambda: self.choose_color(text_color_var, "text", settings_window),
            primary=True,
        )
        text_button.pack(side=tk.LEFT, padx=(8, 0))

        self._add_setting_label(
            display_section,
            "Output Monitor:",
            "Monitor where the translation output appears.",
            label_opts,
            pady=(10, 4),
        )
        self.monitors = self.get_monitors()
        monitor_labels = self.get_monitor_labels()
        if not monitor_labels:
            monitor_labels = ["Monitor 1"]
        monitor_var = tk.StringVar(value=monitor_labels[min(self.monitor_index, len(monitor_labels) - 1)])
        monitor_menu = tk.OptionMenu(
            display_section,
            monitor_var,
            *monitor_labels,
            command=lambda _value: on_output_monitor_change(),
        )
        self._apply_option_menu_style(monitor_menu, monitor_var)
        monitor_menu.pack(anchor="w")

        self._add_setting_label(
            display_section,
            "Controller Monitor:",
            "Monitor where the settings window opens.",
            label_opts,
            pady=(10, 4),
        )
        settings_monitor_var = tk.StringVar(
            value=monitor_labels[min(self.settings_monitor_index, len(monitor_labels) - 1)]
        )
        settings_monitor_menu = tk.OptionMenu(
            display_section,
            settings_monitor_var,
            *monitor_labels,
            command=lambda _value: on_settings_monitor_change(),
        )
        self._apply_option_menu_style(settings_monitor_menu, settings_monitor_var)
        settings_monitor_menu.pack(anchor="w")

        focus_lock_row = tk.Frame(display_section, bg=section_bg)
        focus_lock_row.pack(anchor="w", fill=tk.X, pady=(10, 0))
        lock_output_focus_var = tk.BooleanVar(value=self.lock_output_focus)
        lock_output_focus_check = tk.Checkbutton(
            focus_lock_row,
            text="Always keep on top of other apps",
            variable=lock_output_focus_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        lock_output_focus_check.pack(side=tk.LEFT)
        self._create_help_icon(
            focus_lock_row,
            "Keeps the fullscreen output window on top of other windows and attempts to focus it. Leave this off to let other apps appear above the output window.",
            section_bg,
            settings_fg,
        )

        clear_inactivity_row = tk.Frame(display_section, bg=section_bg)
        clear_inactivity_row.pack(anchor="w", fill=tk.X, pady=(10, 0))
        clear_display_on_inactivity_var = tk.BooleanVar(
            value=self.clear_display_on_inactivity
        )
        clear_display_on_inactivity_check = tk.Checkbutton(
            clear_inactivity_row,
            text="Clear display after inactivity",
            variable=clear_display_on_inactivity_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        clear_display_on_inactivity_check.pack(side=tk.LEFT)
        self._create_help_icon(
            clear_inactivity_row,
            "Wipes the output window back to blank after this many seconds "
            "with no new speech, instead of leaving old captions sitting on "
            "screen during a pause. The timer resets on every new line or "
            "live update, so it only clears once speech actually stops.",
            section_bg,
            settings_fg,
        )

        clear_inactivity_seconds_row = tk.Frame(display_section, bg=section_bg)
        clear_inactivity_seconds_row.pack(anchor="w", fill=tk.X, pady=(4, 0))
        tk.Label(
            clear_inactivity_seconds_row,
            text="Seconds of silence before clearing:",
            bg=section_bg,
            fg=settings_fg,
        ).pack(side=tk.LEFT)
        clear_display_inactivity_seconds_var = tk.IntVar(
            value=self.clear_display_inactivity_seconds
        )
        clear_inactivity_seconds_spinbox = tk.Spinbox(
            clear_inactivity_seconds_row,
            from_=self.CLEAR_DISPLAY_INACTIVITY_MIN,
            to=self.CLEAR_DISPLAY_INACTIVITY_MAX,
            textvariable=clear_display_inactivity_seconds_var,
            width=6,
        )
        self._apply_input_style(clear_inactivity_seconds_spinbox)
        clear_inactivity_seconds_spinbox.pack(side=tk.LEFT, padx=(8, 0))

        def sync_clear_inactivity_seconds_state(*_args):
            state = tk.NORMAL if clear_display_on_inactivity_var.get() else tk.DISABLED
            clear_inactivity_seconds_spinbox.config(state=state)

        clear_display_on_inactivity_var.trace_add(
            "write", sync_clear_inactivity_seconds_state
        )
        sync_clear_inactivity_seconds_state()

        def on_settings_monitor_change(*_args):
            if settings_monitor_var.get() in monitor_labels:
                self.settings_monitor_index = monitor_labels.index(settings_monitor_var.get())
                settings_device, settings_origin = self._monitor_identity_for_index(
                    self.settings_monitor_index
                )
                self.settings_monitor_device = settings_device
                self.settings_monitor_origin = settings_origin
                self._move_settings_window_to_monitor(self.settings_monitor_index)

        def on_output_monitor_change(*_args):
            if monitor_var.get() in monitor_labels:
                self.monitor_index = monitor_labels.index(monitor_var.get())
                monitor_device, monitor_origin = self._monitor_identity_for_index(
                    self.monitor_index
                )
                self.monitor_device = monitor_device
                self.monitor_origin = monitor_origin
                if self.is_fullscreen:
                    self.enter_fullscreen()
                else:
                    self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
                    self.root.after(0, self.maximize_window)

        # Also handle programmatic changes.
        settings_monitor_var.trace_add("write", lambda *_args: on_settings_monitor_change())
        monitor_var.trace_add("write", lambda *_args: on_output_monitor_change())

        monitor_id_button = self._make_button(
            display_section,
            "Show Monitor Numbers",
            command=self.show_monitor_ids,
            primary=True,
        )
        monitor_id_button.pack(anchor="w", pady=(8, 0))

        return {
            "theme_var": theme_var,
            "lines_var": lines_var,
            "video_lines_var": video_lines_var,
            "bg_color_var": bg_color_var,
            "text_color_var": text_color_var,
            "lock_output_focus_var": lock_output_focus_var,
            "clear_display_on_inactivity_var": clear_display_on_inactivity_var,
            "clear_display_inactivity_seconds_var": clear_display_inactivity_seconds_var,
            "monitor_var": monitor_var,
            "settings_monitor_var": settings_monitor_var,
            "monitor_labels": monitor_labels,
            "video_feed_enabled_var": video_feed_enabled_var,
            "video_device_var": video_device_var,
            "video_caption_alpha_var": video_caption_alpha_var,
        }

    def _build_audio_section(self, audio_section, label_opts):
        self._add_setting_label(
            audio_section,
            "Audio Device:",
            "Input device used for speech capture.",
            label_opts,
            pady=(0, 4),
        )
        preferred_label = self._resolve_preferred_device_label(self.preferred_device_label)
        if preferred_label:
            selected_device = preferred_label
        elif self.devices:
            if self.microphone_index is not None and 0 <= self.microphone_index < len(self.devices):
                selected_device = self.devices[self.microphone_index]
            else:
                selected_device = self.devices[0]
        else:
            selected_device = "No devices"
        self.device_var = tk.StringVar(value=selected_device)
        if selected_device in self.devices:
            self.microphone_index = self.devices.index(selected_device)
            if not self.preferred_device_label:
                self.preferred_device_label = selected_device
        self.device_menu = tk.OptionMenu(audio_section, self.device_var, *self.devices)
        self._apply_option_menu_style(self.device_menu, self.device_var)
        self.device_menu.pack(anchor="w")
        self.device_var.trace_add("write", lambda *_args: self._handle_audio_device_change())

        return {
        }

    def _handle_audio_device_change(self):
        label = self.device_var.get()
        if label not in self.device_indices:
            self.microphone_index = None
            return
        new_index = self.devices.index(label)
        device_changed = new_index != self.microphone_index
        self.microphone_index = new_index
        if not self.device_refresh_in_progress:
            self.preferred_device_label = label
            self.save_settings()
        if device_changed:
            self._request_capture_restart()
            self._request_audio_level_stream_restart()

    def _build_advanced_section(
        self,
        content,
        label_opts,
        section_bg,
        settings_fg,
        section_font,
    ):
        toggle_var = tk.BooleanVar(value=False)
        toggle_row = tk.Frame(content, bg=self._ui_palette["window_bg"])
        toggle_row.pack(fill=tk.X, pady=(12, 0))
        toggle_button = self._make_button(
            toggle_row,
            "Show Advanced Settings",
            command=None,
            primary=True,
        )
        toggle_button.pack(anchor="w")

        advanced_section = tk.LabelFrame(
            content,
            text="Advanced",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )

        def toggle_advanced():
            if toggle_var.get():
                toggle_var.set(False)
                toggle_button.config(text="Show Advanced Settings")
                advanced_section.pack_forget()
            else:
                toggle_var.set(True)
                toggle_button.config(text="Hide Advanced Settings")
                advanced_section.pack(fill=tk.X, pady=(8, 0))

        toggle_button.config(command=toggle_advanced)

        filters_section = tk.LabelFrame(
            advanced_section,
            text="Filters",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        filters_section.pack(fill=tk.X, pady=(10, 0))
        self._add_setting_label(
            filters_section,
            "Bad words filter:",
            "Words to omit from the output.",
            label_opts,
            pady=(0, 4),
        )

        bad_words_toggle_var = tk.BooleanVar(value=False)
        bad_words_toggle_button = self._make_button(
            filters_section,
            self.SHOW_LIST_LABEL,
            command=None,
            primary=True,
        )
        bad_words_toggle_button.pack(anchor="w", pady=(0, 6))

        bad_words_en_container = tk.Frame(filters_section, bg=section_bg)
        bad_words_es_container = tk.Frame(filters_section, bg=section_bg)

        tk.Label(
            bad_words_en_container,
            text="English bad words (comma-separated):",
            **label_opts,
        ).pack(anchor="w", pady=(0, 4))
        bad_words_en_text = tk.Text(bad_words_en_container, height=4, width=50)
        self._apply_input_style(bad_words_en_text)
        bad_words_en_text.insert(
            tk.END, ", ".join(sorted(self.bad_words_by_lang.get("en", [])))
        )
        bad_words_en_text.pack(anchor="w")

        tk.Label(
            bad_words_es_container,
            text="Spanish bad words (comma-separated):",
            **label_opts,
        ).pack(anchor="w", pady=(10, 4))
        bad_words_es_text = tk.Text(bad_words_es_container, height=4, width=50)
        self._apply_input_style(bad_words_es_text)
        bad_words_es_text.insert(
            tk.END, ", ".join(sorted(self.bad_words_by_lang.get("es", [])))
        )
        bad_words_es_text.pack(anchor="w")

        def update_bad_words_visibility():
            show = bad_words_toggle_var.get()
            bad_words_toggle_button.config(
                text=self.HIDE_LIST_LABEL if show else self.SHOW_LIST_LABEL
            )
            if show:
                bad_words_en_container.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
                bad_words_es_container.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
            else:
                bad_words_en_container.pack_forget()
                bad_words_es_container.pack_forget()

        def toggle_bad_words_list():
            bad_words_toggle_var.set(not bad_words_toggle_var.get())
            update_bad_words_visibility()

        bad_words_toggle_button.config(command=toggle_bad_words_list)
        update_bad_words_visibility()

        self._add_setting_label(
            filters_section,
            "Custom Vocabulary (comma-separated):",
            "Words or phrases to bias recognition and preserve capitalization.",
            label_opts,
            pady=(10, 4),
        )

        custom_vocab_toggle_var = tk.BooleanVar(value=False)
        custom_vocab_toggle_button = self._make_button(
            filters_section,
            self.SHOW_LIST_LABEL,
            command=None,
            primary=True,
        )
        custom_vocab_toggle_button.pack(anchor="w")

        custom_vocab_en_container = tk.Frame(filters_section, bg=section_bg)
        custom_vocab_es_container = tk.Frame(filters_section, bg=section_bg)

        tk.Label(
            custom_vocab_en_container,
            text="English Bible names (comma-separated):",
            **label_opts,
        ).pack(anchor="w", pady=(0, 4))
        custom_vocab_en_text = tk.Text(custom_vocab_en_container, height=4, width=50)
        self._apply_input_style(custom_vocab_en_text)
        custom_vocab_en_text.insert(
            tk.END, ", ".join(self.custom_vocabulary_by_lang.get("en", []))
        )
        custom_vocab_en_text.pack(anchor="w")

        tk.Label(
            custom_vocab_es_container,
            text="Spanish Bible names (comma-separated):",
            **label_opts,
        ).pack(anchor="w", pady=(10, 4))
        custom_vocab_es_text = tk.Text(custom_vocab_es_container, height=4, width=50)
        self._apply_input_style(custom_vocab_es_text)
        custom_vocab_es_text.insert(
            tk.END, ", ".join(self.custom_vocabulary_by_lang.get("es", []))
        )
        custom_vocab_es_text.pack(anchor="w")

        def update_custom_vocab_visibility():
            show = custom_vocab_toggle_var.get()
            custom_vocab_toggle_button.config(
                text=self.HIDE_LIST_LABEL if show else self.SHOW_LIST_LABEL
            )
            if show:
                custom_vocab_en_container.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
                custom_vocab_es_container.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
            else:
                custom_vocab_en_container.pack_forget()
                custom_vocab_es_container.pack_forget()

        def toggle_custom_vocab_list():
            custom_vocab_toggle_var.set(not custom_vocab_toggle_var.get())
            update_custom_vocab_visibility()

        custom_vocab_toggle_button.config(command=toggle_custom_vocab_list)
        update_custom_vocab_visibility()

        logging_section = tk.LabelFrame(
            advanced_section,
            text="Logging",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        logging_section.pack(fill=tk.X, pady=(10, 0))
        self._add_setting_label(
            logging_section,
            "Logging mode:",
            "Normal keeps status/error and finalized output logs. Debug adds pipeline traces. Evaluation adds raw transcribed/translated comparison logs. Full enables all logs.",
            label_opts,
            pady=(0, 4),
        )
        logging_mode_options = [
            ("Normal", "normal"),
            ("Debug", "debug"),
            ("Evaluation", "evaluation"),
            ("Full", "full"),
        ]
        logging_mode_display = [name for name, _code in logging_mode_options]
        logging_mode_map = dict(logging_mode_options)
        rev_logging_mode_map = {code: name for name, code in logging_mode_options}
        logging_mode_var = tk.StringVar(
            value=rev_logging_mode_map.get(
                self.logging_mode,
                logging_mode_display[0],
            )
        )
        logging_mode_menu = tk.OptionMenu(
            logging_section,
            logging_mode_var,
            *logging_mode_display,
        )
        self._apply_option_menu_style(logging_mode_menu, logging_mode_var)
        logging_mode_menu.pack(anchor="w")

        startup_section = tk.LabelFrame(
            advanced_section,
            text="Startup",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        startup_section.pack(fill=tk.X, pady=(10, 0))
        startup_row = tk.Frame(startup_section, bg=section_bg)
        startup_row.pack(anchor="w", fill=tk.X)
        start_with_windows_var = tk.BooleanVar(value=self.start_with_windows)
        start_with_windows_check = tk.Checkbutton(
            startup_row,
            text="Start app when Windows starts",
            variable=start_with_windows_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        if os.name != "nt":
            start_with_windows_check.configure(state=tk.DISABLED)
        start_with_windows_check.pack(side=tk.LEFT)
        self._create_help_icon(
            startup_row,
            "Adds/removes this app in your Windows user startup registry key.",
            section_bg,
            settings_fg,
        )

        gpu_section = tk.LabelFrame(
            advanced_section,
            text="Local GPU Runtime",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        gpu_section.pack(fill=tk.X, pady=(10, 0))
        self._add_setting_label(
            gpu_section,
            "CUDA directory:",
            "Optional Windows path used to find CUDA Toolkit 12.x and cuDNN 9.x DLLs for local faster-whisper GPU mode. Select the CUDA toolkit folder or its bin folder.",
            label_opts,
            pady=(0, 4),
        )
        cuda_directory_var = tk.StringVar(value=self.cuda_directory)
        cuda_row = tk.Frame(gpu_section, bg=section_bg)
        cuda_row.pack(fill=tk.X)
        cuda_directory_entry = tk.Entry(
            cuda_row,
            textvariable=cuda_directory_var,
            width=58,
        )
        self._apply_input_style(cuda_directory_entry)
        cuda_directory_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        cuda_browse_button = self._make_button(
            cuda_row,
            "Browse",
            command=lambda: self.choose_directory(
                cuda_directory_var,
                "Select CUDA toolkit or bin directory",
            ),
            primary=True,
        )
        cuda_browse_button.pack(side=tk.LEFT, padx=(8, 0))
        cuda_clear_button = self._make_button(
            cuda_row,
            "Clear",
            command=lambda: cuda_directory_var.set(""),
        )
        cuda_clear_button.pack(side=tk.LEFT, padx=(8, 0))

        return {
            "bad_words_en_text": bad_words_en_text,
            "bad_words_es_text": bad_words_es_text,
            "custom_vocab_en_text": custom_vocab_en_text,
            "custom_vocab_es_text": custom_vocab_es_text,
            "logging_mode_var": logging_mode_var,
            "logging_mode_map": logging_mode_map,
            "start_with_windows_var": start_with_windows_var,
            "cuda_directory_var": cuda_directory_var,
        }

    def _build_transcription_section(self, transcription_section, label_opts):
        section_bg = label_opts["bg"]
        settings_fg = label_opts["fg"]
        transcription_intro_label = tk.Label(
            transcription_section,
            text=(
                "RealtimeSTT transcribes spoken audio to text locally in real "
                "time, using faster-whisper (an optimized, offline port of "
                "OpenAI's Whisper). Whisper's multilingual models understand "
                "99 languages and can auto-detect which one is being spoken."
            ),
            bg=section_bg,
            fg=settings_fg,
            justify="left",
            font=(self.ui_font_family, 9),
        )
        transcription_intro_label.pack(anchor="w", fill=tk.X, pady=(0, 6))
        self._bind_responsive_wraplength(transcription_intro_label)

        # ── RealtimeSTT settings panel ─────────────────────────────────
        realtime_stt_container = tk.Frame(transcription_section, bg=label_opts["bg"])
        realtime_stt_container.pack(fill=tk.X, pady=(10, 0))
        source_lang_row = tk.Frame(realtime_stt_container, bg=section_bg)
        source_lang_row.pack(anchor="w", fill=tk.X, pady=(0, 4))
        tk.Label(
            source_lang_row,
            text="Source language:",
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9, "bold"),
        ).pack(side=tk.LEFT)
        self._create_help_icon(
            source_lang_row,
            "The language your speech is transcribed as. Type to search all "
            + str(len(whisper_language_options())) + " languages "
            "RealtimeSTT/Whisper supports: "
            + ", ".join(name for name, _code in whisper_language_options())
            + ". Whisper only transcribes here - it never translates. (Its own "
            "built-in translate mode can only output English, which is why "
            "this app doesn't use it.) To translate the transcript into "
            "another language, turn on Local NLLB in the Translation section "
            "below, which supports 200 languages independent of this setting.",
            section_bg,
            settings_fg,
        )
        stt_language_options = [("Auto-detect", "auto")] + whisper_language_options()
        stt_source_lang_combobox, stt_source_lang_var, stt_source_lang_map = (
            self._build_searchable_language_combobox(
                realtime_stt_container,
                stt_language_options,
                current_code=(self.source_lang or "auto"),
                default_display="Auto-detect",
            )
        )
        stt_source_lang_combobox.pack(anchor="w", pady=(0, 8))

        self._add_setting_label(
            realtime_stt_container,
            "Device:",
            "Auto uses CUDA when available, otherwise CPU.",
            label_opts,
            pady=(0, 4),
        )
        stt_device_options = ["Auto", "CUDA", "CPU"]
        stt_device_var = tk.StringVar(
            value={
                "auto": "Auto",
                "cuda": "CUDA",
                "cpu": "CPU",
            }.get(self.stt_device, "Auto")
        )
        stt_device_menu = tk.OptionMenu(
            realtime_stt_container,
            stt_device_var,
            *stt_device_options,
        )
        self._apply_option_menu_style(stt_device_menu, stt_device_var)
        stt_device_menu.pack(anchor="w")

        self._add_setting_label(
            realtime_stt_container,
            "Final model:",
            "Accurate faster-whisper model used after each utterance ends. Larger models are more "
            "accurate but need more VRAM and take longer per utterance.",
            label_opts, pady=(10, 4),
        )
        realtime_stt_final_model_options = [
            ("tiny (~1 GB VRAM)", "tiny"),
            ("base (~1 GB VRAM)", "base"),
            ("small (~2 GB VRAM)", "small"),
            ("medium (~5 GB VRAM)", "medium"),
            ("distil-large-v3 (~6 GB VRAM, fast)", "distil-large-v3"),
            ("large-v3-turbo (~6 GB VRAM, fast)", "large-v3-turbo"),
            ("large-v2 (~10 GB VRAM)", "large-v2"),
            ("large-v3 (~10 GB VRAM, recommended)", "large-v3"),
        ]
        realtime_stt_final_model_map = dict(realtime_stt_final_model_options)
        realtime_stt_final_model_rev_map = {
            value: name for name, value in realtime_stt_final_model_options
        }
        realtime_stt_final_model_display = [
            name for name, _ in realtime_stt_final_model_options
        ]
        realtime_stt_final_model_var = tk.StringVar(
            value=realtime_stt_final_model_rev_map.get(
                self.realtime_stt_final_model, realtime_stt_final_model_display[-1]
            )
        )
        realtime_stt_final_model_menu = tk.OptionMenu(
            realtime_stt_container,
            realtime_stt_final_model_var,
            *realtime_stt_final_model_display,
        )
        self._apply_option_menu_style(realtime_stt_final_model_menu, realtime_stt_final_model_var)
        realtime_stt_final_model_menu.pack(anchor="w")

        self._add_setting_label(
            realtime_stt_container,
            "Realtime model:",
            "Fast model used internally every ~0.2 s to drive dynamic silence detection. Not shown "
            "on screen — kept small so it doesn't compete with the final model for GPU time.",
            label_opts, pady=(10, 4),
        )
        realtime_stt_realtime_model_options = [
            ("tiny (~1 GB VRAM, recommended)", "tiny"),
            ("base (~1 GB VRAM)", "base"),
            ("small (~2 GB VRAM)", "small"),
        ]
        realtime_stt_realtime_model_map = dict(realtime_stt_realtime_model_options)
        realtime_stt_realtime_model_rev_map = {
            value: name for name, value in realtime_stt_realtime_model_options
        }
        realtime_stt_realtime_model_display = [
            name for name, _ in realtime_stt_realtime_model_options
        ]
        realtime_stt_realtime_model_var = tk.StringVar(
            value=realtime_stt_realtime_model_rev_map.get(
                self.realtime_stt_realtime_model, realtime_stt_realtime_model_display[0]
            )
        )
        realtime_stt_realtime_model_menu = tk.OptionMenu(
            realtime_stt_container,
            realtime_stt_realtime_model_var,
            *realtime_stt_realtime_model_display,
        )
        self._apply_option_menu_style(
            realtime_stt_realtime_model_menu, realtime_stt_realtime_model_var
        )
        realtime_stt_realtime_model_menu.pack(anchor="w")

        self._add_setting_label(
            realtime_stt_container,
            "Voice Sensitivity:",
            "How easily speech is detected. Lower catches softer/quieter speech; higher ignores background noise better.",
            label_opts, pady=(10, 4),
        )
        realtime_stt_silero_var = tk.DoubleVar(value=self.realtime_stt_silero_sensitivity)
        realtime_stt_silero_spin = tk.Spinbox(
            realtime_stt_container, from_=0.1, to=0.9, increment=0.05,
            textvariable=realtime_stt_silero_var, width=8,
        )
        self._apply_input_style(realtime_stt_silero_spin)
        realtime_stt_silero_spin.pack(anchor="w")

        interim_row = tk.Frame(realtime_stt_container, bg=section_bg)
        interim_row.pack(anchor="w", fill=tk.X, pady=(10, 0))
        show_interim_text_var = tk.BooleanVar(value=self.show_interim_text)
        show_interim_check = tk.Checkbutton(
            interim_row,
            text="Show live interim text (near-realtime)",
            variable=show_interim_text_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        show_interim_check.pack(side=tk.LEFT)
        self._create_help_icon(
            interim_row,
            "Shows words on the bottom line of the output as they are being "
            "spoken, so captions keep up with the live video feed instead of "
            "trailing it by a sentence. Interim text may correct itself as "
            "speech continues; the finalized sentence replaces it, appearing "
            "all at once instead of the word-by-word roll-up. Only "
            "available with translation off: translating a partial, still-"
            "changing sentence produces reordered, inconsistent output "
            "compared to the eventual finalized translation, so this is "
            "disabled whenever translation is on.",
            section_bg,
            settings_fg,
        )

        return {
            "stt_source_lang_var": stt_source_lang_var,
            "stt_source_lang_map": stt_source_lang_map,
            "stt_device_var": stt_device_var,
            "realtime_stt_final_model_var": realtime_stt_final_model_var,
            "realtime_stt_final_model_map": realtime_stt_final_model_map,
            "realtime_stt_final_model_rev_map": realtime_stt_final_model_rev_map,
            "realtime_stt_realtime_model_var": realtime_stt_realtime_model_var,
            "realtime_stt_realtime_model_map": realtime_stt_realtime_model_map,
            "realtime_stt_realtime_model_rev_map": realtime_stt_realtime_model_rev_map,
            "realtime_stt_silero_var": realtime_stt_silero_var,
            "show_interim_text_var": show_interim_text_var,
            "show_interim_check": show_interim_check,
        }

    def _build_translation_section(self, translation_section, label_opts, stt_source_lang_var):
        section_bg = label_opts["bg"]
        settings_fg = label_opts["fg"]
        enable_translation_var = tk.BooleanVar(value=self.translation_enabled)
        translate_row = tk.Frame(translation_section, bg=label_opts["bg"])
        translate_row.pack(anchor="w", pady=(0, 8), fill=tk.X)
        translate_check = tk.Checkbutton(
            translate_row,
            text="Enable translation",
            variable=enable_translation_var,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            selectcolor=label_opts["bg"],
            activebackground=label_opts["bg"],
        )
        translate_check.pack(side=tk.LEFT)
        self._create_help_icon(
            translate_row,
            "Translation OFF: transcripts pass through. Translation ON: source text is translated by Local NLLB.",
            label_opts["bg"],
            label_opts["fg"],
        )
        toggle_state_label = tk.Label(
            translation_section,
            text="Current mode: Translation OFF",
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            font=(self.ui_font_family, 9),
        )
        toggle_state_label.pack(anchor="w", pady=(0, 8))
        output_lang_label = tk.Label(
            translation_section,
            text="",
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            font=(self.ui_font_family, 9),
        )
        output_lang_label.pack(anchor="w", pady=(0, 8))
        input_lang_label = tk.Label(
            translation_section,
            text="",
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            font=(self.ui_font_family, 9),
        )
        input_lang_label.pack(anchor="w", pady=(0, 8))

        nllb_container = tk.Frame(translation_section, bg=section_bg)
        nllb_container.pack(fill=tk.X, pady=(0, 8))
        nllb_help = (
            "Local NLLB uses Meta's NLLB-200 models to translate text locally, "
            "offline, supporting 200 languages. Choose a model size below based "
            "on your hardware — larger models translate more accurately but need "
            "more VRAM/disk space and run slower. The app will ask before "
            "downloading the selected model; after the first download, "
            "translation runs fully offline from the local cache."
        )
        nllb_help_label = tk.Label(
            nllb_container,
            text=nllb_help,
            bg=section_bg,
            fg=settings_fg,
            justify="left",
            font=(self.ui_font_family, 9),
        )
        nllb_help_label.pack(anchor="w", fill=tk.X, pady=(0, 6))
        self._bind_responsive_wraplength(nllb_help_label)
        nllb_scope_label = tk.Label(
            nllb_container,
            text=(
                "Local NLLB translates transcripts after ASR (RealtimeSTT/"
                "faster-whisper) — it does not perform speech recognition or "
                "punctuation restoration. faster-whisper only transcribes; its "
                "own built-in translate mode can only output English, so this "
                "app never uses it. Local NLLB translates from the Source "
                "language selected in the Transcription section (there's no "
                "separate source-language setting here, since translation "
                "quality depends on NLLB being told the language the "
                "transcript is actually in) into any of the 200 languages "
                "below."
            ),
            bg=section_bg,
            fg=settings_fg,
            justify="left",
            font=(self.ui_font_family, 9),
        )
        nllb_scope_label.pack(anchor="w", fill=tk.X, pady=(0, 8))
        self._bind_responsive_wraplength(nllb_scope_label)

        status_row = tk.Frame(nllb_container, bg=section_bg)
        status_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            status_row,
            text="Local NLLB status:",
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9, "bold"),
        ).pack(side=tk.LEFT)
        self.local_nllb_status_var = tk.StringVar(value=self.nllb_status)
        tk.Label(
            status_row,
            textvariable=self.local_nllb_status_var,
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9),
        ).pack(side=tk.LEFT, padx=(6, 0))
        self.local_nllb_message_var = tk.StringVar(
            value=self._local_nllb_status_message()
        )
        nllb_message_label = tk.Label(
            nllb_container,
            textvariable=self.local_nllb_message_var,
            bg=section_bg,
            fg=settings_fg,
            justify="left",
            font=(self.ui_font_family, 9),
        )
        nllb_message_label.pack(anchor="w", fill=tk.X, pady=(0, 8))
        self._bind_responsive_wraplength(nllb_message_label)

        self._add_setting_label(
            nllb_container,
            "Model name:",
            "Hugging Face model id for local text translation. Larger models translate more "
            "accurately but need more VRAM/RAM and disk space, and run slower.",
            label_opts,
            pady=(0, 4),
        )
        nllb_model_name_options = [
            (
                "nllb-200-distilled-600M (~2.5 GB disk, ~4-6 GB VRAM, recommended)",
                "facebook/nllb-200-distilled-600M",
            ),
            (
                "nllb-200-distilled-1.3B (~5.5 GB disk, ~6-8 GB VRAM)",
                "facebook/nllb-200-distilled-1.3B",
            ),
            (
                "nllb-200-1.3B (~5.5 GB disk, ~8-10 GB VRAM, dense/higher quality)",
                "facebook/nllb-200-1.3B",
            ),
            (
                "nllb-200-3.3B (~13 GB disk, ~16+ GB VRAM, highest quality)",
                "facebook/nllb-200-3.3B",
            ),
        ]
        nllb_model_name_map = dict(nllb_model_name_options)
        nllb_model_name_rev_map = {value: name for name, value in nllb_model_name_options}
        nllb_model_name_display = [name for name, _ in nllb_model_name_options]
        local_nllb_model_name_var = tk.StringVar(
            value=nllb_model_name_rev_map.get(
                self.local_nllb_model_name, nllb_model_name_display[0]
            )
        )
        nllb_model_name_menu = tk.OptionMenu(
            nllb_container,
            local_nllb_model_name_var,
            *nllb_model_name_display,
        )
        self._apply_option_menu_style(nllb_model_name_menu, local_nllb_model_name_var)
        nllb_model_name_menu.pack(anchor="w", pady=(0, 8))

        self._add_setting_label(
            nllb_container,
            "Device:",
            "Auto uses CUDA when available, otherwise CPU.",
            label_opts,
            pady=(0, 4),
        )
        nllb_device_options = ["Auto", "CUDA", "CPU"]
        local_nllb_device_var = tk.StringVar(
            value={
                "auto": "Auto",
                "cuda": "CUDA",
                "cpu": "CPU",
            }.get(self.local_nllb_device, "Auto")
        )
        nllb_device_menu = tk.OptionMenu(
            nllb_container,
            local_nllb_device_var,
            *nllb_device_options,
        )
        self._apply_option_menu_style(nllb_device_menu, local_nllb_device_var)
        nllb_device_menu.pack(anchor="w", pady=(0, 8))

        nllb_all_language_options = nllb_language_options()

        # No separate NLLB source-language picker: the source is always the
        # Transcription section's Source language (see _resolve_local_nllb_source_lang),
        # since NLLB translation quality depends on being told the language
        # the transcript is actually in, which is exactly what governs what
        # Whisper transcribes - letting them diverge only ever produced a
        # broken configuration (mismatched Whisper language hint -> garbled
        # transcription -> garbage translation), never a useful one.
        self._add_setting_label(
            nllb_container,
            "Target language:",
            "Language the translated transcript is produced in. Type to search all 200 languages.",
            label_opts,
            pady=(0, 4),
        )
        (
            nllb_target_combobox,
            local_nllb_target_lang_var,
            local_nllb_target_lang_map,
        ) = self._build_searchable_language_combobox(
            nllb_container,
            nllb_all_language_options,
            current_code=self.local_nllb_target_lang,
            default_display="English",
        )
        nllb_target_combobox.pack(anchor="w", pady=(0, 8))

        self._add_setting_label(
            nllb_container,
            "Max chars per chunk:",
            "Long transcripts are split by paragraph, sentence, or length before translation.",
            label_opts,
            pady=(0, 4),
        )
        local_nllb_max_chars_var = tk.IntVar(value=self.local_nllb_max_chars)
        nllb_max_spin = tk.Spinbox(
            nllb_container,
            from_=250,
            to=20000,
            increment=250,
            textvariable=local_nllb_max_chars_var,
            width=10,
        )
        self._apply_input_style(nllb_max_spin)
        nllb_max_spin.pack(anchor="w", pady=(0, 8))

        action_row = tk.Frame(nllb_container, bg=section_bg)
        action_row.pack(fill=tk.X)

        download_button = self._make_button(
            action_row,
            "Download Local NLLB model",
            command=lambda: self._download_or_check_local_nllb_from_vars(
                local_nllb_model_name_var,
                local_nllb_device_var,
                local_nllb_max_chars_var,
                model_name_map=nllb_model_name_map,
            ),
            primary=True,
        )
        download_button.pack(side=tk.LEFT)

        test_button = self._make_button(
            action_row,
            "Test Local NLLB",
            command=lambda: self._run_local_nllb_test_from_vars(
                local_nllb_model_name_var,
                local_nllb_device_var,
                local_nllb_max_chars_var,
                test_button,
                self.local_nllb_message_var,
                model_name_map=nllb_model_name_map,
            ),
            primary=True,
        )
        test_button.pack(side=tk.LEFT, padx=(8, 0))
        self.local_nllb_download_button = download_button
        self.local_nllb_test_button = test_button

        refresh_label = lambda *_args: self._refresh_translation_toggle_label(
            enable_translation_var,
            toggle_state_label,
            output_lang_label,
            input_lang_label,
            local_nllb_target_lang_var,
            stt_source_lang_var,
        )
        sync_runtime = lambda *_args: self._sync_translation_toggle_runtime(
            enable_translation_var
        )

        def handle_nllb_config_change(*_args):
            if self.nllb_status in ("Checking", "Downloading", "Loading"):
                return
            config = self._local_nllb_config_from_vars(
                local_nllb_model_name_var,
                local_nllb_device_var,
                local_nllb_max_chars_var,
                model_name_map=nllb_model_name_map,
            )
            if self.nllb_ready_config == self._local_nllb_config_tuple_from_config(config):
                return
            self.nllb_model_loaded = False
            self.nllb_ready_config = None
            self._set_local_nllb_status(
                "Not downloaded",
                "Local NLLB settings changed. Download or retry for the selected model/cache.",
            )

        def maybe_start_nllb_prewarm(*_args):
            # Translation is opt-in — don't check the cache or prompt for a
            # ~2.5 GB download until the user actually turns translation on.
            if not self._coerce_bool(enable_translation_var.get(), default=False):
                return
            if self.nllb_status in ("Checking", "Downloading", "Loading", "Ready"):
                return
            self._start_local_nllb_cache_check(
                self._local_nllb_config_from_vars(
                    local_nllb_model_name_var,
                    local_nllb_device_var,
                    local_nllb_max_chars_var,
                    model_name_map=nllb_model_name_map,
                ),
                prompt_if_missing=True,
            )

        enable_translation_var.trace_add("write", refresh_label)
        enable_translation_var.trace_add("write", sync_runtime)
        enable_translation_var.trace_add("write", maybe_start_nllb_prewarm)
        local_nllb_target_lang_var.trace_add("write", refresh_label)
        stt_source_lang_var.trace_add("write", refresh_label)
        local_nllb_model_name_var.trace_add("write", handle_nllb_config_change)
        local_nllb_device_var.trace_add("write", handle_nllb_config_change)
        refresh_label()
        self._refresh_local_nllb_runtime_ui()
        if self.translation_enabled:
            maybe_start_nllb_prewarm()
        else:
            self._set_local_nllb_status(
                "Not selected",
                "Translation is off. Enable it above to check or download the Local NLLB model.",
            )
            # Nothing to wait on — unblock the startup overlay's translation
            # half immediately instead of hanging on a check that will never
            # run (mirrors _mark_startup_stt_ready for the STT half).
            self._mark_startup_translation_ready()

        return {
            "enable_translation_var": enable_translation_var,
            "local_nllb_model_name_var": local_nllb_model_name_var,
            "local_nllb_model_name_map": nllb_model_name_map,
            "local_nllb_model_name_rev_map": nllb_model_name_rev_map,
            "local_nllb_device_var": local_nllb_device_var,
            "local_nllb_target_lang_var": local_nllb_target_lang_var,
            "local_nllb_target_lang_map": local_nllb_target_lang_map,
            "local_nllb_max_chars_var": local_nllb_max_chars_var,
        }

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
        parent = self.settings_window if self.settings_window is not None else self.root
        messagebox.showinfo("Hardware Autodetect", result_var.get(), parent=parent)

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

    def _show_local_nllb_progress_popup(self, message):
        parent = self.settings_window if self.settings_window is not None else self.root
        palette = self._settings_palette()
        popup = tk.Toplevel(parent)
        popup.title("Local NLLB")
        popup.configure(bg=palette["section_bg"])
        popup.resizable(False, False)
        popup.transient(parent)
        # A download/verification in progress can't be safely interrupted
        # (partially-written model files, a half-initialized torch model),
        # so block the close button the same way the camera scan popup does.
        popup.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = tk.Frame(popup, bg=palette["section_bg"], padx=24, pady=18)
        frame.pack()
        tk.Label(
            frame,
            text="Preparing Local NLLB...",
            bg=palette["section_bg"],
            fg=palette["text"],
            font=(self.ui_font_family, 11, "bold"),
        ).pack(pady=(0, 10))
        # Indeterminate (bouncing) bar, matching the camera-scan popup - a
        # determinate fill bar here would visibly stall for long stretches
        # (a multi-GB download or model load has no reliable byte-progress
        # signal to drive it), which reads as frozen.
        progress = ttkb.Progressbar(
            frame,
            mode="indeterminate",
            length=280,
            bootstyle=self._surface_bootstyle(palette["section_bg"]),
        )
        progress.pack()
        progress.start(15)
        status_var = tk.StringVar(value=message)
        tk.Label(
            frame,
            textvariable=status_var,
            bg=palette["section_bg"],
            fg=palette["muted_text"],
            font=(self.ui_font_family, 9),
            wraplength=280,
            justify="left",
        ).pack(pady=(8, 0))

        popup.update_idletasks()
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - popup.winfo_width()) // 2
            py = parent.winfo_rooty() + (parent.winfo_height() - popup.winfo_height()) // 2
            popup.geometry(f"+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass
        try:
            popup.grab_set()
        except Exception:
            pass

        self._local_nllb_popup = popup
        self._local_nllb_popup_progress = progress
        self._local_nllb_popup_status_var = status_var

    def _update_local_nllb_progress_popup(self, message):
        status_var = getattr(self, "_local_nllb_popup_status_var", None)
        if status_var is not None:
            try:
                status_var.set(message)
            except Exception:
                pass

    def _close_local_nllb_progress_popup(self):
        popup = getattr(self, "_local_nllb_popup", None)
        progress = getattr(self, "_local_nllb_popup_progress", None)
        if progress is not None:
            try:
                progress.stop()
            except Exception:
                pass
        if popup is not None:
            try:
                popup.grab_release()
            except Exception:
                pass
            try:
                popup.destroy()
            except Exception:
                pass
        self._local_nllb_popup = None
        self._local_nllb_popup_progress = None
        self._local_nllb_popup_status_var = None

    def _sync_local_nllb_progress_popup(self):
        # Suppressed until the startup loading overlay has cleared (see
        # _show_startup_loading_overlay) - if translation was already on
        # from a previous run, the cache check/download/verification that
        # runs at startup is already covered by that overlay's own bouncing
        # bar, so a second popup on top of it would just be redundant
        # chrome stacked on chrome. After startup, any check/download/load
        # (enabling translation + Apply, changing the model/device dropdown,
        # or the manual Download/Check for Updates button) shows this one.
        in_progress_statuses = ("Checking", "Downloading", "Downloaded", "Loading")
        should_show = self.app_startup_ready and self.nllb_status in in_progress_statuses
        if should_show:
            message = self._local_nllb_status_message()
            if getattr(self, "_local_nllb_popup", None) is not None:
                self._update_local_nllb_progress_popup(message)
            else:
                self._show_local_nllb_progress_popup(message)
        else:
            self._close_local_nllb_progress_popup()

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

    def _refresh_local_nllb_runtime_ui(self):
        in_progress = bool(self.nllb_download_in_progress or self.nllb_check_in_progress)
        in_progress = in_progress or self.nllb_status in ("Checking", "Downloading", "Loading")
        ready = self.nllb_status == "Ready"
        try:
            if self.local_nllb_status_var is not None:
                self.local_nllb_status_var.set(self.nllb_status)
            if self.local_nllb_message_var is not None:
                self.local_nllb_message_var.set(self._local_nllb_status_message())
        except Exception:
            pass
        try:
            if self.local_nllb_download_button is not None:
                download_state = tk.DISABLED if in_progress else tk.NORMAL
                download_label = "Check for Updates" if ready else "Download Local NLLB model"
                self.local_nllb_download_button.config(state=download_state, text=download_label)
        except Exception:
            pass
        try:
            if self.local_nllb_test_button is not None:
                test_state = tk.NORMAL if (not in_progress and ready) else tk.DISABLED
                self.local_nllb_test_button.config(state=test_state)
        except Exception:
            pass

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

    def _confirm_local_nllb_download(self, model_name):
        model_name = (model_name or "").strip() or self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        parent = self.settings_window if self.settings_window is not None else self.root
        result = {"download": False}
        dialog = tk.Toplevel(parent)
        dialog.title("Download Local NLLB model?")
        dialog.configure(bg=self._settings_palette()["section_bg"])
        dialog.resizable(False, False)
        try:
            dialog.transient(parent)
            dialog.grab_set()
        except Exception:
            pass
        frame = tk.Frame(dialog, bg=self._settings_palette()["section_bg"], padx=18, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)
        message = (
            "Local NLLB translation requires downloading Meta's NLLB-200 "
            "distilled 600M model.\n\n"
            f"Model:\n{model_name}\n\n"
            "This download only needs to happen once. After it is cached, "
            "Local NLLB can run offline.\n\n"
            "Do you want to download it now?"
        )
        tk.Label(
            frame,
            text=message,
            bg=self._settings_palette()["section_bg"],
            fg=self._settings_palette()["text"],
            justify="left",
            wraplength=420,
            font=(self.ui_font_family, 10),
        ).pack(anchor="w", fill=tk.X)
        button_row = tk.Frame(frame, bg=self._settings_palette()["section_bg"])
        button_row.pack(anchor="e", fill=tk.X, pady=(16, 0))

        def choose_download():
            result["download"] = True
            dialog.destroy()

        def choose_cancel():
            result["download"] = False
            dialog.destroy()

        download_button = self._make_button(
            button_row,
            "Download",
            command=choose_download,
            primary=True,
        )
        download_button.pack(side=tk.RIGHT)
        cancel_button = self._make_button(
            button_row,
            "Cancel",
            command=choose_cancel,
        )
        cancel_button.pack(side=tk.RIGHT, padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", choose_cancel)
        try:
            dialog.update_idletasks()
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            parent_w = max(1, parent.winfo_width())
            parent_h = max(1, parent.winfo_height())
            dialog_w = dialog.winfo_width()
            dialog_h = dialog.winfo_height()
            x = parent_x + max(0, (parent_w - dialog_w) // 2)
            y = parent_y + max(0, (parent_h - dialog_h) // 2)
            dialog.geometry(f"+{x}+{y}")
            download_button.focus_set()
        except Exception:
            pass
        dialog.wait_window()
        return bool(result["download"])

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

    def _refresh_translation_toggle_label(
        self,
        enable_translation_var,
        toggle_state_label,
        output_lang_label,
        input_lang_label,
        local_nllb_target_lang_var,
        stt_source_lang_var,
    ):
        enabled = self._coerce_bool(enable_translation_var.get(), default=False)
        if enabled:
            toggle_state_label.config(text="Current mode: Translation ON (Local NLLB)")
            target = (local_nllb_target_lang_var.get() or "").strip() or "English"
            output_lang_label.config(text=f"Output language: {target}")
        else:
            toggle_state_label.config(text="Current mode: Translation OFF")
            output_lang_label.config(text="Output language: same as input (translation is off)")
        source = (stt_source_lang_var.get() or "").strip() or "Auto-detect"
        if source.lower() == "auto-detect":
            detected = (self.auto_detect_lang or "").strip().lower()
            if detected:
                source = f"Auto-detect (currently: {self._language_label(detected)})"
        input_lang_label.config(
            text=f"Input language: {source} (set in Transcription section above)"
        )

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

    def choose_color(self, color_var, color_type, parent):
        color = colorchooser.askcolor(title=f"Choose {color_type} color", parent=parent)
        if color[1]:  # color[1] is the hex value
            color_var.set(color[1])

    def choose_directory(self, path_var, title):
        parent = self.settings_window if self.settings_window is not None else self.root
        selected = filedialog.askdirectory(
            title=title,
            parent=parent,
            mustexist=True,
        )
        if selected:
            path_var.set(self._normalize_optional_directory(selected))

    def apply_colors(self):
        bg = self.bg_color
        self.root.config(bg=bg)
        self.text_canvas.config(bg=bg)
        self.text_canvas.itemconfigure(self.text_item, fill=self.text_color)
        for item in self.text_line_items:
            self.text_canvas.itemconfigure(item, fill=self.text_color)

    def _apply_canvas_padding(self):
        pad = 0 if self.is_fullscreen else self.canvas_margin
        self.text_canvas.grid_configure(padx=pad, pady=pad)

