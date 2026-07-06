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


class SettingsMixin:
    def load_settings(self):
        data = self._read_settings_data()
        if data is None:
            return
        self._load_model_and_engine_settings(data)
        self._load_display_settings(data)
        self._load_bad_word_settings(data)
        self._load_custom_vocabulary_settings(data)
        self._load_runtime_settings(data)
        self._configure_cuda_dll_search_path()
        self._resolve_loaded_monitor_settings()

    def _read_settings_data(self):
        if not os.path.exists(self.settings_path):
            return None
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _load_model_and_engine_settings(self, data):
        self.stt_device = self._normalize_stt_device(
            data.get("stt_device", data.get("faster_whisper_device", self.stt_device))
        )
        self.realtime_stt_final_model = str(
            data.get("realtime_stt_final_model", self.realtime_stt_final_model)
        ).strip() or "large-v3"
        self.realtime_stt_realtime_model = str(
            data.get("realtime_stt_realtime_model", self.realtime_stt_realtime_model)
        ).strip() or "tiny"
        self.realtime_stt_silero_sensitivity = self._coerce_float_range(
            data.get("realtime_stt_silero_sensitivity", self.realtime_stt_silero_sensitivity),
            0.4, 0.1, 0.9,
        )

    def _load_display_settings(self, data):
        self.bg_color = data.get("bg_color", self.bg_color)
        self.text_color = data.get("text_color", self.text_color)
        self.max_lines = data.get("max_lines", self.max_lines)
        self.lock_output_focus = self._coerce_bool(
            data.get("lock_output_focus", self.lock_output_focus),
            default=self.lock_output_focus,
        )
        self.video_feed_enabled = self._coerce_bool(
            data.get("video_feed_enabled", self.video_feed_enabled),
            default=self.video_feed_enabled,
        )
        video_device_index = data.get("video_device_index", self.video_device_index)
        try:
            self.video_device_index = (
                int(video_device_index) if video_device_index is not None else None
            )
        except Exception:
            self.video_device_index = None
        self.video_caption_bar_alpha = self._coerce_float_range(
            data.get("video_caption_bar_alpha", self.video_caption_bar_alpha),
            default=self.video_caption_bar_alpha,
            minimum=0.0,
            maximum=1.0,
        )

    def _load_bad_word_settings(self, data):
        bad_words_by_lang = data.get("bad_words_by_lang")
        if isinstance(bad_words_by_lang, dict):
            self.bad_words_by_lang = {
                lang: {word.strip().lower() for word in (words or []) if word.strip()}
                for lang, words in bad_words_by_lang.items()
            }
        else:
            legacy = data.get("bad_words", list(self.bad_words_by_lang.get("en", [])))
            self.bad_words_by_lang["en"] = {word.strip().lower() for word in legacy if word.strip()}
        if "en" not in self.bad_words_by_lang:
            self.bad_words_by_lang["en"] = set(self.default_bad_words_en())
        if "es" not in self.bad_words_by_lang:
            self.bad_words_by_lang["es"] = set(self.default_bad_words_es())
        enabled = data.get("bad_word_filters_enabled")
        if isinstance(enabled, dict):
            self.bad_word_filters_enabled = {
                lang: bool(enabled.get(lang))
                for lang in self.bad_words_by_lang.keys()
            }
        elif isinstance(enabled, list):
            enabled_set = {str(lang) for lang in enabled}
            self.bad_word_filters_enabled = {
                lang: lang in enabled_set for lang in self.bad_words_by_lang.keys()
            }
        # Filters are mandatory; always enable every available language.
        for lang in self.bad_words_by_lang.keys():
            self.bad_word_filters_enabled[lang] = True
        self._refresh_bad_words()

    def _load_custom_vocabulary_settings(self, data):
        vocab_enabled = data.get("custom_vocab_langs_enabled")
        if isinstance(vocab_enabled, dict):
            self.custom_vocab_langs_enabled = {
                lang: bool(vocab_enabled.get(lang))
                for lang in self.custom_vocabulary_by_lang.keys()
            }
        elif isinstance(vocab_enabled, list):
            enabled_set = {str(lang) for lang in vocab_enabled}
            self.custom_vocab_langs_enabled = {
                lang: lang in enabled_set for lang in self.custom_vocabulary_by_lang.keys()
            }
        else:
            for lang in self.custom_vocabulary_by_lang.keys():
                if lang not in self.custom_vocab_langs_enabled:
                    self.custom_vocab_langs_enabled[lang] = False
        # Vocabulary filters are mandatory; always enable every available language.
        for lang in self.custom_vocabulary_by_lang.keys():
            self.custom_vocab_langs_enabled[lang] = True
        vocab_by_lang = data.get("custom_vocabulary_by_lang")
        if isinstance(vocab_by_lang, dict):
            self.custom_vocabulary_by_lang = {
                lang: [word.strip() for word in (words or []) if str(word).strip()]
                for lang, words in vocab_by_lang.items()
            }
        else:
            legacy_vocab = data.get(
                "custom_vocabulary",
                list(self.custom_vocabulary_by_lang.get("en", [])),
            )
            self.custom_vocabulary_by_lang["en"] = [
                word.strip() for word in legacy_vocab if str(word).strip()
            ]
        if "en" not in self.custom_vocabulary_by_lang:
            self.custom_vocabulary_by_lang["en"] = self.default_biblical_terms()
        if "es" not in self.custom_vocabulary_by_lang:
            self.custom_vocabulary_by_lang["es"] = self.default_biblical_terms_es()

    def _load_runtime_settings(self, data):
        self.logging_mode = self._normalize_logging_mode(
            data.get("logging_mode", self.logging_mode)
        )
        self._apply_logging_mode_flags()
        self.flush_timeout_ms = data.get("flush_timeout_ms", self.flush_timeout_ms)
        self.translation_enabled = self._coerce_bool(
            data.get("translation_enabled", self.translation_enabled),
            default=self.translation_enabled,
        )
        if any(
            key in data for key in ("source_lang", "target_lang", "auto_switch_translation")
        ):
            self.source_lang = data.get("source_lang", self.source_lang)
            self.target_lang = data.get("target_lang", self.target_lang)
            self.auto_switch_translation = self._coerce_bool(
                data.get("auto_switch_translation", self.auto_switch_translation),
                default=self.auto_switch_translation,
            )
            self._normalize_translation_settings()
        else:
            self._apply_translation_mode_defaults()
        self._load_text_translation_settings(data)
        self.biblical_books = data.get("biblical_books", self.biblical_books)
        self.preferred_device_label = data.get(
            "preferred_device_label", self.preferred_device_label
        )
        self.start_with_windows = self._coerce_bool(
            data.get("start_with_windows", self.start_with_windows),
            default=self.start_with_windows,
        )
        self.cuda_directory = self._normalize_optional_directory(
            data.get("cuda_directory", self.cuda_directory)
        )
        self.settings_geometry = data.get("settings_geometry", self.settings_geometry)
        self.settings_monitor_index = int(
            data.get("settings_monitor_index", self.settings_monitor_index)
        )
        self.monitor_index = int(data.get("monitor_index", self.monitor_index))
        self.monitor_device = data.get("monitor_device", self.monitor_device)
        self.monitor_origin = data.get("monitor_origin", self.monitor_origin)
        self.settings_monitor_device = data.get(
            "settings_monitor_device", self.settings_monitor_device
        )
        self.settings_monitor_origin = data.get(
            "settings_monitor_origin", self.settings_monitor_origin
        )

    def _load_text_translation_settings(self, data):
        self.local_nllb_model_name = str(
            data.get("local_nllb_model_name", self.local_nllb_model_name)
        ).strip() or self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        self.local_nllb_device = self._normalize_local_nllb_device(
            data.get("local_nllb_device", self.local_nllb_device)
        )
        self.local_nllb_source_lang = self._normalize_local_nllb_lang_setting(
            data.get("local_nllb_source_lang", self.local_nllb_source_lang),
            default=self.LOCAL_NLLB_DEFAULT_SOURCE_LANG,
            allow_auto=True,
        )
        self.local_nllb_target_lang = self._normalize_local_nllb_lang_setting(
            data.get("local_nllb_target_lang", self.local_nllb_target_lang),
            default=self.LOCAL_NLLB_DEFAULT_TARGET_LANG,
            allow_auto=False,
        )
        self.local_nllb_max_chars = self._coerce_int_range(
            data.get("local_nllb_max_chars", self.local_nllb_max_chars),
            self.LOCAL_NLLB_DEFAULT_MAX_CHARS,
            250,
            20000,
        )
        self.local_nllb_cache_dir = self._normalize_optional_directory(
            data.get("local_nllb_cache_dir", self.local_nllb_cache_dir)
        )

    def _resolve_loaded_monitor_settings(self):
        if not self.monitors:
            self.monitors = self.get_monitors()
        if self.monitors:
            self.monitor_index = self._resolve_monitor_index(
                self.monitor_index, self.monitor_device, self.monitor_origin
            )
            self.settings_monitor_index = self._resolve_monitor_index(
                self.settings_monitor_index,
                self.settings_monitor_device,
                self.settings_monitor_origin,
            )

    def _coerce_bool(self, value, default=False):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("1", "true", "yes", "on", "enabled"):
                return True
            if normalized in ("0", "false", "no", "off", "disabled", ""):
                return False
        return bool(default)

    def _coerce_float_range(self, value, default, minimum, maximum):
        try:
            coerced = float(value)
        except Exception:
            coerced = float(default)
        return max(float(minimum), min(float(maximum), coerced))

    def _coerce_int_range(self, value, default, minimum, maximum):
        try:
            coerced = int(float(value))
        except Exception:
            coerced = int(default)
        return max(int(minimum), min(int(maximum), coerced))

    def _normalize_logging_mode(self, value):
        mode = str(value or "").strip().lower()
        mode = mode.replace("-", "_").replace(" ", "_")
        aliases = {
            "": "normal",
            "standard": "normal",
            "default": "normal",
            "verbose": "debug",
            "diagnostic": "debug",
            "diagnostics": "debug",
            "comparison": "evaluation",
            "compare": "evaluation",
            "eval": "evaluation",
            "all": "full",
            "debug_evaluation": "full",
            "debug+evaluation": "full",
            "debug_and_evaluation": "full",
        }
        mode = aliases.get(mode, mode)
        if mode not in self.LOGGING_MODE_OPTIONS:
            return "normal"
        return mode

    def _apply_logging_mode_flags(self):
        self.logging_mode = self._normalize_logging_mode(self.logging_mode)
        self.status_log_enabled = True
        self.finalized_logs_enabled = True
        self.transcript_trace_enabled = self.logging_mode in ("debug", "full")
        self.comparison_logs_enabled = self.logging_mode in ("evaluation", "full")

    def _short_response_preview(self, value, limit=500):
        preview = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        if len(preview) > limit:
            return preview[:limit] + "..."
        return preview

    def _normalize_lang_code(self, value, default="", allow_auto=False):
        lang = str(value or "").strip().lower()
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if "_" in lang:
            lang = lang.split("_", 1)[0]
        if allow_auto and lang == "auto":
            return "auto"
        if len(lang) == 2 and lang.isalpha():
            return lang
        return default

    def _normalize_text_translation_provider(self, value):
        # Local NLLB is the only supported text translation provider.
        return "local_nllb"

    def _normalize_local_nllb_device(self, value):
        return self._normalize_device_choice(value)

    def _normalize_stt_device(self, value):
        return self._normalize_device_choice(value)

    def _normalize_device_choice(self, value):
        device = str(value or "").strip().lower()
        if device in ("auto", "cuda", "cpu"):
            return device
        return "auto"

    def _normalize_local_nllb_lang_setting(self, value, default="", allow_auto=False):
        lang = str(value or "").strip()
        if not lang:
            return default
        normalized = lang.lower().replace("-", "_").replace(" ", "_")
        auto_values = {
            "auto",
            "selected",
            "selected_source",
            "auto_from_selected_source_language",
        }
        if allow_auto and normalized in auto_values:
            return self.LOCAL_NLLB_DEFAULT_SOURCE_LANG
        mapped = self._nllb_language_code_for_app_language(lang)
        return mapped or lang

    def _nllb_language_code_for_app_language(self, value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        if re.match(r"^[a-z]{3}_[A-Za-z]+$", raw):
            return raw
        key = raw.strip().lower().replace("_", "-")
        key = re.sub(r"\s+", " ", key)
        if key in self.LOCAL_NLLB_LANG_ALIASES:
            return self.LOCAL_NLLB_LANG_ALIASES[key]
        simple = key.split("-", 1)[0]
        return self.LOCAL_NLLB_LANG_ALIASES.get(simple, "")

    def _resolve_local_nllb_source_lang(self, override=None):
        configured = (
            str(self.local_nllb_source_lang if override is None else override)
            .strip()
        )
        normalized = configured.lower().replace("-", "_").replace(" ", "_")
        if not configured or normalized in (
            "auto",
            "selected",
            "selected_source",
            "auto_from_selected_source_language",
        ):
            mapped = self._nllb_language_code_for_app_language(
                self._effective_source_lang() or self.source_lang
            )
            if mapped:
                return mapped
            raise ValueError(self.LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE)
        mapped = self._nllb_language_code_for_app_language(configured)
        if not mapped:
            raise ValueError(self.LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE)
        return mapped

    def _resolve_local_nllb_target_lang(self, override=None):
        configured = str(
            self.local_nllb_target_lang if override is None else override
        ).strip()
        if not configured:
            configured = self._effective_target_lang() or self.target_lang or "en"
        mapped = self._nllb_language_code_for_app_language(configured)
        if not mapped:
            raise ValueError(self.LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE)
        return mapped

    def _nllb_code_to_two_letter(self, code):
        code = (code or "").strip().lower()
        if code.startswith("eng"):
            return "en"
        if code.startswith("spa"):
            return "es"
        return ""

    def _normalize_translation_settings(self):
        self.auto_switch_translation = self._coerce_bool(
            self.auto_switch_translation,
            default=False,
        )
        source_default = "es" if self.translation_enabled else "en"
        self.source_lang = self._normalize_lang_code(
            self.source_lang,
            default=source_default,
            allow_auto=True,
        )
        self.target_lang = self._normalize_lang_code(
            self.target_lang,
            default="en",
            allow_auto=False,
        )

    def _apply_translation_mode_defaults(self):
        self.source_lang = "en"
        self.target_lang = "en"
        if self.translation_enabled:
            self.source_lang = "es"
            self.target_lang = "en"
        self.auto_switch_translation = False

    def save_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            try:
                self.settings_geometry = self.settings_window.geometry()
            except Exception:
                pass
        if not self.monitors:
            self.monitors = self.get_monitors()
        monitor_device, monitor_origin = self._monitor_identity_for_index(self.monitor_index)
        settings_device, settings_origin = self._monitor_identity_for_index(
            self.settings_monitor_index
        )
        self.monitor_device = monitor_device
        self.monitor_origin = monitor_origin
        self.settings_monitor_device = settings_device
        self.settings_monitor_origin = settings_origin
        data = {
            "speech_engine": self.speech_engine,
            "stt_device": self.stt_device,
            "realtime_stt_final_model": self.realtime_stt_final_model,
            "realtime_stt_realtime_model": self.realtime_stt_realtime_model,
            "realtime_stt_silero_sensitivity": self.realtime_stt_silero_sensitivity,
            "bg_color": self.bg_color,
            "text_color": self.text_color,
            "max_lines": self.max_lines,
            "lock_output_focus": self.lock_output_focus,
            "video_feed_enabled": self.video_feed_enabled,
            "video_device_index": self.video_device_index,
            "video_caption_bar_alpha": self.video_caption_bar_alpha,
            "bad_words_by_lang": {
                lang: sorted(words) for lang, words in self.bad_words_by_lang.items()
            },
            "bad_word_filters_enabled": sorted(
                [lang for lang, enabled in self.bad_word_filters_enabled.items() if enabled]
            ),
            "custom_vocabulary_by_lang": {
                lang: list(words) for lang, words in self.custom_vocabulary_by_lang.items()
            },
            "custom_vocab_langs_enabled": sorted(
                [lang for lang, enabled in self.custom_vocab_langs_enabled.items() if enabled]
            ),
            "flush_timeout_ms": self.flush_timeout_ms,
            "logging_mode": self.logging_mode,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "translation_enabled": self.translation_enabled,
            "local_nllb_model_name": self.local_nllb_model_name,
            "local_nllb_device": self.local_nllb_device,
            "local_nllb_source_lang": self.local_nllb_source_lang,
            "local_nllb_target_lang": self.local_nllb_target_lang,
            "local_nllb_max_chars": self.local_nllb_max_chars,
            "local_nllb_cache_dir": self.local_nllb_cache_dir,
            "auto_switch_translation": self.auto_switch_translation,
            "biblical_books": self.biblical_books,
            "preferred_device_label": self.preferred_device_label,
            "start_with_windows": self.start_with_windows,
            "cuda_directory": self.cuda_directory,
            "monitor_index": self.monitor_index,
            "monitor_device": monitor_device,
            "monitor_origin": monitor_origin,
            "settings_geometry": self.settings_geometry,
            "settings_monitor_index": self.settings_monitor_index,
            "settings_monitor_device": settings_device,
            "settings_monitor_origin": settings_origin,
        }
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _normalize_optional_directory(self, value):
        path = str(value or "").strip().strip("\"'")
        if not path:
            return ""
        try:
            path = os.path.expandvars(path)
            path = os.path.expanduser(path)
            return os.path.normpath(path)
        except Exception:
            return path

    def _resolve_cuda_search_directories(self, directory=None):
        base = self._normalize_optional_directory(
            self.cuda_directory if directory is None else directory
        )
        if not base:
            return []
        root = base
        base_name = os.path.basename(base).lower()
        parent_name = os.path.basename(os.path.dirname(base)).lower()
        if base_name == "bin":
            root = os.path.dirname(base)
        elif base_name == "x64" and parent_name == "lib":
            root = os.path.dirname(os.path.dirname(base))
        candidates = [
            os.path.join(root, "bin"),
            os.path.join(root, "lib", "x64"),
            root,
            base,
        ]
        resolved = []
        seen = set()
        for path in candidates:
            normalized = self._normalize_optional_directory(path)
            key = normalized.lower()
            if not normalized or key in seen or not os.path.isdir(normalized):
                continue
            seen.add(key)
            resolved.append(normalized)
        return resolved

    def _configure_cuda_dll_search_path(self):
        handles = getattr(self, "_cuda_dll_directory_handles", [])
        while handles:
            handle = handles.pop()
            try:
                handle.close()
            except Exception:
                pass
        if os.name != "nt":
            return
        add_dll_directory = getattr(os, "add_dll_directory", None)
        if add_dll_directory is None:
            return
        for path in self._resolve_cuda_search_directories():
            try:
                self._cuda_dll_directory_handles.append(add_dll_directory(path))
            except Exception:
                continue

