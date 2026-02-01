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

class TranslationApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Python Translation App")
        self.font_family = self.pick_font_family(
            ["DejaVu Sans", "Liberation Sans", "Arial", "Helvetica"]
        )
        self.translator = Translator()
        self.recognizer = sr.Recognizer()
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
        
        self.text_label = tk.Text(
            self.root,
            wrap=tk.WORD,
            state='disabled',
            font=(self.font_family, 24),
        )
        self.text_label.grid(row=0, column=0, sticky='nsew', padx=10, pady=10)

        self.bg_color = "#000000"  # Background color
        self.text_color = "#ffffff"  # Text color
        self.font_size = 50  # Font size
        
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

        self.fullscreen_button = tk.Button(
            self.root,
            text="Toggle Fullscreen",
            command=self.toggle_fullscreen,
        )
        self.fullscreen_button.grid(row=2, column=0, pady=10)
        self.fullscreen_button.grid_remove()

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
        self.fullscreen_button.grid()
        self.root.config(menu=self.menubar)
        if self.status_hide_after_id is not None:
            self.root.after_cancel(self.status_hide_after_id)
        self.status_hide_after_id = self.root.after(duration_ms, self.hide_status)

    def hide_status(self):
        self.status_label.grid_remove()
        self.fullscreen_button.grid_remove()
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
    
    def get_audio_devices(self):
        p = pyaudio.PyAudio()
        input_devices = []
        
        for i in range(p.get_device_count()):
            device_info = p.get_device_info_by_index(i)
            if device_info.get('maxInputChannels', 0) > 0:
                input_devices.append((i, device_info.get('name', 'Unknown')))
        
        p.terminate()
        
        # Only list real input devices to avoid unreliable loopback capture.
        devices = []
        self.device_indices = {}
        self.device_types = {}
        for idx, name in input_devices:
            label = f"Input ({idx}): {name}"
            devices.append(label)
            self.device_indices[label] = idx
            self.device_types[label] = 'input'
            
        return devices if devices else ["No devices found"]
    
    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return

        settings_window = tk.Toplevel(self.root)
        self.settings_window = settings_window
        settings_window.title("Settings")
        settings_window.geometry("420x600")
        settings_window.minsize(420, 600)
        settings_bg = "#111111"
        section_bg = "#1a1a1a"
        settings_fg = "#f5f5f5"
        settings_window.configure(bg=settings_bg)
        label_opts = {"bg": settings_bg, "fg": settings_fg}
        section_font = (self.font_family, 12, "bold")

        def on_settings_close():
            if self.settings_window is not None:
                self.settings_window.destroy()
                self.settings_window = None

        settings_window.protocol("WM_DELETE_WINDOW", on_settings_close)

        content = tk.Frame(settings_window, bg=settings_bg)
        content.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

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

        tk.Label(filters_section, text="Bad words filter (comma-separated):", **label_opts).pack(anchor="w", pady=(0, 4))
        bad_words_text = tk.Text(filters_section, height=5, width=50)
        bad_words_text.insert(tk.END, ', '.join(sorted(self.bad_words)))
        bad_words_text.pack(fill=tk.BOTH, expand=True)

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
        
        def save_settings():
            self.max_lines = lines_var.get()
            bad_words_str = bad_words_text.get("1.0", tk.END).strip()
            self.bad_words = set(word.strip().lower() for word in bad_words_str.split(',') if word.strip())
            self.api_key = api_key_var.get().strip()
            self.bg_color = bg_color_var.get()
            self.text_color = text_color_var.get()
            self.font_size = font_size_var.get()
            if self.device_var.get() in self.device_indices:
                self.microphone_index = self.devices.index(self.device_var.get())
            else:
                self.microphone_index = None
            self.apply_colors()
            self.update_display()
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
        self.text_label.config(bg=self.bg_color, fg=self.text_color)
        if hasattr(self, "status_label"):
            self.status_label.config(bg=self.bg_color, fg=self.text_color)
    
    def listen_and_translate(self):
        while self.listening:
            try:
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
            if self.api_key:
                # Use Google Cloud Speech-to-Text REST API
                text = self.recognize_google_rest(audio, self.api_key)
            else:
                # Use free Google Speech Recognition
                text = self.recognizer.recognize_google(audio)
        except Exception as e:
            self.update_status(f"Speech error: {e}")
            return
        
        self.update_status("Translating...")
        try:
            translated = self.translator.translate(text, dest='en').text
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
                "languageCode": "en-US"
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
            filtered_text = self.filter_bad_words(text)
            self.translations.append(filtered_text)
            if len(self.translations) > self.max_lines:
                self.translations.pop(0)
            display_text = '\n'.join(self.translations)
            font_size = self.font_size
            self.text_label.config(state='normal', font=(self.font_family, font_size))
            self.text_label.delete(1.0, tk.END)
            self.text_label.insert(tk.END, display_text)
            self.text_label.config(state='disabled')
        self.root.after(0, update)
    
    def filter_bad_words(self, text):
        filtered = text
        for word in self.bad_words:
            pattern = r"\b" + re.escape(word) + r"\b"
            filtered = re.sub(pattern, '***', filtered, flags=re.IGNORECASE)
        return filtered
    
    def update_display(self):
        def update():
            display_text = '\n'.join(self.filter_bad_words(t) for t in self.translations[-self.max_lines:])
            font_size = self.font_size
            self.text_label.config(state='normal', font=(self.font_family, font_size))
            self.text_label.delete(1.0, tk.END)
            self.text_label.insert(tk.END, display_text)
            self.text_label.config(state='disabled')
        self.root.after(0, update)
    
    def update_status(self, msg):
        def update():
            self.status_label.config(text=f"Status: {msg}")
        self.root.after(0, update)

if __name__ == "__main__":
    app = TranslationApp()
