# Rhema - live speech transcription and translation, run locally.
# Copyright (C) 2026 Zachary Price
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import tkinter as tk
from tkinter import font as tkfont
from threading import Thread, Lock
import queue
import time
from collections import deque
import os
import sys
import ttkbootstrap as ttkb

from app_constants import AppConstants
from app_lifecycle_mixin import AppLifecycleMixin, bootstrap_and_run
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
from video_capture_mixin import VideoCaptureMixin
from update_mixin import UpdateMixin

class TranslationApp(
    AppConstants,
    AppLifecycleMixin,
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
    VideoCaptureMixin,
    UpdateMixin,
):
    def __init__(self):
        self.set_dpi_awareness()
        self.app_data_dir = self._get_app_data_dir()
        self.settings_path = os.path.join(self.app_data_dir, "settings.json")
        # Checked before load_settings() creates/overwrites anything -
        # used to decide whether to run Hardware Autodetect unprompted
        # on this launch (see open_settings).
        self.is_first_run = not os.path.exists(self.settings_path)
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
        self.root = tk.Tk()
        self.root.title("Rhema")
        self.font_family = self.pick_font_family(
            ["DejaVu Sans", "Liberation Sans", "Arial", "Helvetica"]
        )
        self.ui_font_family = self.pick_font_family(
            ["Segoe UI", "SF Pro Text", "Inter", "Noto Sans", self.font_family]
        )
        self.style = ttkb.Style(theme="bootstrap-light")
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
        self._video_capture_defaults()
        self.text_translation_provider = "local_nllb"
        self.local_nllb_model_name = self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        self.local_nllb_device = "auto"
        self.local_nllb_target_lang = self.LOCAL_NLLB_DEFAULT_TARGET_LANG
        self.local_nllb_max_chars = self.LOCAL_NLLB_DEFAULT_MAX_CHARS
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
        self.local_nllb_test_button = None
        # Startup readiness gate: the settings window shows a blocking
        # loading overlay (see _show_startup_loading_overlay in
        # settings_ui_mixin.py) until both the RealtimeSTT model and Local
        # NLLB are done loading/verifying, so the user can't change settings
        # out from under an in-progress load.
        self.startup_stt_ready = False
        self.startup_translation_ready = False
        self.startup_video_scan_ready = False
        self.app_startup_ready = False
        # Set when the startup camera scan (open_settings ->
        # _show_startup_loading_overlay -> _refresh_video_devices) is still
        # probing devices at the point start_video_feed() would normally be
        # called - opening the real feed concurrently with that scan can
        # lose the race for the camera handle and fail. See
        # _mark_startup_video_scan_ready, which starts the feed once the
        # scan is out of the way instead.
        self._start_video_feed_after_startup_scan = False
        self._startup_loading_overlay = None
        self._startup_loading_progress = None
        self._settings_menu_bar = None
        self.speech_engine = "realtime-stt"
        self.stt_device = "auto"
        self.cuda_directory = ""
        self._cuda_dll_directory_handles = []
        self.last_faster_whisper_confidence = None
        self.device_menu = None
        self.device_sample_rates_by_index = {}
        self.preferred_device_label = ""
        self.device_refresh_in_progress = False
        self.start_with_windows = False
        self.lock_output_focus = False
        self.sentence_buffer = ""
        self.sentence_buffer_pretranslated = False
        self.sentence_buffer_source_text = ""
        self.sentence_lock = Lock()
        self.sentence_flush_ms = 100
        self.sentence_last_update = 0.0
        self.sentence_max_chars = 200
        # Without the live interim row, a finalized chunk is the only
        # feedback the viewer gets - forcing a flush sooner (smaller max)
        # when interim is off keeps run-on speech (no punctuation) from
        # sitting silently in the buffer as long before anything appears.
        # With interim on, the live row already shows that speech as it
        # happens, so the larger chunk size above is fine.
        self.sentence_max_chars_no_interim = 100
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
        self._output_snapshot_photo = None
        self._output_snapshot_raw_image = None
        self._output_snapshot_after_id = None
        self.settings_geometry = None
        self.settings_maximized = False
        self.options_geometry = None
        self.options_maximized = True
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
        self.ui_theme = "light"  # Controller/Options theme: "light" or "dark"

        self.toggle_fullscreen_button = None

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
        self.video_image_item = self.text_canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.caption_bar_item = self.text_canvas.create_image(0, 0, anchor="sw", state="hidden")
        self.video_status_var = None
        self.video_device_menu = None
        self.video_device_var = None
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
        self.root.after(0, self.enter_fullscreen)
        self.root.after(50, self.show_status_temporarily)
        self.root.bind_all("<F11>", self.toggle_fullscreen_event)
        self.root.bind_all("<Control-Alt-f>", self.toggle_fullscreen_event)
        self.root.bind_all("<Escape>", self.toggle_fullscreen_event)
        self.root.bind_all("<Control-s>", self.open_settings_event)
        self.root.bind_all("<Control-q>", self.close_app_event)
        self.root.focus_set()
        self.listening = True
        self.translations = []
        self.max_lines = self.LINES_NO_VIDEO_DEFAULT  # Number of lines when video overlay is off
        self.video_max_lines = self.LINES_VIDEO_DEFAULT  # Number of lines when video overlay is on
        self.bad_words_by_lang = {
            "en": set(self.default_bad_words_en()),
            "es": set(self.default_bad_words_es()),
        }
        self.bad_word_filters_enabled = {"en": True, "es": True}
        self.active_bad_words = set()
        self.custom_vocab_langs_enabled = {"en": True, "es": True}
        self.settings_window = None
        self.options_window = None
        self._autodetect_transcription_vars = None
        self._autodetect_translation_vars = None
        self.is_applying_settings = False
        self.live_line = ""
        self.show_interim_text = False
        self._interim_latest_text = ""
        self._interim_render_scheduled = False
        self.display_drip_queue = deque()
        self.display_drip_after_id = None
        self.display_drip_deadline = 0.0
        self.display_page_lines = []
        self.clear_display_on_inactivity = False
        self.clear_display_inactivity_seconds = self.CLEAR_DISPLAY_INACTIVITY_DEFAULT
        self._display_inactivity_after_id = None
        self.chunk_size = 120
        self.display_speed_factor = 1.0
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
        self.translation_enabled = False
        self.auto_switch_translation = False
        self.is_paused = False
        self.load_settings()
        self._apply_ui_theme()
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
        if getattr(self, "_video_scan_in_progress", False):
            # open_settings() above just kicked off the startup camera scan
            # (see _show_startup_loading_overlay); it probes every camera
            # index via the same native APIs start_video_feed() needs, so
            # starting the real feed right now can lose that race. Deferred
            # to _mark_startup_video_scan_ready instead.
            self._start_video_feed_after_startup_scan = True
        else:
            self.start_video_feed()

        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        # Ctrl+C in the console raises KeyboardInterrupt here instead of
        # going through WM_DELETE_WINDOW, so route it through the same
        # shutdown path (stops RealtimeSTT's recorder before its
        # multiprocessing child gets torn down by the interrupt itself,
        # which otherwise races into a BrokenPipeError on the pipe).
        self.on_closing()
    
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

    # on_closing() now comes from AppLifecycleMixin (app_lifecycle_mixin.py) -
    # shared with WebTranslationApp so the shutdown watchdog can't drift
    # out of sync between the two apps.


if __name__ == "__main__":
    # freeze_support()/CWD-pinning now live in bootstrap_and_run()
    # (app_lifecycle_mixin.py) - shared with main_webview.py.
    app = bootstrap_and_run(TranslationApp)
