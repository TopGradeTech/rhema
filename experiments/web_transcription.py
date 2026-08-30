r"""Port test: real RealtimeSTT + real local NLLB translation, no Tk anywhere.

Follow-on to web_controller.py, which proved the audio meter runs headless.
This proves the thing that actually matters: can the transcription and
translation pipeline itself - RealtimeSttMixin driving RealtimeSTT,
TranscriptionMixin's sentence buffering/queueing, TranslationMixin's local
NLLB calls, TextFilterMixin's vocabulary/scripture/spacing cleanup - run with
zero Tk, streaming real finals into a pywebview window from a live
microphone?

Yes, and the boundary this file draws is the finding. Every engine mixin is
used unmodified; what gets replaced falls into exactly three groups:

1. Presentation (DisplayMixin): update_text, update_status, render_text,
   _queue_interim_display, _meter_display_commit, _record_chunk_latency and
   _realtime_stt_on_recorded_chunk. These are the methods that draw to a Tk
   Canvas or poke a Tk widget. Replacing a canvas draw with a push to a web
   page is the entire shape of the port - done here with the simplest
   possible presentation (a scrolling list) so the engine below can be judged
   on its own. The real roll-up caption paging those methods implement is
   still the hard part a full port has to rebuild.
2. Local NLLB readiness glue that lives in settings_ui_mixin.py's
   Download/Test flow, which this test skips (the model is already cached).
3. _get_app_data_dir, so logs land under experiments/ instead of churning
   the app's own.

Everything else - RealtimeSTT lifecycle and dynamic silence tuning, sentence
buffering and flush heuristics, the translation queue and batching, NLLB
chunking and inference, language filtering, custom vocabulary and scripture
formatting, audio device enumeration, the dBFS meter math - runs untouched.

Settings below mirror the app's own settings.json (large-v3/tiny models,
facebook/nllb-200-3.3B, CUDA) rather than picking new ones, so this test
exercises models already known to be downloaded and working on this machine.

Setup: .venv\Scripts\pip.exe install pywebview   (same as web_controller.py -
       not a real dependency of the shipping app)

Run:  .venv\Scripts\python.exe experiments\web_transcription.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import json
import os
import queue
import sys
import threading
import time
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


def _preferred_device_label():
    """The app's own configured microphone, read from its settings.json, so
    this test listens on the same device rather than whatever Windows calls
    the default. Read directly rather than via load_settings(), which would
    also pull in monitor/geometry/UI state this test has no use for."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("preferred_device_label") or "")
    except Exception:
        return ""

# Plain data (regexes, message strings, language alias tables) reused
# verbatim from TranslationApp's class body rather than duplicated - these
# constants carry no Tk dependency, they just happen to live on a class that
# also has some.
_CONSTANT_NAMES = (
    "STATUS_LISTENING",
    "LOGGING_MODE_OPTIONS",
    "NON_WORD_PATTERN",
    "UNICODE_WORD_PATTERN",
    "UNICODE_WORD_CHAR_PATTERN",
    "UNICODE_LETTER_PATTERN",
    "SPANISH_WORD_PATTERN",
    "SPANISH_DIACRITIC_PATTERN",
    "TERMINAL_PUNCTUATION_PATTERN",
    "TRAILING_EDGE_PUNCTUATION_PATTERN",
    "PUNCTUATION_SPACING_PATTERN",
    "URL_SCHEME_PATTERN",
    "BARE_DOMAIN_PATTERN",
    "COMMON_DOMAIN_SUFFIXES",
    "LOCAL_NLLB_DEFAULT_MODEL_NAME",
    "LOCAL_NLLB_DEFAULT_TARGET_LANG",
    "LOCAL_NLLB_DEFAULT_MAX_CHARS",
    "LOCAL_NLLB_LANG_ALIASES",
    "LOCAL_NLLB_UNSUPPORTED_LANGUAGE_MESSAGE",
    "LOCAL_NLLB_MODEL_UNAVAILABLE_MESSAGE",
    "LOCAL_NLLB_MISSING_DEPENDENCIES_MESSAGE",
    "LOCAL_NLLB_NOT_READY_MESSAGE",
    "LOCAL_NLLB_CUDA_OOM_MESSAGE",
    "LOCAL_NLLB_TIMEOUT_MESSAGE",
    "LOCAL_NLLB_FAILED_MESSAGE",
    "STT_EDGE_NOISE_PREFIX_PATTERNS",
    "STT_EDGE_NOISE_SUFFIX_PATTERNS",
    "STT_STRICT_NOISE_MARKERS_NORMALIZED",
)


class FakeRoot:
    """Stands in for Tk's root just enough to satisfy `self.root.after(...)`
    calls sprinkled through the mixins for UI-thread marshaling. There is no
    UI thread here, so `after` just runs the callback soon on a timer thread
    instead, and `after_cancel` is a no-op - nothing in the paths this test
    exercises depends on a cancel actually landing."""

    def after(self, delay_ms, fn, *args):
        def safe_call():
            try:
                fn(*args)
            except Exception:
                pass

        timer = threading.Timer(max(0, delay_ms) / 1000.0, safe_call)
        timer.daemon = True
        timer.start()
        return timer

    def after_cancel(self, timer):
        try:
            timer.cancel()
        except Exception:
            pass


class HeadlessEngine(
    RealtimeSttMixin,
    TranscriptionMixin,
    TranslationMixin,
    TextFilterMixin,
    AudioCaptureMixin,
    DisplayMixin,
    MonitorMixin,
    LoggingMixin,
    SettingsMixin,
):
    """Real engine mixins, no Tk. Presentation methods below push to a web
    page instead of drawing to a Canvas; everything else is unmodified.

    DisplayMixin is mixed in whole rather than cherry-picked so the split is
    visible: the methods this class redefines are exactly the ones that touch
    a Tk Canvas or widget, and everything else in it (the dBFS meter math, the
    latency formatting, sentence-payload unpacking) is reused as-is. Python
    resolves this class's own definitions ahead of the base, so the overrides
    win without DisplayMixin needing any edit."""

    def _get_app_data_dir(self):
        # LoggingMixin's real version resolves to the repo root, which is
        # where the app itself writes. Sharing it would mean this experiment
        # churns the app's diagnostic logs - _prune_old_log_sessions keeps
        # only the 5 newest sessions, so a few test runs silently delete real
        # ones. Kept under experiments/ instead.
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_logs")
        os.makedirs(path, exist_ok=True)
        return path

    def __init__(self, push_state):
        self.push_state = push_state  # callback(dict) -> None

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
        self._apply_logging_mode_flags()  # sets status/trace/comparison/finalized log flags

        self.root = FakeRoot()
        self.listening = True
        self.live_line = ""
        self.show_interim_text = False
        self.speech_engine = "realtime-stt"

        # Read by _mark_startup_stt_ready. In the app these gate the settings
        # window's loading overlay; here nothing is gated, but the flag still
        # has to exist because the engine sets it unconditionally.
        self.startup_stt_ready = False

        # --- audio meter (real DisplayMixin math, as in web_controller.py) ---
        # RealtimeSTT taps the recorder's own chunks via on_recorded_chunk,
        # so the meter needs no second input stream.
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
        # Copied verbatim from TranslationApp.__init__ - instance data there,
        # not a class constant, so there is nothing importable to reuse.
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
    # Local NLLB glue that normally lives in settings_ui_mixin.py (Options'
    # Download/Test flow). This test skips that flow entirely and assumes
    # the model is already cached - exactly like settings.json's real
    # configuration is on this machine.
    # ------------------------------------------------------------------ #

    def _local_nllb_ready_for_translation(self):
        return True

    def prewarm_translation(self):
        """Load NLLB onto the GPU before any real speech arrives.

        The app does this at startup via _execute_local_nllb_test, behind the
        settings window's loading overlay. This test bypasses settings_ui_mixin
        entirely, so without an equivalent the first utterance pays the whole
        model load - and because _translate_with_local_nllb times from before
        _get_local_nllb_components, that load is reported as translation time.
        Measured on this machine: 12,727 ms for the first utterance against
        194-340 ms for every one after it, with the intervening speech piling
        up behind it in the queue.
        """
        self.push_state({"type": "status", "text": "Loading translation model..."})
        started = time.time()
        try:
            # A real translation is what actually populates local_nllb_model,
            # and no overrides are passed so it warms the configured direction
            # rather than some other language pair's tokenizer setup.
            self._translate_with_local_nllb("Hello.")
        except Exception as exc:
            self.push_state({"type": "status", "text": f"Translation model failed: {exc}"})
            return
        self.push_state({
            "type": "status",
            "text": f"Translation model ready ({int((time.time() - started) * 1000)} ms)",
        })

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
    # Presentation - the only methods actually replaced. Everything above
    # this line, and everything in the five mixins, is unmodified engine
    # code from the shipping app.
    # ------------------------------------------------------------------ #

    def _unpack_sentence_payload(self, payload):
        if isinstance(payload, dict):
            return payload.get("text", ""), payload.get("queued_at"), payload
        if isinstance(payload, tuple):
            if len(payload) == 3:
                return payload[0], payload[1], payload[2] if isinstance(payload[2], dict) else {}
            if len(payload) == 2:
                return payload[0], payload[1], {}
        return payload, None, {}

    def render_text(self):
        pass  # no canvas here - nothing to redraw

    def _record_chunk_latency(self, started_at, latency_meta=None, rendered_at=None):
        pass  # only hit on the empty-translation branch; not this test's point

    def _meter_display_commit(self, text, latency_meta=None, stage="display_commit"):
        # Real version drip-reveals word-by-word onto the Canvas and applies
        # filter_bad_words per-segment; both are display concerns that don't
        # exist yet here. Direct commit instead.
        self.update_text(text, latency_meta=latency_meta)

    def update_status(self, msg):
        if msg == self.STATUS_LISTENING or msg.startswith("Listening"):
            msg = self._listening_status_message()
        self._log_status(msg)
        self.push_state({"type": "status", "text": msg})

    def update_text(self, text, latency_meta=None):
        incoming = (text or "").strip()
        if not incoming:
            return
        self._trace_pipeline("display_update_input", incoming)
        meta = latency_meta or {}
        queued_at = meta.get("queued_at")
        latency_ms = int((time.time() - queued_at) * 1000) if queued_at else None
        self.push_state({
            "type": "final",
            "text": incoming,
            "source_text": meta.get("translation_source_text") or meta.get("source_text") or "",
            "latency_ms": latency_ms,
            "translate_ms": meta.get("translate_nllb_ms"),
        })

    def _realtime_stt_on_recorded_chunk(self, chunk):
        # The engine throttles its own recompute to ~25/sec by bumping
        # audio_level_last_update; chunks arrive ~5x faster than that. Push
        # only when that timestamp actually moved, or the page gets every
        # chunk and the JS bridge does 5x the work for the same numbers.
        before = self.audio_level_last_update
        super()._realtime_stt_on_recorded_chunk(chunk)
        if self.audio_level_last_update != before:
            self.push_state({"type": "level", "value": round(self.audio_level_target, 1)})

    _last_interim_push = 0.0

    def _queue_interim_display(self, text):
        # Real gate (_interim_display_active) forces this off whenever
        # translation is on, which it always is here - bypassed so the raw
        # partial is visible as proof the recorder is live, throttled to
        # ~8/sec same as the real render-coalescing window.
        now = time.time()
        if now - self._last_interim_push < 0.12:
            return
        self._last_interim_push = now
        self.push_state({"type": "interim", "text": (text or "").strip()})


for _name in _CONSTANT_NAMES:
    setattr(HeadlessEngine, _name, getattr(TranslationApp, _name))


HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><style>
:root{--bg:#1E2228;--card:#262A33;--text:#E5E7EB;--muted:#9CA3AF;--border:#3A3F4B;--accent:#5B8FF7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);user-select:none;
 font:13px/1.55 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.app{padding:14px;display:flex;flex-direction:column;gap:10px;height:100vh}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px}
.card h2{margin:0 0 8px;font-size:10.5px;font-weight:600;letter-spacing:.09em;
 text-transform:uppercase;color:var(--muted)}
#status{font-size:13px}
#interim{color:var(--muted);font-style:italic;min-height:1.4em}
.meter{height:8px;background:#14171C;border:1px solid var(--border);border-radius:999px;
 overflow:hidden;margin-top:8px}
.meter>div{height:100%;width:0;border-radius:999px;
 background:linear-gradient(90deg,var(--accent),#7AA5FF);transition:width 70ms linear}
.log{flex:1;overflow-y:auto;display:flex;flex-direction:column-reverse;gap:8px}
.entry{border-bottom:1px solid #2F343E;padding-bottom:8px}
.entry .src{color:#6B7280;font-size:11.5px}
.entry .out{font-size:16px;margin-top:2px}
.entry .meta{color:var(--muted);font-size:10.5px;margin-top:3px;font-variant-numeric:tabular-nums}
</style></head><body>
<div class="app">
  <div class="card">
    <h2>Engine status</h2>
    <div id="status">starting...</div>
    <div id="interim"></div>
    <div class="meter"><div id="bar"></div></div>
  </div>
  <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:0">
    <h2>Finalized output (real RealtimeSTT -&gt; real local NLLB)</h2>
    <div class="log" id="log"></div>
  </div>
</div>
<script>
function applyState(s){
  if(s.type === 'status'){ status.textContent = s.text }
  else if(s.type === 'level'){ bar.style.width = s.value + '%' }
  else if(s.type === 'interim'){ interim.textContent = s.text }
  else if(s.type === 'final'){
    interim.textContent = ''
    const entry = document.createElement('div')
    entry.className = 'entry'
    let html = ''
    if(s.source_text){ html += '<div class="src">' + escapeHtml(s.source_text) + '</div>' }
    html += '<div class="out">' + escapeHtml(s.text) + '</div>'
    const metaParts = []
    if(s.latency_ms != null){ metaParts.push(s.latency_ms + ' ms total') }
    if(s.translate_ms != null){ metaParts.push(s.translate_ms + ' ms NLLB') }
    if(metaParts.length){ html += '<div class="meta">' + metaParts.join(' &middot; ') + '</div>' }
    entry.innerHTML = html
    log.prepend(entry)
  }
}
function escapeHtml(t){
  const d = document.createElement('div')
  d.textContent = t
  return d.innerHTML
}
</script></body></html>
"""


def main():
    window = webview.create_window(
        "Rhema - transcription+translation port test",
        html=HTML,
        width=560,
        height=640,
        background_color="#1E2228",
    )

    def push_state(payload):
        try:
            window.evaluate_js(f"applyState({json.dumps(payload)})")
        except Exception:
            pass

    engine = HeadlessEngine(push_state)

    def on_loaded():
        engine.translation_thread = Thread(target=engine._translation_worker, daemon=True)
        engine.translation_thread.start()
        engine.display_thread = Thread(target=engine._display_worker, daemon=True)
        engine.display_thread.start()
        # Prewarm on its own thread so the NLLB load overlaps RealtimeSTT's
        # rather than running after it - both are slow, both are mostly
        # transfer, and there is 24 GB of VRAM for the pair of them.
        Thread(target=engine.prewarm_translation, daemon=True).start()
        engine._start_realtime_stt()

    def on_closed():
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
