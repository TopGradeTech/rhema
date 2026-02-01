import speech_recognition as sr
from googletrans import Translator
import tkinter as tk
from tkinter import colorchooser
from tkinter import font as tkfont
from threading import Thread
import time
import re
import requests
import base64
import json
import pyaudio
from collections import deque
import os

class TranslationApp:
    def __init__(self):
        self.settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
        self.root = tk.Tk()
        self.root.title("Python Translation App")
        self.font_family = self.pick_font_family(
            ["DejaVu Sans", "Liberation Sans", "Arial", "Helvetica"]
        )
        self.translator = Translator()
        self.recognizer = sr.Recognizer()
        self.allow_loopback = False
        self.devices = self.get_audio_devices()
        self.microphone_index = 0 if self.devices else None
        
        # Restore window manager controls and menu for reliability.
        self.root.overrideredirect(False)
        self.menubar = tk.Menu(self.root)
        self.menubar.add_command(label="Settings", command=self.open_settings)
        self.root.config(menu=None)
        
        self.root.grid_rowconfigure(0, weight=8)  # 80% height for text
        self.root.grid_rowconfigure(1, weight=0)  # status line
        self.root.grid_columnconfigure(0, weight=1)
        
        self.bg_color = "#000000"  # Background color
        self.text_color = "#ffffff"  # Text color
        self.font_size = 50  # Font size

        self.text_font = tkfont.Font(family=self.font_family, size=self.font_size)
        self.text_canvas = tk.Canvas(self.root, bg=self.bg_color, highlightthickness=0)
        self.text_canvas.grid(row=0, column=0, sticky='nsew', padx=10, pady=10)
        self.text_padding = 10
        self.live_gap = 10
        self.history_item = self.text_canvas.create_text(
            self.text_padding,
            0,
            anchor="sw",
            text="",
            fill=self.text_color,
            font=self.text_font,
            width=0,
        )
        self.live_item = self.text_canvas.create_text(
            self.text_padding,
            0,
            anchor="sw",
            text="",
            fill=self.text_color,
            font=self.text_font,
            width=0,
        )
        self.text_canvas.bind("<Configure>", self.on_canvas_resize)
        
        self.status_label = tk.Label(
            self.root,
            text="",
            anchor="w",
            bg=self.bg_color,
            fg=self.text_color,
            font=(self.font_family, 10),
            bd=0,
            highlightthickness=0,
        )
        self.status_label.grid(row=1, column=0, sticky='sw', padx=10, pady=(0, 5))
        self.status_label.grid_remove()
        self.status_hide_after_id = None

        self.controls_frame = tk.Frame(self.root, bg=self.bg_color)
        self.controls_frame.grid(row=2, column=0, pady=10)

        self.fullscreen_button = tk.Button(
            self.controls_frame,
            text="Toggle Fullscreen",
            command=self.toggle_fullscreen,
        )
        self.fullscreen_button.pack(side=tk.LEFT, padx=(0, 8))

        self.pause_button = tk.Button(
            self.controls_frame,
            text="Pause",
            command=self.toggle_pause,
        )
        self.pause_button.pack(side=tk.LEFT)
        self.controls_frame.grid_remove()

        self.root.config(menu=None)
        
        self.apply_colors()  # Apply default colors
        
        self.is_fullscreen = True
        self.root.after(0, lambda: self.root.attributes("-fullscreen", True))
        self.root.bind_all("<F11>", self.toggle_fullscreen_event)
        self.root.bind_all("<Control-Alt-f>", self.toggle_fullscreen_event)
        self.root.bind_all("<Escape>", self.exit_fullscreen_event)
        self.root.bind_all("<Control-s>", self.open_settings_event)
        self.root.bind_all("<Control-q>", self.close_app_event)
        self.root.bind("<Motion>", self.on_mouse_move)
        self.root.focus_set()
        self.listening = True
        self.translations = []
        self.max_lines = 8  # Default number of lines
        self.bad_words = set(["fuck", "shit", "ass", "bitch", "damn", "hell", "crap", "piss", "dick", "cock", "pussy", "tits", "cunt", "bastard", "slut", "whore"])
        self.api_key = ""  # Google STT API key
        self.settings_window = None
        self.text_queue = deque()
        self.is_flushing_queue = False
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
        self.is_paused = False
        self.scroll_speed_px = 20
        self.scroll_offset = 0.0
        self.scroll_last_time = time.time()
        self.scroll_after_id = None
        self.history_bbox_height = 0
        self.live_bbox_height = 0
        self.history_lines = []
        self.live_lines = []
        self.enable_scrolling = False

        self.load_settings()
        self.text_font.configure(size=self.font_size)
        self.apply_colors()
        self.render_text()
        self.start_scroll_loop()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.thread = Thread(target=self.listen_and_translate)
        self.thread.daemon = True
        self.thread.start()
        
        self.root.mainloop()
    
    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes("-fullscreen", self.is_fullscreen)

    def toggle_fullscreen_event(self, event):
        self.toggle_fullscreen()

    def exit_fullscreen_event(self, event):
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.root.attributes("-fullscreen", False)
        return "break"

    def open_settings_event(self, event):
        self.show_status_temporarily()
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.destroy()
            self.settings_window = None
        else:
            self.open_settings()
        return "break"

    def close_app_event(self, event):
        self.on_closing()
        return "break"

    def on_mouse_move(self, event):
        self.show_status_temporarily()

    def show_status_temporarily(self, duration_ms=2000):
        self.status_label.grid()
        self.controls_frame.grid()
        self.root.config(menu=self.menubar)
        if self.status_hide_after_id is not None:
            self.root.after_cancel(self.status_hide_after_id)
        self.status_hide_after_id = self.root.after(duration_ms, self.hide_status)

    def hide_status(self):
        self.status_label.grid_remove()
        self.controls_frame.grid_remove()
        self.root.config(menu=None)
        self.status_hide_after_id = None

    def pick_font_family(self, candidates):
        available = set(tkfont.families())
        for name in candidates:
            if name in available:
                return name
        return "TkDefaultFont"
    
    def on_closing(self):
        self.listening = False
        self.root.quit()

    def load_settings(self):
        if not os.path.exists(self.settings_path):
            return
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self.api_key = data.get("api_key", self.api_key)
        self.bg_color = data.get("bg_color", self.bg_color)
        self.text_color = data.get("text_color", self.text_color)
        self.font_size = data.get("font_size", self.font_size)
        self.max_lines = data.get("max_lines", self.max_lines)
        self.bad_words = set(data.get("bad_words", list(self.bad_words)))
        self.chunk_size = data.get("chunk_size", self.chunk_size)
        self.chunk_delay_ms = data.get("chunk_delay_ms", self.chunk_delay_ms)
        self.flush_timeout_ms = data.get("flush_timeout_ms", self.flush_timeout_ms)
        self.source_lang = data.get("source_lang", self.source_lang)
        self.target_lang = data.get("target_lang", self.target_lang)
        self.transcription_mode = data.get("transcription_mode", self.transcription_mode)
        self.custom_vocabulary = data.get("custom_vocabulary", self.custom_vocabulary)
        self.biblical_books = data.get("biblical_books", self.biblical_books)
        self.scroll_speed_px = data.get("scroll_speed_px", self.scroll_speed_px)
        self.allow_loopback = data.get("allow_loopback", self.allow_loopback)
        self.enable_scrolling = data.get("enable_scrolling", self.enable_scrolling)

    def save_settings(self):
        data = {
            "api_key": self.api_key,
            "bg_color": self.bg_color,
            "text_color": self.text_color,
            "font_size": self.font_size,
            "max_lines": self.max_lines,
            "bad_words": sorted(self.bad_words),
            "chunk_size": self.chunk_size,
            "chunk_delay_ms": self.chunk_delay_ms,
            "flush_timeout_ms": self.flush_timeout_ms,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "transcription_mode": self.transcription_mode,
            "custom_vocabulary": self.custom_vocabulary,
            "biblical_books": self.biblical_books,
            "scroll_speed_px": self.scroll_speed_px,
            "allow_loopback": self.allow_loopback,
            "enable_scrolling": self.enable_scrolling,
        }
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
    
    def get_audio_devices(self):
        p = pyaudio.PyAudio()
        input_devices = []
        output_devices = []
        
        for i in range(p.get_device_count()):
            device_info = p.get_device_info_by_index(i)
            if device_info.get('maxInputChannels', 0) > 0:
                input_devices.append((i, device_info.get('name', 'Unknown')))
            elif device_info.get('maxOutputChannels', 0) > 0:
                output_devices.append((i, device_info.get('name', 'Unknown')))
        
        p.terminate()
        
        # Include input devices and optionally output devices (for loopback/monitor sources).
        devices = []
        self.device_indices = {}
        self.device_types = {}
        for idx, name in input_devices:
            label = f"Input ({idx}): {name}"
            devices.append(label)
            self.device_indices[label] = idx
            self.device_types[label] = 'input'
        if self.allow_loopback:
            for idx, name in output_devices:
                label = f"Output ({idx}): {name}"
                devices.append(label)
                self.device_indices[label] = idx
                self.device_types[label] = 'output'
            
        return devices if devices else ["No devices found"]
    
    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return

        settings_window = tk.Toplevel(self.root)
        self.settings_window = settings_window
        settings_window.title("Settings")
        settings_window.geometry("480x640")
        settings_window.minsize(480, 640)
        settings_window.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - settings_window.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - settings_window.winfo_height()) // 2
        settings_window.geometry(f"+{x}+{y}")
        settings_bg = "#f7f7f7"
        section_bg = "#ffffff"
        settings_fg = "#222222"
        settings_window.configure(bg=settings_bg)
        label_opts = {"bg": settings_bg, "fg": settings_fg}
        section_font = (self.font_family, 12, "bold")

        def on_settings_close():
            if self.settings_window is not None:
                self.settings_window.unbind_all("<MouseWheel>")
                self.settings_window.unbind_all("<Button-4>")
                self.settings_window.unbind_all("<Button-5>")
                self.settings_window.destroy()
                self.settings_window = None

        settings_window.protocol("WM_DELETE_WINDOW", on_settings_close)

        canvas = tk.Canvas(settings_window, bg=settings_bg, highlightthickness=0)
        scrollbar = tk.Scrollbar(settings_window, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        content = tk.Frame(canvas, bg=settings_bg)
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def on_canvas_configure(event):
            canvas.itemconfigure(canvas_window, width=event.width)

        def on_content_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", on_canvas_configure)
        content.bind("<Configure>", on_content_configure)
        def on_mousewheel(event):
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")

        settings_window.bind_all("<MouseWheel>", on_mousewheel)
        settings_window.bind_all("<Button-4>", on_mousewheel)
        settings_window.bind_all("<Button-5>", on_mousewheel)

        content.configure(padx=12, pady=12)

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
        
        tk.Label(display_section, text="Number of lines to show:", **label_opts).pack(anchor="w", pady=(0, 4))
        lines_var = tk.IntVar(value=self.max_lines)
        lines_spinbox = tk.Spinbox(display_section, from_=1, to=10, textvariable=lines_var)
        lines_spinbox.pack(fill=tk.X)

        tk.Label(display_section, text="Background Color:", **label_opts).pack(anchor="w", pady=(10, 4))
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
        
        tk.Label(display_section, text="Text Color:", **label_opts).pack(anchor="w", pady=(10, 4))
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
        
        tk.Label(display_section, text="Font Size:", **label_opts).pack(anchor="w", pady=(10, 4))
        font_size_var = tk.IntVar(value=self.font_size)
        font_size_scale = tk.Scale(display_section, from_=12, to=72, orient=tk.HORIZONTAL, variable=font_size_var)
        font_size_scale.pack(fill=tk.X)

        tk.Label(display_section, text="Text Chunk Size (chars):", **label_opts).pack(anchor="w", pady=(10, 4))
        chunk_size_var = tk.IntVar(value=self.chunk_size)
        chunk_size_spin = tk.Spinbox(display_section, from_=20, to=300, textvariable=chunk_size_var)
        chunk_size_spin.pack(fill=tk.X)

        tk.Label(display_section, text="Chunk Delay (ms):", **label_opts).pack(anchor="w", pady=(10, 4))
        chunk_delay_var = tk.IntVar(value=self.chunk_delay_ms)
        chunk_delay_spin = tk.Spinbox(display_section, from_=50, to=2000, increment=50, textvariable=chunk_delay_var)
        chunk_delay_spin.pack(fill=tk.X)

        tk.Label(display_section, text="Scroll Speed (px/sec):", **label_opts).pack(anchor="w", pady=(10, 4))
        scroll_speed_var = tk.IntVar(value=self.scroll_speed_px)
        scroll_speed_spin = tk.Spinbox(display_section, from_=5, to=200, increment=5, textvariable=scroll_speed_var)
        scroll_speed_spin.pack(fill=tk.X)

        scroll_enabled_var = tk.BooleanVar(value=self.enable_scrolling)
        scroll_enabled_check = tk.Checkbutton(
            display_section,
            text="Enable scrolling (beta)",
            variable=scroll_enabled_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        scroll_enabled_check.pack(anchor="w", pady=(6, 0))

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
        
        tk.Label(audio_section, text="Audio Device:", **label_opts).pack(anchor="w", pady=(0, 4))
        self.device_var = tk.StringVar(value=self.devices[self.microphone_index] if self.devices else "No devices")
        device_menu = tk.OptionMenu(audio_section, self.device_var, *self.devices)
        device_menu.pack(fill=tk.X)

        loopback_var = tk.BooleanVar(value=self.allow_loopback)
        loopback_check = tk.Checkbutton(
            audio_section,
            text="Allow output/loopback capture (PipeWire/WASAPI)",
            variable=loopback_var,
            bg=section_bg,
            fg=settings_fg,
            selectcolor=section_bg,
            activebackground=section_bg,
        )
        loopback_check.pack(anchor="w", pady=(6, 0))

        tk.Label(audio_section, text="Transcription Engine:", **label_opts).pack(anchor="w", pady=(10, 4))
        transcription_options = [
            ("Google (Free)", "google_free"),
            ("Google Cloud (API Key)", "google_cloud"),
        ]
        transcription_display = [name for name, _ in transcription_options]
        transcription_map = {name: code for name, code in transcription_options}
        rev_transcription_map = {code: name for name, code in transcription_options}
        transcription_var = tk.StringVar(
            value=rev_transcription_map.get(self.transcription_mode, "Google (Free)")
        )
        transcription_menu = tk.OptionMenu(audio_section, transcription_var, *transcription_display)
        transcription_menu.pack(fill=tk.X)

        tk.Label(audio_section, text="Custom Vocabulary (comma-separated):", **label_opts).pack(anchor="w", pady=(10, 4))
        vocab_text = tk.Text(audio_section, height=4, width=50)
        vocab_text.insert(tk.END, ", ".join(self.custom_vocabulary))
        vocab_text.pack(fill=tk.X)

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

        tk.Label(filters_section, text="Bad words filter:", **label_opts).pack(anchor="w", pady=(0, 4))
        toggle_var = tk.BooleanVar(value=False)

        bad_words_container = tk.Frame(filters_section, bg=section_bg)
        bad_words_container.pack(fill=tk.BOTH, expand=True)
        bad_words_container.pack_forget()

        bad_words_text = tk.Text(bad_words_container, height=5, width=50)
        bad_words_text.insert(tk.END, ', '.join(sorted(self.bad_words)))
        bad_words_text.pack(fill=tk.BOTH, expand=True)

        def toggle_bad_words():
            if toggle_var.get():
                bad_words_container.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
                toggle_button.config(text="Hide list")
            else:
                bad_words_container.pack_forget()
                toggle_button.config(text="Show list")

        toggle_button = tk.Button(filters_section, text="Edit filter", command=lambda: toggle_var.set(not toggle_var.get()) or toggle_bad_words())
        toggle_button.pack(anchor="w")

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
        
        tk.Label(api_section, text="Google STT API Key (optional):", **label_opts).pack(anchor="w", pady=(0, 4))
        api_key_var = tk.StringVar(value=self.api_key)
        api_key_entry = tk.Entry(api_section, textvariable=api_key_var, width=50)
        api_key_entry.pack(fill=tk.X)

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

        tk.Label(translation_section, text="Translate from:", **label_opts).pack(anchor="w", pady=(0, 4))
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
        lang_map = {name: code for name, code in lang_options}
        rev_lang_map = {code: name for name, code in lang_options}

        source_lang_var = tk.StringVar(value=rev_lang_map.get(self.source_lang, "Auto Detect"))
        source_menu = tk.OptionMenu(translation_section, source_lang_var, *lang_display)
        source_menu.pack(fill=tk.X)

        tk.Label(translation_section, text="Translate to:", **label_opts).pack(anchor="w", pady=(10, 4))
        target_lang_var = tk.StringVar(value=rev_lang_map.get(self.target_lang, "English"))
        target_menu = tk.OptionMenu(translation_section, target_lang_var, *lang_display)
        target_menu.pack(fill=tk.X)
        
        def save_settings():
            self.max_lines = lines_var.get()
            bad_words_str = bad_words_text.get("1.0", tk.END).strip()
            self.bad_words = set(word.strip().lower() for word in bad_words_str.split(',') if word.strip())
            self.api_key = api_key_var.get().strip()
            self.bg_color = bg_color_var.get()
            self.text_color = text_color_var.get()
            self.font_size = font_size_var.get()
            self.text_font.configure(size=self.font_size)
            self.chunk_size = max(20, int(chunk_size_var.get()))
            self.chunk_delay_ms = max(50, int(chunk_delay_var.get()))
            self.scroll_speed_px = max(5, int(scroll_speed_var.get()))
            self.enable_scrolling = bool(scroll_enabled_var.get())
            self.source_lang = lang_map.get(source_lang_var.get(), "auto")
            self.target_lang = lang_map.get(target_lang_var.get(), "en")
            self.transcription_mode = transcription_map.get(
                transcription_var.get(),
                "google_free",
            )
            vocab_str = vocab_text.get("1.0", tk.END).strip()
            self.custom_vocabulary = [v.strip() for v in vocab_str.split(",") if v.strip()]
            self.allow_loopback = bool(loopback_var.get())
            # Refresh device list if loopback setting changed.
            self.devices = self.get_audio_devices()
            if self.device_var.get() not in self.devices:
                self.device_var.set(self.devices[0] if self.devices else "No devices")
            if self.device_var.get() in self.device_indices:
                self.microphone_index = self.devices.index(self.device_var.get())
            else:
                self.microphone_index = None
            self.apply_colors()
            self.update_display()
            self.save_settings()
            # Don't destroy here, let user close manually
        
        button_frame = tk.Frame(settings_window, bg=settings_bg)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(0, 12))

        save_button = tk.Button(button_frame, text="Save", command=save_settings)
        save_button.pack(side=tk.LEFT, padx=10, pady=10)
        
        close_button = tk.Button(button_frame, text="Close", command=on_settings_close)
        close_button.pack(side=tk.RIGHT, padx=10, pady=10)
    
    def choose_color(self, color_var, color_type, parent):
        color = colorchooser.askcolor(title=f"Choose {color_type} color", parent=parent)
        if color[1]:  # color[1] is the hex value
            color_var.set(color[1])
    
    def apply_colors(self):
        self.text_canvas.config(bg=self.bg_color)
        self.text_canvas.itemconfigure(self.history_item, fill=self.text_color)
        self.text_canvas.itemconfigure(self.live_item, fill=self.text_color)
        if hasattr(self, "status_label"):
            self.status_label.config(bg=self.bg_color, fg=self.text_color)
        if hasattr(self, "controls_frame"):
            self.controls_frame.config(bg=self.bg_color)
    
    def listen_and_translate(self):
        while self.listening:
            try:
                if self.is_paused:
                    self.update_status("Paused")
                    time.sleep(0.2)
                    continue
                device_name = None
                if (
                    self.microphone_index is not None
                    and self.devices
                    and self.microphone_index < len(self.devices)
                ):
                    device_name = self.devices[self.microphone_index]
                if not device_name or device_name not in self.device_indices:
                    self.update_status("No audio device selected")
                    time.sleep(1)
                    continue
                if self.device_types.get(device_name) != "input":
                    if not self.allow_loopback:
                        self.update_status("Selected device is output-only (enable loopback)")
                        time.sleep(1)
                        continue
                    self.update_status("Loopback capture (output)")
                    
                device_index = self.device_indices.get(device_name, 0)
                # Use microphone input
                with sr.Microphone(device_index=device_index, sample_rate=16000) as source:
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    self.update_status("Listening...")
                    audio = self.recognizer.listen(source, timeout=1, phrase_time_limit=10)
                    self.process_audio(audio)
                        
            except sr.WaitTimeoutError:
                time.sleep(0.1)
            except sr.UnknownValueError:
                self.update_status("Could not understand audio")
            except sr.RequestError as e:
                self.update_status(f"API Error: {e}")
            except Exception as e:
                self.update_status(f"Error: {e}")
    
    def process_audio(self, audio):
        self.update_status("Processing speech...")
        try:
            if self.transcription_mode == "google_cloud":
                if not self.api_key:
                    raise Exception("Google Cloud selected but API key is empty")
                text = self.recognize_google_rest(audio, self.api_key)
            else:
                text = self.recognizer.recognize_google(audio)
        except Exception as e:
            self.update_status(f"Speech error: {e}")
            return
        
        self.update_status("Translating...")
        try:
            translated = self.translator.translate(
                text,
                src=self.source_lang,
                dest=self.target_lang,
            ).text
            translated = self.apply_custom_vocabulary(translated)
            translated = self.format_scripture_refs(translated)
            translated = self.clean_text_spacing(translated)
        except Exception as e:
            self.update_status(f"Translation error: {e}")
            translated = text
        self.update_text(translated)
        self.update_status("Listening...")
    
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
                "enableAutomaticPunctuation": True,
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
            else:
                raise Exception(f"No transcript in response: {result}, audio length: {len(audio_data)} bytes, sample rate: {audio.sample_rate}")
        else:
            raise sr.RequestError(f"API error {response.status_code}: {response.text}")
    
    def update_text(self, text):
        def update():
            incoming = text.strip()
            if not incoming:
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
        for chunk in self.chunk_text(text, self.chunk_size):
            self.text_queue.append(chunk)
        if not self.is_flushing_queue:
            self.flush_text_queue()

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
        if self.live_lines:
            self.history_lines.extend(self.live_lines)
        self.live_lines = [filtered_text]
        if len(self.history_lines) > self.max_lines:
            self.history_lines = self.history_lines[-self.max_lines:]
        self.translations = self.history_lines + self.live_lines
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
        def update():
            self.status_label.config(text=f"Status: {msg}")
        self.root.after(0, update)

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self.pause_button.config(text="Resume" if self.is_paused else "Pause")
        self.update_status("Paused" if self.is_paused else "Listening...")

    def on_canvas_resize(self, event):
        width = max(10, event.width - (self.text_padding * 2))
        self.text_canvas.itemconfigure(self.history_item, width=width, font=self.text_font)
        self.text_canvas.itemconfigure(self.live_item, width=width, font=self.text_font)
        self.update_text_position()

    def update_text_position(self):
        height = self.text_canvas.winfo_height()
        live_y = height - self.text_padding
        history_y = live_y - self.live_bbox_height - self.live_gap - self.scroll_offset
        self.text_canvas.coords(self.live_item, self.text_padding, live_y)
        self.text_canvas.coords(self.history_item, self.text_padding, history_y)

    def render_text(self):
        history_text = '\n'.join(self.filter_bad_words(t) for t in self.history_lines)
        live_text = '\n'.join(self.filter_bad_words(t) for t in self.live_lines)
        self.text_canvas.itemconfigure(self.history_item, text=history_text, font=self.text_font)
        self.text_canvas.itemconfigure(self.live_item, text=live_text, font=self.text_font)
        self.text_canvas.update_idletasks()
        self.update_text_metrics()
        self.clamp_text_to_fit()
        self.update_text_position()

    def update_text_metrics(self):
        history_bbox = self.text_canvas.bbox(self.history_item)
        live_bbox = self.text_canvas.bbox(self.live_item)
        self.history_bbox_height = (history_bbox[3] - history_bbox[1]) if history_bbox else 0
        self.live_bbox_height = (live_bbox[3] - live_bbox[1]) if live_bbox else 0

    def clamp_text_to_fit(self):
        height = max(1, self.text_canvas.winfo_height())
        available = max(1, height - (self.text_padding * 2))
        total = self.history_bbox_height + self.live_bbox_height
        if self.history_lines and total > available:
            while self.history_lines and total > available:
                self.history_lines.pop(0)
                history_text = '\n'.join(self.filter_bad_words(t) for t in self.history_lines)
                self.text_canvas.itemconfigure(self.history_item, text=history_text, font=self.text_font)
                self.text_canvas.update_idletasks()
                self.update_text_metrics()
                total = self.history_bbox_height + self.live_bbox_height
        if not self.history_lines and self.live_bbox_height > available:
            self.truncate_live_to_fit(available)

    def truncate_live_to_fit(self, available_height):
        if not self.live_lines:
            return
        text = self.live_lines[0]
        while len(text) > 10:
            text = text[: max(10, int(len(text) * 0.85))].rstrip() + "..."
            self.text_canvas.itemconfigure(self.live_item, text=text, font=self.text_font)
            self.text_canvas.update_idletasks()
            self.update_text_metrics()
            if self.live_bbox_height <= available_height:
                self.live_lines[0] = text
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
        if not self.history_lines and not self.live_lines:
            self.scroll_offset = 0.0
            self.update_text_position()
            self.scroll_after_id = self.root.after(16, self.scroll_tick)
            return

        speed_scale = max(1.0, len(self.history_lines) / max(1, self.max_lines))
        self.scroll_offset += (self.scroll_speed_px * speed_scale) * dt
        line_height = self.text_font.metrics("linespace") or 1
        height = self.text_canvas.winfo_height()
        history_y = height - self.text_padding - self.live_bbox_height - self.live_gap - self.scroll_offset
        top = history_y - self.history_bbox_height
        if top <= -line_height and len(self.history_lines) > 1:
            while top <= -line_height and len(self.history_lines) > 1:
                self.history_lines.pop(0)
                # Keep visual position stable when dropping a line.
                self.scroll_offset += line_height
                self.translations = self.history_lines + self.live_lines
                self.render_text()
                history_y = height - self.text_padding - self.live_bbox_height - self.live_gap - self.scroll_offset
                top = history_y - self.history_bbox_height
        else:
            self.update_text_position()
        self.scroll_after_id = self.root.after(16, self.scroll_tick)


if __name__ == "__main__":
    app = TranslationApp()
