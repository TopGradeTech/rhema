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

from tooltip import Tooltip


class SettingsUIMixin:
    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return

        settings_window = tk.Toplevel(self.root)
        self.settings_window = settings_window
        settings_window.title("Translation Controller")
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

        content = self._build_settings_canvas(settings_window, settings_bg)
        display_vars, audio_vars, transcription_vars, translation_vars, advanced_vars = (
            self._build_settings_sections(
                content,
                settings_window,
                label_opts,
                section_bg,
                settings_fg,
                section_font,
            )
        )
        # API key visibility depends on the selected speech engine.
        dirty_ctx = self._new_settings_dirty_context()

        button_frame = tk.Frame(settings_window, bg=settings_bg)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(8, 12))

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
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 10),
            bd=0,
            highlightthickness=0,
        )
        self.status_label.pack(fill=tk.X)

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

        toggle_fullscreen_button = self._make_button(
            button_frame,
            "Toggle Fullscreen",
            command=self.toggle_fullscreen,
            primary=True,
        )
        toggle_fullscreen_button.pack(side=tk.RIGHT, padx=(0, 10), pady=10)

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
        try:
            save_button.configure(takefocus=0)
        except Exception:
            pass
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
        self._start_audio_level_updates()
        if not self.app_startup_ready:
            self._show_startup_loading_overlay(settings_window, settings_bg, settings_fg)

    def _show_startup_loading_overlay(self, settings_window, settings_bg, settings_fg):
        """Block settings interaction behind a full-window overlay until both
        RealtimeSTT and Local NLLB have finished their initial load/verify
        pass, so the user can't change device/model settings out from under
        a load already in progress. Shown once per app run; see
        _check_startup_ready/_mark_startup_stt_ready/
        _mark_startup_translation_ready.
        """
        overlay = tk.Frame(settings_window, bg=settings_bg)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
        overlay.bind("<Button-1>", lambda _e: "break")

        center = tk.Frame(overlay, bg=settings_bg)
        center.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(
            center,
            text="Starting up...",
            bg=settings_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 14, "bold"),
        ).pack(pady=(0, 8))
        tk.Label(
            center,
            text="Loading speech recognition and translation models.",
            bg=settings_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 10),
            wraplength=420,
            justify="center",
        ).pack(pady=(0, 14))
        progress = ttkb.Progressbar(center, mode="indeterminate", length=320)
        progress.pack(pady=(0, 10))
        progress.start(15)
        # Two dedicated lines instead of mirroring the shared status bar:
        # once RealtimeSTT is ready it fires a "Listening..." update on every
        # idle cycle, which drowned out Local NLLB's (much rarer, often much
        # slower) download/loading messages and made a still-working NLLB
        # load look identical to a hang.
        self._startup_overlay_stt_var = tk.StringVar(value="Speech recognition: Loading...")
        tk.Label(
            center,
            textvariable=self._startup_overlay_stt_var,
            bg=settings_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9),
            wraplength=420,
            justify="center",
        ).pack()
        self._startup_overlay_translation_var = tk.StringVar(value="Translation: Loading...")
        tk.Label(
            center,
            textvariable=self._startup_overlay_translation_var,
            bg=settings_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9),
            wraplength=420,
            justify="center",
        ).pack()

        self._startup_loading_overlay = overlay
        self._startup_loading_progress = progress
        self._poll_startup_overlay_status()

    def _poll_startup_overlay_status(self):
        if self._startup_loading_overlay is None:
            return
        try:
            self._startup_overlay_stt_var.set(
                "Speech recognition: "
                + ("Ready" if self.startup_stt_ready else "Loading...")
            )
            self._startup_overlay_translation_var.set(
                f"Translation: {self._local_nllb_status_message() or self.nllb_status}"
            )
        except Exception:
            pass
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
        self._startup_overlay_stt_var = None
        self._startup_overlay_translation_var = None

    def _check_startup_ready(self):
        if self.app_startup_ready:
            return
        if not (self.startup_stt_ready and self.startup_translation_ready):
            return
        self.app_startup_ready = True
        self._hide_startup_loading_overlay()

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
            messagebox.showerror("Apply Failed", f"{exc}")
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
        if self.is_fullscreen:
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

    def _apply_display_vars(self, display_vars):
        self.max_lines = display_vars["lines_var"].get()
        self.bg_color = display_vars["bg_color_var"].get()
        self.text_color = display_vars["text_color_var"].get()
        if "lock_output_focus_var" in display_vars:
            self.lock_output_focus = bool(display_vars["lock_output_focus_var"].get())
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
        if "stt_device_var" in transcription_vars:
            self.stt_device = self._normalize_stt_device(
                transcription_vars["stt_device_var"].get()
            )
        self.realtime_stt_final_model = self._optional_mapped_api_setting(
            transcription_vars,
            "realtime_stt_final_model_var",
            "realtime_stt_final_model_map",
            current_value=self.realtime_stt_final_model,
            mapped_default="large-v3",
        )
        self.realtime_stt_realtime_model = self._optional_mapped_api_setting(
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
        # device/model/sensitivity are only read when RealtimeSTT constructs
        # its recorder, so a live rebuild is needed for the change to apply.
        if (
            self.stt_device != previous_device
            or self.realtime_stt_final_model != previous_final_model
            or self.realtime_stt_realtime_model != previous_realtime_model
            or self.realtime_stt_silero_sensitivity != previous_silero_sensitivity
        ):
            self._request_capture_restart()

    def _optional_mapped_api_setting(
        self,
        api_vars,
        var_key,
        map_key,
        current_value,
        mapped_default,
    ):
        if var_key not in api_vars or map_key not in api_vars:
            return current_value
        selected = api_vars[var_key].get()
        mapping = api_vars[map_key]
        return mapping.get(selected, mapped_default)

    def _optional_string_api_setting(
        self,
        api_vars,
        var_key,
        current_value,
        empty_default=None,
    ):
        if var_key not in api_vars:
            return current_value
        value = api_vars[var_key].get().strip()
        if empty_default is None:
            return value
        return value or empty_default

    def _apply_translation_vars(self, translation_vars):
        previous_nllb_config = (
            self.local_nllb_model_name,
            self.local_nllb_device,
            self.local_nllb_cache_dir,
        )
        was_translation_enabled = bool(self.translation_enabled)
        new_translation_enabled = was_translation_enabled
        if "enable_translation_var" in translation_vars:
            new_translation_enabled = self._coerce_bool(
                translation_vars["enable_translation_var"].get(),
                default=False,
            )
        self.translation_enabled = new_translation_enabled
        if self.translation_enabled != was_translation_enabled:
            self._apply_translation_mode_defaults()
        else:
            self._normalize_translation_settings()
        self.local_nllb_model_name = self._optional_mapped_api_setting(
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
        self.local_nllb_source_lang = self._optional_mapped_api_setting(
            translation_vars,
            "local_nllb_source_lang_var",
            "local_nllb_source_lang_map",
            current_value=self.local_nllb_source_lang,
            mapped_default=self.LOCAL_NLLB_DEFAULT_SOURCE_LANG,
        )
        self.local_nllb_target_lang = self._optional_mapped_api_setting(
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
        if "local_nllb_cache_dir_var" in translation_vars:
            self.local_nllb_cache_dir = self._normalize_optional_directory(
                translation_vars["local_nllb_cache_dir_var"].get()
            )
        next_nllb_config = (
            self.local_nllb_model_name,
            self.local_nllb_device,
            self.local_nllb_cache_dir,
        )
        if next_nllb_config != previous_nllb_config:
            with self.local_nllb_lock:
                self.local_nllb_tokenizer = None
                self.local_nllb_model = None
                self.local_nllb_model_config = None
                self.local_nllb_resolved_device = ""
            self.nllb_model_loaded = False
            self.nllb_ready_config = None
            self.nllb_last_error = ""
            gc.collect()
        self._trace_pipeline(
            "translation_toggle_applied",
            "",
            translation_enabled=self.translation_enabled,
            text_translation_provider=self.text_translation_provider,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            local_nllb_model=self.local_nllb_model_name,
            local_nllb_device=self.local_nllb_device,
            local_nllb_source_lang=self.local_nllb_source_lang,
            local_nllb_target_lang=self.local_nllb_target_lang,
        )
        if was_translation_enabled and not self.translation_enabled:
            self._clear_translation_backlog_after_disable()
        self._start_local_nllb_cache_check(
            self._local_nllb_runtime_config(),
            prompt_if_missing=True,
        )

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

    def _apply_settings_geometry(self, settings_window):
        settings_window.geometry("960x1280")
        settings_window.minsize(960, 1280)
        settings_window.update_idletasks()
        self._maximize_settings_window(settings_window)
        geometry_monitor_index = self._monitor_index_from_saved_settings_geometry(
            settings_window
        )
        if self._settings_window_requires_reposition(geometry_monitor_index):
            self._position_settings_window(settings_window)
        self._move_settings_window_to_selected_monitor()

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

    def _settings_palette(self):
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

    def _make_button(self, parent, text, command=None, primary=False):
        bootstyle = PRIMARY if primary else None
        button = ttkb.Button(parent, text=text, command=command, bootstyle=bootstyle)
        try:
            button.configure(takefocus=0)
        except Exception:
            pass
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

    def _apply_option_menu_style(self, menu):
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
    
    def _compute_scaled_font_size(self):
        base_size = max(12, int(self.font_size))
        lines = max(1, int(self.max_lines))
        scaled = int(round(base_size * (8.0 / lines)))
        return max(12, min(scaled, 120))

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
        lines = max(1, int(self.max_lines))
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
        if self.preview_font is not None:
            preview_size = max(12, int(best * 0.5))
            self.preview_font.configure(size=preview_size)

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
        available = max(1, height - (self.text_padding * 2))
        lines = max(1, int(self.max_lines))
        self._ensure_line_items(lines)
        line_height = self.text_font.metrics("linespace") or 1
        if lines > 1 and available > line_height:
            step = (available - line_height) / (lines - 1)
        else:
            step = 0

        slots = [""] * lines
        for i, line in enumerate(display_lines[-lines:]):
            slots[lines - len(display_lines[-lines:]) + i] = line

        for idx, line in enumerate(slots):
            y = self.text_padding + (idx * step)
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

    def _build_preview_section(self, content, label_opts, section_bg, settings_fg, section_font):
        preview_section = tk.LabelFrame(
            content,
            text="Output Preview",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        preview_section.pack(fill=tk.X, pady=(0, 10))

        tk.Label(preview_section, text="Current output:", **label_opts).pack(anchor="w", pady=(0, 4))
        preview_size = max(12, int(self._compute_scaled_font_size() * 0.5))
        self.preview_font = tkfont.Font(family=self.font_family, size=preview_size)
        self.preview_widget = tk.Label(
            preview_section,
            text=self.preview_placeholder,
            bg=self.bg_color,
            fg=self.text_color,
            font=self.preview_font,
            justify="left",
            anchor="nw",
            height=4,
            relief="solid",
            borderwidth=1,
        )
        self.preview_widget.pack(fill=tk.X)

        def update_preview_wrap(event):
            widget = self.preview_widget
            if widget and widget.winfo_exists():
                widget.config(wraplength=max(1, event.width - 10))

        self.preview_widget.bind(self.CONFIGURE_EVENT, update_preview_wrap)
        self._sync_preview_colors()
        try:
            self.render_text()
        except Exception:
            pass

    def _build_settings_sections(
        self,
        content,
        settings_window,
        label_opts,
        section_bg,
        settings_fg,
        section_font,
    ):
        self._build_preview_section(content, label_opts, section_bg, settings_fg, section_font)

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
        translation_vars = self._build_translation_section(translation_section, label_opts)

        hardware_section = tk.LabelFrame(
            content,
            text="Hardware Autodetect",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        hardware_section.pack(fill=tk.X, pady=(10, 0))
        self._build_hardware_autodetect_section(
            hardware_section, label_opts, transcription_vars, translation_vars
        )

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

        def on_mousewheel(event):
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")

        for event_name in self.SCROLL_EVENTS:
            settings_window.bind_all(event_name, on_mousewheel)

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
            "Number of lines to show:",
            "Maximum number of translated lines kept on screen.",
            label_opts,
            pady=(0, 4),
        )
        lines_var = tk.IntVar(value=self.max_lines)
        lines_spinbox = tk.Spinbox(display_section, from_=1, to=10, textvariable=lines_var)
        self._apply_input_style(lines_spinbox)
        lines_spinbox.pack(anchor="w")

        self._add_setting_label(
            display_section,
            "Background Color:",
            "Background color for the output overlay and preview.",
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
            "Monitor where the fullscreen translation output appears.",
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
        self._apply_option_menu_style(monitor_menu)
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
        self._apply_option_menu_style(settings_monitor_menu)
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
            "lines_var": lines_var,
            "bg_color_var": bg_color_var,
            "text_color_var": text_color_var,
            "lock_output_focus_var": lock_output_focus_var,
            "monitor_var": monitor_var,
            "settings_monitor_var": settings_monitor_var,
            "monitor_labels": monitor_labels,
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
        self._apply_option_menu_style(self.device_menu)
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
        self._apply_option_menu_style(logging_mode_menu)
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
        tk.Label(
            transcription_section,
            text=(
                "RealtimeSTT transcribes spoken audio to text locally in real "
                "time, using faster-whisper (an optimized, offline port of "
                "OpenAI's Whisper). Whisper's multilingual models understand "
                "99 languages and can auto-detect which one is being spoken."
            ),
            bg=section_bg,
            fg=settings_fg,
            wraplength=560,
            justify="left",
            font=(self.ui_font_family, 9),
        ).pack(anchor="w", pady=(0, 6))

        # ── RealtimeSTT settings panel ─────────────────────────────────
        realtime_stt_container = tk.Frame(transcription_section, bg=label_opts["bg"])
        realtime_stt_container.pack(fill=tk.X, pady=(10, 0))
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
        self._apply_option_menu_style(stt_device_menu)
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
        self._apply_option_menu_style(realtime_stt_final_model_menu)
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
        self._apply_option_menu_style(realtime_stt_realtime_model_menu)
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

        return {
            "stt_device_var": stt_device_var,
            "realtime_stt_final_model_var": realtime_stt_final_model_var,
            "realtime_stt_final_model_map": realtime_stt_final_model_map,
            "realtime_stt_final_model_rev_map": realtime_stt_final_model_rev_map,
            "realtime_stt_realtime_model_var": realtime_stt_realtime_model_var,
            "realtime_stt_realtime_model_map": realtime_stt_realtime_model_map,
            "realtime_stt_realtime_model_rev_map": realtime_stt_realtime_model_rev_map,
            "realtime_stt_silero_var": realtime_stt_silero_var,
        }

    def _build_translation_section(self, translation_section, label_opts):
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
            text=self.OUTPUT_LANGUAGE_ENGLISH_LABEL,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            font=(self.ui_font_family, 9),
        )
        output_lang_label.pack(anchor="w", pady=(0, 8))

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
        tk.Label(
            nllb_container,
            text=nllb_help,
            bg=section_bg,
            fg=settings_fg,
            wraplength=560,
            justify="left",
            font=(self.ui_font_family, 9),
        ).pack(anchor="w", pady=(0, 6))
        tk.Label(
            nllb_container,
            text=(
                "Local NLLB translates transcripts after ASR. It does not perform "
                "speech recognition or punctuation restoration."
            ),
            bg=section_bg,
            fg=settings_fg,
            wraplength=560,
            justify="left",
            font=(self.ui_font_family, 9),
        ).pack(anchor="w", pady=(0, 8))

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
            wraplength=560,
            justify="left",
            font=(self.ui_font_family, 9),
        )
        nllb_message_label.pack(anchor="w", fill=tk.X, pady=(0, 8))

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
        self._apply_option_menu_style(nllb_model_name_menu)
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
        self._apply_option_menu_style(nllb_device_menu)
        nllb_device_menu.pack(anchor="w", pady=(0, 8))

        nllb_language_options = [
            ("English", "eng_Latn"),
            ("Spanish", "spa_Latn"),
            ("French", "fra_Latn"),
            ("German", "deu_Latn"),
            ("Italian", "ita_Latn"),
            ("Portuguese", "por_Latn"),
            ("Dutch", "nld_Latn"),
            ("Russian", "rus_Cyrl"),
            ("Ukrainian", "ukr_Cyrl"),
            ("Arabic", "arb_Arab"),
            ("Hindi", "hin_Deva"),
            ("Chinese (Simplified)", "zho_Hans"),
            ("Chinese (Traditional)", "zho_Hant"),
            ("Japanese", "jpn_Jpan"),
            ("Korean", "kor_Hang"),
        ]

        lang_row = tk.Frame(nllb_container, bg=section_bg)
        lang_row.pack(fill=tk.X, pady=(0, 8))
        source_col = tk.Frame(lang_row, bg=section_bg)
        source_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        target_col = tk.Frame(lang_row, bg=section_bg)
        target_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._add_setting_label(
            source_col,
            "Source language:",
            "Auto uses the app's selected source language.",
            label_opts,
            pady=(0, 4),
        )
        nllb_source_options = [
            ("Auto (use selected source language)", self.LOCAL_NLLB_DEFAULT_SOURCE_LANG)
        ] + nllb_language_options
        nllb_source_display = [name for name, _ in nllb_source_options]
        local_nllb_source_lang_map = dict(nllb_source_options)
        nllb_source_rev_map = {code: name for name, code in nllb_source_options}
        local_nllb_source_lang_var = tk.StringVar(
            value=nllb_source_rev_map.get(
                self.local_nllb_source_lang, nllb_source_display[0]
            )
        )
        nllb_source_menu = tk.OptionMenu(
            source_col,
            local_nllb_source_lang_var,
            *nllb_source_display,
        )
        self._apply_option_menu_style(nllb_source_menu)
        nllb_source_menu.pack(anchor="w")
        self._add_setting_label(
            target_col,
            "Target language:",
            "Language the translated transcript is produced in.",
            label_opts,
            pady=(0, 4),
        )
        nllb_target_display = [name for name, _ in nllb_language_options]
        local_nllb_target_lang_map = dict(nllb_language_options)
        nllb_target_rev_map = {code: name for name, code in nllb_language_options}
        local_nllb_target_lang_var = tk.StringVar(
            value=nllb_target_rev_map.get(
                self.local_nllb_target_lang, nllb_target_display[0]
            )
        )
        nllb_target_menu = tk.OptionMenu(
            target_col,
            local_nllb_target_lang_var,
            *nllb_target_display,
        )
        self._apply_option_menu_style(nllb_target_menu)
        nllb_target_menu.pack(anchor="w")

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

        self._add_setting_label(
            nllb_container,
            "Cache directory:",
            "Optional. Blank uses the standard Hugging Face cache.",
            label_opts,
            pady=(0, 4),
        )
        local_nllb_cache_dir_var = tk.StringVar(value=self.local_nllb_cache_dir)
        cache_row = tk.Frame(nllb_container, bg=section_bg)
        cache_row.pack(fill=tk.X, pady=(0, 8))
        nllb_cache_entry = tk.Entry(
            cache_row,
            textvariable=local_nllb_cache_dir_var,
            width=48,
        )
        self._apply_input_style(nllb_cache_entry)
        nllb_cache_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        cache_browse_button = self._make_button(
            cache_row,
            "Browse",
            command=lambda: self.choose_directory(
                local_nllb_cache_dir_var,
                "Select Local NLLB cache directory",
            ),
            primary=True,
        )
        cache_browse_button.pack(side=tk.LEFT, padx=(8, 0))
        cache_clear_button = self._make_button(
            cache_row,
            "Clear",
            command=lambda: local_nllb_cache_dir_var.set(""),
        )
        cache_clear_button.pack(side=tk.LEFT, padx=(8, 0))

        action_row = tk.Frame(nllb_container, bg=section_bg)
        action_row.pack(fill=tk.X)

        download_button = self._make_button(
            action_row,
            "Download Local NLLB model",
            command=lambda: self._download_local_nllb_from_vars(
                local_nllb_model_name_var,
                local_nllb_device_var,
                local_nllb_cache_dir_var,
                local_nllb_max_chars_var,
                prompt=True,
                model_name_map=nllb_model_name_map,
            ),
            primary=True,
        )
        download_button.pack(side=tk.LEFT)

        retry_button = self._make_button(
            action_row,
            "Retry download",
            command=lambda: self._download_local_nllb_from_vars(
                local_nllb_model_name_var,
                local_nllb_device_var,
                local_nllb_cache_dir_var,
                local_nllb_max_chars_var,
                prompt=True,
                model_name_map=nllb_model_name_map,
            ),
        )
        retry_button.pack(side=tk.LEFT, padx=(8, 0))

        test_button = self._make_button(
            action_row,
            "Test Local NLLB",
            command=lambda: self._run_local_nllb_test_from_vars(
                local_nllb_model_name_var,
                local_nllb_device_var,
                local_nllb_cache_dir_var,
                local_nllb_max_chars_var,
                test_button,
                self.local_nllb_message_var,
                model_name_map=nllb_model_name_map,
            ),
            primary=True,
        )
        test_button.pack(side=tk.LEFT, padx=(8, 0))
        self.local_nllb_download_button = download_button
        self.local_nllb_retry_button = retry_button
        self.local_nllb_test_button = test_button

        refresh_label = lambda *_args: self._refresh_translation_toggle_label(
            enable_translation_var,
            toggle_state_label,
            output_lang_label,
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
                local_nllb_cache_dir_var,
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
                    local_nllb_cache_dir_var,
                    local_nllb_max_chars_var,
                    model_name_map=nllb_model_name_map,
                ),
                prompt_if_missing=True,
            )

        enable_translation_var.trace_add("write", refresh_label)
        enable_translation_var.trace_add("write", sync_runtime)
        enable_translation_var.trace_add("write", maybe_start_nllb_prewarm)
        local_nllb_model_name_var.trace_add("write", handle_nllb_config_change)
        local_nllb_device_var.trace_add("write", handle_nllb_config_change)
        local_nllb_cache_dir_var.trace_add("write", handle_nllb_config_change)
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
        fixed_input_label = tk.Label(
            translation_section,
            text="Default input language: English when OFF, Spanish when ON",
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            font=(self.ui_font_family, 9),
        )
        fixed_input_label.pack(anchor="w")

        return {
            "enable_translation_var": enable_translation_var,
            "local_nllb_model_name_var": local_nllb_model_name_var,
            "local_nllb_model_name_map": nllb_model_name_map,
            "local_nllb_model_name_rev_map": nllb_model_name_rev_map,
            "local_nllb_device_var": local_nllb_device_var,
            "local_nllb_source_lang_var": local_nllb_source_lang_var,
            "local_nllb_source_lang_map": local_nllb_source_lang_map,
            "local_nllb_target_lang_var": local_nllb_target_lang_var,
            "local_nllb_target_lang_map": local_nllb_target_lang_map,
            "local_nllb_max_chars_var": local_nllb_max_chars_var,
            "local_nllb_cache_dir_var": local_nllb_cache_dir_var,
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

    def _build_hardware_autodetect_section(
        self,
        section,
        label_opts,
        transcription_vars,
        translation_vars,
    ):
        section_bg = label_opts["bg"]
        settings_fg = label_opts["fg"]
        tk.Label(
            section,
            text=(
                "Detects your GPU's available VRAM and sets the Final model, "
                "Realtime model (above), and NLLB model (Translation, above) "
                "dropdowns to a fitting recommendation. Click Apply afterward "
                "to actually use them."
            ),
            bg=section_bg,
            fg=settings_fg,
            wraplength=560,
            justify="left",
            font=(self.ui_font_family, 9),
        ).pack(anchor="w", pady=(0, 8))

        result_var = tk.StringVar(value="")
        tk.Label(
            section,
            textvariable=result_var,
            bg=section_bg,
            fg=settings_fg,
            wraplength=560,
            justify="left",
            font=(self.ui_font_family, 9),
        ).pack(anchor="w", pady=(0, 8))

        detect_button = self._make_button(
            section,
            "Autodetect Hardware",
            command=lambda: self._autodetect_hardware_models(
                transcription_vars, translation_vars, result_var
            ),
            primary=True,
        )
        detect_button.pack(anchor="w")

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

        if cuda_available and vram_gb:
            hardware_desc = f"{gpu_name or 'NVIDIA GPU'} ({vram_gb:.1f} GB VRAM)"
        else:
            hardware_desc = "No CUDA GPU detected (CPU only)"
        result_var.set(
            f"Detected: {hardware_desc}. Set Final model to "
            f"{final_display or recommendation['final_model']}, Realtime model to "
            f"{realtime_display or recommendation['realtime_model']}, and NLLB model to "
            f"{nllb_display or recommendation['nllb_model']}. Click Apply to use them."
        )

    def _run_local_nllb_test_from_vars(
        self,
        model_name_var,
        device_var,
        cache_dir_var,
        max_chars_var,
        test_button,
        test_status_var,
        model_name_map=None,
    ):
        config = self._local_nllb_config_from_vars(
            model_name_var,
            device_var,
            cache_dir_var,
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
        cache_dir_var,
        max_chars_var,
        model_name_map=None,
    ):
        selected_model_name = model_name_var.get().strip()
        if model_name_map:
            selected_model_name = model_name_map.get(selected_model_name, selected_model_name)
        return {
            "model_name": selected_model_name or self.LOCAL_NLLB_DEFAULT_MODEL_NAME,
            "device": self._normalize_local_nllb_device(device_var.get()),
            "cache_dir": self._normalize_optional_directory(cache_dir_var.get()),
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
                cache_dir=config["cache_dir"],
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
            "cache_dir": self._normalize_optional_directory(self.local_nllb_cache_dir),
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
            self._normalize_optional_directory(config.get("cache_dir", "")),
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
            self._refresh_local_nllb_runtime_ui()

        try:
            self.root.after(0, update)
        except Exception:
            update()

    def _refresh_local_nllb_runtime_ui(self):
        in_progress = bool(self.nllb_download_in_progress or self.nllb_check_in_progress)
        in_progress = in_progress or self.nllb_status in ("Checking", "Downloading", "Loading")
        ready = self.nllb_status == "Ready"
        not_downloaded = self.nllb_status == "Not downloaded"
        error = self.nllb_status == "Error"
        try:
            if self.local_nllb_status_var is not None:
                self.local_nllb_status_var.set(self.nllb_status)
            if self.local_nllb_message_var is not None:
                self.local_nllb_message_var.set(self._local_nllb_status_message())
        except Exception:
            pass
        try:
            if self.local_nllb_download_button is not None:
                download_state = tk.DISABLED if in_progress or ready else tk.NORMAL
                self.local_nllb_download_button.config(state=download_state)
        except Exception:
            pass
        try:
            if self.local_nllb_retry_button is not None:
                retry_state = tk.NORMAL if (not in_progress and (error or not_downloaded)) else tk.DISABLED
                self.local_nllb_retry_button.config(state=retry_state)
        except Exception:
            pass
        try:
            if self.local_nllb_test_button is not None:
                test_state = tk.NORMAL if (not in_progress and ready) else tk.DISABLED
                self.local_nllb_test_button.config(state=test_state)
        except Exception:
            pass

    def _download_local_nllb_from_vars(
        self,
        model_name_var,
        device_var,
        cache_dir_var,
        max_chars_var,
        prompt=True,
        model_name_map=None,
    ):
        config = self._local_nllb_config_from_vars(
            model_name_var,
            device_var,
            cache_dir_var,
            max_chars_var,
            model_name_map=model_name_map,
        )
        self._start_local_nllb_download(config, prompt=prompt)

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

    def _local_nllb_model_kwargs(self, cache_dir, local_files_only=True, create_cache=False):
        kwargs = {"local_files_only": bool(local_files_only)}
        cache_dir = self._normalize_optional_directory(cache_dir)
        if cache_dir:
            if create_cache:
                try:
                    os.makedirs(cache_dir, exist_ok=True)
                except Exception as exc:
                    raise sr.RequestError(self.LOCAL_NLLB_CACHE_ERROR_MESSAGE) from exc
            kwargs["cache_dir"] = cache_dir
        return kwargs

    def _is_local_nllb_model_cached(self, config):
        _torch_module, AutoModelForSeq2SeqLM, AutoTokenizer = self._import_local_nllb_dependencies()
        kwargs = self._local_nllb_model_kwargs(
            config.get("cache_dir", ""),
            local_files_only=True,
        )
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
        kwargs = self._local_nllb_model_kwargs(
            config.get("cache_dir", ""),
            local_files_only=False,
            create_cache=True,
        )
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
    ):
        enabled = self._coerce_bool(enable_translation_var.get(), default=False)
        if enabled:
            toggle_state_label.config(text="Current mode: Translation ON (Local NLLB)")
        else:
            toggle_state_label.config(text="Current mode: Translation OFF")
        output_lang_label.config(text=self.OUTPUT_LANGUAGE_ENGLISH_LABEL)

    def _sync_translation_toggle_runtime(self, enable_translation_var):
        enabled = self._coerce_bool(enable_translation_var.get(), default=False)
        previous = bool(self.translation_enabled)
        if enabled == previous:
            return
        self.translation_enabled = enabled
        self._apply_translation_mode_defaults()
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
        self.root.config(bg=self.bg_color)
        self.text_canvas.config(bg=self.bg_color)
        self.text_canvas.itemconfigure(self.text_item, fill=self.text_color)
        for item in self.text_line_items:
            self.text_canvas.itemconfigure(item, fill=self.text_color)
        self._sync_preview_colors()

    def _get_output_colors(self):
        bg = self.bg_color
        fg = self.text_color
        try:
            bg = self.text_canvas.cget("bg")
        except Exception:
            pass
        try:
            fg = self.text_canvas.itemcget(self.text_item, "fill")
        except Exception:
            pass
        return bg, fg

    def _sync_preview_colors(self):
        if self.preview_widget is None or not self.preview_widget.winfo_exists():
            return
        bg, fg = self._get_output_colors()
        try:
            self.preview_widget.config(bg=bg, fg=fg)
        except Exception:
            pass

    def _apply_canvas_padding(self):
        pad = 0 if self.is_fullscreen else self.canvas_margin
        self.text_canvas.grid_configure(padx=pad, pady=pad)
    
