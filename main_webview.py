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

"""Phases 3-4 of the pywebview port (see the approved port plan): the real
Output window - real STT+NLLB+caption pipeline, real video overlay, real
persisted settings - built parallel to main.py/TranslationApp, which keeps
working unchanged throughout the whole port per the plan's architecture
decision.

Deliberately still narrow in scope, matching the plan's own phase
ordering, not an oversight:
  - No Controller/Options windows yet (Phase 5/6) - self.settings_window/
    self.options_window stay None for real, and every real mixin method
    that touches them already guards on that (winfo_exists()-style checks),
    the same way the Tk app behaves before those windows are first built.
  - Video overlay RENDERING is real as of Phase 4 (WebVideoCaptureMixin,
    web_video_capture_mixin.py) - device enumeration, capture-thread
    lifecycle, and letterbox math all reused unmodified from
    VideoCaptureMixin; only the two PhotoImage-coupled methods
    (_render_video_frame/_update_caption_bar) are overridden, using the
    data-URI-per-frame approach experiments/web_video_overlay.py already
    measured as fast enough. Phase 3 originally needed VideoCaptureMixin
    mixed in anyway (discovered live: this dev machine's own settings.json
    already has video_feed_enabled=true from earlier testing, and
    DisplayMixin's shared render path calls its caption-bar-sizing method
    unconditionally whenever that setting is on) - Phase 4 is what makes
    start_video_feed() safe to actually call, rather than leaving the
    frame-capture side dark.
  - No menu bar / global hotkeys yet (the Phase 0 design question of
    JS keydown vs. a low-level hook is Phase 5's to answer).
  - Real per-monitor placement is wired as of Phase 8 (WebMonitorMixin,
    web_monitor_mixin.py) - enter_fullscreen() moves/resizes onto the real
    selected monitor_index before toggling native fullscreen, reusing the
    DPI-aware physical-to-logical conversion webview_bridge.py already
    proved. Real per-monitor selection in the Options form (monitor_var/
    settings_monitor_var) is still not wired, though - that remains a
    "Monitor 1" placeholder (see build_web_options's own comment); only
    the underlying placement mechanism is real so far, not the UI to
    change it away from settings.json's persisted value.

Everything else is the real thing: RealtimeSttMixin, TranscriptionMixin,
TranslationMixin, TextFilterMixin, AudioCaptureMixin, DisplayMixin, and
SettingsUIMixin/MonitorMixin (mixed in whole and unmodified, exactly as
experiments/web_output_window.py already proved - their chrome-only
methods, e.g. open_settings/_build_menu_bar, are simply never called from
here; splitting them into logic-only + Web-chrome counterparts is Phase
6/8's job, not this one's) all run verbatim against real settings.json
data and real speech.
"""

import os
import queue
import sys
import time
from collections import deque
from threading import Lock, Thread

import webview

from app_constants import AppConstants
from app_lifecycle_mixin import AppLifecycleMixin, bootstrap_and_run
from logging_mixin import LoggingMixin
from settings_mixin import SettingsMixin
from web_monitor_mixin import WebMonitorMixin
from settings_ui_mixin import SettingsUIMixin
from audio_capture_mixin import AudioCaptureMixin
from transcription_mixin import TranscriptionMixin
from translation_mixin import TranslationMixin
from text_filter_mixin import TextFilterMixin
from display_mixin import DisplayMixin
from realtime_stt_mixin import RealtimeSttMixin
from web_messagebox import WebMessageBoxMixin
from web_settings_ui_mixin import WebSettingsUIMixin
from web_video_capture_mixin import WebVideoCaptureMixin
from webview_bridge import (
    FakeRoot,
    WebCanvas,
    WebMeasurer,
    is_webview2_runtime_available,
)

# CSS font stack, not a single resolved name - see pick_font_family's
# override below for why that's the right substitute for tkfont.families().
FONT_FAMILY_STACK = "'Segoe UI', 'DejaVu Sans', Arial, sans-serif"

OUTPUT_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;cursor:none}
#c{display:block;width:100%;height:100%}
</style></head><body>
<canvas id="c"></canvas>
<script>
const canvas = document.getElementById('c')
const ctx = canvas.getContext('2d')
const items = {}

function initCanvas(){
  const dpr = window.devicePixelRatio || 1
  const totalW = window.innerWidth, totalH = window.innerHeight
  canvas.width = Math.round(totalW * dpr)
  canvas.height = Math.round(totalH * dpr)
  canvas.style.width = totalW + 'px'
  canvas.style.height = totalH + 'px'
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return {w: totalW, h: totalH}
}

function measure(font, text){
  ctx.font = font
  return ctx.measureText(text).width
}

function lineHeight(font){
  ctx.font = font
  const m = ctx.measureText('Hg')
  return m.fontBoundingBoxAscent + m.fontBoundingBoxDescent
}

function applyCanvasOps(ops){
  for (const op of ops){
    if (op.op === 'bg'){
      document.body.style.background = op.color
    } else if (op.op === 'create'){
      items[op.id] = {type: 'text', x: op.x, y: op.y, anchor: op.anchor || 'nw', text: op.text || '',
                       fill: op.fill || '#fff', font: op.font || "16px sans-serif", state: 'normal'}
    } else if (op.op === 'create_image'){
      items[op.id] = {type: 'image', x: op.x, y: op.y, anchor: op.anchor || 'nw',
                       state: op.state || 'normal', img: null, ready: false, src: null}
    } else if (op.op === 'coords'){
      const it = items[op.id]
      if (it){ it.x = op.x; it.y = op.y }
    } else if (op.op === 'config'){
      const it = items[op.id]
      if (!it) continue
      if (it.type === 'image'){
        if (op.state !== undefined) it.state = op.state
        if (op.image !== undefined && op.image !== it.src){
          it.src = op.image
          if (!op.image){
            it.img = null; it.ready = false
          } else {
            const im = new Image()
            im.onload = () => { it.img = im; it.ready = true; repaint() }
            im.src = op.image
          }
        }
      } else {
        if (op.text !== undefined) it.text = op.text
        if (op.fill !== undefined) it.fill = op.fill
        if (op.font !== undefined && op.font) it.font = op.font
        if (op.state !== undefined) it.state = op.state
      }
    } else if (op.op === 'delete'){
      delete items[op.id]
    }
  }
  repaint()
}

function anchoredXY(it, w, h){
  // Tk anchors: the point given to create_image/create_text is the named
  // corner (or edge midpoint) of the item, not always its top-left -
  // "sw" (the caption bar, docked to the bottom) sits ABOVE its y
  // coordinate, for instance.
  let x = it.x, y = it.y
  if (it.anchor.includes('e')) x -= w
  else if (!it.anchor.includes('w')) x -= w / 2
  if (it.anchor.includes('s')) y -= h
  else if (!it.anchor.includes('n')) y -= h / 2
  return [x, y]
}

function repaint(){
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.textAlign = 'left'
  for (const id in items){
    const it = items[id]
    if (it.state !== 'normal') continue
    if (it.type === 'image'){
      if (!it.ready) continue
      const [x, y] = anchoredXY(it, it.img.width, it.img.height)
      ctx.drawImage(it.img, x, y)
    } else if (it.text){
      ctx.font = it.font
      ctx.fillStyle = it.fill
      ctx.textBaseline = it.anchor.includes('s') ? 'bottom' : (it.anchor.includes('n') ? 'top' : 'middle')
      ctx.fillText(it.text, it.x, it.y)
    }
  }
}
</script></body></html>
"""


class WebTranslationApp(
    AppConstants,
    AppLifecycleMixin,
    WebMessageBoxMixin,
    WebSettingsUIMixin,
    LoggingMixin,
    SettingsMixin,
    WebMonitorMixin,
    SettingsUIMixin,
    AudioCaptureMixin,
    TranscriptionMixin,
    TranslationMixin,
    TextFilterMixin,
    DisplayMixin,
    RealtimeSttMixin,
    WebVideoCaptureMixin,
):
    def pick_font_family(self, candidates):
        """The real one (main.py) resolves a single installed font name via
        tkfont.families(), which needs a live Tk interpreter this app
        doesn't have. A CSS font-family stack does the same job better: the
        browser itself falls back through the list per the same "first
        installed wins" logic, and can even fall back per-glyph rather than
        committing to one winner for the whole string. Quoted only when a
        name contains a space, matching real CSS font-family syntax."""
        return ", ".join(f"'{name}'" if " " in name else name for name in candidates)

    def render_text(self):
        # The one line this port adds on top of the real, unmodified
        # DisplayMixin.render_text: flush the queued canvas ops into a
        # single evaluate_js round trip. Proved in
        # experiments/web_output_window.py.
        super().render_text()
        self.text_canvas.flush()

    def __init__(self):
        self.set_dpi_awareness()
        self.app_data_dir = self._get_app_data_dir()
        self.settings_path = os.path.join(self.app_data_dir, "settings.json")
        self.is_first_run = not os.path.exists(self.settings_path)
        self.log_session_timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.log_retained_sessions = 5
        self.session_log_prefixes = (
            "error", "transcript", "finalized", "transcribed", "translated",
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
        # Controller-window-only widgets (Phase 5). Every real call site
        # that touches these already guards with a None/winfo_exists()
        # check (e.g. DisplayMixin._set_chunk_latency_label_text,
        # _render_audio_level_meter), so leaving them permanently None
        # here is the same state the Tk app is in before its Controller
        # window is first built - not a special case this file adds.
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

        self.root = FakeRoot(on_error=self._write_unhandled_exception)
        # Set early so any status/latency/meter update that fires before
        # build_web_controller() runs (e.g. a very early update_status()
        # call) finds a real, already-guarded None rather than an
        # AttributeError.
        self._controller_window = None
        self._options_window = None
        self._var_interpreter = None

        self.font_family = self.pick_font_family(
            ["DejaVu Sans", "Liberation Sans", "Arial", "Helvetica"]
        )
        self.ui_font_family = self.pick_font_family(
            ["Segoe UI", "SF Pro Text", "Inter", "Noto Sans", self.font_family]
        )
        self.allow_loopback = False
        self.recommended_host_api = ""
        self.available_host_apis = []
        self._realtime_stt_defaults()
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
        # Real tk.Variable-backed status vars are Options-window plumbing
        # (Phase 6, TkVariableInterpreter) - not needed for the Output
        # window's own pipeline.
        self.local_nllb_status_var = None
        self.local_nllb_message_var = None
        self.local_nllb_download_button = None
        self.local_nllb_test_button = None
        self.startup_stt_ready = False
        self.startup_translation_ready = False
        self.startup_video_scan_ready = False
        self.app_startup_ready = False
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

        self.bg_color = "#000000"
        self.text_color = "#ffffff"
        self.font_size = 50
        self.ui_theme = "light"
        self.toggle_fullscreen_button = None
        self.canvas_margin = 10
        self.video_image_item = None
        self.caption_bar_item = None
        self.video_status_var = None
        self.video_device_menu = None
        self.video_device_var = None
        self.text_padding = 12
        self.min_chars_per_line = 40
        self.text_line_items = []
        self.status_label = None
        self.status_hide_after_id = None
        self.overlay_visible = False
        self.is_fullscreen = True
        self.use_custom_fullscreen = False  # unused here - WebMonitorMixin's enter_fullscreen() has no such branch
        self.prev_geometry = None
        self.prev_overrideredirect = None
        self.prev_topmost = None
        self.listening = True
        self.translations = []
        self.max_lines = self.LINES_NO_VIDEO_DEFAULT
        self.video_max_lines = self.LINES_VIDEO_DEFAULT
        self._video_capture_defaults()
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
        self._refresh_bad_words()
        self.devices = self.get_audio_devices()
        self.microphone_index = 0 if self.devices else None

        if not is_webview2_runtime_available():
            self._show_error_dialog(
                "Rhema",
                "The Microsoft Edge WebView2 Runtime is required and was not "
                "found. Install it from https://developer.microsoft.com/"
                "microsoft-edge/webview2/ and run Rhema again.",
            )
            sys.exit(1)

        storage_path = os.path.join(self.app_data_dir, "webview_data")
        # Deliberately NOT fullscreen=True here (Phase 3-7's approach) -
        # Phase 8's enter_fullscreen() needs the window in a known windowed
        # state to move+resize it onto the REAL selected monitor before
        # toggling fullscreen (see web_monitor_mixin.py's own comment on
        # why that order matters). Starting fullscreen=True here would
        # fullscreen on whatever monitor pywebview defaults to first,
        # then require an exit+re-enter to correct it.
        window = webview.create_window(
            "Rhema",
            html=OUTPUT_HTML,
            background_color=self.bg_color,
        )
        self._window = window
        window.events.loaded += self._on_window_loaded
        window.events.closing += self.on_closing

        self._install_exception_hook()

        # Blocks for the app's lifetime, matching TranslationApp's own
        # self.root.mainloop() at the end of its __init__ - bootstrap_and_run
        # expects the constructor to hold the process open either way.
        webview.start(storage_path=storage_path, private_mode=False, debug=False)

    def _on_window_loaded(self):
        # Must run BEFORE initCanvas() below: enter_fullscreen() (Phase 8,
        # WebMonitorMixin) moves/resizes the real window onto the selected
        # monitor and toggles native fullscreen, changing window.innerWidth/
        # innerHeight - initCanvas() needs to read the FINAL size, not
        # whatever size the window happened to be created at. A brief
        # settle delay matches the same one
        # experiments/web_multimonitor.py used after its own
        # move+resize+toggle_fullscreen sequence, before trusting the new
        # geometry is visible to anything that queries it.
        self.enter_fullscreen()
        time.sleep(0.3)
        dims = self._window.evaluate_js("initCanvas()")
        width, height = int(dims["w"]), int(dims["h"])
        self.text_canvas = WebCanvas(self._window, width, height)
        self.text_font = WebMeasurer(self._window, FONT_FAMILY_STACK, self.font_size, 96.0)
        self.video_image_item = self.text_canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.caption_bar_item = self.text_canvas.create_image(0, 0, anchor="sw", state="hidden")
        self.text_item = self.text_canvas.create_text(
            self.text_padding, 0, anchor="sw", text="", fill=self.text_color,
            font=self.text_font, width=0,
        )
        self.apply_colors()
        self._apply_scaled_fonts()
        self.render_text()

        # Built before the STT/translation threads start, so the first
        # real update_status()/_render_audio_level_meter() calls have a
        # real Controller window to push into, matching main.py's own
        # open_settings()-before-thread-start ordering.
        self.build_web_controller()
        # Eager-but-hidden, matching open_settings()'s real behavior
        # (_build_options_dialog runs unconditionally at launch): building
        # the Translation section's vars kicks off the real NLLB cache-
        # check, which is what eventually flips startup_translation_ready
        # and lets the Controller's startup loading overlay (Phase 7)
        # dismiss itself. Revealed for real only via File > Options.
        self.build_web_options(hidden=True)

        self.translation_thread = Thread(target=self._translation_worker, daemon=True)
        self.translation_thread.start()
        self.display_thread = Thread(target=self._display_worker, daemon=True)
        self.display_thread.start()
        self._start_audio_level_stream_thread()
        self.thread = Thread(target=self.listen_and_translate, daemon=True)
        self.thread.start()
        # Matches main.py's own __init__ (the _video_scan_in_progress gate
        # there exists only because open_settings()'s startup camera scan
        # can race the real device handle - not reachable yet here, since
        # there's no Options window in this phase to run that scan from).
        # start_video_feed() itself already no-ops if video_feed_enabled is
        # false or video_device_index is None, matching real settings.
        self.start_video_feed()


if __name__ == "__main__":
    app = bootstrap_and_run(WebTranslationApp)
