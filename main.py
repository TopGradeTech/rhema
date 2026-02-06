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
import base64
import audioop
import json
import pyaudio
from collections import deque
import os
import sys
import traceback
import io

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
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
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
        self.translator = Translator()
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 0.7
        self.recognizer.non_speaking_duration = 0.4
        self.recognizer.phrase_threshold = 0.3
        self.allow_loopback = False
        self.loopback_chunk_seconds = 1.0
        self.phrase_time_limit = 8.0
        self.preferred_host_api = ""
        self.available_host_apis = []
        self.cloud_auto_punct = False
        self.openai_api_key = ""
        self.openai_translate_model = "gpt-4o-mini"
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
        self.sentence_flush_ms = 1000
        self.sentence_last_update = 0.0
        self.sentence_max_chars = 200
        self.sentence_queue = queue.Queue()
        self.preview_widget = None
        self.preview_font = None
        self.preview_placeholder = "Preview will appear here."
        self.settings_geometry = None
        self.settings_monitor_index = 0
        self.monitor_id_windows = []
        self.monitors = self.get_monitors()
        self.monitor_index = 0
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
        self.text_canvas = tk.Canvas(self.root, bg=self.bg_color, highlightthickness=0)
        self.text_canvas.grid(row=0, column=0, sticky='nsew', padx=10, pady=10)
        self.text_padding = 24
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
        self.bad_words = {
            "fuck",
            "shit",
            "ass",
            "bitch",
            "damn",
            "hell",
            "crap",
            "piss",
            "dick",
            "cock",
            "pussy",
            "tits",
            "cunt",
            "bastard",
            "slut",
            "whore",
        }
        self.api_key = ""  # Google STT API key
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
        self.chunk_size = 65
        self.chunk_delay_ms = 300
        self.flush_timeout_ms = 2000
        self.pending_text = ""
        self.flush_after_id = None
        self.source_lang = "auto"
        self.target_lang = "en"
        self.transcription_mode = "google_free"
        self.custom_vocabulary = self.default_biblical_terms()
        self.biblical_books = self.default_biblical_books()
        self.translation_enabled = True
        self.is_paused = False
        self.scroll_speed_px = 20
        self.scroll_offset = 0.0
        self.scroll_last_time = time.time()
        self.scroll_after_id = None
        self.text_bbox_height = 0
        self.enable_scrolling = False
        self.load_settings()
        self.devices = self.get_audio_devices()
        self.microphone_index = 0 if self.devices else None
        self._apply_scaled_fonts()
        self.apply_colors()
        self.render_text()
        self.start_scroll_loop()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self._install_exception_hook()
        
        self.open_settings()
        if self.settings_window is not None and self.settings_window.winfo_exists():
            try:
                self.settings_window.focus_force()
            except Exception:
                pass

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
        self.api_key = data.get("api_key", self.api_key)
        self.openai_api_key = data.get("openai_api_key", self.openai_api_key)
        self.bg_color = data.get("bg_color", self.bg_color)
        self.text_color = data.get("text_color", self.text_color)
        self.max_lines = data.get("max_lines", self.max_lines)
        self.bad_words = set(data.get("bad_words", list(self.bad_words)))
        self.chunk_size = data.get("chunk_size", self.chunk_size)
        self.chunk_delay_ms = data.get("chunk_delay_ms", self.chunk_delay_ms)
        self.flush_timeout_ms = data.get("flush_timeout_ms", self.flush_timeout_ms)
        self.sentence_flush_ms = data.get("sentence_flush_ms", self.sentence_flush_ms)
        self.source_lang = data.get("source_lang", self.source_lang)
        self.target_lang = data.get("target_lang", self.target_lang)
        self.translation_enabled = bool(data.get("translation_enabled", self.translation_enabled))
        self.transcription_mode = data.get("transcription_mode", self.transcription_mode)
        self.cloud_auto_punct = bool(data.get("cloud_auto_punct", self.cloud_auto_punct))
        self.custom_vocabulary = data.get("custom_vocabulary", self.custom_vocabulary)
        self.biblical_books = data.get("biblical_books", self.biblical_books)
        self.scroll_speed_px = data.get("scroll_speed_px", self.scroll_speed_px)
        self.allow_loopback = data.get("allow_loopback", self.allow_loopback)
        self.preferred_device_label = data.get(
            "preferred_device_label", self.preferred_device_label
        )
        try:
            self.loopback_chunk_seconds = float(
                data.get("loopback_chunk_seconds", self.loopback_chunk_seconds)
            )
        except Exception:
            pass
        self.preferred_host_api = data.get("preferred_host_api", self.preferred_host_api)
        self.rms_gate_enabled = bool(data.get("rms_gate_enabled", self.rms_gate_enabled))
        self.rms_gate_factor = float(data.get("rms_gate_factor", self.rms_gate_factor))
        self.enable_scrolling = data.get("enable_scrolling", self.enable_scrolling)
        self.settings_geometry = data.get("settings_geometry", self.settings_geometry)
        self.settings_monitor_index = int(data.get("settings_monitor_index", self.settings_monitor_index))
        self.monitor_index = int(data.get("monitor_index", self.monitor_index))
        if not self.monitors:
            self.monitors = self.get_monitors()
        if self.monitors:
            self.monitor_index = max(0, min(self.monitor_index, len(self.monitors) - 1))
            self.settings_monitor_index = max(0, min(self.settings_monitor_index, len(self.monitors) - 1))

    def save_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            try:
                self.settings_geometry = self.settings_window.geometry()
            except Exception:
                pass
        data = {
            "api_key": self.api_key,
            "openai_api_key": self.openai_api_key,
            "bg_color": self.bg_color,
            "text_color": self.text_color,
            "max_lines": self.max_lines,
            "bad_words": sorted(self.bad_words),
            "chunk_size": self.chunk_size,
            "chunk_delay_ms": self.chunk_delay_ms,
            "flush_timeout_ms": self.flush_timeout_ms,
            "sentence_flush_ms": self.sentence_flush_ms,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "translation_enabled": self.translation_enabled,
            "transcription_mode": self.transcription_mode,
            "cloud_auto_punct": self.cloud_auto_punct,
            "custom_vocabulary": self.custom_vocabulary,
            "biblical_books": self.biblical_books,
            "scroll_speed_px": self.scroll_speed_px,
            "allow_loopback": self.allow_loopback,
            "preferred_device_label": self.preferred_device_label,
            "loopback_chunk_seconds": self.loopback_chunk_seconds,
            "preferred_host_api": self.preferred_host_api,
            "rms_gate_enabled": self.rms_gate_enabled,
            "rms_gate_factor": self.rms_gate_factor,
            "enable_scrolling": self.enable_scrolling,
            "monitor_index": self.monitor_index,
            "settings_geometry": self.settings_geometry,
            "settings_monitor_index": self.settings_monitor_index,
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
                    return monitors
            except Exception:
                pass

        try:
            self.root.update_idletasks()
            width = self.root.winfo_screenwidth()
            height = self.root.winfo_screenheight()
        except Exception:
            width = 1920
            height = 1080
        return [{"left": 0, "top": 0, "right": width, "bottom": height, "device": "", "primary": True}]

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

    def enter_fullscreen(self):
        if not self.is_fullscreen:
            return
        if self.prev_geometry is None:
            self.prev_geometry = self.root.geometry()
        if self.use_custom_fullscreen:
            self._prepare_custom_fullscreen_state()
            self._apply_custom_fullscreen()
        else:
            self._apply_standard_fullscreen()

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
            if self.prev_overrideredirect is not None:
                try:
                    self.root.overrideredirect(self.prev_overrideredirect)
                except Exception:
                    pass
            if self.prev_topmost is not None:
                try:
                    self.root.attributes("-topmost", self.prev_topmost)
                except Exception:
                    pass
        else:
            self.root.attributes("-fullscreen", False)
        if self.prev_geometry:
            self.root.geometry(self.prev_geometry)
        self.prev_geometry = None
    
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
        preferred_api = self._normalize_host_api(self.preferred_host_api)
        if preferred_api:
            filtered_inputs = self._filter_entries_by_host_api(input_devices, preferred_api)
            filtered_outputs = self._filter_entries_by_host_api(output_devices, preferred_api)
            filtered_loopbacks = self._filter_entries_by_host_api(loopback_inputs, preferred_api)
            if filtered_inputs or filtered_outputs:
                input_devices = filtered_inputs
                output_devices = filtered_outputs
                loopback_inputs = filtered_loopbacks
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
        index = entry.get("index", 0)
        name = entry.get("name", "Unknown")
        return f"{device_type} ({index}) [{host_api}]: {name}"

    def _group_device_entries(self, entries):
        grouped = {}
        order = []
        for entry in entries:
            name = entry.get("name", "")
            key = self._normalize_device_name(name) or name.lower()
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(entry)

        grouped_entries = []
        for key in order:
            grouped_entries.extend(
                sorted(
                    grouped[key],
                    key=lambda item: (
                        item.get("host_api") or "",
                        item.get("index", 0),
                    ),
                )
            )
        return grouped_entries

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

        for entry in self._group_device_entries(input_devices):
            label = self._format_device_label("Input", entry)
            self._register_device_label(devices, label, entry.get("index", 0), "input")

        if self.allow_loopback:
            for entry in self._group_device_entries(output_devices):
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
        settings_bg = "#f7f7f7"
        section_bg = "#ffffff"
        settings_fg = "#222222"
        settings_window.configure(bg=settings_bg)
        label_opts = {"bg": settings_bg, "fg": settings_fg}
        section_font = (self.font_family, 12, "bold")

        settings_window.protocol("WM_DELETE_WINDOW", self.on_closing)

        content = self._build_settings_canvas(settings_window, settings_bg)
        display_vars, audio_vars, filters_vars, api_vars, translation_vars = self._build_settings_sections(
            content,
            settings_window,
            label_opts,
            section_bg,
            settings_fg,
            section_font,
        )
        self._update_api_key_visibility(audio_vars, api_vars)

        def on_engine_change(*_args):
            self._update_api_key_visibility(audio_vars, api_vars)

        audio_vars["transcription_var"].trace_add("write", on_engine_change)
        
        def save_settings():
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
                )
                self._show_apply_success()
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
                try:
                    save_button.config(state=tk.NORMAL)
                except Exception:
                    pass

        button_frame = tk.Frame(settings_window, bg=settings_bg)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(0, 12))

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
            font=(self.font_family, 10),
            bd=0,
            highlightthickness=0,
        )
        self.status_label.pack(fill=tk.X)

        self.pause_button = tk.Button(
            status_section,
            text="Pause",
            command=self.toggle_pause,
        )
        self.pause_button.pack(anchor="w", pady=(8, 0))

        self.test_mic_button = tk.Button(
            status_section,
            text="Test Mic",
            command=self.run_mic_test,
        )
        self.test_mic_button.pack(anchor="w", pady=(6, 0))

        save_button = tk.Button(button_frame, text="Apply", command=save_settings)
        save_button.pack(side=tk.RIGHT, padx=10, pady=10)

    def _apply_settings_vars(
        self,
        display_vars,
        audio_vars,
        filters_vars,
        api_vars,
        translation_vars,
    ):
        self._apply_display_vars(display_vars)
        self._apply_filter_vars(filters_vars)
        self._apply_api_vars(api_vars)
        self._apply_translation_vars(translation_vars)
        self._apply_audio_vars(audio_vars)
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
        self.chunk_size = max(20, int(display_vars["chunk_size_var"].get()))
        self.chunk_delay_ms = max(50, int(display_vars["chunk_delay_var"].get()))
        self.sentence_flush_ms = max(300, int(display_vars["sentence_flush_var"].get()))
        self.scroll_speed_px = max(5, int(display_vars["scroll_speed_var"].get()))
        self.enable_scrolling = bool(display_vars["scroll_enabled_var"].get())
        monitor_labels = display_vars["monitor_labels"]
        monitor_value = display_vars["monitor_var"].get()
        settings_monitor_value = display_vars["settings_monitor_var"].get()
        if monitor_value in monitor_labels:
            self.monitor_index = monitor_labels.index(monitor_value)
        if settings_monitor_value in monitor_labels:
            self.settings_monitor_index = monitor_labels.index(settings_monitor_value)

    def _apply_filter_vars(self, filters_vars):
        bad_words_str = filters_vars["bad_words_text"].get("1.0", tk.END).strip()
        self.bad_words = {word.strip().lower() for word in bad_words_str.split(",") if word.strip()}

    def _apply_api_vars(self, api_vars):
        self.api_key = api_vars["api_key_var"].get().strip()
        if "openai_api_key_var" in api_vars:
            self.openai_api_key = api_vars["openai_api_key_var"].get().strip()

    def _apply_translation_vars(self, translation_vars):
        self.source_lang = translation_vars["lang_map"].get(
            translation_vars["source_lang_var"].get(),
            "auto",
        )
        self.target_lang = translation_vars["lang_map"].get(
            translation_vars["target_lang_var"].get(),
            "en",
        )
        if "transcribe_only_var" in translation_vars:
            self.translation_enabled = not bool(translation_vars["transcribe_only_var"].get())

    def _apply_audio_vars(self, audio_vars):
        self.transcription_mode = audio_vars["transcription_map"].get(
            audio_vars["transcription_var"].get(),
            "google_free",
        )
        cloud_var = audio_vars.get("cloud_auto_punct_var")
        if cloud_var is not None:
            self.cloud_auto_punct = bool(cloud_var.get())
        vocab_str = audio_vars["vocab_text"].get("1.0", tk.END).strip()
        self.custom_vocabulary = [v.strip() for v in vocab_str.split(",") if v.strip()]
        self.allow_loopback = bool(audio_vars["loopback_var"].get())
        try:
            loopback_chunk = float(audio_vars["loopback_chunk_var"].get())
        except Exception:
            loopback_chunk = self.loopback_chunk_seconds
        self.loopback_chunk_seconds = max(0.2, min(loopback_chunk, 5.0))
        host_api_label = audio_vars["host_api_var"].get().strip()
        host_api_value = audio_vars.get("host_api_map", {}).get(host_api_label, host_api_label)
        self.preferred_host_api = "" if host_api_value == "Any" else host_api_value
        self.rms_gate_enabled = bool(audio_vars["rms_gate_var"].get())
        try:
            self.rms_gate_factor = float(audio_vars["rms_gate_factor_var"].get())
        except Exception:
            pass
        self.rms_gate_factor = max(0.5, min(self.rms_gate_factor, 5.0))

    def _refresh_audio_devices(self):
        # Refresh device list if loopback setting changed.
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
        if self.settings_geometry:
            try:
                settings_window.geometry(self.settings_geometry)
            except Exception:
                self.settings_geometry = None
        if not self.settings_geometry:
            self._position_settings_window(settings_window)

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
            font=(self.font_family, 10, "bold"),
            cursor="question_arrow",
        )
        icon.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(icon, help_text)
        return icon
    
    def _validate_int_entry(self, proposed):
        return proposed == "" or proposed.isdigit()

    def _compute_scaled_font_size(self):
        base_size = max(12, int(self.font_size))
        lines = max(1, int(self.max_lines))
        scaled = int(round(base_size * (8.0 / lines)))
        return max(12, min(scaled, 120))

    def _apply_scaled_fonts(self):
        self._fit_font_to_lines()

    def _fit_font_to_lines(self, max_size=None):
        height = self.text_canvas.winfo_height()
        if height <= 1:
            return
        available = max(1, height - (self.text_padding * 2))
        lines = max(1, int(self.max_lines))
        approx = max(12, int(available / max(1, lines)))
        max_size = int(max_size or min(320, int(approx * 1.6)))
        min_size = 12

        def fits(size):
            self.text_font.configure(size=size)
            line_height = self.text_font.metrics("linespace") or 1
            return (line_height * lines) <= available

        lo, hi = min_size, max_size
        best = min_size
        while lo <= hi:
            mid = (lo + hi) // 2
            if fits(mid):
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

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
            self.text_canvas.itemconfigure(self.text_line_items[idx], text=line, font=self.text_font)

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

        return display_vars, audio_vars, filters_vars, api_vars, translation_vars

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
        bg_entry.pack(side=tk.LEFT)
        bg_button = tk.Button(
            bg_frame,
            text="Choose",
            command=lambda: self.choose_color(bg_color_var, "background", settings_window),
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
        text_entry.pack(side=tk.LEFT)
        text_button = tk.Button(
            text_frame,
            text="Choose",
            command=lambda: self.choose_color(text_color_var, "text", settings_window),
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
        settings_monitor_menu.pack(anchor="w")

        def on_settings_monitor_change(*_args):
            if settings_monitor_var.get() in monitor_labels:
                self.settings_monitor_index = monitor_labels.index(settings_monitor_var.get())
                self.move_window_to_monitor(settings_window, self.settings_monitor_index, keep_size=True)

        def on_output_monitor_change(*_args):
            if monitor_var.get() in monitor_labels:
                self.monitor_index = monitor_labels.index(monitor_var.get())
                if self.is_fullscreen:
                    self.enter_fullscreen()
                else:
                    self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
                    self.root.after(0, self.maximize_window)

        # Also handle programmatic changes.
        settings_monitor_var.trace_add("write", lambda *_args: on_settings_monitor_change())
        monitor_var.trace_add("write", lambda *_args: on_output_monitor_change())

        monitor_id_button = tk.Button(
            display_section,
            text="Show Monitor Numbers",
            command=self.show_monitor_ids,
        )
        monitor_id_button.pack(anchor="w", pady=(8, 0))

        def start_fullscreen():
            if not self.is_fullscreen:
                self.is_fullscreen = True
            self.enter_fullscreen()
            self.hide_status()

        start_fullscreen_button = tk.Button(
            display_section,
            text="Start Fullscreen",
            command=start_fullscreen,
        )
        start_fullscreen_button.pack(anchor="w", pady=(8, 0))

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

        self._add_setting_label(
            display_section,
            "Text Chunk Size (chars):",
            "Target character length before batching text into a line.",
            label_opts,
            pady=(10, 4),
        )
        chunk_size_var = tk.IntVar(value=self.chunk_size)
        chunk_size_spin = tk.Spinbox(display_section, from_=20, to=300, textvariable=chunk_size_var)
        chunk_size_spin.pack(anchor="w")

        self._add_setting_label(
            display_section,
            "Chunk Delay (ms):",
            "Delay between displaying chunks or lines.",
            label_opts,
            pady=(10, 4),
        )
        chunk_delay_var = tk.IntVar(value=self.chunk_delay_ms)
        chunk_delay_spin = tk.Spinbox(display_section, from_=50, to=2000, increment=50, textvariable=chunk_delay_var)
        chunk_delay_spin.pack(anchor="w")

        self._add_setting_label(
            display_section,
            "Response Delay (ms):",
            "Wait time after last speech before flushing a sentence.",
            label_opts,
            pady=(10, 4),
        )
        sentence_flush_var = tk.IntVar(value=self.sentence_flush_ms)
        sentence_flush_spin = tk.Spinbox(
            display_section,
            from_=300,
            to=3000,
            increment=100,
            textvariable=sentence_flush_var,
        )
        sentence_flush_spin.pack(anchor="w")

        self._add_setting_label(
            display_section,
            "Scroll Speed (px/sec):",
            "Pixels per second when scrolling is enabled.",
            label_opts,
            pady=(10, 4),
        )
        scroll_speed_var = tk.IntVar(value=self.scroll_speed_px)
        scroll_speed_spin = tk.Spinbox(display_section, from_=5, to=200, increment=5, textvariable=scroll_speed_var)
        scroll_speed_spin.pack(anchor="w")

        scroll_enabled_var = tk.BooleanVar(value=self.enable_scrolling)
        scroll_row = tk.Frame(display_section, bg=section_bg)
        scroll_row.pack(anchor="w", pady=(6, 0), fill=tk.X)
        scroll_enabled_check = tk.Checkbutton(
            scroll_row,
            text="Enable scrolling (beta)",
            variable=scroll_enabled_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        scroll_enabled_check.pack(side=tk.LEFT)
        self._create_help_icon(
            scroll_row,
            "Scroll lines upward as new text arrives.",
            section_bg,
            settings_fg,
        )

        return {
            "lines_var": lines_var,
            "bg_color_var": bg_color_var,
            "text_color_var": text_color_var,
            "monitor_var": monitor_var,
            "settings_monitor_var": settings_monitor_var,
            "monitor_labels": monitor_labels,
            "chunk_size_var": chunk_size_var,
            "chunk_delay_var": chunk_delay_var,
            "sentence_flush_var": sentence_flush_var,
            "scroll_speed_var": scroll_speed_var,
            "scroll_enabled_var": scroll_enabled_var,
        }

    def _build_audio_section(self, audio_section, label_opts, section_bg, settings_fg):
        self._add_setting_label(
            audio_section,
            "Audio Device:",
            "Input or loopback device used for speech capture.",
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

        host_api_values = ["Any"]
        if self.available_host_apis:
            host_api_values.extend(self.available_host_apis)
        if self.preferred_host_api and self.preferred_host_api not in host_api_values:
            host_api_values.append(self.preferred_host_api)
        recommended_api = self._pick_recommended_host_api(host_api_values)
        host_api_labels = []
        host_api_map = {}
        for value in host_api_values:
            label = value
            if recommended_api and value == recommended_api:
                label = f"{value} (Recommended)"
            host_api_labels.append(label)
            host_api_map[label] = value
        host_api_value = self.preferred_host_api or "Any"
        host_api_label = next(
            (label for label, value in host_api_map.items() if value == host_api_value),
            host_api_value,
        )

        self._add_setting_label(
            audio_section,
            "Preferred Host API (optional):",
            "Filter devices to a specific host API (for example, Windows WASAPI).",
            label_opts,
            pady=(10, 4),
        )
        host_api_var = tk.StringVar(value=host_api_label)
        host_api_menu = tk.OptionMenu(audio_section, host_api_var, *host_api_labels)
        host_api_menu.pack(anchor="w")
        def on_host_api_change(*_args):
            host_api_label_value = host_api_var.get().strip()
            host_api_value = host_api_map.get(host_api_label_value, host_api_label_value)
            self.preferred_host_api = "" if host_api_value == "Any" else host_api_value
            self._refresh_audio_devices()
            self.save_settings()

        host_api_var.trace_add("write", on_host_api_change)

        loopback_var = tk.BooleanVar(value=self.allow_loopback)
        loopback_row = tk.Frame(audio_section, bg=section_bg)
        loopback_row.pack(anchor="w", pady=(6, 0), fill=tk.X)
        loopback_check = tk.Checkbutton(
            loopback_row,
            text="Allow output/loopback capture (PipeWire/WASAPI)",
            variable=loopback_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        loopback_check.pack(side=tk.LEFT)
        self._create_help_icon(
            loopback_row,
            "Allow choosing output devices and capture via loopback inputs.",
            section_bg,
            settings_fg,
        )

        loopback_chunk_container = tk.Frame(audio_section, bg=section_bg)

        self._add_setting_label(
            loopback_chunk_container,
            "Loopback Chunk (seconds):",
            "Shorter chunks reduce latency but increase API calls.",
            label_opts,
            pady=(10, 4),
        )
        loopback_chunk_var = tk.DoubleVar(value=self.loopback_chunk_seconds)
        loopback_chunk_spin = tk.Spinbox(
            loopback_chunk_container,
            from_=0.2,
            to=5.0,
            increment=0.1,
            textvariable=loopback_chunk_var,
        )
        loopback_chunk_spin.pack(anchor="w")

        def _toggle_loopback_chunk(*_args):
            if loopback_var.get():
                loopback_chunk_container.pack(anchor="w", fill=tk.X)
            else:
                loopback_chunk_container.pack_forget()

        loopback_var.trace_add("write", _toggle_loopback_chunk)
        _toggle_loopback_chunk()

        gate_row = tk.Frame(audio_section, bg=section_bg)
        gate_row.pack(anchor="w", pady=(6, 0), fill=tk.X)
        rms_gate_var = tk.BooleanVar(value=self.rms_gate_enabled)
        rms_gate_check = tk.Checkbutton(
            gate_row,
            text="Ignore low-energy audio (RMS gate)",
            variable=rms_gate_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        rms_gate_check.pack(side=tk.LEFT)
        self._create_help_icon(
            gate_row,
            "Skip recognition when audio energy is below the ambient noise threshold.",
            section_bg,
            settings_fg,
        )

        self._add_setting_label(
            audio_section,
            "RMS Gate Factor:",
            "Multiplier on the ambient noise energy threshold. Higher = stricter.",
            label_opts,
            pady=(10, 4),
        )
        rms_gate_factor_var = tk.DoubleVar(value=self.rms_gate_factor)
        rms_gate_spin = tk.Spinbox(
            audio_section,
            from_=0.5,
            to=5.0,
            increment=0.1,
            textvariable=rms_gate_factor_var,
        )
        rms_gate_spin.pack(anchor="w")

        self._add_setting_label(
            audio_section,
            "Speech API Engine:",
            "Speech-to-text backend used for recognition.",
            label_opts,
            pady=(10, 4),
        )
        transcription_options = [
            ("Google (Free)", "google_free"),
            ("Google Cloud (API Key)", "google_cloud"),
            ("OpenAI (gpt-4o-mini-transcribe)", "openai_whisper"),
        ]
        transcription_display = [name for name, _ in transcription_options]
        transcription_map = dict(transcription_options)
        rev_transcription_map = {code: name for name, code in transcription_options}
        transcription_var = tk.StringVar(
            value=rev_transcription_map.get(self.transcription_mode, "Google (Free)")
        )
        transcription_menu = tk.OptionMenu(audio_section, transcription_var, *transcription_display)
        transcription_menu.pack(anchor="w")

        cloud_punct_row = tk.Frame(audio_section, bg=section_bg)
        cloud_punct_row.pack(anchor="w", pady=(6, 0), fill=tk.X)
        cloud_auto_punct_var = tk.BooleanVar(value=self.cloud_auto_punct)
        cloud_punct_check = tk.Checkbutton(
            cloud_punct_row,
            text="Google Cloud auto punctuation",
            variable=cloud_auto_punct_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        cloud_punct_check.pack(side=tk.LEFT)
        self._create_help_icon(
            cloud_punct_row,
            "Enable Google's automatic punctuation. Disable to reduce mid-sentence breaks.",
            section_bg,
            settings_fg,
        )

        self._add_setting_label(
            audio_section,
            "Custom Vocabulary (comma-separated):",
            "Words or phrases to bias recognition and preserve capitalization.",
            label_opts,
            pady=(10, 4),
        )
        vocab_text = tk.Text(audio_section, height=4, width=50)
        vocab_text.insert(tk.END, ", ".join(self.custom_vocabulary))
        vocab_text.pack(anchor="w")

        return {
            "loopback_var": loopback_var,
            "loopback_chunk_var": loopback_chunk_var,
            "transcription_var": transcription_var,
            "transcription_map": transcription_map,
            "cloud_auto_punct_var": cloud_auto_punct_var,
            "vocab_text": vocab_text,
            "host_api_var": host_api_var,
            "host_api_map": host_api_map,
            "rms_gate_var": rms_gate_var,
            "rms_gate_factor_var": rms_gate_factor_var,
        }

    def _build_filters_section(self, filters_section, label_opts, section_bg):
        self._add_setting_label(
            filters_section,
            "Bad words filter:",
            "Words to mask with *** in the output.",
            label_opts,
            pady=(0, 4),
        )
        toggle_var = tk.BooleanVar(value=False)

        bad_words_container = tk.Frame(filters_section, bg=section_bg)
        bad_words_container.pack(fill=tk.BOTH, expand=True)
        bad_words_container.pack_forget()

        bad_words_text = tk.Text(bad_words_container, height=5, width=50)
        bad_words_text.insert(tk.END, ', '.join(sorted(self.bad_words)))
        bad_words_text.pack(anchor="w")

        def toggle_bad_words():
            if toggle_var.get():
                bad_words_container.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
                toggle_button.config(text="Hide list")
            else:
                bad_words_container.pack_forget()
                toggle_button.config(text="Show list")

        toggle_button = tk.Button(
            filters_section,
            text="Edit filter",
            command=lambda: toggle_var.set(not toggle_var.get()) or toggle_bad_words(),
        )
        toggle_button.pack(anchor="w")

        return {"bad_words_text": bad_words_text}

    def _build_api_section(self, api_section, label_opts):
        google_key_container = tk.Frame(api_section, bg=label_opts["bg"])
        google_key_container.pack(fill=tk.X, pady=(0, 10))
        self._add_setting_label(
            google_key_container,
            "Google STT API Key (optional):",
            "API key for Google Cloud Speech-to-Text (used when engine is Google Cloud).",
            label_opts,
            pady=(0, 4),
        )
        api_key_var = tk.StringVar(value=self.api_key)
        api_key_frame = tk.Frame(google_key_container, bg=label_opts["bg"])
        api_key_frame.pack(fill=tk.X)
        api_key_entry = tk.Entry(api_key_frame, textvariable=api_key_var, width=50, show="*")
        api_key_entry.pack(side=tk.LEFT)

        show_var = tk.BooleanVar(value=False)

        def toggle_show():
            show = "" if show_var.get() else "*"
            api_key_entry.config(show=show)

        show_button = tk.Checkbutton(
            api_key_frame,
            text="Show",
            variable=show_var,
            command=toggle_show,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            selectcolor=label_opts["bg"],
            activebackground=label_opts["bg"],
        )
        show_button.pack(side=tk.LEFT, padx=(8, 0))
        self._create_help_icon(
            api_key_frame,
            "Reveal or hide the API key in this field.",
            label_opts["bg"],
            label_opts["fg"],
        )

        openai_key_container = tk.Frame(api_section, bg=label_opts["bg"])
        openai_key_container.pack(fill=tk.X)
        self._add_setting_label(
            openai_key_container,
            "OpenAI API Key (optional):",
            "API key for OpenAI transcribe (used when engine is OpenAI).",
            label_opts,
            pady=(0, 4),
        )
        openai_key_var = tk.StringVar(value=self.openai_api_key)
        openai_key_frame = tk.Frame(openai_key_container, bg=label_opts["bg"])
        openai_key_frame.pack(fill=tk.X)
        openai_key_entry = tk.Entry(openai_key_frame, textvariable=openai_key_var, width=50, show="*")
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
        return {
            "api_key_var": api_key_var,
            "openai_api_key_var": openai_key_var,
            "google_key_container": google_key_container,
            "openai_key_container": openai_key_container,
        }

    def _update_api_key_visibility(self, audio_vars, api_vars):
        mode = audio_vars["transcription_map"].get(
            audio_vars["transcription_var"].get(),
            "google_free",
        )
        google_container = api_vars.get("google_key_container")
        openai_container = api_vars.get("openai_key_container")

        def toggle(container, show, pady):
            if container is None:
                return
            if show:
                if not container.winfo_ismapped():
                    container.pack(fill=tk.X, pady=pady)
            else:
                if container.winfo_ismapped():
                    container.pack_forget()

        toggle(google_container, mode == "google_cloud", pady=(0, 10))
        toggle(openai_container, mode == "openai_whisper", pady=(0, 0))

    def _build_translation_section(self, translation_section, label_opts):
        self._add_setting_label(
            translation_section,
            "Translate from:",
            "Source language for the speech text (auto-detect available).",
            label_opts,
            pady=(0, 4),
        )
        lang_options = [
            ("Auto Detect", "auto"),
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

        source_lang_var = tk.StringVar(value=rev_lang_map.get(self.source_lang, "Auto Detect"))
        source_menu = tk.OptionMenu(translation_section, source_lang_var, *lang_display)
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
        target_menu.pack(anchor="w")

        transcribe_only_var = tk.BooleanVar(value=not self.translation_enabled)
        transcribe_row = tk.Frame(translation_section, bg=label_opts["bg"])
        transcribe_row.pack(anchor="w", pady=(8, 0), fill=tk.X)
        transcribe_check = tk.Checkbutton(
            transcribe_row,
            text="Transcribe only (disable translation)",
            variable=transcribe_only_var,
            bg=label_opts["bg"],
            fg=label_opts["fg"],
            selectcolor=label_opts["bg"],
            activebackground=label_opts["bg"],
        )
        transcribe_check.pack(side=tk.LEFT)
        self._create_help_icon(
            transcribe_row,
            "Show raw speech transcription without translating.",
            label_opts["bg"],
            label_opts["fg"],
        )

        return {
            "source_lang_var": source_lang_var,
            "target_lang_var": target_lang_var,
            "lang_map": lang_map,
            "transcribe_only_var": transcribe_only_var,
        }
        
    def choose_color(self, color_var, color_type, parent):
        color = colorchooser.askcolor(title=f"Choose {color_type} color", parent=parent)
        if color[1]:  # color[1] is the hex value
            color_var.set(color[1])
    
    def apply_colors(self):
        self.text_canvas.config(bg=self.bg_color)
        self.text_canvas.itemconfigure(self.text_item, fill=self.text_color)
        for item in self.text_line_items:
            self.text_canvas.itemconfigure(item, fill=self.text_color)
        if self.preview_widget is not None and self.preview_widget.winfo_exists():
            self.preview_widget.config(bg=self.bg_color, fg=self.text_color)
    
    def listen_and_translate(self):
        self._start_capture_thread()
        while self.listening:
            try:
                if self._pause_if_needed():
                    continue
                self._flush_sentence_buffer_if_due()
                self._drain_sentence_queue()
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
            if self.transcription_mode == "google_cloud":
                if not self.api_key:
                    raise ValueError("Google Cloud selected but API key is empty")
                text = self.recognize_google_rest(audio, self.api_key)
            elif self.transcription_mode == "openai_whisper":
                if not self.openai_api_key:
                    raise ValueError("OpenAI Whisper selected but API key is empty")
                text = self.recognize_openai_whisper(audio, self.openai_api_key)
            else:
                text = self.recognizer.recognize_google(audio)
            text = text.strip()
            if not text:
                return ""
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
                self._translate_and_display(sentence)
    
    def recognize_google_rest(self, audio, api_key):
        url = f"https://speech.googleapis.com/v1/speech:recognize?key={api_key}"
        headers = {'Content-Type': 'application/json'}
        audio_data = audio.get_raw_data()
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        data = {
            "config": {
                "encoding": "LINEAR16",
                "sampleRateHertz": audio.sample_rate,
                "languageCode": "en-US",
                "enableAutomaticPunctuation": bool(self.cloud_auto_punct),
                "speechContexts": [
                    {"phrases": self.custom_vocabulary}
                ],
            },
            "audio": {
                "content": audio_base64
            }
        }
        response = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
        if response.status_code == 200:
            result = response.json()
            if ('results' in result and result['results'] and 
                'alternatives' in result['results'][0] and 
                result['results'][0]['alternatives'] and
                'transcript' in result['results'][0]['alternatives'][0]):
                return result['results'][0]['alternatives'][0]['transcript']
            return ""
        else:
            raise sr.RequestError(f"API error {response.status_code}: {response.text}")

    def _maybe_openai_language(self):
        lang = (self.source_lang or "").strip().lower()
        if not lang or lang == "auto":
            return ""
        if len(lang) == 2 and lang.isalpha():
            return lang
        return ""

    def recognize_openai_whisper(self, audio, api_key):
        url = "https://api.openai.com/v1/audio/transcriptions"
        audio_bytes = audio.get_wav_data()
        file_obj = io.BytesIO(audio_bytes)
        files = {"file": ("audio.wav", file_obj, "audio/wav")}
        data = {
            "model": "gpt-4o-mini-transcribe",
            "response_format": "json",
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
        try:
            self.sentence_queue.put_nowait(buffer_text)
        except Exception:
            pass

    def _drain_sentence_queue(self):
        while True:
            try:
                sentence = self.sentence_queue.get_nowait()
            except queue.Empty:
                return
            self._translate_and_display(sentence)

    def _translate_with_google(self, text):
        return self.translator.translate(
            text,
            src=self.source_lang,
            dest=self.target_lang,
        ).text

    def _build_openai_translation_prompt(self, text):
        source = (self.source_lang or "auto").strip().lower()
        target = (self.target_lang or "en").strip().lower()
        if source and source != "auto":
            instruction = (
                f"Translate from {source} to {target}. "
                "Return only the translation."
            )
        else:
            instruction = f"Translate to {target}. Return only the translation."
        return f"{instruction}\n\n{text}"

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

    def _translate_and_display(self, text):
        self.update_status("Transcribing..." if not self.translation_enabled else "Translating...")
        try:
            if self.translation_enabled:
                translated = self._translate_text(text)
            else:
                translated = text
            translated = self.apply_custom_vocabulary(translated)
            translated = self.format_scripture_refs(translated)
            translated = self.clean_text_spacing(translated)
        except Exception as e:
            self.update_status(f"Translation error: {e}")
            translated = text
        if self.transcription_mode == "google_cloud" and not self.cloud_auto_punct:
            translated = self._apply_basic_punctuation(translated)
        self.update_text(translated)
        self.update_status(self.STATUS_LISTENING)
    
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
    
    def filter_bad_words(self, text):
        filtered = text
        for word in self.bad_words:
            pattern = r"\b" + re.escape(word) + r"\b"
            filtered = re.sub(pattern, '***', filtered, flags=re.IGNORECASE)
        return filtered

    def apply_custom_vocabulary(self, text):
        if not self.custom_vocabulary:
            return text
        replacements = {v.lower(): v for v in self.custom_vocabulary}
        def repl(match):
            key = match.group(0).lower()
            return replacements.get(key, match.group(0))
        pattern = r"\b(" + "|".join(re.escape(v) for v in self.custom_vocabulary) + r")\b"
        return re.sub(pattern, repl, text, flags=re.IGNORECASE)

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
    
    def update_display(self):
        def update():
            self.render_text()
        self.root.after(0, update)
    
    def update_status(self, msg):
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
        self.update_text_position()

    def update_text_position(self):
        if self.enable_scrolling:
            height = self.text_canvas.winfo_height()
            y = height - self.text_padding - self.scroll_offset
            self.text_canvas.coords(self.text_item, self.text_padding, y)

    def render_text(self):
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
        if self.enable_scrolling:
            self.text_canvas.itemconfigure(self.text_item, state="normal")
            for item in self.text_line_items:
                self.text_canvas.itemconfigure(item, state="hidden")
            self.text_canvas.itemconfigure(self.text_item, text=display_text, font=self.text_font, width=0)
        else:
            self.text_canvas.itemconfigure(self.text_item, text="", state="hidden")
            for item in self.text_line_items:
                self.text_canvas.itemconfigure(item, state="normal")
            self._update_line_items(display_lines)
        self.text_canvas.update_idletasks()
        self.update_preview(display_text)
        self.update_text_metrics()
        self.update_text_position()

    def update_preview(self, text):
        if not self.preview_widget:
            return

        def update():
            widget = self.preview_widget
            if not widget or not widget.winfo_exists():
                return
            widget.config(text=text if text else self.preview_placeholder)

        self.root.after(0, update)

    def update_text_metrics(self):
        if self.enable_scrolling:
            bbox = self.text_canvas.bbox(self.text_item)
            self.text_bbox_height = (bbox[3] - bbox[1]) if bbox else 0
        else:
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

    def start_scroll_loop(self):
        if self.scroll_after_id is not None:
            return
        self.scroll_last_time = time.time()
        self.scroll_after_id = self.root.after(16, self.scroll_tick)

    def scroll_tick(self):
        if not self.enable_scrolling:
            self.scroll_offset = 0.0
            self.update_text_position()
            self.scroll_after_id = self.root.after(200, self.scroll_tick)
            return
        now = time.time()
        dt = now - self.scroll_last_time
        self.scroll_last_time = now
        if not self.translations:
            self.scroll_offset = 0.0
            self.update_text_position()
            self.scroll_after_id = self.root.after(16, self.scroll_tick)
            return

        speed_scale = max(1.0, len(self.translations) / max(1, self.max_lines))
        self.scroll_offset += (self.scroll_speed_px * speed_scale) * dt
        line_height = self.text_font.metrics("linespace") or 1
        height = self.text_canvas.winfo_height()
        y = height - self.text_padding - self.scroll_offset
        top = y - self.text_bbox_height
        if top <= -line_height and len(self.translations) > 1:
            while top <= -line_height and len(self.translations) > 1:
                self.translations.pop(0)
                # Keep visual position stable when dropping a line.
                self.scroll_offset += line_height
                self.render_text()
                y = height - self.text_padding - self.scroll_offset
                top = y - self.text_bbox_height
        else:
            self.update_text_position()
        self.scroll_after_id = self.root.after(16, self.scroll_tick)


if __name__ == "__main__":
    app = TranslationApp()
