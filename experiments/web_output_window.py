r"""Port test: the real fullscreen output window, word-by-word drip reveal
included, with zero Tk.

Follow-on to web_transcription.py (proved the engine runs headless) and
caption_layout_probe.py (proved the roll-up layout math is measurer-agnostic).
This is the piece those two left open: does the actual output window - the
roll-up paging, the drip reveal timing, the frozen-line rendering - work when
it is a real fullscreen window instead of an in-memory probe?

The answer this file is built to give is yes, and by construction rather than
assertion: DisplayMixin and SettingsUIMixin are mixed in whole and untouched.
Every method that makes a rendering decision - _fit_font_to_lines,
_wrap_lines_to_width, _append_to_display_page, _meter_display_commit,
_display_drip_tick, _update_line_items - runs exactly as shipped. The only
things replaced are the two primitives underneath them, same as the layout
probe:

    self.text_canvas   -> WebCanvas    (create_text/coords/itemconfigure/
                                         delete/winfo_width/winfo_height,
                                         backed by a real <canvas> instead of
                                         a Tk Canvas)
    self.text_font      -> WebMeasurer  (measure/metrics/configure/cget,
                                         backed by the same canvas's
                                         measureText - what gets measured is
                                         what gets painted)

render_text() gets one line added (a flush of queued canvas ops into a single
evaluate_js round trip) and nothing else - the drip/paging logic it calls is
the shipped body, unmodified.

The engine side is web_transcription.py's HeadlessEngine verbatim (same real
RealtimeSTT + local NLLB pipeline, same three replacement groups documented
there). What's new here is that update_text/_meter_display_commit are no
longer overridden - the real drip reveal and roll-up paging now run for real,
because there is finally a canvas for them to paint onto.

Out of scope, deliberately (still open per the port agenda): the video
overlay, Options dialog, multi-monitor placement, and true resize handling -
this window is fixed-size for its one session, matching a single fullscreen
monitor exactly the way the shipping app's output window is once it's placed.

Setup: .venv\Scripts\pip.exe install pywebview   (not a real dependency of
       the shipping app - see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_output_window.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import json
import os
import queue
import sys
import threading
import time
from collections import deque
from threading import Lock, Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview  # noqa: E402

from main import TranslationApp  # noqa: E402  (class-level constants only - never instantiated)
from realtime_stt_mixin import RealtimeSttMixin  # noqa: E402
from transcription_mixin import TranscriptionMixin  # noqa: E402
from translation_mixin import TranslationMixin  # noqa: E402
from text_filter_mixin import TextFilterMixin  # noqa: E402
from audio_capture_mixin import AudioCaptureMixin  # noqa: E402
from display_mixin import DisplayMixin  # noqa: E402
from settings_ui_mixin import SettingsUIMixin  # noqa: E402
from monitor_mixin import MonitorMixin  # noqa: E402
from logging_mixin import LoggingMixin  # noqa: E402
from settings_mixin import SettingsMixin  # noqa: E402

# Mirrors settings.json - already-downloaded models, not new choices.
SOURCE_LANG = "en"
TARGET_LANG = "es"
STT_DEVICE = "cuda"
NLLB_DEVICE = "cuda"
FINAL_MODEL = "large-v3"
REALTIME_MODEL = "tiny"
NLLB_MODEL_NAME = "facebook/nllb-200-3.3B"
NLLB_TARGET_FLORES = "spa_Latn"

# CSS font stack for the canvas measurer/renderer. No Tk root to probe
# installed families here (this test has none at all), so a web-safe stack
# stands in - the layout probe already showed line breaks survive a measurer
# swap; this is one more swap of the same kind.
FONT_FAMILY = "Arial, 'Segoe UI', sans-serif"
# Browsers treat CSS pixels as a fixed 96/inch regardless of physical DPI, so
# there is no Tk-sourced "real" ppi to match here - 96 is the browser's own
# reference, used consistently for both measuring and drawing.
PIXELS_PER_INCH = 96.0


def _preferred_device_label():
    """The app's own configured microphone, read from its settings.json, so
    this test listens on the same device rather than whatever Windows calls
    the default."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("preferred_device_label") or "")
    except Exception:
        return ""


class FakeRoot:
    """Stands in for Tk's root just enough to satisfy `self.root.after(...)`
    calls sprinkled through the mixins for UI-thread marshaling - including
    the drip reveal's own scheduling (_schedule_display_drip).

    This has to do more than web_transcription.py's version of the same
    shim. That test never touched drip/roll-up state through `after`, so
    firing each call on its own throwaway threading.Timer was harmless. Here,
    _meter_display_commit/_display_drip_tick read and mutate shared state
    (display_drip_queue, display_page_lines) from inside `after` callbacks,
    exactly the way Tk's mainloop guarantees they'll never run concurrently
    with each other. A pile of independent Timer threads does NOT give that
    guarantee - two finals arriving close together raced on display_drip_queue
    and silently dropped everything after the first ("En el ..." and nothing
    else ever rendered) the first time this was driven with real back-to-back
    commits. So: one dedicated thread, one time-ordered queue, callbacks run
    strictly one at a time in fire-time order - the same serialization a real
    port needs regardless of what replaces Tk's mainloop."""

    def __init__(self, on_error=None):
        self._lock = threading.Lock()
        self._heap = []
        self._seq = 0
        self._cancelled = set()
        self._wake = threading.Event()
        self._on_error = on_error
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def after(self, delay_ms, fn, *args):
        import heapq

        fire_time = time.monotonic() + (max(0, delay_ms) / 1000.0)
        with self._lock:
            self._seq += 1
            token = self._seq
            heapq.heappush(self._heap, (fire_time, token, fn, args))
        self._wake.set()
        return token

    def after_cancel(self, token):
        with self._lock:
            self._cancelled.add(token)

    def _run(self):
        import heapq

        while True:
            with self._lock:
                due = self._heap[0] if self._heap else None
            if due is None:
                self._wake.wait()
                self._wake.clear()
                continue
            delay = due[0] - time.monotonic()
            if delay > 0:
                self._wake.wait(timeout=delay)
                self._wake.clear()
                continue
            with self._lock:
                if not self._heap or self._heap[0][1] != due[1]:
                    continue  # a new, earlier item beat us to the lock
                _, token, fn, args = heapq.heappop(self._heap)
                cancelled = token in self._cancelled
                self._cancelled.discard(token)
            if cancelled:
                continue
            try:
                fn(*args)
            except Exception:
                # Real Tk surfaces this through report_callback_exception,
                # which logging_mixin.py hooks to _write_unhandled_exception -
                # a silent `pass` here would swallow the exact class of bug
                # this test exists to catch (a drip/roll-up race, say).
                if self._on_error is not None:
                    self._on_error(*sys.exc_info())
                else:
                    import traceback

                    traceback.print_exc()


def _font_css(font):
    """`font` is whatever DisplayMixin/SettingsUIMixin pass through as the
    canvas item's font - always self.text_font (a WebMeasurer) in practice."""
    if font is None or not hasattr(font, "_css_font"):
        return None
    return font._css_font()


class WebCanvas:
    """The only Tk Canvas surface DisplayMixin/SettingsUIMixin (and, when
    mixed in, VideoCaptureMixin) actually touch: winfo_width/height,
    create_text, create_image, coords, itemconfigure, delete. Backed by a
    real <canvas> in the fullscreen window - draw ops are queued and
    flushed in one evaluate_js round trip per render_text() call rather
    than one per item, since a page can hold up to LINES_NO_VIDEO_MAX items.

    Item ids are handed out in creation order and the JS side iterates its
    item registry by numeric id, which (like Tk's own stacking order) means
    whatever was created first paints first - so as long as callers create
    background items (video, caption bar) before foreground ones (caption
    text), the stacking comes out right with no separate z-index to manage.
    """

    def __init__(self, window, width, height):
        self._window = window
        self._w = width
        self._h = height
        self._next_id = 1
        self._ops = []

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def create_text(self, x, y, anchor="nw", text="", fill="#ffffff", font=None, **_ignored):
        item_id = self._next_id
        self._next_id += 1
        self._ops.append({
            "op": "create",
            "id": item_id,
            "x": x,
            "y": y,
            "anchor": anchor,
            "text": text,
            "fill": fill,
            "font": _font_css(font),
        })
        return item_id

    def create_image(self, x, y, anchor="nw", state="normal", **_ignored):
        item_id = self._next_id
        self._next_id += 1
        self._ops.append({
            "op": "create_image", "id": item_id, "x": x, "y": y, "anchor": anchor, "state": state,
        })
        return item_id

    def coords(self, item_id, x, y):
        self._ops.append({"op": "coords", "id": item_id, "x": x, "y": y})

    def itemconfigure(self, item_id, **kw):
        entry = {"op": "config", "id": item_id}
        if "text" in kw:
            entry["text"] = kw["text"]
        if "fill" in kw:
            entry["fill"] = kw["fill"]
        if "font" in kw:
            entry["font"] = _font_css(kw["font"])
        if "state" in kw:
            entry["state"] = kw["state"]
        if "image" in kw:
            # A data: URI (or "" to clear) - the real code always passes an
            # ImageTk.PhotoImage here instead, which has no browser
            # equivalent; see web_video_overlay.py for what builds this.
            entry["image"] = kw["image"] or ""
        self._ops.append(entry)

    def delete(self, item_id):
        self._ops.append({"op": "delete", "id": item_id})

    def flush(self):
        if not self._ops:
            return
        ops, self._ops = self._ops, []
        try:
            self._window.evaluate_js("applyCanvasOps(%s)" % json.dumps(ops))
        except Exception:
            pass


class WebMeasurer:
    """The same measurer proven in caption_layout_probe.py, reused: a browser
    canvas's measureText() standing in for tkinter.font.Font. Cached per
    (size, text) - _fit_font_to_lines binary-searches font sizes by repeating
    the same handful of probe strings, so the cache turns most of that search
    into dict lookups instead of JS round trips."""

    def __init__(self, window, family, size, pixels_per_inch):
        self._window = window
        self._family = family
        self._size = size
        self._ppi = pixels_per_inch
        self._cache = {}

    def configure(self, **kwargs):
        if "size" in kwargs:
            self._size = kwargs["size"]

    def cget(self, key):
        return self._size if key == "size" else self._family

    def _css_font(self):
        px = self._size * self._ppi / 72.0
        return "%gpx %s" % (px, self._family)

    def measure(self, text):
        key = (self._size, text)
        if key not in self._cache:
            self._cache[key] = int(round(float(self._window.evaluate_js(
                "measure(%s, %s)" % (json.dumps(self._css_font()), json.dumps(text))
            ))))
        return self._cache[key]

    def metrics(self, key):
        if key != "linespace":
            raise KeyError(key)
        cache_key = (self._size, "\x00linespace")
        if cache_key not in self._cache:
            self._cache[cache_key] = int(round(float(self._window.evaluate_js(
                "lineHeight(%s)" % json.dumps(self._css_font())
            ))))
        return self._cache[cache_key]


class OutputEngine(
    RealtimeSttMixin,
    TranscriptionMixin,
    TranslationMixin,
    TextFilterMixin,
    AudioCaptureMixin,
    DisplayMixin,
    SettingsUIMixin,
    MonitorMixin,
    LoggingMixin,
    SettingsMixin,
):
    """Real engine mixins (as in web_transcription.py) plus the real display
    mixins (new here). DisplayMixin/SettingsUIMixin are not cherry-picked -
    every rendering method they define runs unmodified; the overrides below
    are exactly the boundary methods that touch something this test has no
    Tk equivalent for (a status label, an audio meter widget, a DPI-aware
    root) or the app's own settings-window glue (NLLB download/test flow)."""

    def _get_app_data_dir(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_logs")
        os.makedirs(path, exist_ok=True)
        return path

    def __init__(self, push_debug, canvas, measurer, pixels_per_inch):
        self.push_debug = push_debug  # callback(dict) -> None, debug overlay only

        # --- logging (real LoggingMixin methods, isolated under experiments/_logs) ---
        self.log_session_timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.log_retained_sessions = 5
        self.session_log_prefixes = ("error", "transcript", "finalized", "transcribed", "translated")
        self.app_data_dir = self._get_app_data_dir()
        self.error_log_path = self._get_error_log_path()
        self.transcript_trace_path = self._get_transcript_trace_path()
        self.finalized_transcript_path = self._get_finalized_transcript_path()
        self.transcribed_text_path = self._get_transcribed_text_path()
        self.translated_text_path = self._get_translated_text_path()
        self._prune_old_log_sessions()
        self.logging_mode = "full"
        self.status_log_lock = Lock()
        self.last_status_message = None
        self.transcript_trace_lock = Lock()
        self.finalized_transcript_lock = Lock()
        self.transcribed_text_lock = Lock()
        self.transcribed_log_sequence = 0
        self.translated_text_lock = Lock()
        self.translated_log_sequence = 0
        self._apply_logging_mode_flags()

        self.root = FakeRoot(on_error=self._write_unhandled_exception)
        self.listening = True
        self.speech_engine = "realtime-stt"
        self.startup_stt_ready = False

        # --- output window (real DisplayMixin/SettingsUIMixin state) ---
        self._ppi = pixels_per_inch
        self.text_font = measurer
        self.text_canvas = canvas
        self.bg_color = "#000000"
        self.text_color = "#ffffff"
        self.text_padding = 12
        self.min_chars_per_line = 40
        self.text_item = self.text_canvas.create_text(
            self.text_padding, 0, anchor="sw", text="", fill=self.text_color, font=self.text_font,
        )
        self.text_line_items = []
        self.max_lines = self.LINES_NO_VIDEO_DEFAULT
        self.video_max_lines = self.LINES_VIDEO_DEFAULT
        self.video_feed_enabled = False
        self.chunk_size = 120
        self.display_speed_factor = 1.0
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
        self.translations = []
        self.latency_samples = deque(maxlen=20)
        self.chunk_latency_label = None  # guarded by real _set_chunk_latency_label_text

        # --- audio meter (real DisplayMixin math; rendered into the debug overlay) ---
        self.audio_level_bar = None  # guarded by real _render_audio_level_meter
        self.audio_level_fill_item = None
        self.audio_level_target = 0.0
        self.audio_level_last_update = 0.0
        self.audio_level_floor_db = -55.0

        # --- RealtimeSTT ---
        self._realtime_stt_defaults()
        self.realtime_stt_final_model = FINAL_MODEL
        self.realtime_stt_realtime_model = REALTIME_MODEL
        self.stt_device = STT_DEVICE
        self.last_faster_whisper_confidence = None

        # --- audio device enumeration (real MonitorMixin, also Tk-free) ---
        self.portaudio_admin_lock = Lock()
        self.allow_loopback = False
        self.device_sample_rates_by_index = {}
        self.available_host_apis = []
        self.recommended_host_api = ""
        self.device_indices = {}
        self.device_types = {}
        self.loopback_output_map = {}
        self.devices = self.get_audio_devices()
        self.preferred_device_label = _preferred_device_label()
        resolved = self._resolve_preferred_device_label(self.preferred_device_label)
        if resolved:
            self.microphone_index = self.devices.index(resolved)
        elif self.devices:
            self.microphone_index = 0
        else:
            self.microphone_index = None

        # --- language routing ---
        self.source_lang = SOURCE_LANG
        self.target_lang = TARGET_LANG
        self.auto_switch_translation = False
        self.auto_detect_langs = ["en", "es"]
        self.auto_detect_lang = None
        self.auto_detect_streak_lang = None
        self.auto_detect_streak_count = 0
        self.last_stt_pretranslated = False
        self.last_stt_source_text = ""
        self.last_stt_source_lang = ""
        self.last_stt_source_lang_confidence = None
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

        # --- bad-word filtering (real TextFilterMixin - render_text/
        # _commit_display_piece call filter_bad_words for real now) ---
        self.bad_words_by_lang = {
            "en": set(self.default_bad_words_en()),
            "es": set(self.default_bad_words_es()),
        }
        self.bad_word_filters_enabled = {"en": True, "es": True}
        self._refresh_bad_words()
        self.custom_vocab_langs_enabled = {"en": True, "es": True}

        # --- translation ---
        self.translation_enabled = True
        self.text_translation_provider = "local_nllb"
        self.local_nllb_model_name = NLLB_MODEL_NAME
        self.local_nllb_device = NLLB_DEVICE
        self.local_nllb_target_lang = NLLB_TARGET_FLORES
        self.local_nllb_max_chars = self.LOCAL_NLLB_DEFAULT_MAX_CHARS
        self.local_nllb_tokenizer = None
        self.local_nllb_model = None
        self.local_nllb_model_config = None
        self.local_nllb_resolved_device = ""
        self.local_nllb_lock = Lock()

        # --- sentence buffering / queues (TranscriptionMixin) ---
        self.sentence_buffer = ""
        self.sentence_buffer_pretranslated = False
        self.sentence_buffer_source_text = ""
        self.sentence_lock = Lock()
        self.sentence_last_update = 0.0
        self.sentence_max_chars = 200
        self.sentence_max_chars_no_interim = 100
        self.sentence_flush_ms = 100
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

    # ------------------------------------------------------------------ #
    # NLLB readiness glue that normally lives in settings_ui_mixin.py's
    # Download/Test flow (real SettingsUIMixin methods are mixed in here for
    # their display-layout helpers, not this flow - it still needs bypassing,
    # exactly as in web_transcription.py).
    # ------------------------------------------------------------------ #

    def _local_nllb_ready_for_translation(self):
        return True

    def prewarm_translation(self):
        """Load NLLB onto the GPU before any real speech arrives - see
        web_transcription.py for the measured cost of skipping this."""
        self.update_status("Loading translation model...")
        started = time.time()
        try:
            self._translate_with_local_nllb("Hello.")
        except Exception as exc:
            self.update_status(f"Translation model failed: {exc}")
            return
        self.update_status(f"Translation model ready ({int((time.time() - started) * 1000)} ms)")

    def _local_nllb_model_kwargs(self, local_files_only=True):
        return {"local_files_only": bool(local_files_only)}

    def _import_local_nllb_dependencies(self):
        import speech_recognition as sr

        try:
            import torch as torch_module
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            from transformers.utils import logging as hf_logging

            hf_logging.disable_progress_bar()
        except Exception as exc:
            raise sr.RequestError(self.LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE) from exc
        return torch_module, AutoModelForSeq2SeqLM, AutoTokenizer

    # ------------------------------------------------------------------ #
    # Genuine overrides. Everything above the constructor's audio-meter
    # section down to here is either real-mixin state or NLLB glue; nothing
    # below touches drip reveal, roll-up paging, wrapping or font fitting -
    # those are DisplayMixin/SettingsUIMixin's own unmodified bodies.
    # ------------------------------------------------------------------ #

    def _get_pixels_per_inch(self):
        # The real one reads self.root.winfo_fpixels("1i"); there is no Tk
        # root here, so both the layout math and the WebMeasurer it drives
        # are handed the same fixed browser-reference value instead.
        return self._ppi

    def render_text(self):
        super().render_text()
        # The one line this port actually adds: real DisplayMixin.render_text
        # queues Tk-shaped canvas ops through WebCanvas exactly as it always
        # has; this flushes them into a single evaluate_js round trip.
        self.text_canvas.flush()

    def update_status(self, msg):
        if msg == self.STATUS_LISTENING or msg.startswith("Listening"):
            msg = self._listening_status_message()
        self._log_status(msg)
        self.push_debug({"type": "status", "text": msg})

    def _realtime_stt_on_recorded_chunk(self, chunk):
        before = self.audio_level_last_update
        super()._realtime_stt_on_recorded_chunk(chunk)
        if self.audio_level_last_update != before:
            self.push_debug({"type": "level", "value": round(self.audio_level_target, 1)})


for _name in dir(TranslationApp):
    if _name.isupper():
        setattr(OutputEngine, _name, getattr(TranslationApp, _name))


HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;cursor:none}
#stage{display:flex;flex-direction:column;height:100vh}
#c{flex:1;display:block}
#statusbar{flex:0 0 auto;color:#9CA3AF;font:12px/1.4 "Segoe UI",system-ui,sans-serif;
 display:flex;align-items:center;gap:10px;padding:0 10px;background:#000}
#level{width:120px;height:6px;background:#14171C;border-radius:999px;overflow:hidden}
#level>div{height:100%;width:0;background:linear-gradient(90deg,#5B8FF7,#7AA5FF);transition:width 70ms linear}
</style></head><body>
<div id="stage">
  <canvas id="c"></canvas>
  <div id="statusbar"><span id="status">starting...</span><div id="level"><div id="bar"></div></div></div>
</div>
<script>
const canvas = document.getElementById('c')
const ctx = canvas.getContext('2d')
const items = {}

function initCanvas(){
  const dpr = window.devicePixelRatio || 1
  const totalW = window.innerWidth, totalH = window.innerHeight
  const statusH = document.getElementById('statusbar').getBoundingClientRect().height
  const canvasH = totalH - statusH
  canvas.width = Math.round(totalW * dpr)
  canvas.height = Math.round(canvasH * dpr)
  canvas.style.width = totalW + 'px'
  canvas.style.height = canvasH + 'px'
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return {w: totalW, h: canvasH}
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
    if (op.op === 'create'){
      items[op.id] = {type: 'text', x: op.x, y: op.y, anchor: op.anchor || 'nw', text: op.text || '',
                       fill: op.fill || '#fff', font: op.font || "16px 'Arial'", state: 'normal'}
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

function applyDebug(s){
  if (s.type === 'status'){ document.getElementById('status').textContent = s.text }
  else if (s.type === 'level'){ document.getElementById('bar').style.width = s.value + '%' }
}
</script></body></html>
"""


def main():
    window = webview.create_window(
        "Rhema - fullscreen output port test",
        html=HTML,
        fullscreen=True,
        background_color="#000000",
    )

    def push_debug(payload):
        try:
            window.evaluate_js(f"applyDebug({json.dumps(payload)})")
        except Exception:
            pass

    state = {}

    def on_loaded():
        dims = window.evaluate_js("initCanvas()")
        width, height = int(dims["w"]), int(dims["h"])
        canvas = WebCanvas(window, width, height)
        measurer = WebMeasurer(window, FONT_FAMILY, 50, PIXELS_PER_INCH)
        engine = OutputEngine(push_debug, canvas, measurer, PIXELS_PER_INCH)
        state["engine"] = engine
        engine.render_text()  # initial (empty) paint, also fits the starting font size

        engine.translation_thread = Thread(target=engine._translation_worker, daemon=True)
        engine.translation_thread.start()
        engine.display_thread = Thread(target=engine._display_worker, daemon=True)
        engine.display_thread.start()
        Thread(target=engine.prewarm_translation, daemon=True).start()
        engine._start_realtime_stt()

    def on_closed():
        engine = state.get("engine")
        if engine is None:
            return
        engine.listening = False
        try:
            engine._stop_realtime_stt()
        except Exception:
            pass
        try:
            engine._force_kill_realtime_stt_processes()
        except Exception:
            pass

    window.events.loaded += on_loaded
    window.events.closed += on_closed
    webview.start()


if __name__ == "__main__":
    main()
