import speech_recognition as sr
from googletrans import Translator
import tkinter as tk
from tkinter import messagebox
from tkinter import colorchooser
from tkinter import font as tkfont
from threading import Thread, Lock, Event
import queue
import time
import re
import requests
import audioop
import json
import pyaudio
from collections import deque
import os
import sys
import traceback
import io
import math
import tempfile
import ttkbootstrap as ttkb
from ttkbootstrap.constants import PRIMARY


class Tooltip:
    def __init__(self, widget, text, delay_ms=400):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.tipwindow = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _event=None):
        self._cancel()
        self.after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def _show(self):
        if self.tipwindow or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 22
        except Exception:
            x, y = 0, 0
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#111111",
            foreground="#ffffff",
            relief="solid",
            borderwidth=1,
            wraplength=320,
            font=("TkDefaultFont", 9),
        )
        label.pack(ipadx=6, ipady=4)

    def _hide(self, _event=None):
        self._cancel()
        if self.tipwindow is not None:
            try:
                self.tipwindow.destroy()
            except Exception:
                pass
            self.tipwindow = None

class TranslationApp:
    SCROLL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")
    CONFIGURE_EVENT = "<Configure>"
    STATUS_LISTENING = "Listening..."

    def __init__(self):
        self.set_dpi_awareness()
        self.settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
        self.error_log_path = self._get_error_log_path()
        self.status_log_enabled = True  # TEMP: set False to disable status logging
        self.status_log_lock = Lock()
        self.last_status_message = None
        self.last_status_log_time = 0.0
        self.latency_samples = deque(maxlen=20)
        self.chunk_latency_label = None
        self.audio_queue = queue.Queue(maxsize=50)
        self.capture_thread = None
        self.capture_restart_requested = False
        self.capture_suspend_event = Event()
        self.capture_suspended_event = Event()
        self.active_device_index = None
        self.listener_restart_min_interval = 2.0
        self.listener_restart_time = 0.0
        self.last_audio_time = 0.0
        self.no_speech_timeout_count = 0
        self.last_no_speech_notice = 0.0
        self.unknown_speech_count = 0
        self.last_unknown_notice = 0.0
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
        try:
            self.style.configure(
                "primary.round.TButton",
                background="#5B8FF7",
                foreground="#FFFFFF",
                font=(self.ui_font_family, 10, "bold"),
                padding=(12, 6),
            )
            self.style.map(
                "primary.round.TButton",
                background=[("active", "#4A7FEA"), ("pressed", "#4A7FEA")],
                foreground=[("disabled", "#FFFFFF")],
            )
            self.rounded_buttons_supported = True
        except Exception:
            self.rounded_buttons_supported = False
        self.translator = Translator()
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 0.85
        self.recognizer.non_speaking_duration = 0.4
        self.recognizer.phrase_threshold = 0.2
        self.allow_loopback = False
        self.loopback_chunk_seconds = 1.0
        self.phrase_time_limit = 10.0
        self.recommended_host_api = ""
        self.available_host_apis = []
        self.openai_api_key = ""
        self.openai_translate_model = "gpt-4o-mini"
        self.speech_engine = "openai"
        self.faster_whisper_model_name = "medium"
        self.faster_whisper_compute_type = "float16"
        self.faster_whisper_device = "cuda"
        self.faster_whisper_model = None
        self.faster_whisper_model_config = None
        self.device_menu = None
        self.device_sample_rates_by_index = {}
        self.preferred_device_label = ""
        self.device_refresh_in_progress = False
        self._scaled_font_size = None
        self.last_display_line_count = 0
        self.rms_gate_enabled = False
        self.rms_gate_factor = 1.0
        self.sentence_buffer = ""
        self.sentence_lock = Lock()
        self.sentence_flush_ms = 800
        self.sentence_last_update = 0.0
        self.sentence_max_chars = 200
        self.sentence_queue = queue.Queue(maxsize=50)
        self.translation_thread = None
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
        self.chunk_size = 120
        self.chunk_delay_ms = 300
        self.flush_timeout_ms = 2000
        self.pending_text = ""
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
            "con", "por", "para", "del", "se", "al", "lo", "como", "más",
            "pero", "sus", "le", "ya", "o", "este", "sí", "porque", "esta",
            "son", "entre", "cuando", "muy", "sin", "sobre", "también",
            "me", "hasta", "hay", "donde", "quien", "desde", "todo", "nos",
            "durante", "todos", "uno", "les", "ni", "contra", "otros",
            "fue", "ese", "eso", "había", "ante", "ellos",
        }
        self.custom_vocabulary_by_lang = {
            "en": self.default_biblical_terms(),
            "es": self.default_biblical_terms_es(),
        }
        self.biblical_books = self.default_biblical_books()
        self.spanish_bible_name_map = self.default_spanish_bible_map()
        self.spanish_bible_pattern = self._build_spanish_bible_pattern()
        self.translation_enabled = False
        self.auto_switch_translation = False
        self.readability_preset = "medium"
        self.viewing_distance_ft = 10.0
        self.is_paused = False
        self.text_bbox_height = 0
        self.load_settings()
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
        self.thread = Thread(target=self.listen_and_translate)
        self.thread.daemon = True
        self.thread.start()
        
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

    def exit_fullscreen_event(self, event):
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.exit_fullscreen()
        return "break"

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
        self.root.quit()

    def _install_exception_hook(self):
        def handle_exception(exc_type, exc, tb):
            try:
                with open(self.error_log_path, "a", encoding="utf-8") as f:
                    f.write("\n--- Unhandled Exception ---\n")
                    traceback.print_exception(exc_type, exc, tb, file=f)
            except Exception:
                pass
            try:
                messagebox.showerror("Unhandled Error", f"{exc}")
            except Exception:
                pass
        sys.excepthook = handle_exception
        try:
            self.root.report_callback_exception = handle_exception
        except Exception:
            pass
        try:
            import threading

            def _thread_excepthook(args):
                try:
                    with open(self.error_log_path, "a", encoding="utf-8") as f:
                        f.write("\n--- Thread Exception ---\n")
                        f.write(f"Thread: {args.thread.name}\n")
                        traceback.print_exception(
                            args.exc_type,
                            args.exc_value,
                            args.exc_traceback,
                            file=f,
                        )
                except Exception:
                    pass

            threading.excepthook = _thread_excepthook
        except Exception:
            pass
        try:
            import faulthandler

            if not getattr(self, "_fault_log_file", None):
                self._fault_log_file = open(self.error_log_path, "a", encoding="utf-8")
            faulthandler.enable(self._fault_log_file, all_threads=True)
        except Exception:
            pass
        try:
            import atexit

            atexit.register(self._log_process_exit)
        except Exception:
            pass

    def _log_process_exit(self):
        try:
            self._log_status("Process exiting")
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write("\n--- Process Exit ---\n")
                f.write("".join(traceback.format_stack(limit=10)))
        except Exception:
            pass

    def _get_error_log_path(self):
        base_dir = os.path.dirname(__file__)
        if os.name == "nt":
            logs_dir = os.path.join(base_dir, "logs")
            try:
                os.makedirs(logs_dir, exist_ok=True)
            except Exception:
                pass
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            return os.path.join(logs_dir, f"error-{timestamp}.log")
        return os.path.join(base_dir, "error.log")

    def _log_status(self, msg):
        if not self.status_log_enabled:
            return
        now = time.time()
        if msg == self.last_status_message:
            return
        self.last_status_message = msg
        self.last_status_log_time = now
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            ms = int((now - int(now)) * 1000)
            with self.status_log_lock:
                with open(self.error_log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}.{ms:03d}] STATUS: {msg}\n")
        except Exception:
            pass

    def load_settings(self):
        if not os.path.exists(self.settings_path):
            return
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self.openai_api_key = data.get("openai_api_key", self.openai_api_key)
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
        self.bg_color = data.get("bg_color", self.bg_color)
        self.text_color = data.get("text_color", self.text_color)
        self.max_lines = data.get("max_lines", self.max_lines)
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
        self.chunk_size = data.get("chunk_size", self.chunk_size)
        self.chunk_delay_ms = data.get("chunk_delay_ms", self.chunk_delay_ms)
        self.flush_timeout_ms = data.get("flush_timeout_ms", self.flush_timeout_ms)
        self.sentence_flush_ms = data.get("sentence_flush_ms", self.sentence_flush_ms)
        self.source_lang = data.get("source_lang", self.source_lang)
        self.target_lang = data.get("target_lang", self.target_lang)
        self.translation_enabled = bool(data.get("translation_enabled", self.translation_enabled))
        self.auto_switch_translation = bool(
            data.get("auto_switch_translation", self.auto_switch_translation)
        )
        self.readability_preset = str(
            data.get("readability_preset", self.readability_preset)
        ).lower()
        try:
            self.viewing_distance_ft = float(
                data.get("viewing_distance_ft", self.viewing_distance_ft)
            )
        except Exception:
            pass
        if not self.auto_switch_translation and (self.source_lang or "").strip().lower() == "auto":
            self.source_lang = "en"
        self.biblical_books = data.get("biblical_books", self.biblical_books)
        self.preferred_device_label = data.get(
            "preferred_device_label", self.preferred_device_label
        )
        self.rms_gate_enabled = bool(data.get("rms_gate_enabled", self.rms_gate_enabled))
        self.rms_gate_factor = float(data.get("rms_gate_factor", self.rms_gate_factor))
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
            "openai_api_key": self.openai_api_key,
            "speech_engine": self.speech_engine,
            "faster_whisper_model_name": self.faster_whisper_model_name,
            "faster_whisper_compute_type": self.faster_whisper_compute_type,
            "faster_whisper_device": self.faster_whisper_device,
            "bg_color": self.bg_color,
            "text_color": self.text_color,
            "max_lines": self.max_lines,
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
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "translation_enabled": self.translation_enabled,
            "auto_switch_translation": self.auto_switch_translation,
            "readability_preset": self.readability_preset,
            "viewing_distance_ft": self.viewing_distance_ft,
            "biblical_books": self.biblical_books,
            "preferred_device_label": self.preferred_device_label,
            "rms_gate_enabled": self.rms_gate_enabled,
            "rms_gate_factor": self.rms_gate_factor,
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

    def close_settings_window(self):
        if self.settings_window is None:
            return
        if not self.settings_window.winfo_exists():
            self.settings_window = None
            return
        try:
            self.settings_geometry = self.settings_window.geometry()
        except Exception:
            pass
        try:
            for event_name in self.SCROLL_EVENTS:
                self.settings_window.unbind_all(event_name)
        except Exception:
            pass
        self.settings_window.destroy()
        self.settings_window = None
        self.preview_widget = None

    def get_monitors(self):
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG),
                    ]

                class MONITORINFOEXW(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", RECT),
                        ("rcWork", RECT),
                        ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32),
                    ]

                user32 = ctypes.windll.user32
                monitors = []

                MONITORENUMPROC = ctypes.WINFUNCTYPE(
                    wintypes.BOOL,
                    wintypes.HMONITOR,
                    wintypes.HDC,
                    ctypes.POINTER(RECT),
                    wintypes.LPARAM,
                )

                def _callback(h_monitor, hdc, lprc, lparam):
                    info = MONITORINFOEXW()
                    info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                    if user32.GetMonitorInfoW(h_monitor, ctypes.byref(info)):
                        monitors.append(
                            {
                                "left": info.rcMonitor.left,
                                "top": info.rcMonitor.top,
                                "right": info.rcMonitor.right,
                                "bottom": info.rcMonitor.bottom,
                                "device": info.szDevice,
                                "primary": bool(info.dwFlags & 1),
                            }
                        )
                    return True

                user32.EnumDisplayMonitors(
                    0,
                    0,
                    MONITORENUMPROC(_callback),
                    0,
                )

                if monitors:
                    return self._sync_monitor_indices(monitors)
            except Exception:
                pass

        try:
            self.root.update_idletasks()
            width = self.root.winfo_screenwidth()
            height = self.root.winfo_screenheight()
        except Exception:
            width = 1920
            height = 1080
        return self._sync_monitor_indices(
            [{"left": 0, "top": 0, "right": width, "bottom": height, "device": "", "primary": True}]
        )

    def set_dpi_awareness(self):
        if os.name != "nt":
            return
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
                return
            except Exception:
                pass
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        except Exception:
            pass

    def get_monitor_labels(self):
        labels = []
        for i, monitor in enumerate(self.monitors):
            width = monitor["right"] - monitor["left"]
            height = monitor["bottom"] - monitor["top"]
            origin = f'{monitor["left"]},{monitor["top"]}'
            primary = " primary" if monitor.get("primary") else ""
            labels.append(f"Monitor {i + 1} ({width}x{height} @ {origin}{primary})")
        return labels

    def _monitor_origin(self, monitor):
        return f'{monitor.get("left", 0)},{monitor.get("top", 0)}'

    def _find_monitor_index_for_point(self, x, y):
        for i, monitor in enumerate(self.monitors or []):
            if (
                monitor.get("left", 0) <= x < monitor.get("right", 0)
                and monitor.get("top", 0) <= y < monitor.get("bottom", 0)
            ):
                return i
        return None

    def _parse_geometry(self, geometry):
        if not geometry:
            return None
        match = re.match(r"(?:(\d+)x(\d+))?([+-]\d+)([+-]\d+)", str(geometry))
        if not match:
            return None
        width = int(match.group(1)) if match.group(1) else None
        height = int(match.group(2)) if match.group(2) else None
        x = int(match.group(3))
        y = int(match.group(4))
        return width, height, x, y

    def _monitor_identity_for_index(self, monitor_index):
        if not self.monitors:
            return "", ""
        idx = max(0, min(int(monitor_index or 0), len(self.monitors) - 1))
        monitor = self.monitors[idx]
        return monitor.get("device", ""), self._monitor_origin(monitor)

    def _resolve_monitor_index(self, saved_index, saved_device, saved_origin):
        if not self.monitors:
            return max(0, int(saved_index or 0))
        if saved_device:
            for i, monitor in enumerate(self.monitors):
                if monitor.get("device") == saved_device:
                    return i
        if saved_origin:
            for i, monitor in enumerate(self.monitors):
                if self._monitor_origin(monitor) == saved_origin:
                    return i
        return max(0, min(int(saved_index or 0), len(self.monitors) - 1))

    def _sync_monitor_indices(self, monitors):
        self.monitors = monitors or []
        if not self.monitors:
            return self.monitors
        self.monitor_index = self._resolve_monitor_index(
            self.monitor_index, self.monitor_device, self.monitor_origin
        )
        self.settings_monitor_index = self._resolve_monitor_index(
            self.settings_monitor_index,
            self.settings_monitor_device,
            self.settings_monitor_origin,
        )
        return self.monitors

    def show_monitor_ids(self, duration_ms=2000):
        self.monitors = self.get_monitors()
        if not self.monitors:
            return
        # Close any existing overlays.
        for win in tuple(self.monitor_id_windows):
            try:
                win.destroy()
            except Exception:
                pass
        self.monitor_id_windows = []

        for i, monitor in enumerate(self.monitors):
            width = max(1, monitor["right"] - monitor["left"])
            height = max(1, monitor["bottom"] - monitor["top"])
            x = monitor["left"]
            y = monitor["top"]
            overlay = tk.Toplevel(self.root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.configure(bg="#000000")
            overlay.geometry(f"{width}x{height}+{x}+{y}")
            label = tk.Label(
                overlay,
                text=str(i + 1),
                fg="#ffffff",
                bg="#000000",
                font=(self.font_family, max(80, int(min(width, height) * 0.2)), "bold"),
            )
            label.place(relx=0.5, rely=0.5, anchor="center")
            self.monitor_id_windows.append(overlay)

        def close_overlays():
            for win in tuple(self.monitor_id_windows):
                try:
                    win.destroy()
                except Exception:
                    pass
            self.monitor_id_windows = []

        self.root.after(duration_ms, close_overlays)

    def apply_monitor_geometry(self):
        self.monitors = self.get_monitors()
        if not self.monitors:
            return
        self.monitor_index = max(0, min(self.monitor_index, len(self.monitors) - 1))
        idx = max(0, min(self.monitor_index, len(self.monitors) - 1))
        monitor = self.monitors[idx]
        width = monitor["right"] - monitor["left"]
        height = monitor["bottom"] - monitor["top"]
        x = monitor["left"]
        y = monitor["top"]
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def move_window_to_monitor(self, window, monitor_index, keep_size=True):
        self.monitors = self.get_monitors()
        if not self.monitors or window is None:
            return
        idx = max(0, min(monitor_index, len(self.monitors) - 1))
        monitor = self.monitors[idx]
        width = monitor["right"] - monitor["left"]
        height = monitor["bottom"] - monitor["top"]
        x = monitor["left"]
        y = monitor["top"]

        if keep_size:
            try:
                window.update_idletasks()
                w = window.winfo_width()
                h = window.winfo_height()
            except Exception:
                w, h = width, height
            x = monitor["left"] + max(0, (width - w) // 2)
            y = monitor["top"] + max(0, (height - h) // 2)
            window.geometry(f"{w}x{h}+{x}+{y}")
        else:
            window.geometry(f"{width}x{height}+{x}+{y}")

    def _move_settings_window_to_monitor(self, monitor_index):
        if self.settings_window is None or not self.settings_window.winfo_exists():
            return
        window = self.settings_window
        prev_state = None
        try:
            prev_state = window.state()
        except Exception:
            prev_state = None

        if prev_state in ("zoomed", "maximized"):
            try:
                window.state("normal")
                window.update_idletasks()
            except Exception:
                pass

            def do_move():
                if not window.winfo_exists():
                    return
                self.move_window_to_monitor(window, monitor_index, keep_size=True)

                def do_zoom():
                    if not window.winfo_exists():
                        return
                    try:
                        window.state("zoomed")
                    except Exception:
                        pass

                window.after(80, do_zoom)

            window.after(60, do_move)
            return

        self.move_window_to_monitor(window, monitor_index, keep_size=True)

    def enter_fullscreen(self):
        if not self.is_fullscreen:
            return
        if self.prev_geometry is None:
            self.prev_geometry = self.root.geometry()
        if self.use_custom_fullscreen:
            self._prepare_custom_fullscreen_state()
            self._apply_custom_fullscreen()
        else:
            self._prepare_borderless_fullscreen_state()
            self._apply_standard_fullscreen()
        self._apply_canvas_padding()

    def _prepare_borderless_fullscreen_state(self):
        if self.prev_overrideredirect is None:
            try:
                self.prev_overrideredirect = bool(self.root.overrideredirect())
            except Exception:
                self.prev_overrideredirect = False
        try:
            self.root.overrideredirect(True)
        except Exception:
            pass

    def _prepare_custom_fullscreen_state(self):
        if self.prev_overrideredirect is None:
            try:
                self.prev_overrideredirect = bool(self.root.overrideredirect())
            except Exception:
                self.prev_overrideredirect = False
        if self.prev_topmost is None:
            try:
                self.prev_topmost = bool(self.root.attributes("-topmost"))
            except Exception:
                self.prev_topmost = False
        try:
            self.root.state("normal")
        except Exception:
            pass

    def _apply_custom_fullscreen(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-fullscreen", False)
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        self.root.update_idletasks()
        # Apply twice to avoid position being offset by window manager.
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)

    def _apply_standard_fullscreen(self):
        # Some window managers ignore geometry changes while fullscreen is active.
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        self.root.update_idletasks()
        self.root.attributes("-fullscreen", True)

    def exit_fullscreen(self):
        if self.use_custom_fullscreen:
            self.root.attributes("-fullscreen", False)
            if self.prev_topmost is not None:
                try:
                    self.root.attributes("-topmost", self.prev_topmost)
                except Exception:
                    pass
        else:
            self.root.attributes("-fullscreen", False)
        if self.prev_overrideredirect is not None:
            try:
                self.root.overrideredirect(self.prev_overrideredirect)
            except Exception:
                pass
        if self.prev_geometry:
            self.root.geometry(self.prev_geometry)
        self.prev_geometry = None
        self._apply_canvas_padding()
    
    def get_audio_devices(self):
        device_infos, input_devices, output_devices = self._get_device_infos()
        self.device_sample_rates_by_index = {}
        for info in device_infos:
            rate = info.get("default_sample_rate")
            if rate:
                try:
                    self.device_sample_rates_by_index[info["index"]] = int(rate)
                except Exception:
                    pass
        self.available_host_apis = self._get_available_host_apis(device_infos)
        loopback_inputs = self._get_loopback_inputs(device_infos)
        self.recommended_host_api = self._pick_recommended_host_api(self.available_host_apis)
        return self._build_device_list(input_devices, output_devices, loopback_inputs)

    def _get_device_infos(self):
        p = pyaudio.PyAudio()
        input_devices = []
        output_devices = []
        device_infos = []

        try:
            for i in range(p.get_device_count()):
                try:
                    device_info = p.get_device_info_by_index(i)
                except OSError:
                    continue
                host_api_name = self._get_host_api_name(p, device_info)
                entry = {
                    "index": i,
                    "name": device_info.get("name", "Unknown"),
                    "max_input": device_info.get("maxInputChannels", 0),
                    "max_output": device_info.get("maxOutputChannels", 0),
                    "host_api": host_api_name,
                    "default_sample_rate": device_info.get("defaultSampleRate"),
                }
                device_infos.append(entry)
                if entry["max_input"] > 0:
                    input_devices.append(entry)
                elif entry["max_output"] > 0:
                    output_devices.append(entry)
        finally:
            p.terminate()
        return device_infos, input_devices, output_devices

    def _get_host_api_name(self, pyaudio_instance, device_info):
        host_api_index = device_info.get("hostApi")
        if host_api_index is None:
            return ""
        try:
            return pyaudio_instance.get_host_api_info_by_index(host_api_index).get("name", "")
        except Exception:
            return ""

    def _get_available_host_apis(self, device_infos):
        host_apis = {info.get("host_api") for info in device_infos if info.get("host_api")}
        return sorted(host_apis, key=lambda name: name.lower())

    def _first_host_api_keyword(self, lowered, keywords):
        for keyword in keywords:
            for key, value in lowered.items():
                if keyword in key:
                    return value
        return ""

    def _pick_host_api_windows(self, lowered):
        return self._first_host_api_keyword(lowered, ["wasapi", "asio", "wdm", "ks"])

    def _pick_host_api_darwin(self, lowered):
        for key, value in lowered.items():
            if "core" in key and "audio" in key:
                return value
        return ""

    def _pick_host_api_linux(self, lowered):
        return self._first_host_api_keyword(lowered, ["pipewire", "alsa", "pulse"])

    def _pick_recommended_host_api(self, host_api_values):
        values = [v for v in host_api_values if v and v != "Any"]
        lowered = {v.lower(): v for v in values}
        if os.name == "nt":
            return self._pick_host_api_windows(lowered)
        if sys.platform == "darwin":
            return self._pick_host_api_darwin(lowered)
        return self._pick_host_api_linux(lowered)

    def _normalize_host_api(self, name):
        return (name or "").strip().lower()

    def _filter_entries_by_host_api(self, entries, preferred_api):
        preferred = self._normalize_host_api(preferred_api)
        if not preferred:
            return entries
        return [
            entry
            for entry in entries
            if self._normalize_host_api(entry.get("host_api")) == preferred
        ]

    def _is_loopback_name(self, name):
        lowered = name.lower()
        keywords = ["loopback", "stereo mix", "what u hear", "what you hear"]
        return any(k in lowered for k in keywords)

    def _is_loopback_label(self, label):
        if not label:
            return False
        lowered = label.lower()
        keywords = ["loopback", "stereo mix", "what u hear", "what you hear"]
        return any(k in lowered for k in keywords)

    def _normalize_device_name(self, name):
        lowered = name.lower()
        lowered = re.sub(r"\([^)]*\)", "", lowered)
        lowered = re.sub(r"\b(loopback|stereo mix|what u hear|what you hear)\b", "", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered.strip()

    def _sort_device_entries(self, entries):
        return sorted(
            entries,
            key=lambda entry: (
                (entry.get("host_api") or "").lower(),
                self._normalize_device_name(entry.get("name", "")),
                entry.get("index", 0),
            ),
        )

    def _extract_device_label_name(self, label):
        if not label:
            return ""
        if "]: " in label:
            return label.split("]: ", 1)[1]
        if ": " in label:
            return label.split(": ", 1)[1]
        return label

    def _resolve_preferred_device_label(self, preferred_label):
        if not preferred_label:
            return ""
        if preferred_label in self.devices:
            return preferred_label
        preferred_type = None
        if preferred_label.startswith("Input"):
            preferred_type = "input"
        elif preferred_label.startswith("Output"):
            preferred_type = "output"
        preferred_name = self._extract_device_label_name(preferred_label)
        preferred_norm = self._normalize_device_name(preferred_name)
        if not preferred_norm:
            return ""
        for label in self.devices:
            if preferred_type and self.device_types.get(label) != preferred_type:
                continue
            label_name = self._extract_device_label_name(label)
            if self._normalize_device_name(label_name) == preferred_norm:
                return label
        return ""

    def _get_loopback_inputs(self, device_infos):
        loopback_inputs = []
        for info in device_infos:
            if info["max_input"] > 0 and self._is_loopback_name(info["name"]):
                loopback_inputs.append(info)
        return loopback_inputs

    def _register_device_label(self, devices, label, idx, device_type):
        devices.append(label)
        self.device_indices[label] = idx
        self.device_types[label] = device_type

    def _format_device_label(self, device_type, entry):
        host_api = entry.get("host_api") or "Unknown"
        recommended = self._normalize_host_api(self.recommended_host_api)
        if recommended and self._normalize_host_api(host_api) == recommended:
            host_api = f"{host_api} (Recommended)"
        index = entry.get("index", 0)
        name = entry.get("name", "Unknown")
        return f"{device_type} ({index}) [{host_api}]: {name}"

    def _group_device_entries(self, entries):
        return sorted(entries, key=lambda item: item.get("index", 0))

    def _resolve_loopback_match(self, output_name, loopback_inputs):
        matched_input = None
        norm_out = self._normalize_device_name(output_name)
        for candidate in loopback_inputs:
            norm_in = self._normalize_device_name(candidate["name"])
            if norm_out and (norm_out in norm_in or norm_in in norm_out):
                matched_input = candidate["index"]
                break
        if matched_input is None and len(loopback_inputs) == 1:
            matched_input = loopback_inputs[0]["index"]
        return matched_input

    def _build_device_list(self, input_devices, output_devices, loopback_inputs):
        # Include input devices and optionally output devices (for loopback/monitor sources).
        devices = []
        self.device_indices = {}
        self.device_types = {}
        self.loopback_output_map = {}

        for entry in self._group_device_entries(self._sort_device_entries(input_devices)):
            label = self._format_device_label("Input", entry)
            self._register_device_label(devices, label, entry.get("index", 0), "input")

        if self.allow_loopback:
            for entry in self._group_device_entries(self._sort_device_entries(output_devices)):
                label = self._format_device_label("Output", entry)
                self._register_device_label(devices, label, entry.get("index", 0), "output")

                matched_input = self._resolve_loopback_match(entry.get("name", ""), loopback_inputs)
                if matched_input is not None:
                    self.loopback_output_map[label] = matched_input

        return devices if devices else ["No devices found"]
    
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
        dirty_ready = False
        applied_snapshot = None
        dirty_state = {"value": False}
        tracked_getters = []

        def _track_var(var):
            tracked_getters.append(lambda var=var: var.get())
            var.trace_add("write", lambda *_args: update_dirty_state())

        def _track_text(widget):
            tracked_getters.append(
                lambda widget=widget: widget.get("1.0", "end").strip()
            )

            def on_modified(_event, widget=widget):
                if widget.edit_modified():
                    widget.edit_modified(False)
                    update_dirty_state()

            widget.bind("<<Modified>>", on_modified)
            widget.edit_modified(False)

        def _collect_settings_vars(mapping):
            for value in mapping.values():
                if isinstance(value, tk.Variable):
                    _track_var(value)
                elif isinstance(value, tk.Text):
                    _track_text(value)

        def _capture_snapshot():
            snapshot = []
            for getter in tracked_getters:
                try:
                    snapshot.append(getter())
                except Exception:
                    snapshot.append(None)
            return snapshot
        
        def save_settings():
            nonlocal applied_snapshot
            if self.is_applying_settings:
                return
            self.is_applying_settings = True
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
                applied_snapshot = _capture_snapshot()
                self._log_status("Apply finished")
            except Exception as exc:
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
            finally:
                self.is_applying_settings = False
                update_dirty_state(force=True)

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
            text="Chunk latency: -- ms",
            anchor="w",
            bg=section_bg,
            fg=settings_fg,
            font=(self.ui_font_family, 9),
            bd=0,
            highlightthickness=0,
        )
        self.chunk_latency_label.pack(fill=tk.X, pady=(4, 0))
        pending_label = tk.Label(
            status_section,
            text="Pending changes: No",
            anchor="w",
            bg=section_bg,
            fg=palette["muted_text"],
            font=(self.ui_font_family, 9),
            bd=0,
            highlightthickness=0,
        )
        pending_label.pack(fill=tk.X, pady=(2, 0))

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
            command=save_settings,
            primary=True,
        )
        try:
            save_button.configure(takefocus=0)
        except Exception:
            pass
        save_button.pack(side=tk.RIGHT, padx=10, pady=10)

        if self.rounded_buttons_supported:
            primary_style = "primary,round"
            normal_style = "round"
        else:
            primary_style = PRIMARY
            normal_style = None

        def set_dirty_state(is_dirty, force=False):
            if not force and is_dirty == dirty_state["value"]:
                return
            dirty_state["value"] = is_dirty
            try:
                if is_dirty:
                    pending_label.config(text="Pending changes: Yes", fg=palette["accent"])
                    save_button.config(bootstyle=primary_style)
                else:
                    pending_label.config(text="Pending changes: No", fg=palette["muted_text"])
                    save_button.config(bootstyle=normal_style)
            except Exception:
                pass
            try:
                save_button.config(state=tk.DISABLED if self.is_applying_settings else tk.NORMAL)
            except Exception:
                pass

        def update_dirty_state(*_args, force=False):
            if not dirty_ready:
                return
            is_dirty = _capture_snapshot() != applied_snapshot
            set_dirty_state(is_dirty, force=force)

        _collect_settings_vars(display_vars)
        _collect_settings_vars(audio_vars)
        _collect_settings_vars(filters_vars)
        _collect_settings_vars(api_vars)
        _collect_settings_vars(translation_vars)
        _collect_settings_vars(advanced_vars)
        applied_snapshot = _capture_snapshot()
        dirty_ready = True
        set_dirty_state(False, force=True)

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
        self.chunk_size = max(20, int(advanced_vars["chunk_size_var"].get()))
        self.chunk_delay_ms = max(50, int(advanced_vars["chunk_delay_var"].get()))
        self.sentence_flush_ms = max(300, int(advanced_vars["sentence_flush_var"].get()))
        self.rms_gate_enabled = bool(advanced_vars["rms_gate_var"].get())
        try:
            self.rms_gate_factor = float(advanced_vars["rms_gate_factor_var"].get())
        except Exception:
            pass
        self.rms_gate_factor = max(0.5, min(self.rms_gate_factor, 5.0))
        if "readability_preset_var" in advanced_vars:
            self.readability_preset = str(
                advanced_vars["readability_preset_var"].get()
            ).lower()
        if "viewing_distance_var" in advanced_vars:
            try:
                self.viewing_distance_ft = float(advanced_vars["viewing_distance_var"].get())
            except Exception:
                pass
        self.viewing_distance_ft = max(2.0, min(self.viewing_distance_ft, 30.0))
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
        if "speech_engine_var" in api_vars and "speech_engine_map" in api_vars:
            selected = api_vars["speech_engine_var"].get()
            self.speech_engine = api_vars["speech_engine_map"].get(selected, "openai")
        if "openai_api_key_var" in api_vars:
            self.openai_api_key = api_vars["openai_api_key_var"].get().strip()
        if "faster_whisper_model_var" in api_vars:
            self.faster_whisper_model_name = (
                api_vars["faster_whisper_model_var"].get().strip() or "medium"
            )
        if "faster_whisper_compute_var" in api_vars:
            self.faster_whisper_compute_type = (
                api_vars["faster_whisper_compute_var"].get().strip() or "float16"
            )
        if "faster_whisper_device_var" in api_vars:
            self.faster_whisper_device = (
                api_vars["faster_whisper_device_var"].get().strip() or "cuda"
            )
        self.faster_whisper_model = None
        self.faster_whisper_model_config = None

    def _apply_translation_vars(self, translation_vars):
        self.source_lang = translation_vars["lang_map"].get(
            translation_vars["source_lang_var"].get(),
            "auto",
        )
        self.target_lang = translation_vars["lang_map"].get(
            translation_vars["target_lang_var"].get(),
            "en",
        )
        if "enable_translation_var" in translation_vars:
            self.translation_enabled = bool(translation_vars["enable_translation_var"].get())
        if "auto_switch_var" in translation_vars:
            self.auto_switch_translation = bool(translation_vars["auto_switch_var"].get())

    def _apply_audio_vars(self, audio_vars):
        pass

    def _refresh_audio_devices(self):
        # Refresh device list after audio-related settings change.
        self._suspend_capture_for_device_scan()
        self.device_refresh_in_progress = True
        try:
            self.devices = self.get_audio_devices()
            if self.device_menu is not None:
                menu = self.device_menu["menu"]
                menu.delete(0, "end")
                for device in self.devices:
                    menu.add_command(
                        label=device,
                        command=tk._setit(self.device_var, device),
                    )
            preferred_label = self._resolve_preferred_device_label(self.preferred_device_label)
            if preferred_label:
                self.device_var.set(preferred_label)
            elif self.device_var.get() not in self.devices:
                self.device_var.set(self.devices[0] if self.devices else "No devices")
            if self.device_var.get() in self.device_indices:
                self.microphone_index = self.devices.index(self.device_var.get())
            else:
                self.microphone_index = None
        finally:
            self.device_refresh_in_progress = False
            self._resume_capture_after_device_scan()

    def _apply_settings_geometry(self, settings_window):
        settings_window.geometry("960x1280")
        settings_window.minsize(960, 1280)
        settings_window.update_idletasks()
        try:
            if os.name == "nt":
                settings_window.state("zoomed")
            else:
                settings_window.attributes("-zoomed", True)
        except Exception:
            pass
        geometry_monitor_index = None
        if self.settings_geometry:
            try:
                settings_window.geometry(self.settings_geometry)
            except Exception:
                self.settings_geometry = None
            else:
                parsed = self._parse_geometry(self.settings_geometry)
                if parsed:
                    width, height, x, y = parsed
                    if width and height:
                        x = x + width / 2
                        y = y + height / 2
                    geometry_monitor_index = self._find_monitor_index_for_point(x, y)
        if not self.settings_geometry or (
            geometry_monitor_index is None
            or geometry_monitor_index != self.settings_monitor_index
        ):
            self._position_settings_window(settings_window)
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
        if self.rounded_buttons_supported:
            bootstyle = "primary,round" if primary else "round"
        else:
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
    
    def _validate_int_entry(self, proposed):
        return proposed == "" or proposed.isdigit()

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

    def _readability_angle_deg(self):
        preset = str(self.readability_preset or "").lower()
        if preset == "close":
            return 0.30
        if preset == "far":
            return 0.55
        return 0.40

    def _target_line_height_px(self):
        distance_ft = max(2.0, float(self.viewing_distance_ft or 10.0))
        distance_in = distance_ft * 12.0
        angle_deg = self._readability_angle_deg()
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
        approx = max(12, int(available_height / max(1, lines)))
        max_size = int(max_size or min(320, int(approx * 1.6)))
        min_size = 12

        def fits_height(size):
            self.text_font.configure(size=size)
            line_height = self.text_font.metrics("linespace") or 1
            return (line_height * lines) <= available_height

        lo, hi = min_size, max_size
        best_height = min_size
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits_height(mid):
                best_height = mid
                lo = mid + 1
            else:
                hi = mid - 1

        best = best_height
        target_chars = min(self.chunk_size, self.min_chars_per_line)
        if available_width > 1 and target_chars > 0:
            sample = "x" * target_chars

            def fits_width(size):
                self.text_font.configure(size=size)
                return self.text_font.measure(sample) <= available_width

            lo, hi = min_size, best_height
            best_width = min_size
            while lo <= hi:
                mid = (lo + hi) // 2
                if fits_width(mid):
                    best_width = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            best = min(best_height, best_width)

        target_px = self._target_line_height_px()
        target_size = self._font_size_for_line_height(target_px, min_size=min_size, max_size=best)
        if target_size:
            best = min(best, target_size)

        self._scaled_font_size = best
        self.text_font.configure(size=best)
        if self.preview_font is not None:
            preview_size = max(12, int(best * 0.5))
            self.preview_font.configure(size=preview_size)

    def _wrap_lines_to_width(self, lines, max_width):
        if max_width <= 1:
            return lines
        wrapped = []
        for line in lines:
            words = re.findall(r"\S+|\s+", line)
            current = ""
            for token in words:
                if token.isspace():
                    current += token
                    continue
                candidate = f"{current}{token}" if current else token
                if self.text_font.measure(candidate) <= max_width:
                    current = candidate
                    continue
                if current:
                    wrapped.append(current.rstrip())
                    current = ""
                if self.text_font.measure(token) <= max_width:
                    current = token
                    continue
                chunk = ""
                for ch in token:
                    test = f"{chunk}{ch}"
                    if self.text_font.measure(test) <= max_width:
                        chunk = test
                    else:
                        if chunk:
                            wrapped.append(chunk)
                        chunk = ch
                current = chunk
            if current or not line:
                wrapped.append(current.rstrip())
        return wrapped

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

    def _coerce_int_var(self, var, default, min_val=None, max_val=None):
        try:
            value = int(var.get())
        except Exception:
            value = default
        if min_val is not None:
            value = max(min_val, value)
        if max_val is not None:
            value = min(max_val, value)
        try:
            var.set(value)
        except Exception:
            pass
        return value

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
            content,
            display_section,
            label_opts,
            section_bg,
            settings_fg,
            section_font,
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
        audio_vars = self._build_audio_section(audio_section, label_opts, section_bg, settings_fg)

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
        content,
        display_section,
        label_opts,
        section_bg,
        settings_fg,
        section_font,
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
            "monitor_var": monitor_var,
            "settings_monitor_var": settings_monitor_var,
            "monitor_labels": monitor_labels,
        }

    def _build_audio_section(self, audio_section, label_opts, section_bg, settings_fg):
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

        def on_device_change(*_args):
            label = self.device_var.get()
            if label in self.device_indices:
                self.microphone_index = self.devices.index(label)
                if not self.device_refresh_in_progress:
                    self.preferred_device_label = label
                    self.save_settings()
                self._request_capture_restart()
            else:
                self.microphone_index = None

        self.device_var.trace_add("write", on_device_change)

        return {
        }

    def _build_filters_section(self, filters_section, label_opts, section_bg):
        self._add_setting_label(
            filters_section,
            "Bad words filter:",
            "Words to mask with *** in the output.",
            label_opts,
            pady=(0, 4),
        )

        bad_words_toggle_var = tk.BooleanVar(value=False)
        bad_words_toggle_button = self._make_button(
            filters_section,
            "Show list",
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
            "Show list",
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
            bad_words_toggle_button.config(text="Hide list" if show else "Show list")
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
            custom_vocab_toggle_button.config(text="Hide list" if show else "Show list")
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
            from_=300,
            to=3000,
            increment=100,
            textvariable=sentence_flush_var,
        )
        self._apply_input_style(sentence_flush_spin)
        sentence_flush_spin.pack(anchor="w")

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

        readability_section = tk.LabelFrame(
            advanced_section,
            text="Readability",
            bg=section_bg,
            fg=settings_fg,
            font=section_font,
            padx=10,
            pady=10,
        )
        readability_section.pack(fill=tk.X, pady=(0, 10))

        self._add_setting_label(
            readability_section,
            "Preset:",
            "Choose a viewing-distance preset for font sizing.",
            label_opts,
            pady=(0, 4),
        )
        readability_options = ["Close", "Medium", "Far"]
        preset_map = {name.lower(): name for name in readability_options}
        current_preset = preset_map.get(
            str(self.readability_preset or "").lower(), "Medium"
        )
        readability_preset_var = tk.StringVar(value=current_preset)
        readability_menu = tk.OptionMenu(
            readability_section,
            readability_preset_var,
            *readability_options,
        )
        self._apply_option_menu_style(readability_menu)
        readability_menu.pack(anchor="w")

        self._add_setting_label(
            readability_section,
            "Viewing distance (ft):",
            "Used to estimate a readable font size from your seat.",
            label_opts,
            pady=(10, 4),
        )
        viewing_distance_var = tk.DoubleVar(value=self.viewing_distance_ft)
        viewing_distance_spin = tk.Spinbox(
            readability_section,
            from_=2.0,
            to=30.0,
            increment=0.5,
            textvariable=viewing_distance_var,
        )
        self._apply_input_style(viewing_distance_spin)
        viewing_distance_spin.pack(anchor="w")

        return {
            "chunk_size_var": chunk_size_var,
            "chunk_delay_var": chunk_delay_var,
            "sentence_flush_var": sentence_flush_var,
            "rms_gate_var": rms_gate_var,
            "rms_gate_factor_var": rms_gate_factor_var,
            "readability_preset_var": readability_preset_var,
            "viewing_distance_var": viewing_distance_var,
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
            ("OpenAI (gpt-4o-mini-transcribe)", "openai"),
            ("Local (faster-whisper)", "faster-whisper"),
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
            "OpenAI API Key (optional):",
            "API key for OpenAI transcription (and translation if enabled).",
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

        def update_engine_visibility(*_args):
            engine = engine_map.get(speech_engine_var.get(), "openai")
            if engine == "openai":
                openai_key_container.pack(fill=tk.X)
                faster_whisper_container.pack_forget()
            else:
                openai_key_container.pack_forget()
                faster_whisper_container.pack(fill=tk.X, pady=(10, 0))

        speech_engine_var.trace_add("write", update_engine_visibility)
        update_engine_visibility()
        return {
            "speech_engine_var": speech_engine_var,
            "speech_engine_map": engine_map,
            "openai_api_key_var": openai_key_var,
            "openai_key_container": openai_key_container,
            "faster_whisper_model_var": faster_whisper_model_var,
            "faster_whisper_compute_var": faster_whisper_compute_var,
            "faster_whisper_device_var": faster_whisper_device_var,
        }

    def _build_translation_section(self, translation_section, label_opts):
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
            "Translate recognized speech to the target language.",
            label_opts["bg"],
            label_opts["fg"],
        )

        auto_switch_var = tk.BooleanVar(value=self.auto_switch_translation)
        auto_switch_row = tk.Frame(translation_section, bg=label_opts["bg"])
        auto_switch_row.pack(anchor="w", pady=(6, 6), fill=tk.X)
        auto_switch_check = tk.Checkbutton(
            auto_switch_row,
            text="Bilingual mode (EN/ES)",
            variable=auto_switch_var,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            selectcolor=label_opts["bg"],
            activebackground=label_opts["bg"],
        )
        auto_switch_check.pack(side=tk.LEFT)
        self._create_help_icon(
            auto_switch_row,
            "Auto-detect English/Spanish and translate to the other language.",
            label_opts["bg"],
            label_opts["fg"],
        )

        self._add_setting_label(
            translation_section,
            "Translate from:",
            "Source language for the speech text.",
            label_opts,
            pady=(0, 4),
        )
        lang_options = [
            ("English", "en"),
            ("Spanish", "es"),
            ("French", "fr"),
            ("German", "de"),
            ("Italian", "it"),
            ("Portuguese", "pt"),
            ("Dutch", "nl"),
            ("Russian", "ru"),
            ("Japanese", "ja"),
            ("Korean", "ko"),
            ("Chinese (Simplified)", "zh-cn"),
        ]
        lang_display = [name for name, _ in lang_options]
        lang_map = dict(lang_options)
        rev_lang_map = {code: name for name, code in lang_options}

        source_lang_var = tk.StringVar(value=rev_lang_map.get(self.source_lang, "English"))
        source_menu = tk.OptionMenu(translation_section, source_lang_var, *lang_display)
        self._apply_option_menu_style(source_menu)
        source_menu.pack(anchor="w")

        self._add_setting_label(
            translation_section,
            "Translate to:",
            "Target language for translation output.",
            label_opts,
            pady=(10, 4),
        )
        target_lang_var = tk.StringVar(value=rev_lang_map.get(self.target_lang, "English"))
        target_menu = tk.OptionMenu(translation_section, target_lang_var, *lang_display)
        self._apply_option_menu_style(target_menu)
        target_menu.pack(anchor="w")

        return {
            "source_lang_var": source_lang_var,
            "target_lang_var": target_lang_var,
            "lang_map": lang_map,
            "enable_translation_var": enable_translation_var,
            "auto_switch_var": auto_switch_var,
        }
        
    def choose_color(self, color_var, color_type, parent):
        color = colorchooser.askcolor(title=f"Choose {color_type} color", parent=parent)
        if color[1]:  # color[1] is the hex value
            color_var.set(color[1])
    
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
    
    def listen_and_translate(self):
        self._start_capture_thread()
        while self.listening:
            try:
                if self._pause_if_needed():
                    continue
                self._flush_sentence_buffer_if_due()
                if self.capture_thread is None or not self.capture_thread.is_alive():
                    self._start_capture_thread()
                    time.sleep(0.2)
                    continue
                try:
                    audio = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.process_audio(audio)
            except sr.RequestError as e:
                self.update_status(f"API Error: {e}")
            except Exception as e:
                self.update_status(f"Error: {e}")

    def _pause_if_needed(self):
        if not self.is_paused:
            return False
        self.update_status("Paused")
        try:
            while True:
                self.audio_queue.get_nowait()
        except Exception:
            pass
        time.sleep(0.2)
        return True

    def _resolve_capture_device(self):
        device_name = self._get_selected_device_name()
        if not device_name:
            self.update_status("No audio device selected")
            time.sleep(1)
            return None
        if self.device_types.get(device_name) != "input":
            return self._resolve_loopback_device(device_name)
        return self.device_indices.get(device_name, 0)

    def _get_selected_device_name(self):
        if (
            self.microphone_index is None
            or not self.devices
            or self.microphone_index >= len(self.devices)
        ):
            return None
        return self.devices[self.microphone_index]

    def _resolve_loopback_device(self, device_name):
        if not self.allow_loopback:
            self.update_status("Selected device is output-only (enable loopback)")
            time.sleep(1)
            return None
        loopback_index = self.loopback_output_map.get(device_name)
        if loopback_index is None:
            self.update_status(
                "No loopback input for selected output (enable Stereo Mix or install virtual cable)"
            )
            time.sleep(1)
            return None
        self.update_status("Loopback capture (output)")
        return loopback_index

    def _capture_and_process(self, device_index):
        # Use microphone input
        sample_rate = self.device_sample_rates_by_index.get(device_index, 16000)
        with sr.Microphone(device_index=device_index, sample_rate=sample_rate) as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            self.update_status(self.STATUS_LISTENING)
            audio = self.recognizer.listen(
                source, timeout=1, phrase_time_limit=self.phrase_time_limit
            )
            self.process_audio(audio)

    def _audio_callback(self, _recognizer, audio):
        if not self.listening or self.is_paused:
            return
        self.last_audio_time = time.time()
        self.no_speech_timeout_count = 0
        try:
            self.audio_queue.put_nowait(audio)
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self.audio_queue.put_nowait(audio)
            except queue.Full:
                pass

    def _start_capture_thread(self):
        if self.capture_thread is not None and self.capture_thread.is_alive():
            return
        self.capture_thread = Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

    def _suspend_capture_for_device_scan(self, timeout=2.0):
        if self.capture_thread is None or not self.capture_thread.is_alive():
            return True
        self.capture_suspend_event.set()
        self.capture_restart_requested = True
        start = time.time()
        while time.time() - start < timeout:
            if self.capture_suspended_event.is_set():
                return True
            time.sleep(0.05)
        self._log_status("Capture suspend timed out")
        return False

    def _resume_capture_after_device_scan(self):
        self.capture_suspend_event.clear()
        self.capture_suspended_event.clear()
        self._request_capture_restart()

    def _request_capture_restart(self):
        now = time.time()
        if now - self.listener_restart_time < self.listener_restart_min_interval:
            return
        self.listener_restart_time = now
        self.capture_restart_requested = True

    def _capture_loop(self):
        while self.listening:
            if self.capture_suspend_event.is_set():
                self.capture_suspended_event.set()
                time.sleep(0.05)
                continue
            if self.capture_suspended_event.is_set():
                self.capture_suspended_event.clear()
            if self.is_paused:
                time.sleep(0.2)
                continue
            if self.capture_restart_requested:
                self.capture_restart_requested = False
            device_label = self._get_selected_device_name()
            device_index = self._resolve_capture_device()
            if device_index is None:
                time.sleep(0.2)
                continue
            sample_rate = self.device_sample_rates_by_index.get(device_index, 16000)
            use_loopback = (
                self.device_types.get(device_label) != "input"
                or self._is_loopback_label(device_label)
            )
            self.active_device_index = device_index
            try:
                with sr.Microphone(device_index=device_index, sample_rate=sample_rate) as source:
                    if not use_loopback:
                        try:
                            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                        except Exception:
                            pass
                    self.update_status(self.STATUS_LISTENING)
                    while self.listening and not self.is_paused:
                        if self.capture_suspend_event.is_set():
                            break
                        if self.capture_restart_requested:
                            self.capture_restart_requested = False
                            break
                        if self._get_selected_device_name() != device_label:
                            break
                        try:
                            if use_loopback:
                                audio = self.recognizer.record(
                                    source, duration=self.loopback_chunk_seconds
                                )
                            else:
                                audio = self.recognizer.listen(
                                    source, timeout=1, phrase_time_limit=self.phrase_time_limit
                                )
                        except sr.WaitTimeoutError:
                            self._note_no_speech_timeout()
                            continue
                        except OSError as exc:
                            self.update_status(f"Audio device error: {exc}")
                            break
                        except Exception as exc:
                            self.update_status(f"Audio error: {exc}")
                            break
                        self._audio_callback(self.recognizer, audio)
            except Exception as exc:
                self.update_status(f"Audio listener error: {exc}")
                time.sleep(0.5)

    def _note_no_speech_timeout(self):
        self.no_speech_timeout_count += 1
        if self.no_speech_timeout_count < 6:
            return
        now = time.time()
        if now - self.last_no_speech_notice < 5.0:
            return
        self.last_no_speech_notice = now
        self.update_status("No speech detected. Check mic level or device.")

    def _note_unknown_speech(self):
        self.unknown_speech_count += 1
        if self.unknown_speech_count < 3:
            return
        now = time.time()
        if now - self.last_unknown_notice < 5.0:
            return
        self.last_unknown_notice = now
        self.update_status("Heard audio but could not recognize speech.")

    def _reset_speech_counters(self):
        self.no_speech_timeout_count = 0
        self.unknown_speech_count = 0

    def _recognize_audio(self, audio):
        try:
            engine = (self.speech_engine or "openai").lower()
            if engine == "faster-whisper":
                text = self.recognize_faster_whisper(audio)
            else:
                if not self.openai_api_key:
                    raise ValueError("OpenAI Whisper selected but API key is empty")
                text = self.recognize_openai_whisper(audio, self.openai_api_key)
            text = text.strip()
            if not text:
                if engine == "faster-whisper":
                    self._note_unknown_speech()
                return ""
            self._update_auto_detect_language(text)
            self._reset_speech_counters()
            return text
        except sr.UnknownValueError:
            self._note_unknown_speech()
            return ""
        except Exception as exc:
            self.update_status(f"Speech error: {exc}")
            return ""

    def run_mic_test(self):
        Thread(target=self._run_mic_test_worker, daemon=True).start()

    def _run_mic_test_worker(self):
        if self.is_paused:
            self.update_status("Mic test unavailable while paused")
            return
        self.update_status("Mic test: say something...")
        suspended = self._suspend_capture_for_device_scan()
        try:
            device_index = self._resolve_capture_device()
            if device_index is None:
                return
            sample_rate = self.device_sample_rates_by_index.get(device_index, 16000)
            with sr.Microphone(device_index=device_index, sample_rate=sample_rate) as source:
                try:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.3)
                except Exception:
                    pass
                audio = self.recognizer.listen(source, timeout=3, phrase_time_limit=4)
            text = self._recognize_audio(audio)
            if text:
                self.update_status(f"Mic test heard: {text}")
            else:
                self.update_status("Mic test: no speech detected")
        except sr.WaitTimeoutError:
            self.update_status("Mic test timed out (no speech)")
        except Exception as exc:
            self.update_status(f"Mic test failed: {exc}")
        finally:
            if suspended:
                self._resume_capture_after_device_scan()
    
    def process_audio(self, audio):
        if self.rms_gate_enabled:
            try:
                raw = audio.get_raw_data()
                if not raw:
                    return
                rms = audioop.rms(raw, audio.sample_width)
                threshold = self.recognizer.energy_threshold * self.rms_gate_factor
                if rms < threshold:
                    return
            except Exception:
                pass
        text = self._recognize_audio(audio)
        if not text:
            return
        flushed = self._append_sentence_buffer(text)
        if flushed:
            for sentence in flushed:
                self._enqueue_sentence(sentence)
    
    def _auto_detect_enabled(self):
        if self.auto_switch_translation:
            return True
        return (self.source_lang or "").strip().lower() == "auto"

    def _maybe_openai_language(self):
        lang = (self.source_lang or "").strip().lower()
        if self._auto_detect_enabled():
            auto_lang = (self.auto_detect_lang or "").strip().lower()
            if auto_lang in self.auto_detect_langs:
                return auto_lang
            return ""
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if "_" in lang:
            lang = lang.split("_", 1)[0]
        if len(lang) == 2 and lang.isalpha():
            return lang
        return ""

    def _detect_language_from_text(self, text):
        if not text:
            return None
        sample = text.lower()
        tokens = re.findall(r"[a-záéíóúüñ]+", sample)
        if not tokens:
            return None
        es_score = sum(1 for token in tokens if token in self.spanish_common_words)
        en_score = sum(1 for token in tokens if token in self.english_common_words)
        if any(ch in sample for ch in ("á", "é", "í", "ó", "ú", "ñ", "ü", "¿", "¡")):
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

    def recognize_openai_whisper(self, audio, api_key):
        url = "https://api.openai.com/v1/audio/transcriptions"
        audio_bytes = audio.get_wav_data()
        file_obj = io.BytesIO(audio_bytes)
        files = {"file": ("audio.wav", file_obj, "audio/wav")}
        data = {
            "model": "gpt-4o-mini-transcribe",
            "response_format": "json",
            "temperature": 0,
            "prompt": self._build_openai_transcription_prompt(),
        }
        lang = self._maybe_openai_language()
        if lang:
            data["language"] = lang
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.post(url, headers=headers, files=files, data=data, timeout=20)
        if response.status_code == 200:
            payload = response.json()
            return payload.get("text", "")
        raise sr.RequestError(f"OpenAI API error {response.status_code}: {response.text}")

    def _get_faster_whisper_model(self):
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise ValueError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            ) from exc
        config = (
            self.faster_whisper_model_name,
            self.faster_whisper_device,
            self.faster_whisper_compute_type,
        )
        if self.faster_whisper_model is None or self.faster_whisper_model_config != config:
            self.update_status(
                "Loading faster-whisper model (first run can take a few minutes)..."
            )
            try:
                self.faster_whisper_model = WhisperModel(
                    self.faster_whisper_model_name,
                    device=self.faster_whisper_device,
                    compute_type=self.faster_whisper_compute_type,
                )
            except Exception as exc:
                hint = ""
                if str(self.faster_whisper_device).lower() == "cuda":
                    hint = " Try device=cpu or compute type int8."
                raise ValueError(
                    f"faster-whisper failed to initialize: {exc}.{hint}"
                ) from exc
            self.faster_whisper_model_config = config
            try:
                self.update_status("Local model ready")
                self.root.after(1500, self._restore_status_label)
            except Exception:
                pass
        return self.faster_whisper_model

    def recognize_faster_whisper(self, audio):
        model = self._get_faster_whisper_model()
        audio_bytes = audio.get_wav_data()
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_file.write(audio_bytes)
                tmp_path = tmp_file.name
            lang = self._maybe_openai_language()
            kwargs = {}
            if lang:
                kwargs["language"] = lang
            segments, _info = model.transcribe(tmp_path, **kwargs)
            text = "".join(segment.text for segment in segments).strip()
            return text
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _build_openai_transcription_prompt(self):
        prompt = (
            "Transcribe the audio verbatim. Do not add commentary or instructions. "
            "If no speech is present, return an empty string."
        )
        if self._auto_detect_enabled() and self.auto_detect_langs:
            lang_labels = []
            for code in self.auto_detect_langs:
                if code == "en":
                    lang_labels.append("English")
                elif code == "es":
                    lang_labels.append("Spanish")
                else:
                    lang_labels.append(code)
            if lang_labels:
                prompt += " The audio is in " + " or ".join(lang_labels) + "."
        return prompt

    def _append_sentence_buffer(self, text):
        text = text.strip()
        if not text:
            return []
        with self.sentence_lock:
            if self.sentence_buffer:
                self.sentence_buffer = f"{self.sentence_buffer} {text}"
            else:
                self.sentence_buffer = text
            self.sentence_last_update = time.time()
            buffer_text = self.sentence_buffer.strip()
            if (
                len(buffer_text) >= self.sentence_max_chars
                or re.search(r"[.!?][\"')\\]]*$", buffer_text)
            ):
                self.sentence_buffer = ""
                return [buffer_text]
        return []

    def _flush_sentence_buffer_if_due(self):
        if not self.sentence_buffer:
            return
        if (time.time() - self.sentence_last_update) * 1000 < self.sentence_flush_ms:
            return
        with self.sentence_lock:
            if not self.sentence_buffer:
                return
            buffer_text = self.sentence_buffer.strip()
            self.sentence_buffer = ""
        self._enqueue_sentence(buffer_text)

    def _enqueue_sentence(self, text):
        if not text:
            return
        payload = (text, time.time())
        try:
            self.sentence_queue.put_nowait(payload)
            return
        except queue.Full:
            pass
        try:
            self.sentence_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.sentence_queue.put_nowait(payload)
        except Exception:
            pass

    def _translation_worker(self):
        while self.listening:
            try:
                payload = self.sentence_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            sentence, started_at = self._unpack_sentence_payload(payload)
            if not sentence:
                continue
            self._translate_and_display(sentence, started_at)

    def _drain_sentence_queue(self):
        while True:
            try:
                payload = self.sentence_queue.get_nowait()
            except queue.Empty:
                return
            sentence, started_at = self._unpack_sentence_payload(payload)
            self._translate_and_display(sentence, started_at)

    def _translate_with_google(self, text):
        return self.translator.translate(
            text,
            src=self._effective_source_lang(),
            dest=self._effective_target_lang(),
        ).text

    def _build_openai_translation_prompt(self, text):
        source = (self._effective_source_lang() or "auto").strip().lower()
        target = (self._effective_target_lang() or "en").strip().lower()
        if source and source != "auto":
            instruction = (
                f"Translate from {source} to {target}. "
                "Return only the translation."
            )
        else:
            instruction = f"Translate to {target}. Return only the translation."
        return f"{instruction}\n\n{text}"

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
        if code == "en":
            return "English"
        if code == "es":
            return "Spanish"
        if not code:
            return ""
        return code

    def _listening_status_message(self):
        source = (self.source_lang or "").strip().lower()
        if source == "auto" or self.auto_switch_translation:
            choices = [self._language_label(c) for c in self.auto_detect_langs]
            choices = [c for c in choices if c]
            base = "Listening"
            if choices:
                mode_label = "Bilingual" if self.auto_switch_translation else "Auto"
                base += f" ({mode_label}: {'/'.join(choices)}"
                detected = (self.auto_detect_lang or "").strip().lower()
                if detected in self.auto_detect_langs:
                    base += f", Detected: {self._language_label(detected)})"
                else:
                    base += ")"
                return base
        label = self._language_label(source)
        if label:
            return f"Listening ({label})"
        return self.STATUS_LISTENING

    def _extract_openai_output_text(self, payload):
        if not isinstance(payload, dict):
            return ""
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        output = payload.get("output", [])
        if not isinstance(output, list):
            return ""
        texts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "output_text":
                        text = part.get("text", "")
                        if text:
                            texts.append(text)
            elif isinstance(content, str) and content:
                texts.append(content)
        return " ".join(t.strip() for t in texts if t.strip()).strip()

    def _translate_with_openai(self, text):
        if not self.openai_api_key:
            raise ValueError("OpenAI API key is empty")
        url = "https://api.openai.com/v1/responses"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.openai_translate_model,
            "input": self._build_openai_translation_prompt(text),
            "temperature": 0,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code != 200:
            raise sr.RequestError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )
        output_text = self._extract_openai_output_text(response.json())
        if not output_text:
            raise sr.RequestError("OpenAI API returned empty translation")
        return output_text

    def _translate_text(self, text):
        if self.openai_api_key:
            try:
                return self._translate_with_openai(text)
            except Exception as exc:
                self._log_status(f"OpenAI translation failed: {exc}")
        return self._translate_with_google(text)

    def _translate_and_display(self, text, started_at=None):
        self.update_status("Transcribing..." if not self.translation_enabled else "Translating...")
        try:
            if self.translation_enabled:
                translated = self._translate_text(text)
            else:
                translated = text
            translated = self.apply_custom_vocabulary(translated)
            if self.translation_enabled and self._effective_target_lang().startswith("en"):
                translated = self.apply_spanish_bible_name_map(translated)
            translated = self.format_scripture_refs(translated)
            translated = self.clean_text_spacing(translated)
        except Exception as e:
            self.update_status(f"Translation error: {e}")
            translated = text
        self.update_text(translated)
        self._record_chunk_latency(started_at)
        self.update_status(self.STATUS_LISTENING)

    def _unpack_sentence_payload(self, payload):
        if isinstance(payload, tuple) and len(payload) == 2:
            return payload[0], payload[1]
        return payload, None

    def _record_chunk_latency(self, started_at):
        if not started_at:
            return
        elapsed_ms = int((time.time() - started_at) * 1000)
        if elapsed_ms < 0:
            return
        self.latency_samples.append(elapsed_ms)
        avg_ms = int(sum(self.latency_samples) / max(1, len(self.latency_samples)))

        def update():
            if not self.chunk_latency_label or not self.chunk_latency_label.winfo_exists():
                return
            self.chunk_latency_label.config(
                text=f"Chunk latency: {elapsed_ms} ms (avg {avg_ms} ms)"
            )

        self.root.after(0, update)
    
    def update_text(self, text):
        def update():
            incoming = text.strip()
            if not incoming:
                return

            if self.word_by_word:
                self.enqueue_text(incoming)
                return

            if self.pending_text:
                self.pending_text = f"{self.pending_text} {incoming}"
            else:
                self.pending_text = incoming

            if len(self.pending_text) >= self.chunk_size:
                self.enqueue_text(self.pending_text)
                self.pending_text = ""

            if self.flush_after_id is not None:
                self.root.after_cancel(self.flush_after_id)
            self.flush_after_id = self.root.after(self.flush_timeout_ms, self.flush_pending_text)
        self.root.after(0, update)

    def enqueue_text(self, text):
        if self.word_by_word:
            for chunk in self.chunk_text(text, self.chunk_size):
                self.word_reveal_queue.append(chunk)
            if not self.is_revealing_words:
                self.start_word_reveal()
            return
        for chunk in self.chunk_text(text, self.chunk_size):
            self.text_queue.append(chunk)
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
        if not self.current_reveal_words:
            if self.current_reveal_text:
                self.translations.append(self.current_reveal_text)
                if len(self.translations) > self.max_lines:
                    self.translations = self.translations[-self.max_lines:]
                self.current_reveal_text = ""
            if not self.word_reveal_queue:
                self.is_revealing_words = False
                self.live_line = ""
                self.render_text()
                return
            sentence = self.word_reveal_queue.popleft()
            self.current_reveal_words = re.findall(r"\S+", sentence)

        next_word = self.current_reveal_words.pop(0)
        if self.current_reveal_text:
            self.current_reveal_text = f"{self.current_reveal_text} {next_word}"
        else:
            self.current_reveal_text = next_word
        self.live_line = self.current_reveal_text
        self.render_text()
        self.root.after(self.chunk_delay_ms, self.reveal_next_word)

    def flush_pending_text(self):
        self.flush_after_id = None
        if not self.pending_text:
            return
        self.enqueue_text(self.pending_text)
        self.pending_text = ""

    def flush_text_queue(self):
        if not self.text_queue:
            self.is_flushing_queue = False
            return

        self.is_flushing_queue = True
        chunk = self.text_queue.popleft()
        filtered_text = self.filter_bad_words(chunk)
        self.translations.append(filtered_text)
        if len(self.translations) > self.max_lines:
            self.translations = self.translations[-self.max_lines:]
        self.render_text()
        self.root.after(self.chunk_delay_ms, self.flush_text_queue)

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
    
    def _refresh_bad_words(self):
        active = set()
        for words in (self.bad_words_by_lang or {}).values():
            active.update(words)
        self.active_bad_words = active

    def _effective_filter_lang(self):
        if self.translation_enabled:
            lang = (self._effective_target_lang() or "").strip().lower()
        else:
            lang = (self._effective_source_lang() or "").strip().lower()
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if "_" in lang:
            lang = lang.split("_", 1)[0]
        if lang == "auto":
            return ""
        return lang

    def _get_bad_words_for_output(self):
        vocab = self.bad_words_by_lang or {}
        if not vocab:
            return set()
        lang = self._effective_filter_lang()
        if lang and lang in vocab:
            return vocab.get(lang, set())
        if "en" in vocab:
            return vocab.get("en", set())
        merged = set()
        for words in vocab.values():
            merged.update(words)
        return merged

    def filter_bad_words(self, text):
        active_bad_words = self._get_bad_words_for_output()
        if not active_bad_words:
            return text
        filtered = text
        for word in active_bad_words:
            pattern = r"\b" + re.escape(word) + r"\b"
            filtered = re.sub(pattern, "***", filtered, flags=re.IGNORECASE)
        return filtered

    def apply_custom_vocabulary(self, text):
        vocabulary = self._get_custom_vocabulary_for_output()
        if not vocabulary:
            return text
        replacements = {v.lower(): v for v in vocabulary}
        def repl(match):
            key = match.group(0).lower()
            return replacements.get(key, match.group(0))
        pattern = r"\b(" + "|".join(re.escape(v) for v in vocabulary) + r")\b"
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)

    def _get_custom_vocabulary_for_output(self):
        vocab_by_lang = self.custom_vocabulary_by_lang or {}
        if not vocab_by_lang:
            return []
        if self.translation_enabled:
            lang = (self._effective_target_lang() or "").lower()
        else:
            lang = (self._effective_source_lang() or "").lower()
        if "-" in lang:
            lang = lang.split("-", 1)[0]
        if lang and lang != "auto":
            if lang in vocab_by_lang:
                return vocab_by_lang.get(lang, [])
            if "en" in vocab_by_lang:
                return vocab_by_lang.get("en", [])
            return []
        if "en" in vocab_by_lang:
            return vocab_by_lang["en"]
        return next(iter(vocab_by_lang.values()), [])

    def apply_spanish_bible_name_map(self, text):
        if not text or not self.spanish_bible_pattern:
            return text
        def repl(match):
            raw = match.group(0)
            replacement = self.spanish_bible_name_map.get(raw.lower(), raw)
            if raw.isupper():
                return replacement.upper()
            return replacement
        return self.spanish_bible_pattern.sub(repl, text)

    def clean_text_spacing(self, text):
        text = re.sub(r'([.!?])(?=[A-Za-z])', r'\1 ', text)
        text = re.sub(r'\s{2,}', ' ', text)
        return text.strip()

    def _apply_basic_punctuation(self, text):
        text = (text or "").strip()
        if not text:
            return text
        if text[0].islower():
            text = text[0].upper() + text[1:]
        if re.search(r"[.!?][\"')\]]*$", text):
            return text
        if re.search(r"[\"')\]]$", text):
            return re.sub(r"([\"')\]]$)", r".\g<1>", text)
        if re.search(r"[,:;]$", text):
            return re.sub(r"[,:;]$", ".", text)
        return f"{text}."

    def format_scripture_refs(self, text):
        if not self.biblical_books:
            return text
        book_pattern = "|".join(re.escape(b) for b in self.biblical_books)
        pattern = (
            r"\b(" + book_pattern + r")\b"
            r"(?:\s+chapter)?\s+(\d{1,3})"
            r"(?:\s*[:]\s*|\s+verse\s+|\s+)(\d{1,3})\b"
        )
        def repl(match):
            book = match.group(1)
            chapter = match.group(2)
            verse = match.group(3)
            return f"{book} {chapter}:{verse}"
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)

    def _build_spanish_bible_pattern(self):
        if not self.spanish_bible_name_map:
            return None
        keys = sorted(self.spanish_bible_name_map.keys(), key=len, reverse=True)
        try:
            return re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b", flags=re.IGNORECASE)
        except re.error:
            return None

    def default_bad_words_en(self):
        return [
            "ass",
            "asshole",
            "bastard",
            "bitch",
            "boner",
            "cock",
            "crap",
            "cunt",
            "damn",
            "dick",
            "fuck",
            "piss",
            "pussy",
            "shit",
            "slut",
            "tits",
            "whore",
        ]

    def default_bad_words_es(self):
        return [
            "cabrón",
            "coño",
            "cabron",
            "carajo",
            "chingar",
            "chingada",
            "chingado",
            "chingados",
            "culo",
            "follar",
            "gilipollas",
            "hostia",
            "joder",
            "maldita",
            "maldito",
            "mierda",
            "pendejo",
            "perra",
            "pinche",
            "polla",
            "puta",
            "puto",
            "verga",
        ]

    def default_spanish_bible_map(self):
        pairs = [
            ("génesis", "Genesis"),
            ("genesis", "Genesis"),
            ("éxodo", "Exodus"),
            ("exodo", "Exodus"),
            ("levítico", "Leviticus"),
            ("levitico", "Leviticus"),
            ("números", "Numbers"),
            ("numeros", "Numbers"),
            ("deuteronomio", "Deuteronomy"),
            ("josué", "Joshua"),
            ("josue", "Joshua"),
            ("jueces", "Judges"),
            ("rut", "Ruth"),
            ("1 samuel", "1 Samuel"),
            ("2 samuel", "2 Samuel"),
            ("1 reyes", "1 Kings"),
            ("2 reyes", "2 Kings"),
            ("1 crónicas", "1 Chronicles"),
            ("2 crónicas", "2 Chronicles"),
            ("1 cronicas", "1 Chronicles"),
            ("2 cronicas", "2 Chronicles"),
            ("esdras", "Ezra"),
            ("nehemías", "Nehemiah"),
            ("nehemias", "Nehemiah"),
            ("ester", "Esther"),
            ("job", "Job"),
            ("salmos", "Psalms"),
            ("salmo", "Psalm"),
            ("proverbios", "Proverbs"),
            ("eclesiastés", "Ecclesiastes"),
            ("eclesiastes", "Ecclesiastes"),
            ("cantar de los cantares", "Song of Solomon"),
            ("cantar de salomón", "Song of Solomon"),
            ("cantar de salomon", "Song of Solomon"),
            ("cantares", "Song of Solomon"),
            ("isaías", "Isaiah"),
            ("isaias", "Isaiah"),
            ("jeremías", "Jeremiah"),
            ("jeremias", "Jeremiah"),
            ("lamentaciones", "Lamentations"),
            ("ezequiel", "Ezekiel"),
            ("daniel", "Daniel"),
            ("oseas", "Hosea"),
            ("joel", "Joel"),
            ("amós", "Amos"),
            ("amos", "Amos"),
            ("abdías", "Obadiah"),
            ("abdias", "Obadiah"),
            ("jonás", "Jonah"),
            ("jonas", "Jonah"),
            ("miqueas", "Micah"),
            ("nahúm", "Nahum"),
            ("nahum", "Nahum"),
            ("habacuc", "Habakkuk"),
            ("sofónías", "Zephaniah"),
            ("sofonias", "Zephaniah"),
            ("hageo", "Haggai"),
            ("zacarías", "Zechariah"),
            ("zacarias", "Zechariah"),
            ("malaquías", "Malachi"),
            ("malaquias", "Malachi"),
            ("mateo", "Matthew"),
            ("marcos", "Mark"),
            ("lucas", "Luke"),
            ("juan", "John"),
            ("hechos", "Acts"),
            ("romanos", "Romans"),
            ("1 corintios", "1 Corinthians"),
            ("2 corintios", "2 Corinthians"),
            ("gálatas", "Galatians"),
            ("galatas", "Galatians"),
            ("efesios", "Ephesians"),
            ("filipenses", "Philippians"),
            ("colosenses", "Colossians"),
            ("1 tesalonicenses", "1 Thessalonians"),
            ("2 tesalonicenses", "2 Thessalonians"),
            ("1 timoteo", "1 Timothy"),
            ("2 timoteo", "2 Timothy"),
            ("tito", "Titus"),
            ("filemón", "Philemon"),
            ("filemon", "Philemon"),
            ("hebreos", "Hebrews"),
            ("santiago", "James"),
            ("1 pedro", "1 Peter"),
            ("2 pedro", "2 Peter"),
            ("1 juan", "1 John"),
            ("2 juan", "2 John"),
            ("3 juan", "3 John"),
            ("judas", "Jude"),
            ("apocalipsis", "Revelation"),
            ("jesús", "Jesus"),
            ("jesus", "Jesus"),
            ("moisés", "Moses"),
            ("moises", "Moses"),
            ("abraham", "Abraham"),
            ("isaac", "Isaac"),
            ("jacob", "Jacob"),
            ("josé", "Joseph"),
            ("jose", "Joseph"),
            ("david", "David"),
            ("salomón", "Solomon"),
            ("salomon", "Solomon"),
            ("samuel", "Samuel"),
            ("pablo", "Paul"),
            ("pedro", "Peter"),
            ("maría", "Mary"),
            ("maria", "Mary"),
            ("jerusalén", "Jerusalem"),
            ("jerusalen", "Jerusalem"),
            ("belén", "Bethlehem"),
            ("belen", "Bethlehem"),
            ("nazaret", "Nazareth"),
            ("galilea", "Galilee"),
            ("jericó", "Jericho"),
            ("jerico", "Jericho"),
            ("capernaum", "Capernaum"),
            ("judea", "Judea"),
            ("samaria", "Samaria"),
            ("betania", "Bethany"),
            ("gólgota", "Golgotha"),
            ("golgota", "Golgotha"),
            ("calvario", "Calvary"),
            ("monte sinai", "Mount Sinai"),
            ("monte sinaí", "Mount Sinai"),
            ("monte sion", "Mount Zion"),
            ("monte sión", "Mount Zion"),
            ("jordán", "Jordan"),
            ("jordan", "Jordan"),
            ("mar de galilea", "Sea of Galilee"),
            ("mar muerto", "Dead Sea"),
            ("damasco", "Damascus"),
            ("asiria", "Assyria"),
            ("babilonia", "Babylon"),
            ("egipto", "Egypt"),
            ("roma", "Rome"),
            ("antioquía", "Antioch"),
            ("antioquia", "Antioch"),
            ("corinto", "Corinth"),
            ("éfeso", "Ephesus"),
            ("efeso", "Ephesus"),
            ("filipos", "Philippi"),
            ("tesalónica", "Thessalonica"),
            ("tesalonica", "Thessalonica"),
            ("tarso", "Tarsus"),
            ("patmos", "Patmos"),
        ]
        return {spanish.lower(): english for spanish, english in pairs}

    def default_biblical_books(self):
        return [
            "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
            "Joshua", "Judges", "Ruth", "1 Samuel", "2 Samuel",
            "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles",
            "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
            "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah",
            "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos",
            "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk",
            "Zephaniah", "Haggai", "Zechariah", "Malachi",
            "Matthew", "Mark", "Luke", "John", "Acts", "Romans",
            "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians",
            "Philippians", "Colossians", "1 Thessalonians",
            "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus",
            "Philemon", "Hebrews", "James", "1 Peter", "2 Peter",
            "1 John", "2 John", "3 John", "Jude", "Revelation"
        ]

    def default_biblical_terms(self):
        return [
            "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
            "Joshua", "Judges", "Ruth", "1 Samuel", "2 Samuel",
            "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles",
            "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
            "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah",
            "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos",
            "Obadiah", "Jonah", "Micah", "Nahum", "Habakkuk",
            "Zephaniah", "Haggai", "Zechariah", "Malachi",
            "Matthew", "Mark", "Luke", "John", "Acts", "Romans",
            "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians",
            "Philippians", "Colossians", "1 Thessalonians",
            "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus",
            "Philemon", "Hebrews", "James", "1 Peter", "2 Peter",
            "1 John", "2 John", "3 John", "Jude", "Revelation",
            "Moses", "Abraham", "Isaac", "Jacob", "Joseph",
            "David", "Solomon", "Samuel", "Isaiah", "Jeremiah",
            "Ezekiel", "Daniel", "Paul", "Peter", "Mary", "Jesus",
            "Jerusalem", "Bethlehem", "Nazareth", "Galilee", "Jericho",
            "Capernaum", "Nazareth", "Judea", "Samaria", "Bethany",
            "Golgotha", "Calvary", "Mount Sinai", "Mount Zion",
            "Jordan", "Sea of Galilee", "Dead Sea", "Damascus",
            "Assyria", "Babylon", "Egypt", "Rome", "Antioch",
            "Corinth", "Ephesus", "Philippi", "Thessalonica",
            "Tarsus", "Patmos"
        ]

    def default_biblical_terms_es(self):
        return [
            "Génesis", "Éxodo", "Levítico", "Números", "Deuteronomio",
            "Josué", "Jueces", "Rut", "1 Samuel", "2 Samuel",
            "1 Reyes", "2 Reyes", "1 Crónicas", "2 Crónicas",
            "Esdras", "Nehemías", "Ester", "Job", "Salmos", "Salmo",
            "Proverbios", "Eclesiastés", "Cantar de los Cantares",
            "Isaías", "Jeremías", "Lamentaciones", "Ezequiel", "Daniel",
            "Oseas", "Joel", "Amós", "Abdías", "Jonás", "Miqueas",
            "Nahúm", "Habacuc", "Sofonías", "Hageo", "Zacarías",
            "Malaquías", "Mateo", "Marcos", "Lucas", "Juan", "Hechos",
            "Romanos", "1 Corintios", "2 Corintios", "Gálatas",
            "Efesios", "Filipenses", "Colosenses", "1 Tesalonicenses",
            "2 Tesalonicenses", "1 Timoteo", "2 Timoteo", "Tito",
            "Filemón", "Hebreos", "Santiago", "1 Pedro", "2 Pedro",
            "1 Juan", "2 Juan", "3 Juan", "Judas", "Apocalipsis",
            "Jesús", "Moisés", "Abraham", "Isaac", "Jacob", "José",
            "David", "Salomón", "Samuel", "Pablo", "Pedro", "María",
            "Jerusalén", "Belén", "Nazaret", "Galilea", "Jericó",
            "Capernaum", "Judea", "Samaria", "Betania", "Gólgota",
            "Calvario", "Monte Sinaí", "Monte Sión", "Jordán",
            "Mar de Galilea", "Mar Muerto", "Damasco", "Asiria",
            "Babilonia", "Egipto", "Roma", "Antioquía", "Corinto",
            "Éfeso", "Filipos", "Tesalónica", "Tarso", "Patmos"
        ]
    
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
        base_lines = list(self.translations)
        if self.live_line:
            base_lines.append(self.live_line)
        width = max(10, self.text_canvas.winfo_width() - (self.text_padding * 2))
        wrapped_lines = self._wrap_lines_to_width(
            [self.filter_bad_words(t) for t in base_lines],
            width,
        )
        display_lines = wrapped_lines[-self.max_lines:]
        self.last_display_line_count = len(display_lines)
        display_text = "\n".join(display_lines)
        self.text_canvas.itemconfigure(self.text_item, text="", state="hidden")
        for item in self.text_line_items:
            self.text_canvas.itemconfigure(item, state="normal")
        self._update_line_items(display_lines)
        self.text_canvas.update_idletasks()
        self.update_preview(display_text)
        self.update_text_metrics()

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

    def clamp_text_to_fit(self):
        height = max(1, self.text_canvas.winfo_height())
        available = max(1, height - (self.text_padding * 2))
        if self.text_bbox_height > available:
            self.truncate_text_to_fit(available)

    def truncate_text_to_fit(self, available_height):
        if not self.translations:
            return
        text = '\n'.join(self.filter_bad_words(t) for t in self.translations[-self.max_lines:])
        while len(text) > 10:
            text = text[: max(10, int(len(text) * 0.85))].rstrip() + "..."
            self.text_canvas.itemconfigure(self.text_item, text=text, font=self.text_font)
            self.text_canvas.update_idletasks()
            self.update_text_metrics()
            if self.text_bbox_height <= available_height:
                return


if __name__ == "__main__":
    app = TranslationApp()
