import tkinter as tk
from tkinter import messagebox
from tkinter import colorchooser
from tkinter import filedialog
from tkinter import font as tkfont
from threading import Thread, Lock
import queue
import time
import re
import requests
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
from logging_mixin import LoggingMixin
from settings_mixin import SettingsMixin
from monitor_mixin import MonitorMixin
from settings_ui_mixin import SettingsUIMixin
from audio_capture_mixin import AudioCaptureMixin
from transcription_mixin import TranscriptionMixin
from translation_mixin import TranslationMixin
from text_filter_mixin import TextFilterMixin
from display_mixin import DisplayMixin
from realtime_stt_mixin import RealtimeSttMixin

class TranslationApp(
    LoggingMixin,
    SettingsMixin,
    MonitorMixin,
    SettingsUIMixin,
    AudioCaptureMixin,
    TranscriptionMixin,
    TranslationMixin,
    TextFilterMixin,
    DisplayMixin,
    RealtimeSttMixin,
):
    SCROLL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")
    CONFIGURE_EVENT = "<Configure>"
    STATUS_LISTENING = "Listening..."
    SHOW_LIST_LABEL = "Show list"
    HIDE_LIST_LABEL = "Hide list"
    OUTPUT_LANGUAGE_ENGLISH_LABEL = "Output language: English"
    NON_WORD_PATTERN = r"[^\w]+"
    UNICODE_WORD_PATTERN = r"[^\W_]+"
    UNICODE_WORD_CHAR_PATTERN = r"[^\W_]"
    UNICODE_LETTER_PATTERN = r"[^\W\d_]"
    SPANISH_WORD_PATTERN = r"[a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1]+"
    SPANISH_DIACRITIC_PATTERN = r"[\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1\u00bf\u00a1]"
    TERMINAL_PUNCTUATION_PATTERN = r"[.!?][\"')\]]*$"
    TRAILING_EDGE_PUNCTUATION_PATTERN = r"(?:^(?:\.{2,}|[\s\-:;,])+|(?:\.{2,}|[\s\-:;,])+$)"
    PUNCTUATION_SPACING_PATTERN = r"\s+([,.;:!?])"
    URL_SCHEME_PATTERN = r"(?:https?://|www\.)"
    BARE_DOMAIN_PATTERN = r"\b(?:[a-z0-9-]+\.)+[a-z]{2,24}\b"
    COMMON_DOMAIN_SUFFIXES = frozenset(
        {
            "ai",
            "app",
            "biz",
            "ca",
            "co",
            "com",
            "de",
            "dev",
            "edu",
            "es",
            "fr",
            "gov",
            "info",
            "io",
            "me",
            "mx",
            "net",
            "org",
            "tv",
            "uk",
            "us",
        }
    )
    BOOK_1_SAMUEL = "1 Samuel"
    BOOK_2_SAMUEL = "2 Samuel"
    BOOK_1_KINGS = "1 Kings"
    BOOK_2_KINGS = "2 Kings"
    BOOK_1_CHRONICLES = "1 Chronicles"
    BOOK_2_CHRONICLES = "2 Chronicles"
    BOOK_SONG_OF_SOLOMON = "Song of Solomon"
    BOOK_1_CORINTHIANS = "1 Corinthians"
    BOOK_2_CORINTHIANS = "2 Corinthians"
    BOOK_1_THESSALONIANS = "1 Thessalonians"
    BOOK_2_THESSALONIANS = "2 Thessalonians"
    LOCAL_NLLB_DEFAULT_MODEL_NAME = "facebook/nllb-200-distilled-600M"
    LOCAL_NLLB_DEFAULT_SOURCE_LANG = "auto_from_selected_source_language"
    LOCAL_NLLB_DEFAULT_TARGET_LANG = "eng_Latn"
    LOCAL_NLLB_DEFAULT_MAX_CHARS = 4000
    LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE = (
        "Local NLLB does not yet have a language-code mapping for this language."
    )
    LOCAL_NLLB_MODEL_UNAVAILABLE_MESSAGE = (
        "Local NLLB is not ready. Download and test the model before using "
        "Local NLLB translation."
    )
    LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE = (
        "Local NLLB requires transformers, sentencepiece, and torch. Install "
        "dependencies, then select Local NLLB again."
    )
    LOCAL_NLLB_NOT_READY_MESSAGE = (
        "Local NLLB is not ready. Download and test the model before using "
        "Local NLLB translation."
    )
    LOCAL_NLLB_DOWNLOAD_CANCELED_MESSAGE = (
        "Local NLLB download canceled. Select another translation provider or "
        "download the model later."
    )
    LOCAL_NLLB_DOWNLOAD_FAILED_MESSAGE = (
        "Could not download Local NLLB model. Check your internet connection "
        "and click Retry download."
    )
    LOCAL_NLLB_CACHE_ERROR_MESSAGE = (
        "Could not write Local NLLB model cache. Check disk space and permissions."
    )
    LOCAL_NLLB_CUDA_OOM_MESSAGE = (
        "Local NLLB translation ran out of GPU memory. Try CPU mode or close other GPU applications."
    )
    LOCAL_NLLB_VERIFICATION_CUDA_OOM_MESSAGE = (
        "Local NLLB downloaded, but GPU loading ran out of memory. Try CPU mode."
    )
    LOCAL_NLLB_TIMEOUT_MESSAGE = (
        "Local NLLB translation timed out. Try a shorter transcript or CPU/GPU setting change."
    )
    LOCAL_NLLB_FAILED_MESSAGE = (
        "Local NLLB translation failed. The source transcript is still available."
    )
    LOCAL_NLLB_LANG_ALIASES = {
        "ar": "arb_Arab",
        "ara": "arb_Arab",
        "arabic": "arb_Arab",
        "arb": "arb_Arab",
        "chinese": "zho_Hans",
        "chinese simplified": "zho_Hans",
        "chinese traditional": "zho_Hant",
        "de": "deu_Latn",
        "deu": "deu_Latn",
        "dutch": "nld_Latn",
        "en": "eng_Latn",
        "eng": "eng_Latn",
        "english": "eng_Latn",
        "es": "spa_Latn",
        "fra": "fra_Latn",
        "fre": "fra_Latn",
        "french": "fra_Latn",
        "fr": "fra_Latn",
        "ger": "deu_Latn",
        "german": "deu_Latn",
        "hi": "hin_Deva",
        "hin": "hin_Deva",
        "hindi": "hin_Deva",
        "it": "ita_Latn",
        "ita": "ita_Latn",
        "italian": "ita_Latn",
        "ja": "jpn_Jpan",
        "japanese": "jpn_Jpan",
        "jpn": "jpn_Jpan",
        "ko": "kor_Hang",
        "kor": "kor_Hang",
        "korean": "kor_Hang",
        "nl": "nld_Latn",
        "nld": "nld_Latn",
        "por": "por_Latn",
        "portuguese": "por_Latn",
        "pt": "por_Latn",
        "ru": "rus_Cyrl",
        "rus": "rus_Cyrl",
        "russian": "rus_Cyrl",
        "spa": "spa_Latn",
        "spanish": "spa_Latn",
        "uk": "ukr_Cyrl",
        "ukr": "ukr_Cyrl",
        "ukrainian": "ukr_Cyrl",
        "zh": "zho_Hans",
        "zh-cn": "zho_Hans",
        "zh-hans": "zho_Hans",
        "zh-hant": "zho_Hant",
        "zh-tw": "zho_Hant",
        "zho": "zho_Hans",
    }
    LOGGING_MODE_OPTIONS = ("normal", "debug", "evaluation", "full")
    WINDOWS_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    WINDOWS_STARTUP_VALUE_NAME = "TopGradePythonTranslation"
    GRATITUDE_SHORT_PHRASES = frozenset(
        {
            "thank you",
            "thanks",
            "thanks you",
            "thank you very much",
            "thank you so much",
            "thanks very much",
            "thanks so much",
            "thank you for watching",
            "thanks for watching",
            "thank you for listening",
            "thanks for listening",
        }
    )
    STT_EDGE_NOISE_PREFIX_PATTERNS = (
        r"^\s*(?:thanks?|thank\s+you)(?:\s+(?:very|so)\s+much)?\s+for\s+(?:watching|listening)\b[\s.!?,:;\"']*",
        r"^\s*thank\s+you\s+very\s+much\b[\s.!?,:;\"']*",
        r"^\s*gracias(?:\s+muchas)?\b[\s.!?,:;\"']*",
        r"^\s*welcome\s+to\s+another\s+episode\s+of\s+(?:my\s+|the\s+)?channel\b[\s.!?,:;\"']*",
        r"^\s*welcome\s+(?:back\s+)?to\s+(?:my\s+|the\s+)?channel\b[\s.!?,:;\"']*",
        r"^\s*don'?t\s+forget\s+to\s+like\s+and\s+subscribe\b[\s.!?,:;\"']*",
    )
    STT_EDGE_NOISE_SUFFIX_PATTERNS = (
        r"[\s.!?,:;\"']*(?:thanks?|thank\s+you)(?:\s+(?:very|so)\s+much)?\s+for\s+(?:watching|listening)\s*$",
        r"[\s.!?,:;\"']*thank\s+you\s+very\s+much\s*$",
        r"[\s.!?,:;\"']*gracias(?:\s+muchas)?\s*$",
    )
    STT_STRICT_NOISE_MARKERS_NORMALIZED = frozenset(
        {
            "transcribe the audio verbatim",
            "transcribe audio verbatim",
            "context",
            "contexto",
            "there is no speech",
            "there is no speech in the audio",
            "no speech",
            "no speech detected",
            "there isn t any",
            "there is no doubt",
            "i don t know",
            "i do not know",
            "no i don t know",
            "no i do not know",
            "i m not sure",
            "text to translate",
            "texto a traducir",
            "please provide the text you need translated",
            "sure please provide the text you need translated",
            "of course please provide the text you need translated",
            "certainly please provide the text you would like translated",
            "sure please provide the text you want translated",
            "lo siento no puedo ayudar con eso",
            "i can t help with that",
            "i cannot help with that",
            "can t help with that",
            "cannot help with that",
            "i can t assist with that",
            "i cannot assist with that",
            "subtitulos realizados por la comunidad de amara org",
            "subtÃ­tulos realizados por la comunidad de amara org",
            "please see the complete disclaimer",
            "thank you for watching",
            "thanks for watching",
            "thank you for listening",
            "thanks for listening",
            "this video is for educational purposes only",
            "the audio is in english",
            "the audio is in spanish",
            "the audio is in english or spanish",
        }
    )

    def __init__(self):
        self.set_dpi_awareness()
        self.app_data_dir = self._get_app_data_dir()
        self.settings_path = os.path.join(self.app_data_dir, "settings.json")
        self.log_session_timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.log_retained_sessions = 5
        self.session_log_prefixes = (
            "error",
            "transcript",
            "finalized",
            "transcribed",
            "translated",
        )
        self.error_log_path = self._get_error_log_path()
        self.transcript_trace_path = self._get_transcript_trace_path()
        self.finalized_transcript_path = self._get_finalized_transcript_path()
        self.transcribed_text_path = self._get_transcribed_text_path()
        self.translated_text_path = self._get_translated_text_path()
        self._prune_old_log_sessions()
        self.logging_mode = "normal"
        self.status_log_enabled = True
        self.status_log_lock = Lock()
        self.transcript_trace_enabled = False
        self.transcript_trace_lock = Lock()
        self.finalized_transcript_lock = Lock()
        self.transcribed_text_lock = Lock()
        self.translated_text_lock = Lock()
        self.finalized_logs_enabled = True
        self.comparison_logs_enabled = False
        self.transcribed_log_sequence = 0
        self.translated_log_sequence = 0
        self.portaudio_admin_lock = Lock()
        self.last_status_message = None
        self.latency_samples = deque(maxlen=20)
        self.chunk_latency_label = None
        self.audio_level_label = None
        self.audio_level_bar = None
        self.audio_level_fill_item = None
        self.audio_level_value = 0.0
        self.audio_level_target = 0.0
        self.audio_level_last_update = 0.0
        self.audio_level_last_meter_update = 0.0
        self.audio_level_floor_db = -55.0
        self.audio_level_attack_per_second = 260.0
        self.audio_level_release_per_second = 42.0
        self.audio_level_tick_ms = 50
        self.audio_level_after_id = None
        self.audio_level_thread = None
        self.audio_level_restart_requested = False
        self._audio_level_last_error_log = 0.0
        self.no_speech_timeout_count = 0
        self.unknown_speech_count = 0
        self.root = tk.Tk()
        self.root.title("Translation Output")
        self.font_family = self.pick_font_family(
            ["DejaVu Sans", "Liberation Sans", "Arial", "Helvetica"]
        )
        self.ui_font_family = self.pick_font_family(
            ["Segoe UI", "SF Pro Text", "Inter", "Noto Sans", self.font_family]
        )
        self.style = ttkb.Style(theme="flatly")
        self.rounded_buttons_supported = False
        self.style.configure(
            "primary.TButton",
            background="#5B8FF7",
            foreground="#FFFFFF",
            font=(self.ui_font_family, 10, "bold"),
            padding=(12, 6),
        )
        self.style.map(
            "primary.TButton",
            background=[("active", "#4A7FEA"), ("pressed", "#4A7FEA")],
            foreground=[("disabled", "#FFFFFF")],
        )
        self.allow_loopback = False
        self.recommended_host_api = ""
        self.available_host_apis = []
        self._realtime_stt_defaults()
        self.text_translation_provider = "local_nllb"
        self.local_nllb_model_name = self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        self.local_nllb_device = "auto"
        self.local_nllb_source_lang = self.LOCAL_NLLB_DEFAULT_SOURCE_LANG
        self.local_nllb_target_lang = self.LOCAL_NLLB_DEFAULT_TARGET_LANG
        self.local_nllb_max_chars = self.LOCAL_NLLB_DEFAULT_MAX_CHARS
        self.local_nllb_cache_dir = ""
        self.local_nllb_tokenizer = None
        self.local_nllb_model = None
        self.local_nllb_model_config = None
        self.local_nllb_resolved_device = ""
        self.local_nllb_lock = Lock()
        self.nllb_status = "Not selected"
        self.nllb_download_in_progress = False
        self.nllb_check_in_progress = False
        self.nllb_model_loaded = False
        self.nllb_last_error = ""
        self.nllb_status_detail = ""
        self.nllb_ready_config = None
        self.local_nllb_last_unready_notice = 0.0
        self.local_nllb_status_var = None
        self.local_nllb_message_var = None
        self.local_nllb_download_button = None
        self.local_nllb_retry_button = None
        self.local_nllb_test_button = None
        self.speech_engine = "realtime-stt"
        self.stt_device = "cuda"
        self.cuda_directory = ""
        self._cuda_dll_directory_handles = []
        self.last_faster_whisper_confidence = None
        self.device_menu = None
        self.device_sample_rates_by_index = {}
        self.preferred_device_label = ""
        self.device_refresh_in_progress = False
        self.last_display_line_count = 0
        self.start_with_windows = False
        self.lock_output_focus = False
        self.sentence_buffer = ""
        self.sentence_buffer_pretranslated = False
        self.sentence_buffer_source_text = ""
        self.sentence_lock = Lock()
        self.sentence_flush_ms = 100
        self.sentence_last_update = 0.0
        self.sentence_max_chars = 200
        self.sentence_fragment_grace_ms = 250
        self.sentence_timeout_min_words = 3
        self.sentence_queue = queue.Queue(maxsize=120)
        self.sentence_queue_high_water_ratio = 0.75
        self.sentence_queue_relief_ratio = 0.5
        self.translation_backlog_batch_max = 4
        self.finalized_output_queue = queue.Queue(maxsize=180)
        self.finalized_output_queue_high_water_ratio = 0.8
        self.finalized_output_queue_relief_ratio = 0.6
        self.queue_backpressure_notice_interval_sec = 2.5
        self.last_queue_backpressure_notice = 0.0
        self.translation_thread = None
        self.display_thread = None
        self.last_stt_pretranslated = False
        self.last_stt_source_text = ""
        self.last_stt_source_lang = ""
        self.last_stt_source_lang_confidence = None
        self.preview_widget = None
        self.preview_font = None
        self.preview_placeholder = "Preview will appear here."
        self.settings_geometry = None
        self.settings_monitor_index = 0
        self.settings_monitor_device = ""
        self.settings_monitor_origin = ""
        self.monitor_device = ""
        self.monitor_origin = ""
        self.monitor_id_windows = []
        self.monitor_index = 0
        self.monitors = self.get_monitors()
        self.devices = []
        self.microphone_index = None
        
        # Restore window manager controls for reliability.
        self.root.overrideredirect(False)
        self.empty_menubar = tk.Menu(self.root)
        self.menubar = None
        self.root.config(menu=self.empty_menubar)
        
        self.root.grid_rowconfigure(0, weight=8)  # 80% height for text
        self.root.grid_rowconfigure(1, weight=0)  # status line
        self.root.grid_columnconfigure(0, weight=1)
        
        self.bg_color = "#000000"  # Background color
        self.text_color = "#ffffff"  # Text color
        self.font_size = 50  # Font size

        self.text_font = tkfont.Font(family=self.font_family, size=self.font_size)
        self.canvas_margin = 10
        self.text_canvas = tk.Canvas(self.root, bg=self.bg_color, highlightthickness=0)
        self.text_canvas.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=self.canvas_margin,
            pady=self.canvas_margin,
        )
        self.text_padding = 12
        self.min_chars_per_line = 40
        self.text_item = self.text_canvas.create_text(
            self.text_padding,
            0,
            anchor="sw",
            text="",
            fill=self.text_color,
            font=self.text_font,
            width=0,
        )
        self.text_line_items = []
        self.text_canvas.bind(self.CONFIGURE_EVENT, self.on_canvas_resize)
        self._resize_after_id = None
        
        self.status_label = None
        self.status_hide_after_id = None
        self.overlay_visible = False

        self.root.config(menu=self.empty_menubar)
        
        self.apply_colors()  # Apply default colors
        
        self.is_fullscreen = True
        self.use_custom_fullscreen = os.name == "nt"
        self.prev_geometry = None
        self.prev_overrideredirect = None
        self.prev_topmost = None
        if self.is_fullscreen:
            self.root.after(0, self.enter_fullscreen)
        else:
            self.root.after(0, self.maximize_window)
        self.root.after(50, self.show_status_temporarily)
        self.root.bind_all("<F11>", self.toggle_fullscreen_event)
        self.root.bind_all("<Control-Alt-f>", self.toggle_fullscreen_event)
        self.root.bind_all("<Escape>", self.toggle_fullscreen_event)
        self.root.bind_all("<Control-s>", self.open_settings_event)
        self.root.bind_all("<Control-q>", self.close_app_event)
        self.root.focus_set()
        self.listening = True
        self.translations = []
        self.max_lines = 8  # Default number of lines
        self.bad_words_by_lang = {
            "en": set(self.default_bad_words_en()),
            "es": set(self.default_bad_words_es()),
        }
        self.bad_word_filters_enabled = {"en": True, "es": True}
        self.active_bad_words = set()
        self.custom_vocab_langs_enabled = {"en": True, "es": True}
        self.settings_window = None
        self.is_applying_settings = False
        self.text_queue = deque()
        self.is_flushing_queue = False
        self.word_by_word = True
        self.word_reveal_queue = deque()
        self.is_revealing_words = False
        self.live_line = ""
        self.current_reveal_words = []
        self.current_reveal_text = ""
        self.current_reveal_latency_meta = None
        self.chunk_size = 120
        self.chunk_delay_ms = 90
        self.flush_timeout_ms = 1400
        self.display_speed_factor = 1.0
        self.dynamic_fast_display_enabled = True
        self.fast_display_hold_sec = 2.0
        self.fast_display_until = 0.0
        self.fast_display_sentence_queue_ratio = 0.25
        self.fast_display_reveal_queue_items = 2
        self.fast_display_translate_ms = 1200
        self.pending_text = ""
        self.pending_latency_meta = None
        self.last_openai_translate_ms = None
        self.flush_after_id = None
        self.source_lang = "auto"
        self.target_lang = "en"
        self.auto_detect_langs = ["en", "es"]
        self.auto_detect_lang = None
        self.auto_detect_streak_lang = None
        self.auto_detect_streak_count = 0
        self.english_common_words = {
            "the", "and", "to", "of", "in", "that", "it", "is", "for",
            "on", "with", "as", "was", "are", "be", "this", "from", "by",
            "or", "not", "have", "you", "we", "they", "he", "she", "his",
            "her", "their", "what", "which", "when", "who", "how", "all",
            "one", "about", "would", "can", "will",
        }
        self.spanish_common_words = {
            "el", "la", "los", "las", "de", "que", "y", "en", "un", "una",
            "con", "por", "para", "del", "se", "al", "lo", "como", "mÃ¡s",
            "pero", "sus", "le", "ya", "o", "este", "sÃ­", "porque", "esta",
            "son", "entre", "cuando", "muy", "sin", "sobre", "tambiÃ©n",
            "me", "hasta", "hay", "donde", "quien", "desde", "todo", "nos",
            "durante", "todos", "uno", "les", "ni", "contra", "otros",
            "fue", "ese", "eso", "habÃ­a", "ante", "ellos",
        }
        self.custom_vocabulary_by_lang = {
            "en": self.default_biblical_terms(),
            "es": self.default_biblical_terms_es(),
        }
        self.biblical_books = self.default_biblical_books()
        self.spanish_bible_name_map = {}
        self.spanish_bible_pattern = self._build_spanish_bible_pattern()
        self.translation_enabled = False
        self.auto_switch_translation = False
        self.is_paused = False
        self.text_bbox_height = 0
        self.load_settings()
        self._sync_windows_startup_entry()
        self._refresh_bad_words()
        self.devices = self.get_audio_devices()
        self.microphone_index = 0 if self.devices else None
        self._apply_scaled_fonts()
        self.apply_colors()
        self.render_text()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self._install_exception_hook()
        
        self.open_settings()
        if self.settings_window is not None and self.settings_window.winfo_exists():
            try:
                self.settings_window.focus_force()
            except Exception:
                pass

        self.translation_thread = Thread(target=self._translation_worker, daemon=True)
        self.translation_thread.start()
        self.display_thread = Thread(target=self._display_worker, daemon=True)
        self.display_thread.start()
        self.thread = Thread(target=self.listen_and_translate)
        self.thread.daemon = True
        self.thread.start()
        self._start_audio_level_stream_thread()
        
        self.root.mainloop()
    
    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.enter_fullscreen()
            self.hide_status()
        else:
            self.exit_fullscreen()
            self.show_status_temporarily()

    def maximize_window(self):
        # Move to the selected output monitor before maximizing.
        try:
            try:
                self.root.state("normal")
            except Exception:
                pass
            self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
            self.root.update_idletasks()
        except Exception:
            pass
        if os.name == "nt":
            try:
                self.root.state("zoomed")
            except Exception:
                pass
        else:
            try:
                self.root.attributes("-zoomed", True)
            except Exception:
                pass

    def toggle_fullscreen_event(self, event):
        self.toggle_fullscreen()

    def open_settings_event(self, event):
        self.show_status_temporarily()
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
        else:
            self.open_settings()
        return "break"

    def close_app_event(self, event):
        self.on_closing()
        return "break"

    def show_status_temporarily(self, duration_ms=None):
        if self.overlay_visible or self.status_label is None:
            return
        self.overlay_visible = True
        if self.menubar is not None:
            self.root.config(menu=self.menubar)
        if duration_ms is not None:
            if self.status_hide_after_id is not None:
                self.root.after_cancel(self.status_hide_after_id)
            self.status_hide_after_id = self.root.after(duration_ms, self.hide_status)

    def hide_status(self):
        if self.status_label is None:
            return
        self.root.config(menu=self.empty_menubar)
        if self.status_hide_after_id is not None:
            self.root.after_cancel(self.status_hide_after_id)
        self.status_hide_after_id = None
        self.overlay_visible = False

    def pick_font_family(self, candidates):
        available = set(tkfont.families())
        for name in candidates:
            if name in available:
                return name
        return "TkDefaultFont"
    
    def on_closing(self):
        try:
            self._log_status("App closing requested")
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write("\n--- Close Requested ---\n")
                f.write("".join(traceback.format_stack(limit=10)))
        except Exception:
            pass
        self.listening = False
        try:
            self._stop_realtime_stt()
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        # Hard-exit so RealtimeSTT's multiprocessing child processes are also
        # killed. root.quit() alone only exits the Tkinter loop; os._exit()
        # terminates the entire process tree immediately.
        import os as _os
        _os._exit(0)


if __name__ == "__main__":
    app = TranslationApp()
