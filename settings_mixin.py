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
        self.openai_stt_model = str(
            data.get("openai_stt_model", self.openai_stt_model)
        ).strip() or "whisper-1"
        if self.openai_stt_model not in ("whisper-1", "gpt-4o-transcribe"):
            self.openai_stt_model = "whisper-1"
        self.openai_translation_mode = str(
            data.get("openai_translation_mode", self.openai_translation_mode)
        ).strip() or "whisper"
        if self.openai_translation_mode not in ("whisper", "gpt-4o"):
            self.openai_translation_mode = "whisper"
        self.openai_translate_model = str(
            data.get("openai_translate_model", self.openai_translate_model)
        ).strip() or "gpt-4o"
        self.speech_engine = data.get("speech_engine", self.speech_engine)
        self.faster_whisper_model_name = data.get(
            "faster_whisper_model_name", self.faster_whisper_model_name
        )
        self.faster_whisper_compute_type = data.get(
            "faster_whisper_compute_type", self.faster_whisper_compute_type
        )
        self.faster_whisper_device = data.get(
            "faster_whisper_device", self.faster_whisper_device
        )
        self.faster_whisper_vad_enabled = self._coerce_bool(
            data.get("faster_whisper_vad_enabled", self.faster_whisper_vad_enabled),
            default=self.faster_whisper_vad_enabled,
        )
        self.faster_whisper_vad_threshold = self._coerce_float_range(
            data.get("faster_whisper_vad_threshold", self.faster_whisper_vad_threshold),
            self.faster_whisper_vad_threshold,
            0.10,
            0.95,
        )
        self.faster_whisper_vad_min_silence_ms = self._coerce_int_range(
            data.get(
                "faster_whisper_vad_min_silence_ms",
                self.faster_whisper_vad_min_silence_ms,
            ),
            self.faster_whisper_vad_min_silence_ms,
            100,
            3000,
        )
        self.faster_whisper_vad_speech_pad_ms = self._coerce_int_range(
            data.get(
                "faster_whisper_vad_speech_pad_ms",
                self.faster_whisper_vad_speech_pad_ms,
            ),
            self.faster_whisper_vad_speech_pad_ms,
            0,
            1000,
        )
        self.faster_whisper_vad_min_speech_ms = self._coerce_int_range(
            data.get(
                "faster_whisper_vad_min_speech_ms",
                self.faster_whisper_vad_min_speech_ms,
            ),
            self.faster_whisper_vad_min_speech_ms,
            0,
            1000,
        )
        legacy_sidecar_url = str(data.get("omnilingual_sidecar_url", "") or "").strip()
        self.omnilingual_sidecar_base_url = (
            self._normalize_omnilingual_sidecar_base_url(
                data.get(
                    "omnilingual_sidecar_base_url",
                    legacy_sidecar_url or self.omnilingual_sidecar_base_url,
                )
            )
        )
        self.omnilingual_sidecar_endpoint_path = (
            self._normalize_omnilingual_sidecar_endpoint_path(
                data.get(
                    "omnilingual_sidecar_endpoint_path",
                    self.omnilingual_sidecar_endpoint_path,
                )
            )
        )
        if (
            "omnilingual_sidecar_model" in data
            or "omnilingual_sidecar_model_card" in data
        ):
            self.omnilingual_sidecar_model = str(
                data.get(
                    "omnilingual_sidecar_model",
                    data.get("omnilingual_sidecar_model_card", ""),
                )
            ).strip()
        self.omnilingual_sidecar_language = str(
            data.get(
                "omnilingual_sidecar_language",
                data.get("omnilingual_sidecar_lang", self.omnilingual_sidecar_language),
            )
        ).strip()
        self.omnilingual_sidecar_response_format = (
            self._normalize_omnilingual_sidecar_response_format(
                data.get(
                    "omnilingual_sidecar_response_format",
                    self.omnilingual_sidecar_response_format,
                )
            )
        )
        self.omnilingual_sidecar_timeout_sec = self._coerce_int_range(
            data.get(
                "omnilingual_sidecar_timeout_sec",
                self.omnilingual_sidecar_timeout_sec,
            ),
            self.omnilingual_sidecar_timeout_sec,
            5,
            600,
        )
        kroko_lang = str(data.get("kroko_language_code", self.kroko_language_code)).strip()
        _kroko_legacy = {
            "en": "en-US", "es": "es-ES", "fr": "fr-FR", "de": "de-DE",
            "nl": "nl-NL", "it": "it-IT", "pt": "pt-PT", "bg": "bg-BG", "sv": "sv-SV",
        }
        kroko_lang = _kroko_legacy.get(kroko_lang, kroko_lang)
        self.kroko_language_code = kroko_lang if kroko_lang else "es-ES"
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
        self.realtime_stt_post_speech_silence = self._coerce_float_range(
            data.get("realtime_stt_post_speech_silence", self.realtime_stt_post_speech_silence),
            0.6, 0.1, 3.0,
        )
        self.realtime_stt_enable_interim = self._coerce_bool(
            data.get("realtime_stt_enable_interim", self.realtime_stt_enable_interim),
            default=True,
        )

    def _load_display_settings(self, data):
        self.bg_color = data.get("bg_color", self.bg_color)
        self.text_color = data.get("text_color", self.text_color)
        self.max_lines = data.get("max_lines", self.max_lines)
        self.lock_output_focus = self._coerce_bool(
            data.get("lock_output_focus", self.lock_output_focus),
            default=self.lock_output_focus,
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
        self.chunk_size = data.get("chunk_size", self.chunk_size)
        self.chunk_delay_ms = data.get("chunk_delay_ms", self.chunk_delay_ms)
        self.flush_timeout_ms = data.get("flush_timeout_ms", self.flush_timeout_ms)
        self.sentence_flush_ms = data.get("sentence_flush_ms", self.sentence_flush_ms)
        try:
            self.display_speed_factor = float(
                data.get("display_speed_factor", self.display_speed_factor)
            )
        except Exception:
            pass
        self.display_speed_factor = max(0.5, min(self.display_speed_factor, 2.5))
        self.loopback_chunk_autotune_enabled = self._coerce_bool(
            data.get(
                "loopback_chunk_autotune_enabled",
                self.loopback_chunk_autotune_enabled,
            ),
            default=self.loopback_chunk_autotune_enabled,
        )
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
        self.rms_gate_enabled = bool(data.get("rms_gate_enabled", self.rms_gate_enabled))
        self.rms_gate_factor = float(data.get("rms_gate_factor", self.rms_gate_factor))
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
        provider_value = data.get("text_translation_provider", None)
        if provider_value is None:
            provider_value = "openai" if self.translation_enabled else "none"
        self.text_translation_provider = self._normalize_text_translation_provider(
            provider_value
        )
        if self.text_translation_provider == "local_nllb":
            self.previous_text_translation_provider = (
                "openai" if self.translation_enabled else "none"
            )
        else:
            self.previous_text_translation_provider = self.text_translation_provider
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

    def _normalize_omnilingual_sidecar_base_url(self, value):
        url = str(value or "").strip()
        if not url:
            return self.OMNILINGUAL_SIDECAR_DEFAULT_BASE_URL
        url = url.rstrip("/")
        known_paths = (
            self.OMNILINGUAL_SIDECAR_DEFAULT_ENDPOINT_PATH,
            "/transcribe",
        )
        lowered = url.lower()
        for path in known_paths:
            if lowered.endswith(path.lower()):
                url = url[: -len(path)].rstrip("/")
                break
        return url or self.OMNILINGUAL_SIDECAR_DEFAULT_BASE_URL

    def _normalize_omnilingual_sidecar_endpoint_path(self, value):
        path = str(value or "").strip()
        if not path:
            return self.OMNILINGUAL_SIDECAR_DEFAULT_ENDPOINT_PATH
        if not path.startswith("/"):
            path = f"/{path}"
        return path

    def _normalize_omnilingual_sidecar_response_format(self, value):
        response_format = str(value or "").strip().lower()
        if response_format in ("json", "text"):
            return response_format
        return self.OMNILINGUAL_SIDECAR_DEFAULT_RESPONSE_FORMAT

    def _build_omnilingual_sidecar_url(self, base_url=None, endpoint_path=None):
        base = self._normalize_omnilingual_sidecar_base_url(
            self.omnilingual_sidecar_base_url if base_url is None else base_url
        )
        path = self._normalize_omnilingual_sidecar_endpoint_path(
            self.omnilingual_sidecar_endpoint_path
            if endpoint_path is None
            else endpoint_path
        )
        return f"{base}{path}"

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
        provider = str(value or "").strip().lower()
        provider = provider.replace("-", "_").replace(" ", "_")
        aliases = {
            "": "none",
            "off": "none",
            "disabled": "none",
            "no_translation": "none",
            "existing_cloud_provider": "openai",
            "cloud": "openai",
            "openai_api": "openai",
            "openai_api_cloud": "openai",
            "api": "openai",
            "nllb": "local_nllb",
            "local": "local_nllb",
            "local_nllb_200": "local_nllb",
            "local_nllb_200_distilled_600m": "local_nllb",
        }
        provider = aliases.get(provider, provider)
        if provider not in self.TEXT_TRANSLATION_PROVIDER_OPTIONS:
            return "none"
        return provider

    def _normalize_local_nllb_device(self, value):
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
            if (self.speech_engine or "").strip().lower() == "omnilingual-sidecar":
                sidecar_lang = self._normalized_omnilingual_sidecar_language_code()
                if sidecar_lang:
                    mapped = self._nllb_language_code_for_app_language(sidecar_lang)
                    if mapped:
                        return mapped
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
            "openai_stt_model": self.openai_stt_model,
            "openai_translation_mode": self.openai_translation_mode,
            "openai_translate_model": self.openai_translate_model,
            "speech_engine": self.speech_engine,
            "faster_whisper_model_name": self.faster_whisper_model_name,
            "faster_whisper_compute_type": self.faster_whisper_compute_type,
            "faster_whisper_device": self.faster_whisper_device,
            "faster_whisper_vad_enabled": self.faster_whisper_vad_enabled,
            "faster_whisper_vad_threshold": self.faster_whisper_vad_threshold,
            "faster_whisper_vad_min_silence_ms": self.faster_whisper_vad_min_silence_ms,
            "faster_whisper_vad_speech_pad_ms": self.faster_whisper_vad_speech_pad_ms,
            "faster_whisper_vad_min_speech_ms": self.faster_whisper_vad_min_speech_ms,
            "omnilingual_sidecar_base_url": self.omnilingual_sidecar_base_url,
            "omnilingual_sidecar_endpoint_path": self.omnilingual_sidecar_endpoint_path,
            "omnilingual_sidecar_model": self.omnilingual_sidecar_model,
            "omnilingual_sidecar_language": self.omnilingual_sidecar_language,
            "omnilingual_sidecar_response_format": self.omnilingual_sidecar_response_format,
            "omnilingual_sidecar_timeout_sec": self.omnilingual_sidecar_timeout_sec,
            "kroko_language_code": self.kroko_language_code,
            "realtime_stt_final_model": self.realtime_stt_final_model,
            "realtime_stt_realtime_model": self.realtime_stt_realtime_model,
            "realtime_stt_silero_sensitivity": self.realtime_stt_silero_sensitivity,
            "realtime_stt_post_speech_silence": self.realtime_stt_post_speech_silence,
            "realtime_stt_enable_interim": self.realtime_stt_enable_interim,
            "bg_color": self.bg_color,
            "text_color": self.text_color,
            "max_lines": self.max_lines,
            "lock_output_focus": self.lock_output_focus,
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
            "chunk_size": self.chunk_size,
            "chunk_delay_ms": self.chunk_delay_ms,
            "flush_timeout_ms": self.flush_timeout_ms,
            "sentence_flush_ms": self.sentence_flush_ms,
            "display_speed_factor": self.display_speed_factor,
            "loopback_chunk_autotune_enabled": self.loopback_chunk_autotune_enabled,
            "logging_mode": self.logging_mode,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "translation_enabled": self.translation_enabled,
            "text_translation_provider": self.text_translation_provider,
            "local_nllb_model_name": self.local_nllb_model_name,
            "local_nllb_device": self.local_nllb_device,
            "local_nllb_source_lang": self.local_nllb_source_lang,
            "local_nllb_target_lang": self.local_nllb_target_lang,
            "local_nllb_max_chars": self.local_nllb_max_chars,
            "local_nllb_cache_dir": self.local_nllb_cache_dir,
            "auto_switch_translation": self.auto_switch_translation,
            "biblical_books": self.biblical_books,
            "preferred_device_label": self.preferred_device_label,
            "rms_gate_enabled": self.rms_gate_enabled,
            "rms_gate_factor": self.rms_gate_factor,
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

