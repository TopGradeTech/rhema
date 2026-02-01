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
        
        # Menu bar
        menubar = tk.Menu(self.root)
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Settings", command=self.open_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menubar)
        
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
        self.font_size = 24  # Font size
        self.apply_colors()  # Apply default colors
        
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
        
        self.fullscreen_button = tk.Button(self.root, text="Toggle Fullscreen", command=self.toggle_fullscreen)
        self.fullscreen_button.grid(row=2, column=0, pady=10)
        
        self.is_fullscreen = False
        self.listening = True
        self.translations = []
        self.max_lines = 8  # Default number of lines
        self.bad_words = set(["fuck", "shit", "ass", "bitch", "damn", "hell", "crap", "piss", "dick", "cock", "pussy", "tits", "cunt", "bastard", "slut", "whore"])
        self.api_key = ""  # Google STT API key
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.thread = Thread(target=self.listen_and_translate)
        self.thread.daemon = True
        self.thread.start()
        
        self.root.mainloop()
    
    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes("-fullscreen", self.is_fullscreen)

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
        settings_window = tk.Toplevel(self.root)
        settings_window.title("Settings")
        settings_window.geometry("420x600")
        settings_window.minsize(420, 600)

        content = tk.Frame(settings_window)
        content.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(content, text="Number of lines to show:").pack(pady=5)
        lines_var = tk.IntVar(value=self.max_lines)
        lines_spinbox = tk.Spinbox(content, from_=1, to=10, textvariable=lines_var)
        lines_spinbox.pack()
        
        tk.Label(content, text="Bad words filter (comma-separated):").pack(pady=5)
        bad_words_text = tk.Text(content, height=5, width=50)
        bad_words_text.insert(tk.END, ', '.join(sorted(self.bad_words)))
        bad_words_text.pack()
        
        tk.Label(content, text="Google STT API Key (optional):").pack(pady=5)
        api_key_var = tk.StringVar(value=self.api_key)
        api_key_entry = tk.Entry(content, textvariable=api_key_var, width=50)
        api_key_entry.pack()
        
        tk.Label(content, text="Audio Device:").pack(pady=5)
        self.device_var = tk.StringVar(value=self.devices[self.microphone_index] if self.devices else "No devices")
        device_menu = tk.OptionMenu(content, self.device_var, *self.devices)
        device_menu.pack()
        
        tk.Label(content, text="Background Color:").pack(pady=5)
        bg_frame = tk.Frame(content)
        bg_frame.pack()
        bg_color_var = tk.StringVar(value=self.bg_color)
        bg_entry = tk.Entry(bg_frame, textvariable=bg_color_var, width=20)
        bg_entry.pack(side=tk.LEFT)
        bg_button = tk.Button(bg_frame, text="Choose", command=lambda: self.choose_color(bg_color_var, "background", settings_window))
        bg_button.pack(side=tk.LEFT)
        
        tk.Label(content, text="Text Color:").pack(pady=5)
        text_frame = tk.Frame(content)
        text_frame.pack()
        text_color_var = tk.StringVar(value=self.text_color)
        text_entry = tk.Entry(text_frame, textvariable=text_color_var, width=20)
        text_entry.pack(side=tk.LEFT)
        text_button = tk.Button(text_frame, text="Choose", command=lambda: self.choose_color(text_color_var, "text", settings_window))
        text_button.pack(side=tk.LEFT)
        
        tk.Label(content, text="Font Size:").pack(pady=5)
        font_size_var = tk.IntVar(value=self.font_size)
        font_size_scale = tk.Scale(content, from_=12, to=72, orient=tk.HORIZONTAL, variable=font_size_var)
        font_size_scale.pack()
        
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
        
        button_frame = tk.Frame(settings_window)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM)

        save_button = tk.Button(button_frame, text="Save", command=save_settings)
        save_button.pack(side=tk.LEFT, padx=10, pady=10)
        
        close_button = tk.Button(button_frame, text="Close", command=settings_window.destroy)
        close_button.pack(side=tk.RIGHT, padx=10, pady=10)
    
    def choose_color(self, color_var, color_type, parent):
        color = colorchooser.askcolor(title=f"Choose {color_type} color", parent=parent)
        if color[1]:  # color[1] is the hex value
            color_var.set(color[1])
    
    def apply_colors(self):
        self.text_label.config(bg=self.bg_color, fg=self.text_color)
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
            pattern = re.escape(word)
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
