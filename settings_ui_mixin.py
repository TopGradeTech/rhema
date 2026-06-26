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
        display_vars, audio_vars, filters_vars, api_vars, translation_vars, advanced_vars = (
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
        self.audio_level_gate_item = self.audio_level_bar.create_line(
            0, 0, 0, 12, fill="#FF7A59", width=2, state="hidden"
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
                filters_vars,
                api_vars,
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
        self._collect_settings_vars_for_dirty_tracking(filters_vars, dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(api_vars, dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(translation_vars, dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(advanced_vars, dirty_ctx)
        dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(dirty_ctx)
        dirty_ctx["dirty_ready"] = True
        self._set_settings_dirty_state(dirty_ctx, False, force=True)
        self._start_audio_level_updates()

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
        filters_vars,
        api_vars,
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
                filters_vars,
                api_vars,
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
        filters_vars,
        api_vars,
        translation_vars,
        advanced_vars,
    ):
        self._apply_display_vars(display_vars)
        self._apply_filter_vars(filters_vars)
        self._apply_api_vars(api_vars)
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
        self.chunk_size = max(20, int(advanced_vars["chunk_size_var"].get()))
        self.chunk_delay_ms = max(50, int(advanced_vars["chunk_delay_var"].get()))
        self.sentence_flush_ms = max(100, int(advanced_vars["sentence_flush_var"].get()))
        if "display_speed_var" in advanced_vars:
            try:
                self.display_speed_factor = float(advanced_vars["display_speed_var"].get())
            except Exception:
                pass
        self.display_speed_factor = max(0.5, min(self.display_speed_factor, 2.5))
        self.rms_gate_enabled = bool(advanced_vars["rms_gate_var"].get())
        try:
            self.rms_gate_factor = float(advanced_vars["rms_gate_factor_var"].get())
        except Exception:
            pass
        self.rms_gate_factor = max(0.5, min(self.rms_gate_factor, 5.0))
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
            self.faster_whisper_model = None
            self.faster_whisper_model_config = None
        self._fit_font_to_lines()

    def _apply_filter_vars(self, filters_vars):
        en_text = filters_vars["bad_words_en_text"].get("1.0", tk.END).strip()
        es_text = filters_vars["bad_words_es_text"].get("1.0", tk.END).strip()
        self.bad_words_by_lang["en"] = {
            word.strip().lower() for word in en_text.split(",") if word.strip()
        }
        self.bad_words_by_lang["es"] = {
            word.strip().lower() for word in es_text.split(",") if word.strip()
        }
        for lang in self.bad_words_by_lang.keys():
            self.bad_word_filters_enabled[lang] = True
        self._refresh_bad_words()
        for lang in self.custom_vocabulary_by_lang.keys():
            self.custom_vocab_langs_enabled[lang] = True
        vocab_en_text = filters_vars["custom_vocab_en_text"].get("1.0", tk.END).strip()
        vocab_es_text = filters_vars["custom_vocab_es_text"].get("1.0", tk.END).strip()
        self.custom_vocabulary_by_lang["en"] = [
            v.strip() for v in vocab_en_text.split(",") if v.strip()
        ]
        self.custom_vocabulary_by_lang["es"] = [
            v.strip() for v in vocab_es_text.split(",") if v.strip()
        ]

    def _apply_api_vars(self, api_vars):
        previous_config = (
            self.faster_whisper_model_name,
            self.faster_whisper_compute_type,
            self.faster_whisper_device,
        )
        next_config = previous_config
        with self.recognition_lock:
            self.speech_engine = self._optional_mapped_api_setting(
                api_vars,
                "speech_engine_var",
                "speech_engine_map",
                current_value=self.speech_engine,
                mapped_default="openai",
            )
            self.openai_stt_model = self._optional_mapped_api_setting(
                api_vars,
                "openai_stt_model_var",
                "openai_stt_model_map",
                current_value=self.openai_stt_model,
                mapped_default="whisper-1",
            )
            self.openai_translation_mode = self._optional_mapped_api_setting(
                api_vars,
                "openai_translation_mode_var",
                "openai_translation_mode_map",
                current_value=self.openai_translation_mode,
                mapped_default="whisper",
            )
            self.openai_translate_model = self._optional_string_api_setting(
                api_vars,
                "openai_translate_model_var",
                current_value=self.openai_translate_model,
                empty_default="gpt-4o",
            )
            self.openai_api_key = self._optional_string_api_setting(
                api_vars,
                "openai_api_key_var",
                current_value=self.openai_api_key,
            )
            self.faster_whisper_model_name = self._optional_string_api_setting(
                api_vars,
                "faster_whisper_model_var",
                current_value=self.faster_whisper_model_name,
                empty_default="medium",
            )
            self.faster_whisper_compute_type = self._optional_string_api_setting(
                api_vars,
                "faster_whisper_compute_var",
                current_value=self.faster_whisper_compute_type,
                empty_default="float16",
            )
            self.faster_whisper_device = self._optional_string_api_setting(
                api_vars,
                "faster_whisper_device_var",
                current_value=self.faster_whisper_device,
                empty_default="cuda",
            )
            if "faster_whisper_vad_enabled_var" in api_vars:
                self.faster_whisper_vad_enabled = bool(
                    api_vars["faster_whisper_vad_enabled_var"].get()
                )
            self.faster_whisper_vad_threshold = self._coerce_float_range(
                api_vars.get("faster_whisper_vad_threshold_var", None).get()
                if "faster_whisper_vad_threshold_var" in api_vars
                else self.faster_whisper_vad_threshold,
                self.faster_whisper_vad_threshold,
                0.10,
                0.95,
            )
            self.faster_whisper_vad_min_silence_ms = self._coerce_int_range(
                api_vars.get("faster_whisper_vad_min_silence_var", None).get()
                if "faster_whisper_vad_min_silence_var" in api_vars
                else self.faster_whisper_vad_min_silence_ms,
                self.faster_whisper_vad_min_silence_ms,
                100,
                3000,
            )
            self.faster_whisper_vad_speech_pad_ms = self._coerce_int_range(
                api_vars.get("faster_whisper_vad_speech_pad_var", None).get()
                if "faster_whisper_vad_speech_pad_var" in api_vars
                else self.faster_whisper_vad_speech_pad_ms,
                self.faster_whisper_vad_speech_pad_ms,
                0,
                1000,
            )
            self.faster_whisper_vad_min_speech_ms = self._coerce_int_range(
                api_vars.get("faster_whisper_vad_min_speech_var", None).get()
                if "faster_whisper_vad_min_speech_var" in api_vars
                else self.faster_whisper_vad_min_speech_ms,
                self.faster_whisper_vad_min_speech_ms,
                0,
                1000,
            )
            self.omnilingual_sidecar_base_url = self._normalize_omnilingual_sidecar_base_url(
                self._optional_string_api_setting(
                    api_vars,
                    "omnilingual_sidecar_base_url_var",
                    current_value=self.omnilingual_sidecar_base_url,
                    empty_default=self.OMNILINGUAL_SIDECAR_DEFAULT_BASE_URL,
                )
            )
            self.omnilingual_sidecar_endpoint_path = (
                self._normalize_omnilingual_sidecar_endpoint_path(
                    self._optional_string_api_setting(
                        api_vars,
                        "omnilingual_sidecar_endpoint_path_var",
                        current_value=self.omnilingual_sidecar_endpoint_path,
                        empty_default=self.OMNILINGUAL_SIDECAR_DEFAULT_ENDPOINT_PATH,
                    )
                )
            )
            self.omnilingual_sidecar_model = self._optional_string_api_setting(
                api_vars,
                "omnilingual_sidecar_model_var",
                current_value=self.omnilingual_sidecar_model,
            )
            self.omnilingual_sidecar_language = self._optional_string_api_setting(
                api_vars,
                "omnilingual_sidecar_language_var",
                current_value=self.omnilingual_sidecar_language,
            )
            self.omnilingual_sidecar_response_format = (
                self._normalize_omnilingual_sidecar_response_format(
                    self._optional_string_api_setting(
                        api_vars,
                        "omnilingual_sidecar_response_format_var",
                        current_value=self.omnilingual_sidecar_response_format,
                        empty_default=self.OMNILINGUAL_SIDECAR_DEFAULT_RESPONSE_FORMAT,
                    )
                )
            )
            self.omnilingual_sidecar_timeout_sec = self._coerce_int_range(
                api_vars.get("omnilingual_sidecar_timeout_var", None).get()
                if "omnilingual_sidecar_timeout_var" in api_vars
                else self.omnilingual_sidecar_timeout_sec,
                self.omnilingual_sidecar_timeout_sec,
                5,
                600,
            )
            self.kroko_api_key = self._optional_string_api_setting(
                api_vars,
                "kroko_api_key_var",
                current_value=self.kroko_api_key,
            )
            kroko_lang = self._optional_mapped_api_setting(
                api_vars,
                "kroko_language_code_var",
                "kroko_language_code_map",
                current_value=self.kroko_language_code,
                mapped_default="en-US",
            )
            self.kroko_language_code = kroko_lang if kroko_lang else "en-US"
            self.realtime_stt_final_model = self._optional_string_api_setting(
                api_vars, "realtime_stt_final_model_var",
                current_value=self.realtime_stt_final_model,
                empty_default="large-v3",
            )
            self.realtime_stt_realtime_model = self._optional_string_api_setting(
                api_vars, "realtime_stt_realtime_model_var",
                current_value=self.realtime_stt_realtime_model,
                empty_default="tiny",
            )
            self.realtime_stt_silero_sensitivity = self._coerce_float_range(
                api_vars["realtime_stt_silero_var"].get()
                if "realtime_stt_silero_var" in api_vars
                else self.realtime_stt_silero_sensitivity,
                self.realtime_stt_silero_sensitivity, 0.1, 0.9,
            )
            self.realtime_stt_post_speech_silence = self._coerce_float_range(
                api_vars["realtime_stt_silence_var"].get()
                if "realtime_stt_silence_var" in api_vars
                else self.realtime_stt_post_speech_silence,
                self.realtime_stt_post_speech_silence, 0.1, 3.0,
            )
            if "realtime_stt_interim_var" in api_vars:
                self.realtime_stt_enable_interim = bool(
                    api_vars["realtime_stt_interim_var"].get()
                )
            next_config = (
                self.faster_whisper_model_name,
                self.faster_whisper_compute_type,
                self.faster_whisper_device,
            )
            if next_config != previous_config:
                # Safe handoff: unload the old local model only while recognition
                # is locked so we never tear it down mid-transcription.
                self.faster_whisper_model = None
                self.faster_whisper_model_config = None
        if next_config != previous_config:
            gc.collect()

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
        previous_provider = self._active_text_translation_provider()
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
        if "text_translation_provider_var" in translation_vars:
            provider = self._optional_mapped_api_setting(
                translation_vars,
                "text_translation_provider_var",
                "text_translation_provider_map",
                current_value=self.text_translation_provider,
                mapped_default=self.text_translation_provider,
            )
            self.text_translation_provider = self._normalize_text_translation_provider(
                provider
            )
        self.local_nllb_model_name = self._optional_string_api_setting(
            translation_vars,
            "local_nllb_model_name_var",
            current_value=self.local_nllb_model_name,
            empty_default=self.LOCAL_NLLB_DEFAULT_MODEL_NAME,
        )
        if "local_nllb_device_var" in translation_vars:
            self.local_nllb_device = self._normalize_local_nllb_device(
                translation_vars["local_nllb_device_var"].get()
            )
        if "local_nllb_source_lang_var" in translation_vars:
            self.local_nllb_source_lang = self._normalize_local_nllb_lang_setting(
                translation_vars["local_nllb_source_lang_var"].get(),
                default=self.LOCAL_NLLB_DEFAULT_SOURCE_LANG,
                allow_auto=True,
            )
        if "local_nllb_target_lang_var" in translation_vars:
            self.local_nllb_target_lang = self._normalize_local_nllb_lang_setting(
                translation_vars["local_nllb_target_lang_var"].get(),
                default=self.LOCAL_NLLB_DEFAULT_TARGET_LANG,
                allow_auto=False,
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
        if self.text_translation_provider == "local_nllb":
            if previous_provider != "local_nllb":
                self.previous_text_translation_provider = previous_provider
            self._start_local_nllb_cache_check(
                self._local_nllb_runtime_config(),
                prompt_if_missing=True,
            )
        else:
            self.previous_text_translation_provider = self.text_translation_provider
            self._set_local_nllb_status("Not selected", "")

    def _apply_audio_vars(self, audio_vars):
        # Audio device changes are applied immediately by the device-menu callback.
        _ = audio_vars
        return None

    def _refresh_audio_devices(self):
        from threading import Thread

        def _worker():
            suspend_timeout = max(2.0, float(self.phrase_time_limit) + 1.0)
            suspended = self._suspend_capture_for_device_scan(timeout=suspend_timeout)
            if not suspended:
                self._log_status("Skipping device refresh while capture is active")
                return
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

        filters_section = tk.LabelFrame(
            content,
            text="Filters",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        filters_section.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        filters_vars = self._build_filters_section(filters_section, label_opts, section_bg)

        api_section = tk.LabelFrame(
            content,
            text="API",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        api_section.pack(fill=tk.X)
        api_vars = self._build_api_section(api_section, label_opts)

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

        advanced_vars = self._build_advanced_section(
            content,
            label_opts,
            section_bg,
            settings_fg,
            section_font,
        )

        return display_vars, audio_vars, filters_vars, api_vars, translation_vars, advanced_vars

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
            text="Lock fullscreen output focus",
            variable=lock_output_focus_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        lock_output_focus_check.pack(side=tk.LEFT)
        self._create_help_icon(
            focus_lock_row,
            "Keeps the fullscreen output window on top and attempts to focus it. Leave this off to let other apps appear above the output window.",
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
        self.microphone_index = self.devices.index(label)
        if not self.device_refresh_in_progress:
            self.preferred_device_label = label
            self.save_settings()
        self._request_capture_restart()
        self._request_audio_level_stream_restart()

    def _build_filters_section(self, filters_section, label_opts, section_bg):
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

        vocab_label_row = self._add_setting_label(
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

        def update_bad_words_visibility():
            show = bad_words_toggle_var.get()
            bad_words_toggle_button.config(
                text=self.HIDE_LIST_LABEL if show else self.SHOW_LIST_LABEL
            )
            if show:
                bad_words_en_container.pack(
                    fill=tk.BOTH,
                    expand=True,
                    pady=(6, 0),
                    before=vocab_label_row,
                )
                bad_words_es_container.pack(
                    fill=tk.BOTH,
                    expand=True,
                    pady=(6, 0),
                    before=vocab_label_row,
                )
            else:
                bad_words_en_container.pack_forget()
                bad_words_es_container.pack_forget()

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

        def toggle_bad_words_list():
            bad_words_toggle_var.set(not bad_words_toggle_var.get())
            update_bad_words_visibility()

        def toggle_custom_vocab_list():
            custom_vocab_toggle_var.set(not custom_vocab_toggle_var.get())
            update_custom_vocab_visibility()

        bad_words_toggle_button.config(command=toggle_bad_words_list)
        custom_vocab_toggle_button.config(command=toggle_custom_vocab_list)

        update_bad_words_visibility()
        update_custom_vocab_visibility()

        return {
            "bad_words_en_text": bad_words_en_text,
            "bad_words_es_text": bad_words_es_text,
            "custom_vocab_en_text": custom_vocab_en_text,
            "custom_vocab_es_text": custom_vocab_es_text,
        }

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

        text_section = tk.LabelFrame(
            advanced_section,
            text="Text Manipulation",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        text_section.pack(fill=tk.X, pady=(0, 10))

        self._add_setting_label(
            text_section,
            "Text Chunk Size (chars):",
            "Target character length before batching text into a line.",
            label_opts,
            pady=(0, 4),
        )
        chunk_size_var = tk.IntVar(value=self.chunk_size)
        chunk_size_spin = tk.Spinbox(
            text_section, from_=20, to=300, textvariable=chunk_size_var
        )
        self._apply_input_style(chunk_size_spin)
        chunk_size_spin.pack(anchor="w")

        self._add_setting_label(
            text_section,
            "Chunk Delay (ms):",
            "Delay between displaying chunks or lines.",
            label_opts,
            pady=(10, 4),
        )
        chunk_delay_var = tk.IntVar(value=self.chunk_delay_ms)
        chunk_delay_spin = tk.Spinbox(
            text_section,
            from_=50,
            to=2000,
            increment=50,
            textvariable=chunk_delay_var,
        )
        self._apply_input_style(chunk_delay_spin)
        chunk_delay_spin.pack(anchor="w")

        self._add_setting_label(
            text_section,
            "Response Delay (ms):",
            "Wait time after last speech before flushing a sentence.",
            label_opts,
            pady=(10, 4),
        )
        sentence_flush_var = tk.IntVar(value=self.sentence_flush_ms)
        sentence_flush_spin = tk.Spinbox(
            text_section,
            from_=100,
            to=3000,
            increment=100,
            textvariable=sentence_flush_var,
        )
        self._apply_input_style(sentence_flush_spin)
        sentence_flush_spin.pack(anchor="w")

        self._add_setting_label(
            text_section,
            "Display Speed:",
            "Scales display timing. Higher is faster (uses your current delay settings as base).",
            label_opts,
            pady=(10, 4),
        )
        speed_row = tk.Frame(text_section, bg=section_bg)
        speed_row.pack(fill=tk.X)
        display_speed_var = tk.DoubleVar(value=self.display_speed_factor)
        speed_value_label = tk.Label(
            speed_row,
            text=f"{self.display_speed_factor:.2f}x",
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9),
        )

        def _on_speed_change(value):
            try:
                speed_value_label.config(text=f"{float(value):.2f}x")
            except Exception:
                pass

        speed_scale = tk.Scale(
            speed_row,
            from_=0.5,
            to=2.5,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=display_speed_var,
            command=_on_speed_change,
            length=240,
            bg=section_bg,
            fg=settings_fg,
            highlightthickness=0,
            bd=0,
        )
        speed_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        speed_value_label.pack(side=tk.LEFT, padx=(8, 0))
        _on_speed_change(display_speed_var.get())

        noise_section = tk.LabelFrame(
            advanced_section,
            text="Noise Cancellation",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        noise_section.pack(fill=tk.X)

        gate_row = tk.Frame(noise_section, bg=section_bg)
        gate_row.pack(anchor="w", pady=(0, 0), fill=tk.X)
        rms_gate_var = tk.BooleanVar(value=self.rms_gate_enabled)
        rms_gate_check = tk.Checkbutton(
            gate_row,
            text="Enable noise gate",
            variable=rms_gate_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        rms_gate_check.pack(side=tk.LEFT)
        self._create_help_icon(
            gate_row,
            "Suppress very quiet audio before transcription.",
            section_bg,
            settings_fg,
        )

        self._add_setting_label(
            noise_section,
            "Noise Cancellation (strength):",
            "Higher values filter more low-level noise.",
            label_opts,
            pady=(10, 4),
        )
        rms_gate_factor_var = tk.DoubleVar(value=self.rms_gate_factor)
        rms_gate_spin = tk.Spinbox(
            noise_section,
            from_=0.5,
            to=5.0,
            increment=0.1,
            textvariable=rms_gate_factor_var,
        )
        self._apply_input_style(rms_gate_spin)
        rms_gate_spin.pack(anchor="w")

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
            "Normal keeps status/error and finalized output logs. Debug adds pipeline traces and Omnilingual WAVs. Evaluation adds raw transcribed/translated comparison logs. Full enables all logs.",
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
            "chunk_size_var": chunk_size_var,
            "chunk_delay_var": chunk_delay_var,
            "sentence_flush_var": sentence_flush_var,
            "display_speed_var": display_speed_var,
            "rms_gate_var": rms_gate_var,
            "rms_gate_factor_var": rms_gate_factor_var,
            "logging_mode_var": logging_mode_var,
            "logging_mode_map": logging_mode_map,
            "start_with_windows_var": start_with_windows_var,
            "cuda_directory_var": cuda_directory_var,
        }

    def _build_api_section(self, api_section, label_opts):
        self._add_setting_label(
            api_section,
            "Speech API Engine:",
            "Select the speech-to-text engine.",
            label_opts,
            pady=(0, 4),
        )
        speech_engine_options = [
            ("OpenAI (cloud)", "openai"),
            ("Local (faster-whisper)", "faster-whisper"),
            ("Local (Omnilingual sidecar)", "omnilingual-sidecar"),
            ("Kroko ASR (cloud, low-cost)", "kroko"),
            ("RealtimeSTT (local, real-time)", "realtime-stt"),
        ]
        engine_display = [name for name, _ in speech_engine_options]
        engine_map = dict(speech_engine_options)
        rev_engine_map = {code: name for name, code in speech_engine_options}
        speech_engine_var = tk.StringVar(
            value=rev_engine_map.get(self.speech_engine, engine_display[0])
        )
        speech_engine_menu = tk.OptionMenu(
            api_section,
            speech_engine_var,
            *engine_display,
        )
        self._apply_option_menu_style(speech_engine_menu)
        speech_engine_menu.pack(anchor="w")

        openai_key_container = tk.Frame(api_section, bg=label_opts["bg"])
        openai_key_container.pack(fill=tk.X)
        self._add_setting_label(
            openai_key_container,
            "OpenAI API Key:",
            "API key for OpenAI transcription (and translation if enabled). Read from the OPENAI_API_KEY environment variable at startup; set it there for persistence.",
            label_opts,
            pady=(0, 4),
        )
        openai_key_var = tk.StringVar(value=self.openai_api_key)
        openai_key_frame = tk.Frame(openai_key_container, bg=label_opts["bg"])
        openai_key_frame.pack(fill=tk.X)
        openai_key_entry = tk.Entry(openai_key_frame, textvariable=openai_key_var, width=50, show="*")
        self._apply_input_style(openai_key_entry)
        openai_key_entry.pack(side=tk.LEFT)

        openai_show_var = tk.BooleanVar(value=False)

        def toggle_openai_show():
            show = "" if openai_show_var.get() else "*"
            openai_key_entry.config(show=show)

        openai_show_button = tk.Checkbutton(
            openai_key_frame,
            text="Show",
            variable=openai_show_var,
            command=toggle_openai_show,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            selectcolor=label_opts["bg"],
            activebackground=label_opts["bg"],
        )
        openai_show_button.pack(side=tk.LEFT, padx=(8, 0))
        self._create_help_icon(
            openai_key_frame,
            "Reveal or hide the OpenAI API key in this field.",
            label_opts["bg"],
            label_opts["fg"],
        )
        self._add_setting_label(
            openai_key_container,
            "OpenAI STT Model:",
            "Use Whisper primarily for cloud transcription; GPT-4o remains available.",
            label_opts,
            pady=(10, 4),
        )
        openai_stt_model_options = [
            ("Whisper (whisper-1) [Primary]", "whisper-1"),
            ("GPT-4o (gpt-4o-transcribe)", "gpt-4o-transcribe"),
        ]
        openai_stt_model_display = [name for name, _ in openai_stt_model_options]
        openai_stt_model_map = dict(openai_stt_model_options)
        rev_openai_stt_model_map = {code: name for name, code in openai_stt_model_options}
        openai_stt_model_var = tk.StringVar(
            value=rev_openai_stt_model_map.get(
                self.openai_stt_model, openai_stt_model_display[0]
            )
        )
        openai_stt_model_menu = tk.OptionMenu(
            openai_key_container,
            openai_stt_model_var,
            *openai_stt_model_display,
        )
        self._apply_option_menu_style(openai_stt_model_menu)
        openai_stt_model_menu.pack(anchor="w")

        self._add_setting_label(
            openai_key_container,
            "OpenAI Translation Mode:",
            "Whisper mode uses audio translation to English when possible; otherwise GPT-4o is used.",
            label_opts,
            pady=(10, 4),
        )
        openai_translation_mode_options = [
            ("Whisper (audio->English) [Primary]", "whisper"),
            ("GPT-4o (text translation)", "gpt-4o"),
        ]
        openai_translation_mode_display = [
            name for name, _ in openai_translation_mode_options
        ]
        openai_translation_mode_map = dict(openai_translation_mode_options)
        rev_openai_translation_mode_map = {
            code: name for name, code in openai_translation_mode_options
        }
        openai_translation_mode_var = tk.StringVar(
            value=rev_openai_translation_mode_map.get(
                self.openai_translation_mode, openai_translation_mode_display[0]
            )
        )
        openai_translation_mode_menu = tk.OptionMenu(
            openai_key_container,
            openai_translation_mode_var,
            *openai_translation_mode_display,
        )
        self._apply_option_menu_style(openai_translation_mode_menu)
        openai_translation_mode_menu.pack(anchor="w")

        gpt_translate_model_container = tk.Frame(openai_key_container, bg=label_opts["bg"])
        self._add_setting_label(
            gpt_translate_model_container,
            "GPT Translation Model:",
            "Used when translation mode is GPT-4o, or when Whisper translation is unavailable.",
            label_opts,
            pady=(0, 4),
        )
        openai_translate_model_options = ["gpt-4o", "gpt-4o-mini"]
        openai_translate_model_var = tk.StringVar(value=self.openai_translate_model)
        openai_translate_model_menu = tk.OptionMenu(
            gpt_translate_model_container,
            openai_translate_model_var,
            *openai_translate_model_options,
        )
        self._apply_option_menu_style(openai_translate_model_menu)
        openai_translate_model_menu.pack(anchor="w")

        def update_openai_translation_mode_visibility(*_args):
            selected_mode = openai_translation_mode_map.get(
                openai_translation_mode_var.get(), "whisper"
            )
            if selected_mode == "gpt-4o":
                gpt_translate_model_container.pack(fill=tk.X, pady=(10, 0))
            else:
                gpt_translate_model_container.pack_forget()

        openai_translation_mode_var.trace_add(
            "write", update_openai_translation_mode_visibility
        )
        update_openai_translation_mode_visibility()

        faster_whisper_container = tk.Frame(api_section, bg=label_opts["bg"])
        faster_whisper_container.pack(fill=tk.X, pady=(10, 0))
        self._add_setting_label(
            faster_whisper_container,
            "faster-whisper model:",
            "Model name (smaller = faster, e.g. tiny/base/small/medium/large-v3).",
            label_opts,
            pady=(0, 4),
        )
        faster_whisper_model_var = tk.StringVar(value=self.faster_whisper_model_name)
        faster_whisper_model_entry = tk.Entry(
            faster_whisper_container, textvariable=faster_whisper_model_var, width=30
        )
        self._apply_input_style(faster_whisper_model_entry)
        faster_whisper_model_entry.pack(anchor="w")

        self._add_setting_label(
            faster_whisper_container,
            "Compute type:",
            "float16 (GPU) is fastest; int8 for CPU or lower VRAM.",
            label_opts,
            pady=(10, 4),
        )
        compute_options = ["float16", "int8_float16", "int8"]
        faster_whisper_compute_var = tk.StringVar(value=self.faster_whisper_compute_type)
        compute_menu = tk.OptionMenu(
            faster_whisper_container,
            faster_whisper_compute_var,
            *compute_options,
        )
        self._apply_option_menu_style(compute_menu)
        compute_menu.pack(anchor="w")

        self._add_setting_label(
            faster_whisper_container,
            "Device:",
            "Use cuda for NVIDIA GPUs, cpu for local CPU.",
            label_opts,
            pady=(10, 4),
        )
        device_options = ["cuda", "cpu"]
        faster_whisper_device_var = tk.StringVar(value=self.faster_whisper_device)
        device_menu = tk.OptionMenu(
            faster_whisper_container,
            faster_whisper_device_var,
            *device_options,
        )
        self._apply_option_menu_style(device_menu)
        device_menu.pack(anchor="w")

        vad_section = tk.LabelFrame(
            faster_whisper_container,
            text="Voice Activity Detection",
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            font=(self.ui_font_family, 10, "bold"),
            padx=10,
            pady=10,
        )
        vad_section.pack(fill=tk.X, pady=(12, 0))
        faster_whisper_vad_enabled_var = tk.BooleanVar(
            value=self.faster_whisper_vad_enabled
        )
        vad_enabled_row = tk.Frame(vad_section, bg=label_opts["bg"])
        vad_enabled_row.pack(anchor="w", fill=tk.X)
        vad_enabled_check = tk.Checkbutton(
            vad_enabled_row,
            text="Enable faster-whisper VAD",
            variable=faster_whisper_vad_enabled_var,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            selectcolor=label_opts["bg"],
            activebackground=label_opts["bg"],
        )
        vad_enabled_check.pack(side=tk.LEFT)
        self._create_help_icon(
            vad_enabled_row,
            "Silero voice activity detection trims silence before transcription.",
            label_opts["bg"],
            label_opts["fg"],
        )

        faster_whisper_vad_threshold_var = tk.DoubleVar(
            value=self.faster_whisper_vad_threshold
        )
        self._add_setting_label(
            vad_section,
            "Speech threshold:",
            "Lower keeps softer speech; higher rejects more background noise.",
            label_opts,
            pady=(10, 4),
        )
        vad_threshold_spin = tk.Spinbox(
            vad_section,
            from_=0.10,
            to=0.95,
            increment=0.05,
            textvariable=faster_whisper_vad_threshold_var,
            width=8,
        )
        self._apply_input_style(vad_threshold_spin)
        vad_threshold_spin.pack(anchor="w")

        faster_whisper_vad_min_silence_var = tk.IntVar(
            value=self.faster_whisper_vad_min_silence_ms
        )
        self._add_setting_label(
            vad_section,
            "Min silence (ms):",
            "Silence duration before faster-whisper splits speech chunks.",
            label_opts,
            pady=(10, 4),
        )
        vad_min_silence_spin = tk.Spinbox(
            vad_section,
            from_=100,
            to=3000,
            increment=100,
            textvariable=faster_whisper_vad_min_silence_var,
            width=8,
        )
        self._apply_input_style(vad_min_silence_spin)
        vad_min_silence_spin.pack(anchor="w")

        faster_whisper_vad_speech_pad_var = tk.IntVar(
            value=self.faster_whisper_vad_speech_pad_ms
        )
        self._add_setting_label(
            vad_section,
            "Speech padding (ms):",
            "Audio kept before and after detected speech to avoid clipped words.",
            label_opts,
            pady=(10, 4),
        )
        vad_speech_pad_spin = tk.Spinbox(
            vad_section,
            from_=0,
            to=1000,
            increment=50,
            textvariable=faster_whisper_vad_speech_pad_var,
            width=8,
        )
        self._apply_input_style(vad_speech_pad_spin)
        vad_speech_pad_spin.pack(anchor="w")

        faster_whisper_vad_min_speech_var = tk.IntVar(
            value=self.faster_whisper_vad_min_speech_ms
        )
        self._add_setting_label(
            vad_section,
            "Min speech (ms):",
            "Detected speech shorter than this is treated as noise.",
            label_opts,
            pady=(10, 4),
        )
        vad_min_speech_spin = tk.Spinbox(
            vad_section,
            from_=0,
            to=1000,
            increment=50,
            textvariable=faster_whisper_vad_min_speech_var,
            width=8,
        )
        self._apply_input_style(vad_min_speech_spin)
        vad_min_speech_spin.pack(anchor="w")

        vad_controls = (
            vad_threshold_spin,
            vad_min_silence_spin,
            vad_speech_pad_spin,
            vad_min_speech_spin,
        )

        def update_vad_control_state(*_args):
            state = tk.NORMAL if faster_whisper_vad_enabled_var.get() else tk.DISABLED
            for control in vad_controls:
                control.configure(state=state)

        faster_whisper_vad_enabled_var.trace_add("write", update_vad_control_state)
        update_vad_control_state()

        omnilingual_sidecar_container = tk.Frame(api_section, bg=label_opts["bg"])
        self._add_setting_label(
            omnilingual_sidecar_container,
            "Sidecar base URL:",
            "Base URL for the syaffers/omniasr-server Docker sidecar.",
            label_opts,
            pady=(0, 4),
        )
        omnilingual_sidecar_base_url_var = tk.StringVar(
            value=self.omnilingual_sidecar_base_url
        )
        omnilingual_sidecar_base_url_entry = tk.Entry(
            omnilingual_sidecar_container,
            textvariable=omnilingual_sidecar_base_url_var,
            width=50,
        )
        self._apply_input_style(omnilingual_sidecar_base_url_entry)
        omnilingual_sidecar_base_url_entry.pack(anchor="w", fill=tk.X)

        self._add_setting_label(
            omnilingual_sidecar_container,
            "Transcription endpoint path:",
            "OpenAI Whisper-compatible endpoint path on the local sidecar.",
            label_opts,
            pady=(10, 4),
        )
        omnilingual_sidecar_endpoint_path_var = tk.StringVar(
            value=self.omnilingual_sidecar_endpoint_path
        )
        omnilingual_sidecar_endpoint_path_entry = tk.Entry(
            omnilingual_sidecar_container,
            textvariable=omnilingual_sidecar_endpoint_path_var,
            width=50,
        )
        self._apply_input_style(omnilingual_sidecar_endpoint_path_entry)
        omnilingual_sidecar_endpoint_path_entry.pack(anchor="w", fill=tk.X)

        self._add_setting_label(
            omnilingual_sidecar_container,
            "Model:",
            "Optional model value sent to the sidecar; the Docker server may ignore it because the hosted model is configured separately.",
            label_opts,
            pady=(10, 4),
        )
        omnilingual_sidecar_model_var = tk.StringVar(
            value=self.omnilingual_sidecar_model
        )
        omnilingual_sidecar_model_entry = tk.Entry(
            omnilingual_sidecar_container,
            textvariable=omnilingual_sidecar_model_var,
            width=36,
        )
        self._apply_input_style(omnilingual_sidecar_model_entry)
        omnilingual_sidecar_model_entry.pack(anchor="w")

        self._add_setting_label(
            omnilingual_sidecar_container,
            "Language:",
            "Optional language hint, e.g. es or spa_Latn for Spanish transcription even when translation is off. Leave blank to let the sidecar decide.",
            label_opts,
            pady=(10, 4),
        )
        omnilingual_sidecar_language_var = tk.StringVar(
            value=self.omnilingual_sidecar_language
        )
        omnilingual_sidecar_language_entry = tk.Entry(
            omnilingual_sidecar_container,
            textvariable=omnilingual_sidecar_language_var,
            width=20,
        )
        self._apply_input_style(omnilingual_sidecar_language_entry)
        omnilingual_sidecar_language_entry.pack(anchor="w")

        self._add_setting_label(
            omnilingual_sidecar_container,
            "Response format:",
            "Response format field sent with transcription requests.",
            label_opts,
            pady=(10, 4),
        )
        omnilingual_sidecar_response_format_var = tk.StringVar(
            value=self.omnilingual_sidecar_response_format
            or self.OMNILINGUAL_SIDECAR_DEFAULT_RESPONSE_FORMAT
        )
        omnilingual_sidecar_response_format_menu = tk.OptionMenu(
            omnilingual_sidecar_container,
            omnilingual_sidecar_response_format_var,
            "json",
            "text",
        )
        self._apply_option_menu_style(omnilingual_sidecar_response_format_menu)
        omnilingual_sidecar_response_format_menu.pack(anchor="w")

        self._add_setting_label(
            omnilingual_sidecar_container,
            "Request timeout (seconds):",
            "Allow extra time for first-run model load or slow CPU sidecars.",
            label_opts,
            pady=(10, 4),
        )
        omnilingual_sidecar_timeout_var = tk.IntVar(
            value=self.omnilingual_sidecar_timeout_sec
        )
        omnilingual_sidecar_timeout_spin = tk.Spinbox(
            omnilingual_sidecar_container,
            from_=5,
            to=600,
            increment=5,
            textvariable=omnilingual_sidecar_timeout_var,
            width=8,
        )
        self._apply_input_style(omnilingual_sidecar_timeout_spin)
        omnilingual_sidecar_timeout_spin.pack(anchor="w")

        health_row = tk.Frame(omnilingual_sidecar_container, bg=label_opts["bg"])
        health_row.pack(fill=tk.X, pady=(12, 0))
        omnilingual_sidecar_health_var = tk.StringVar(value="Not checked")
        health_details = {"text": ""}

        def show_sidecar_health_details():
            detail = health_details.get("text") or "No details available."
            parent = self.settings_window if self.settings_window is not None else self.root
            try:
                messagebox.showinfo("Sidecar Details", detail, parent=parent)
            except TypeError:
                messagebox.showinfo("Sidecar Details", detail)

        health_details_button = self._make_button(
            health_row,
            "View details",
            command=show_sidecar_health_details,
        )
        try:
            health_details_button.configure(state=tk.DISABLED)
        except Exception:
            pass

        def run_sidecar_health_check():
            self._check_omnilingual_sidecar_from_settings(
                omnilingual_sidecar_base_url_var,
                omnilingual_sidecar_health_var,
                health_details,
                health_check_button,
                health_details_button,
            )

        health_check_button = self._make_button(
            health_row,
            "Check sidecar",
            command=run_sidecar_health_check,
            primary=True,
        )
        health_check_button.pack(side=tk.LEFT)
        health_details_button.pack(side=tk.LEFT, padx=(8, 0))
        health_status_label = tk.Label(
            omnilingual_sidecar_container,
            textvariable=omnilingual_sidecar_health_var,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            font=(self.ui_font_family, 9),
            wraplength=620,
            justify="left",
        )
        health_status_label.pack(anchor="w", pady=(6, 0))

        kroko_container = tk.Frame(api_section, bg=label_opts["bg"])
        self._add_setting_label(
            kroko_container,
            "Kroko API Key:",
            "API key from kroko.ai. Read from KROKO_API_KEY environment variable at startup; set it there for persistence.",
            label_opts,
            pady=(0, 4),
        )
        kroko_api_key_var = tk.StringVar(value=self.kroko_api_key)
        kroko_key_frame = tk.Frame(kroko_container, bg=label_opts["bg"])
        kroko_key_frame.pack(fill=tk.X)
        kroko_key_entry = tk.Entry(
            kroko_key_frame, textvariable=kroko_api_key_var, width=50, show="*"
        )
        self._apply_input_style(kroko_key_entry)
        kroko_key_entry.pack(side=tk.LEFT)
        kroko_show_var = tk.BooleanVar(value=False)

        def toggle_kroko_show():
            kroko_key_entry.config(show="" if kroko_show_var.get() else "*")

        kroko_show_button = tk.Checkbutton(
            kroko_key_frame,
            text="Show",
            variable=kroko_show_var,
            command=toggle_kroko_show,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            selectcolor=label_opts["bg"],
            activebackground=label_opts["bg"],
        )
        kroko_show_button.pack(side=tk.LEFT, padx=(8, 0))

        self._add_setting_label(
            kroko_container,
            "Language:",
            "Language spoken in the audio. Kroko pre-recorded API supports these languages.",
            label_opts,
            pady=(10, 4),
        )
        kroko_language_options = [
            ("English (en-US)", "en-US"),
            ("Spanish (es-ES)", "es-ES"),
            ("French (fr-FR)", "fr-FR"),
            ("German (de-DE)", "de-DE"),
            ("Dutch (nl-NL)", "nl-NL"),
            ("Italian (it-IT)", "it-IT"),
            ("Portuguese (pt-PT)", "pt-PT"),
            ("Bulgarian (bg-BG)", "bg-BG"),
            ("Swedish (sv-SV)", "sv-SV"),
        ]
        kroko_language_display = [name for name, _ in kroko_language_options]
        kroko_language_code_map = dict(kroko_language_options)
        kroko_language_rev_map = {code: name for name, code in kroko_language_options}
        kroko_language_code_var = tk.StringVar(
            value=kroko_language_rev_map.get(
                self.kroko_language_code, kroko_language_display[0]
            )
        )
        kroko_language_menu = tk.OptionMenu(
            kroko_container,
            kroko_language_code_var,
            *kroko_language_display,
        )
        self._apply_option_menu_style(kroko_language_menu)
        kroko_language_menu.pack(anchor="w")

        # ── RealtimeSTT settings panel ─────────────────────────────────
        realtime_stt_container = tk.Frame(api_section, bg=label_opts["bg"])
        self._add_setting_label(
            realtime_stt_container,
            "Final model:",
            "Accurate faster-whisper model used after each utterance ends (e.g. large-v3, medium, small).",
            label_opts, pady=(0, 4),
        )
        realtime_stt_final_model_var = tk.StringVar(value=self.realtime_stt_final_model)
        realtime_stt_final_model_entry = tk.Entry(
            realtime_stt_container, textvariable=realtime_stt_final_model_var, width=20
        )
        self._apply_input_style(realtime_stt_final_model_entry)
        realtime_stt_final_model_entry.pack(anchor="w")

        self._add_setting_label(
            realtime_stt_container,
            "Realtime model:",
            "Fast model used for live interim display every 0.2 s (e.g. tiny, base). Ignored when interim display is off.",
            label_opts, pady=(10, 4),
        )
        realtime_stt_realtime_model_options = ["tiny", "base", "small"]
        realtime_stt_realtime_model_var = tk.StringVar(value=self.realtime_stt_realtime_model)
        realtime_stt_realtime_model_menu = tk.OptionMenu(
            realtime_stt_container,
            realtime_stt_realtime_model_var,
            *realtime_stt_realtime_model_options,
        )
        self._apply_option_menu_style(realtime_stt_realtime_model_menu)
        realtime_stt_realtime_model_menu.pack(anchor="w")

        self._add_setting_label(
            realtime_stt_container,
            "Silero sensitivity:",
            "Voice activity detection threshold (0.1–0.9). Higher = less sensitive; lower catches softer speech.",
            label_opts, pady=(10, 4),
        )
        realtime_stt_silero_var = tk.DoubleVar(value=self.realtime_stt_silero_sensitivity)
        realtime_stt_silero_spin = tk.Spinbox(
            realtime_stt_container, from_=0.1, to=0.9, increment=0.05,
            textvariable=realtime_stt_silero_var, width=8,
        )
        self._apply_input_style(realtime_stt_silero_spin)
        realtime_stt_silero_spin.pack(anchor="w")

        self._add_setting_label(
            realtime_stt_container,
            "Post-speech silence (s):",
            "Seconds of silence after speech before the utterance is finalised and sent to the pipeline.",
            label_opts, pady=(10, 4),
        )
        realtime_stt_silence_var = tk.DoubleVar(value=self.realtime_stt_post_speech_silence)
        realtime_stt_silence_spin = tk.Spinbox(
            realtime_stt_container, from_=0.1, to=3.0, increment=0.1,
            textvariable=realtime_stt_silence_var, width=8,
        )
        self._apply_input_style(realtime_stt_silence_spin)
        realtime_stt_silence_spin.pack(anchor="w")

        realtime_stt_interim_var = tk.BooleanVar(value=self.realtime_stt_enable_interim)
        interim_row = tk.Frame(realtime_stt_container, bg=label_opts["bg"])
        interim_row.pack(anchor="w", pady=(12, 0), fill=tk.X)
        tk.Checkbutton(
            interim_row,
            text="Show live interim text while speaking",
            variable=realtime_stt_interim_var,
            bg=label_opts["bg"], fg=label_opts["fg"],
            selectcolor=label_opts["bg"], activebackground=label_opts["bg"],
        ).pack(side=tk.LEFT)
        self._create_help_icon(
            interim_row,
            "Displays stabilized partial transcription on the live line as you speak. Disable for a cleaner display that only shows committed sentences.",
            label_opts["bg"], label_opts["fg"],
        )

        def update_engine_visibility(*_args):
            engine = engine_map.get(speech_engine_var.get(), "openai")
            openai_key_container.pack_forget()
            faster_whisper_container.pack_forget()
            omnilingual_sidecar_container.pack_forget()
            kroko_container.pack_forget()
            realtime_stt_container.pack_forget()
            if engine == "openai":
                openai_key_container.pack(fill=tk.X)
            elif engine == "faster-whisper":
                faster_whisper_container.pack(fill=tk.X, pady=(10, 0))
            elif engine == "omnilingual-sidecar":
                omnilingual_sidecar_container.pack(fill=tk.X, pady=(10, 0))
            elif engine == "kroko":
                kroko_container.pack(fill=tk.X, pady=(10, 0))
            elif engine == "realtime-stt":
                realtime_stt_container.pack(fill=tk.X, pady=(10, 0))

        speech_engine_var.trace_add("write", update_engine_visibility)
        update_engine_visibility()
        return {
            "speech_engine_var": speech_engine_var,
            "speech_engine_map": engine_map,
            "openai_api_key_var": openai_key_var,
            "openai_stt_model_var": openai_stt_model_var,
            "openai_stt_model_map": openai_stt_model_map,
            "openai_translation_mode_var": openai_translation_mode_var,
            "openai_translation_mode_map": openai_translation_mode_map,
            "openai_translate_model_var": openai_translate_model_var,
            "openai_key_container": openai_key_container,
            "faster_whisper_model_var": faster_whisper_model_var,
            "faster_whisper_compute_var": faster_whisper_compute_var,
            "faster_whisper_device_var": faster_whisper_device_var,
            "faster_whisper_vad_enabled_var": faster_whisper_vad_enabled_var,
            "faster_whisper_vad_threshold_var": faster_whisper_vad_threshold_var,
            "faster_whisper_vad_min_silence_var": faster_whisper_vad_min_silence_var,
            "faster_whisper_vad_speech_pad_var": faster_whisper_vad_speech_pad_var,
            "faster_whisper_vad_min_speech_var": faster_whisper_vad_min_speech_var,
            "omnilingual_sidecar_base_url_var": omnilingual_sidecar_base_url_var,
            "omnilingual_sidecar_endpoint_path_var": omnilingual_sidecar_endpoint_path_var,
            "omnilingual_sidecar_model_var": omnilingual_sidecar_model_var,
            "omnilingual_sidecar_language_var": omnilingual_sidecar_language_var,
            "omnilingual_sidecar_response_format_var": omnilingual_sidecar_response_format_var,
            "omnilingual_sidecar_timeout_var": omnilingual_sidecar_timeout_var,
            "kroko_api_key_var": kroko_api_key_var,
            "kroko_language_code_var": kroko_language_code_var,
            "kroko_language_code_map": kroko_language_code_map,
            "realtime_stt_final_model_var": realtime_stt_final_model_var,
            "realtime_stt_realtime_model_var": realtime_stt_realtime_model_var,
            "realtime_stt_silero_var": realtime_stt_silero_var,
            "realtime_stt_silence_var": realtime_stt_silence_var,
            "realtime_stt_interim_var": realtime_stt_interim_var,
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
            "Translation OFF: transcripts pass through. Translation ON: source text is translated by the selected text translation provider.",
            label_opts["bg"],
            label_opts["fg"],
        )
        self._add_setting_label(
            translation_section,
            "Text translation provider:",
            "Select None, OpenAI API, or Local NLLB for text translation after ASR.",
            label_opts,
            pady=(0, 4),
        )
        provider_options = [
            ("None", "none"),
            ("OpenAI API (cloud)", "openai"),
            ("Local NLLB-200 distilled 600M", "local_nllb"),
        ]
        provider_display = [name for name, _code in provider_options]
        provider_map = dict(provider_options)
        rev_provider_map = {code: name for name, code in provider_options}
        text_translation_provider_var = tk.StringVar(
            value=rev_provider_map.get(
                self.text_translation_provider,
                provider_display[0],
            )
        )
        provider_menu = tk.OptionMenu(
            translation_section,
            text_translation_provider_var,
            *provider_display,
        )
        self._apply_option_menu_style(provider_menu)
        provider_menu.pack(anchor="w", pady=(0, 8))
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
        nllb_help = (
            "Local NLLB uses Meta's NLLB-200 distilled 600M model for offline "
            "text translation. The app will ask before downloading the model. "
            "After the first download, translation can run offline from the local "
            "cache."
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
            "Hugging Face model id for local text translation.",
            label_opts,
            pady=(0, 4),
        )
        local_nllb_model_name_var = tk.StringVar(value=self.local_nllb_model_name)
        nllb_model_entry = tk.Entry(
            nllb_container,
            textvariable=local_nllb_model_name_var,
            width=58,
        )
        self._apply_input_style(nllb_model_entry)
        nllb_model_entry.pack(anchor="w", fill=tk.X, pady=(0, 8))

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

        lang_row = tk.Frame(nllb_container, bg=section_bg)
        lang_row.pack(fill=tk.X, pady=(0, 8))
        source_col = tk.Frame(lang_row, bg=section_bg)
        source_col.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        target_col = tk.Frame(lang_row, bg=section_bg)
        target_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._add_setting_label(
            source_col,
            "Source language code:",
            "Use auto_from_selected_source_language or an NLLB code such as spa_Latn.",
            label_opts,
            pady=(0, 4),
        )
        local_nllb_source_lang_var = tk.StringVar(value=self.local_nllb_source_lang)
        nllb_source_entry = tk.Entry(
            source_col,
            textvariable=local_nllb_source_lang_var,
            width=28,
        )
        self._apply_input_style(nllb_source_entry)
        nllb_source_entry.pack(anchor="w", fill=tk.X)
        self._add_setting_label(
            target_col,
            "Target language code:",
            "Use an NLLB code such as eng_Latn.",
            label_opts,
            pady=(0, 4),
        )
        local_nllb_target_lang_var = tk.StringVar(value=self.local_nllb_target_lang)
        nllb_target_entry = tk.Entry(
            target_col,
            textvariable=local_nllb_target_lang_var,
            width=28,
        )
        self._apply_input_style(nllb_target_entry)
        nllb_target_entry.pack(anchor="w", fill=tk.X)

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
            ),
            primary=True,
        )
        test_button.pack(side=tk.LEFT, padx=(8, 0))
        self.local_nllb_download_button = download_button
        self.local_nllb_retry_button = retry_button
        self.local_nllb_test_button = test_button

        refresh_label = lambda *_args: self._refresh_translation_toggle_label(
            enable_translation_var,
            text_translation_provider_var,
            provider_map,
            toggle_state_label,
            output_lang_label,
        )
        refresh_visibility = lambda *_args: self._refresh_translation_provider_visibility(
            text_translation_provider_var,
            provider_map,
            nllb_container,
            refresh_label,
        )
        sync_runtime = lambda *_args: self._sync_translation_toggle_runtime(
            enable_translation_var
        )
        provider_state = {
            "previous": self._translation_provider_from_var(
                text_translation_provider_var,
                provider_map,
            )
        }

        def handle_provider_change(*_args):
            refresh_visibility()
            provider = self._translation_provider_from_var(
                text_translation_provider_var,
                provider_map,
            )
            if self.suppress_local_nllb_provider_check:
                provider_state["previous"] = provider
                return
            if provider == "local_nllb":
                previous = provider_state.get("previous") or self.previous_text_translation_provider
                if previous != "local_nllb":
                    self.previous_text_translation_provider = previous
                self._start_local_nllb_cache_check(
                    self._local_nllb_config_from_vars(
                        local_nllb_model_name_var,
                        local_nllb_device_var,
                        local_nllb_cache_dir_var,
                        local_nllb_max_chars_var,
                    ),
                    prompt_if_missing=True,
                    provider_var=text_translation_provider_var,
                    provider_map=provider_map,
                )
            else:
                provider_state["previous"] = provider

        def handle_nllb_config_change(*_args):
            provider = self._translation_provider_from_var(
                text_translation_provider_var,
                provider_map,
            )
            if provider != "local_nllb":
                return
            if self.nllb_status in ("Checking", "Downloading", "Loading"):
                return
            config = self._local_nllb_config_from_vars(
                local_nllb_model_name_var,
                local_nllb_device_var,
                local_nllb_cache_dir_var,
                local_nllb_max_chars_var,
            )
            if self.nllb_ready_config == self._local_nllb_config_tuple_from_config(config):
                return
            self.nllb_model_loaded = False
            self.nllb_ready_config = None
            self._set_local_nllb_status(
                "Not downloaded",
                "Local NLLB settings changed. Download or retry for the selected model/cache.",
            )

        enable_translation_var.trace_add("write", refresh_label)
        enable_translation_var.trace_add("write", sync_runtime)
        text_translation_provider_var.trace_add("write", handle_provider_change)
        local_nllb_model_name_var.trace_add("write", handle_nllb_config_change)
        local_nllb_device_var.trace_add("write", handle_nllb_config_change)
        local_nllb_cache_dir_var.trace_add("write", handle_nllb_config_change)
        refresh_label()
        refresh_visibility()
        self._refresh_local_nllb_runtime_ui()
        if (
            self._translation_provider_from_var(
                text_translation_provider_var,
                provider_map,
            )
            == "local_nllb"
        ):
            handle_provider_change()
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
            "text_translation_provider_var": text_translation_provider_var,
            "text_translation_provider_map": provider_map,
            "local_nllb_model_name_var": local_nllb_model_name_var,
            "local_nllb_device_var": local_nllb_device_var,
            "local_nllb_source_lang_var": local_nllb_source_lang_var,
            "local_nllb_target_lang_var": local_nllb_target_lang_var,
            "local_nllb_max_chars_var": local_nllb_max_chars_var,
            "local_nllb_cache_dir_var": local_nllb_cache_dir_var,
        }

    def _run_local_nllb_test_from_vars(
        self,
        model_name_var,
        device_var,
        cache_dir_var,
        max_chars_var,
        test_button,
        test_status_var,
    ):
        config = self._local_nllb_config_from_vars(
            model_name_var,
            device_var,
            cache_dir_var,
            max_chars_var,
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
    ):
        return {
            "model_name": model_name_var.get().strip()
            or self.LOCAL_NLLB_DEFAULT_MODEL_NAME,
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

    def _execute_local_nllb_test(self, config):
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
                message,
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
    ):
        config = self._local_nllb_config_from_vars(
            model_name_var,
            device_var,
            cache_dir_var,
            max_chars_var,
        )
        self._start_local_nllb_download(config, prompt=prompt)

    def _start_local_nllb_cache_check(
        self,
        config,
        prompt_if_missing=False,
        provider_var=None,
        provider_map=None,
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
                provider_var,
                provider_map,
            ),
            daemon=True,
        ).start()

    def _run_local_nllb_cache_check_worker(
        self,
        config,
        prompt_if_missing,
        provider_var,
        provider_map,
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
                provider_var,
                provider_map,
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
        provider_var,
        provider_map,
        result,
    ):
        self.nllb_check_in_progress = False
        if result.get("error"):
            self._set_local_nllb_status("Error", result["error"])
            self.update_status(result["error"])
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
            return
        if self._confirm_local_nllb_download(config.get("model_name", "")):
            self._start_local_nllb_download(config, prompt=False)
            return
        self._set_local_nllb_status(
            "Not downloaded",
            self.LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE,
        )
        self.update_status(self.LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE)
        self._revert_local_nllb_provider_selection(provider_var, provider_map)

    def _start_local_nllb_verification(self, config):
        self._set_local_nllb_status(
            "Loading",
            "Loading Local NLLB and running a test translation.",
        )
        Thread(
            target=lambda: self._run_local_nllb_verification_worker(config),
            daemon=True,
        ).start()

    def _run_local_nllb_verification_worker(self, config):
        message, status = self._execute_local_nllb_test(config)

        def finish():
            try:
                if self.local_nllb_message_var is not None:
                    self.local_nllb_message_var.set(message)
            except Exception:
                pass
            self._refresh_local_nllb_runtime_ui()
            self.update_status(status)

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

    def _revert_local_nllb_provider_selection(self, provider_var, provider_map):
        if provider_var is None or provider_map is None:
            return
        previous = self.previous_text_translation_provider or "none"
        if previous == "local_nllb":
            previous = "none"
        self.suppress_local_nllb_provider_check = True
        try:
            self._set_translation_provider_var_code(provider_var, provider_map, previous)
        finally:
            self.suppress_local_nllb_provider_check = False

    def _set_translation_provider_var_code(self, provider_var, provider_map, provider_code):
        for label, code in provider_map.items():
            if code == provider_code:
                provider_var.set(label)
                return
        provider_var.set("None")

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
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            import torch as torch_module
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
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

    def _translation_provider_from_var(self, provider_var, provider_map):
        return provider_map.get(provider_var.get(), self.text_translation_provider)

    def _refresh_translation_toggle_label(
        self,
        enable_translation_var,
        provider_var,
        provider_map,
        toggle_state_label,
        output_lang_label,
    ):
        enabled = self._coerce_bool(enable_translation_var.get(), default=False)
        provider = self._translation_provider_from_var(provider_var, provider_map)
        if enabled:
            provider_label = {
                "none": "no text provider selected",
                "openai": "OpenAI API",
                "local_nllb": "Local NLLB",
            }.get(provider, provider)
            toggle_state_label.config(
                text=f"Current mode: Translation ON ({provider_label})"
            )
        else:
            toggle_state_label.config(text="Current mode: Translation OFF")
        output_lang_label.config(text=self.OUTPUT_LANGUAGE_ENGLISH_LABEL)

    def _refresh_translation_provider_visibility(
        self,
        provider_var,
        provider_map,
        nllb_container,
        refresh_label,
    ):
        provider = self._translation_provider_from_var(provider_var, provider_map)
        if provider == "local_nllb":
            nllb_container.pack(fill=tk.X, pady=(0, 8))
        else:
            nllb_container.pack_forget()
        refresh_label()

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

    def _check_omnilingual_sidecar_from_settings(
        self,
        base_url_var,
        status_var,
        details_holder,
        check_button,
        details_button,
    ):
        base_url = self._normalize_omnilingual_sidecar_base_url(base_url_var.get())
        status_var.set("Checking...")
        details_holder["text"] = ""
        try:
            check_button.configure(state=tk.DISABLED)
            details_button.configure(state=tk.DISABLED)
        except Exception:
            pass

        def worker():
            result = self._probe_omnilingual_sidecar(base_url)

            def finish():
                status_var.set(result.get("message", "Error - view details"))
                details_holder["text"] = result.get("details", "")
                try:
                    check_button.configure(state=tk.NORMAL)
                    detail_state = (
                        tk.NORMAL if result.get("show_details") else tk.DISABLED
                    )
                    details_button.configure(state=detail_state)
                except Exception:
                    pass

            try:
                self.root.after(0, finish)
            except Exception:
                pass

        Thread(target=worker, daemon=True).start()

    def _probe_omnilingual_sidecar(self, base_url):
        base_url = self._normalize_omnilingual_sidecar_base_url(base_url)
        health_url = f"{base_url}/health-check"
        try:
            response = requests.get(health_url, timeout=5)
        except requests.exceptions.ConnectionError as exc:
            return {
                "message": "Not installed or not running",
                "details": (
                    "Could not connect to the Local Omnilingual server at "
                    f"{health_url}. Start the Docker sidecar, then try again. {exc}"
                ),
                "show_details": True,
            }
        except requests.exceptions.Timeout as exc:
            return {
                "message": "Error - view details",
                "details": (
                    "Timed out while checking the Local Omnilingual server at "
                    f"{health_url}. The model may still be loading. {exc}"
                ),
                "show_details": True,
            }
        except requests.RequestException as exc:
            return {
                "message": "Error - view details",
                "details": f"Could not check {health_url}. {exc}",
                "show_details": True,
            }

        if response.status_code == 404:
            return self._probe_omnilingual_sidecar_docs(base_url, health_url, response)
        if 200 <= response.status_code < 300:
            state, detail = self._omnilingual_health_state_from_response(response)
            return {
                "message": state,
                "details": detail,
                "show_details": state != "Ready",
            }
        return {
            "message": "Error - view details",
            "details": (
                f"{health_url} returned HTTP {response.status_code}. "
                f"Response preview: {self._short_response_preview(response.text)}"
            ),
            "show_details": True,
        }

    def _probe_omnilingual_sidecar_docs(self, base_url, health_url, health_response):
        docs_url = f"{base_url}/docs"
        try:
            response = requests.get(docs_url, timeout=5)
        except requests.exceptions.ConnectionError as exc:
            return {
                "message": "Not installed or not running",
                "details": (
                    f"{health_url} returned 404, then {docs_url} was unreachable. "
                    f"Start the Docker sidecar, then try again. {exc}"
                ),
                "show_details": True,
            }
        except requests.exceptions.Timeout as exc:
            return {
                "message": "Error - view details",
                "details": (
                    f"{health_url} returned 404, then {docs_url} timed out. "
                    f"The model may still be loading. {exc}"
                ),
                "show_details": True,
            }
        except requests.RequestException as exc:
            return {
                "message": "Error - view details",
                "details": f"{health_url} returned 404, then {docs_url} failed. {exc}",
                "show_details": True,
            }
        if 200 <= response.status_code < 300:
            return {
                "message": "Reachable - API reachable; health endpoint unavailable.",
                "details": (
                    f"{health_url} returned 404, but {docs_url} returned "
                    f"HTTP {response.status_code}."
                ),
                "show_details": False,
            }
        return {
            "message": "Error - view details",
            "details": (
                f"{health_url} returned HTTP {health_response.status_code}, then "
                f"{docs_url} returned HTTP {response.status_code}. "
                f"Response preview: {self._short_response_preview(response.text)}"
            ),
            "show_details": True,
        }

    def _omnilingual_health_state_from_response(self, response):
        try:
            payload = response.json()
        except Exception:
            return "Ready", (
                f"/health-check returned HTTP {response.status_code}. "
                "No JSON health payload was provided."
            )
        if not isinstance(payload, dict):
            return "Ready", (
                f"/health-check returned HTTP {response.status_code}. "
                f"Payload preview: {self._short_response_preview(payload)}"
            )
        for key in ("ready", "healthy", "ok"):
            if isinstance(payload.get(key), bool):
                if payload.get(key):
                    return "Ready", f"/health-check returned {key}=true."
                return "Reachable", f"/health-check returned {key}=false."
        status = str(
            payload.get("status")
            or payload.get("state")
            or payload.get("health")
            or ""
        ).strip().lower()
        ready_statuses = {"ok", "ready", "healthy", "up", "running"}
        loading_statuses = {
            "loading",
            "starting",
            "warming",
            "not_ready",
            "not ready",
            "unhealthy",
            "down",
        }
        if status in ready_statuses:
            return "Ready", f"/health-check status is {status}."
        if status in loading_statuses:
            return "Reachable", f"/health-check status is {status}."
        return "Ready", (
            f"/health-check returned HTTP {response.status_code}. "
            f"Payload preview: {self._short_response_preview(payload)}"
        )

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
    
