r"""Port test: does Options' Apply/dirty-tracking mechanism survive when the
widget layer becomes an HTML form instead of Tk widgets?

Builds on web_output_window.py the same way web_video_overlay.py did -
OutputEngine/WebCanvas/WebMeasurer imported and reused, not duplicated.

The dirty-tracking machinery in settings_ui_mixin.py
(_new_settings_dirty_context, _track_settings_var, _capture_settings_snapshot,
_update_settings_dirty_state) is generic list-of-getters/list-equality logic -
nothing in it requires Tk. The one genuine surprise on reading it closely:
_collect_settings_vars_for_dirty_tracking gates on `isinstance(value,
tk.Variable)`, and tk.Variable's get/set/trace_add aren't a swappable
interface the way text_font/text_canvas were - they're backed by a live Tcl
interpreter (Variable.__init__ raises without one). That is NOT true of
everything else in this file: _apply_display_vars, _apply_transcription_vars,
_apply_translation_vars, save_settings/load_settings are all plain Python
that only ever calls .get() on whatever object sits in the vars-dict.

So the split this experiment proves is narrower and more interesting than
"Tk or not": real tk.Variable objects still exist here, backed by a hidden,
never-shown Tk() interpreter used for NOTHING but that Tcl-level
get/set/trace machinery - no widget is ever packed, no window ever mapped.
Every actual FORM CONTROL is HTML; its onchange handler calls into Python
(via pywebview's js_api) and does nothing but `var.set(value)`, which fires
the *real*, unmodified trace_add callback synchronously, which is what
flips the *real* dirty flag. Apply calls the *real* section methods
(_apply_display_vars/_apply_transcription_vars/_apply_translation_vars/
_apply_advanced_vars) directly - not the full _apply_settings_vars
dispatcher, which also calls enter_fullscreen() and device-refresh/
color-repaint machinery that are output-window chrome concerns, not
Options-mechanics ones, and already the subject of the two window
experiments. That boundary is deliberate, not an oversight - see
"Out of scope" below.

~30 of the real ~40 settings are wired now (up from the original 5),
covering every value shape dirty-tracking has to handle: numeric,
color/string, boolean, mapped-combobox (display name -> stored code/value,
using the SAME real option lists and maps settings_ui_mixin.py builds -
whisper_language_options()/nllb_language_options() from languages.py, the
real faster-whisper/NLLB model-size tables), a float slider, and - new in
this pass - free-text Tk.Text widgets (bad words / custom vocabulary).

**Two real findings from wiring the rest of these settings, both about the
hidden Tk() interpreter's thread affinity - the first version of this file
never actually got exercised across threads, since only 5 fields were ever
driven (by hand, slowly) instead of by a scripted test:**

1. pywebview dispatches `window.events.loaded` (see pywebview's own
   event.py: `Event.set()` spins a brand-new `threading.Thread` per firing
   unless `should_lock=True`, which `loaded` isn't) on a throwaway
   background thread, never the thread that ran `webview.create_window()`/
   `main()`. A CPython Tcl interpreter only accepts calls from threads other
   than the one that created it if that creating thread is actively running
   an event loop (mainloop()) to service the cross-thread queue. Since the
   original version of this file created `var_root` in `main()` and
   deliberately never called `.mainloop()` on it (the previous docstring's
   words), constructing tk.Variable/tk.Text objects for it *from inside
   on_loaded* - a different thread - raised
   `RuntimeError: main thread is not in main loop` on every single one.
   Confirmed both the failure and the fix in isolation (see
   project_pywebview_port_progress.md): running `var_root.mainloop()` on a
   small dedicated owner thread (started in main(), never the thread
   on_loaded/js_api calls arrive on) lets every other thread's Tk calls
   through transparently - this is CPython's actual supported multi-thread
   Tkinter model, not a hack. main() below starts that thread and waits for
   the interpreter to exist before building the window.
2. Once a mainloop is actually running, a tk.Text widget's <<Modified>>
   binding - what _track_settings_text in settings_ui_mixin.py uses for
   dirty-tracking, unlike tk.Variable's trace_add which fires synchronously
   with no event loop involved - starts delivering on its own within
   milliseconds, since <<Modified>> is a queued Tk window event and there's
   now something pumping the queue. set_text() below still forces a
   synchronous var_root.update() right after each edit anyway, so the
   dirty flag is guaranteed to already be flipped by the time its result
   reaches the calling JS - without it, a caller could read a stale
   "not dirty" a few milliseconds before the real event lands.

Still wired to fixed values, not exposed in the form (same as before -
required by the real methods' signatures, not something a user changes via
Options in a way this experiment needs to prove): video_lines_var,
monitor_var/settings_monitor_var (single "Monitor 1" placeholder - real
multi-monitor placement is a separate, not-yet-tackled item, see
project_pywebview_port_progress.md).

Out of scope, deliberately, same reasons as the original version of this
file:
- theme_var: applying it calls _apply_ui_theme() (a real ttkbootstrap Style
  + real Tk root/title-bar API) and schedules _rebuild_settings_windows()
  (destroys/recreates real Controller/Options Tk windows) - output-window
  chrome tied to actual Tk windows this minimal engine doesn't have, exactly
  like enter_fullscreen()/apply_colors() were excluded before.
- video_feed_enabled_var/video_device_var/video_caption_alpha_var: this
  engine has no VideoCaptureMixin (start_video_feed/stop_video_feed are
  no-op stubs), so wiring them would just flip inert bookkeeping - the real
  version of these lives in web_video_overlay.py instead.
- enable_translation_var: flipping translation on reaches
  _start_local_nllb_cache_check(prompt_if_missing=True), a real cache probe/
  ~2.5 GB download prompt an unattended port test should not trigger.
  translation_enabled is forced False in __init__ (see the comment there)
  and stays False for the whole session, so the OTHER NLLB settings below
  can be wired safely: _apply_translation_vars only reaches the cache-check
  call in its final `if self.translation_enabled:` line, never entered here.
- start_with_windows_var (writes HKCU\...\Run) and cuda_directory_var
  (validates a real directory and reconfigures the process's DLL search
  path) both write real, persistent state outside this repo - left
  unwired, same as before.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_options.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import os
import sys
import tkinter as tk
from threading import Event, Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

from languages import nllb_language_options, whisper_language_options  # noqa: E402
from web_output_window import (  # noqa: E402
    FONT_FAMILY,
    PIXELS_PER_INCH,
    OutputEngine,
    WebCanvas,
    WebMeasurer,
)

# Real option tables, copied verbatim from settings_ui_mixin.py's
# _build_transcription_section/_build_translation_section/
# _build_advanced_section - not reinvented, so the maps this test exercises
# are the same ones the shipping Options dialog builds.
REALTIME_STT_FINAL_MODEL_OPTIONS = [
    ("tiny (~1 GB VRAM)", "tiny"),
    ("base (~1 GB VRAM)", "base"),
    ("small (~2 GB VRAM)", "small"),
    ("medium (~5 GB VRAM)", "medium"),
    ("distil-large-v3 (~6 GB VRAM, fast)", "distil-large-v3"),
    ("large-v3-turbo (~6 GB VRAM, fast)", "large-v3-turbo"),
    ("large-v2 (~10 GB VRAM)", "large-v2"),
    ("large-v3 (~10 GB VRAM, recommended)", "large-v3"),
]
REALTIME_STT_REALTIME_MODEL_OPTIONS = [
    ("tiny (~1 GB VRAM, recommended)", "tiny"),
    ("base (~1 GB VRAM)", "base"),
    ("small (~2 GB VRAM)", "small"),
]
NLLB_MODEL_NAME_OPTIONS = [
    (
        "nllb-200-distilled-600M (~2.5 GB disk, ~4-6 GB VRAM, recommended)",
        "facebook/nllb-200-distilled-600M",
    ),
    (
        "nllb-200-distilled-1.3B (~5.5 GB disk, ~6-8 GB VRAM)",
        "facebook/nllb-200-distilled-1.3B",
    ),
    (
        "nllb-200-1.3B (~5.5 GB disk, ~8-10 GB VRAM, dense/higher quality)",
        "facebook/nllb-200-1.3B",
    ),
    (
        "nllb-200-3.3B (~13 GB disk, ~16+ GB VRAM, highest quality)",
        "facebook/nllb-200-3.3B",
    ),
]
LOGGING_MODE_OPTIONS = [
    ("Normal", "normal"),
    ("Debug", "debug"),
    ("Evaluation", "evaluation"),
    ("Full", "full"),
]


class OptionsEngine(OutputEngine):
    """Real DisplayMixin/SettingsUIMixin/SettingsMixin, unmodified. Adds a
    minimal but real vars-dict (genuine tk.Variable/tk.Text objects) and the
    real dirty-tracking context, then exposes set_var()/set_text()/apply()
    for the HTML form to call through pywebview's js_api."""

    def __init__(self, push_debug, canvas, measurer, pixels_per_inch, var_root):
        super().__init__(push_debug, canvas, measurer, pixels_per_inch)

        # load_settings()'s per-section loaders all follow the same pattern -
        # data.get(key, self.attr) - so every attribute they can fall back to
        # has to exist FIRST, exactly like main.py's own __init__ sets every
        # default before ever calling load_settings(). OutputEngine already
        # covers most of these (bg_color, max_lines, translation_enabled,
        # stt_device, ...); everything below is what it doesn't need for a
        # captions-only engine but load_settings()/save_settings() still
        # touch unconditionally. Real MonitorMixin/main.py defaults, not
        # invented ones.
        self.settings_path = os.path.join(self.app_data_dir, "settings.json")  # scratch, never the app's real one
        self.ui_theme = "light"
        self.video_caption_bar_alpha = 0.5
        self.lock_output_focus = False
        self.video_device_index = None
        self.start_with_windows = False
        self.cuda_directory = ""
        self._cuda_dll_directory_handles = []
        self.auto_switch_translation = False
        self.preferred_device_label = ""
        self.settings_window = None
        self.options_window = None
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
        self.monitors = self.get_monitors()  # real MonitorMixin enumeration, already Tk-free

        self.load_settings()

        # OutputEngine.__init__ defaults translation_enabled=True (that
        # engine exists to drive a real STT+NLLB pipeline); load_settings()
        # may have just loaded True again from a prior run of this file.
        # Forced off here - _apply_translation_vars({})'s "translation is
        # on" branch reaches _start_local_nllb_cache_check, a real cache
        # probe/download-prompt this test promises not to trigger (see the
        # module docstring), and one that needs nllb_status/
        # local_nllb_status_var/etc this minimal engine never initializes.
        # This holds even now that the OTHER nllb_* fields below are wired -
        # see the module docstring's "Out of scope" section.
        self.translation_enabled = False

        # _apply_display_vars calls these unconditionally; VideoCaptureMixin
        # isn't mixed in here (video is a separate experiment), so they need
        # to exist as no-ops rather than raise AttributeError.
        self.start_video_feed = lambda: None
        self.stop_video_feed = lambda: None

        # The one Tk dependency that doesn't go away: tk.Variable needs a
        # live Tcl interpreter to back .get()/.set()/trace_add, and tk.Text
        # needs one to exist at all. var_root is never shown - it exists
        # purely as that interpreter, not as a window - but IS given a
        # mainloop(), on its own dedicated thread started in main(), so
        # that calls made from other threads (on_loaded/js_api handlers)
        # don't raise "main thread is not in main loop" - see the module
        # docstring's thread-affinity finding. Kept on self so set_text()
        # can force a synchronous pump after each edit.
        self.var_root = var_root
        v = var_root
        only_monitor_label = "Monitor 1"

        stt_source_lang_options = [("Auto-detect", "auto")] + whisper_language_options()
        stt_source_lang_map = dict(stt_source_lang_options)
        stt_source_lang_rev_map = {code: name for name, code in stt_source_lang_options}

        final_model_map = dict(REALTIME_STT_FINAL_MODEL_OPTIONS)
        final_model_rev_map = {v_: k for k, v_ in REALTIME_STT_FINAL_MODEL_OPTIONS}
        realtime_model_map = dict(REALTIME_STT_REALTIME_MODEL_OPTIONS)
        realtime_model_rev_map = {v_: k for k, v_ in REALTIME_STT_REALTIME_MODEL_OPTIONS}

        nllb_model_name_map = dict(NLLB_MODEL_NAME_OPTIONS)
        nllb_model_name_rev_map = {v_: k for k, v_ in NLLB_MODEL_NAME_OPTIONS}
        nllb_target_lang_options = nllb_language_options()
        nllb_target_lang_map = dict(nllb_target_lang_options)
        nllb_target_lang_rev_map = {code: name for name, code in nllb_target_lang_options}

        logging_mode_map = dict(LOGGING_MODE_OPTIONS)
        logging_mode_rev_map = {v_: k for k, v_ in LOGGING_MODE_OPTIONS}

        # Kept for current_values()'s "display" dict below - code -> display
        # name lookups so the form's <select> elements can be set to the
        # right option after options() fills them in.
        self._stt_source_lang_rev_map = stt_source_lang_rev_map
        self._final_model_rev_map = final_model_rev_map
        self._realtime_model_rev_map = realtime_model_rev_map
        self._nllb_model_name_rev_map = nllb_model_name_rev_map
        self._nllb_target_lang_rev_map = nllb_target_lang_rev_map
        self._logging_mode_rev_map = logging_mode_rev_map

        self.display_vars = {
            "lines_var": tk.IntVar(master=v, value=self.max_lines),
            # Required by _apply_display_vars's signature but not exposed in
            # this minimal form - fixed values, same as a real session where
            # the user never touches them.
            "video_lines_var": tk.IntVar(master=v, value=self.video_max_lines),
            "bg_color_var": tk.StringVar(master=v, value=self.bg_color),
            "text_color_var": tk.StringVar(master=v, value=self.text_color),
            "monitor_labels": [only_monitor_label],
            "monitor_var": tk.StringVar(master=v, value=only_monitor_label),
            "settings_monitor_var": tk.StringVar(master=v, value=only_monitor_label),
            "clear_display_on_inactivity_var": tk.BooleanVar(
                master=v, value=self.clear_display_on_inactivity
            ),
            "clear_display_inactivity_seconds_var": tk.IntVar(
                master=v, value=self.clear_display_inactivity_seconds
            ),
            "lock_output_focus_var": tk.BooleanVar(master=v, value=self.lock_output_focus),
        }
        self.transcription_vars = {
            "show_interim_text_var": tk.BooleanVar(master=v, value=self.show_interim_text),
            "stt_device_var": tk.StringVar(master=v, value=self.stt_device),
            "stt_source_lang_var": tk.StringVar(
                master=v,
                value=stt_source_lang_rev_map.get(self.source_lang or "auto", "Auto-detect"),
            ),
            "stt_source_lang_map": stt_source_lang_map,
            "realtime_stt_final_model_var": tk.StringVar(
                master=v,
                value=final_model_rev_map.get(self.realtime_stt_final_model, "large-v3 (~10 GB VRAM, recommended)"),
            ),
            "realtime_stt_final_model_map": final_model_map,
            "realtime_stt_realtime_model_var": tk.StringVar(
                master=v,
                value=realtime_model_rev_map.get(
                    self.realtime_stt_realtime_model, "tiny (~1 GB VRAM, recommended)"
                ),
            ),
            "realtime_stt_realtime_model_map": realtime_model_map,
            "realtime_stt_silero_var": tk.DoubleVar(
                master=v, value=self.realtime_stt_silero_sensitivity
            ),
        }
        # Deliberately excludes enable_translation_var - see the module
        # docstring's "Out of scope" section. Every other field here is safe
        # to apply with translation forced off.
        self.translation_vars = {
            "local_nllb_model_name_var": tk.StringVar(
                master=v,
                value=nllb_model_name_rev_map.get(
                    self.local_nllb_model_name, NLLB_MODEL_NAME_OPTIONS[0][0]
                ),
            ),
            "local_nllb_model_name_map": nllb_model_name_map,
            "local_nllb_device_var": tk.StringVar(master=v, value=self.local_nllb_device),
            "local_nllb_target_lang_var": tk.StringVar(
                master=v,
                value=nllb_target_lang_rev_map.get(self.local_nllb_target_lang, "English"),
            ),
            "local_nllb_target_lang_map": nllb_target_lang_map,
            "local_nllb_max_chars_var": tk.IntVar(master=v, value=self.local_nllb_max_chars),
        }
        # Deliberately excludes start_with_windows_var/cuda_directory_var -
        # see the module docstring's "Out of scope" section.
        self.advanced_vars = {
            "logging_mode_var": tk.StringVar(
                master=v, value=logging_mode_rev_map.get(self.logging_mode, "Normal")
            ),
            "logging_mode_map": logging_mode_map,
            "bad_words_en_text": tk.Text(v),
            "bad_words_es_text": tk.Text(v),
            "custom_vocab_en_text": tk.Text(v),
            "custom_vocab_es_text": tk.Text(v),
        }
        self.advanced_vars["bad_words_en_text"].insert(
            "1.0", ", ".join(sorted(self.bad_words_by_lang.get("en", [])))
        )
        self.advanced_vars["bad_words_es_text"].insert(
            "1.0", ", ".join(sorted(self.bad_words_by_lang.get("es", [])))
        )
        self.advanced_vars["custom_vocab_en_text"].insert(
            "1.0", ", ".join(self.custom_vocabulary_by_lang.get("en", []))
        )
        self.advanced_vars["custom_vocab_es_text"].insert(
            "1.0", ", ".join(self.custom_vocabulary_by_lang.get("es", []))
        )
        for widget in (
            self.advanced_vars["bad_words_en_text"],
            self.advanced_vars["bad_words_es_text"],
            self.advanced_vars["custom_vocab_en_text"],
            self.advanced_vars["custom_vocab_es_text"],
        ):
            widget.edit_modified(False)  # the initial .insert() above flips it; clear before tracking starts

        self.dirty_ctx = self._new_settings_dirty_context()
        self._collect_settings_vars_for_dirty_tracking(self.display_vars, self.dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(self.transcription_vars, self.dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(self.translation_vars, self.dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(self.advanced_vars, self.dirty_ctx)
        self.dirty_ctx["dirty_ready"] = True
        self.dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(self.dirty_ctx)

    # ------------------------------------------------------------------ #
    # js_api surface - called from the HTML form via pywebview.api.*
    # ------------------------------------------------------------------ #

    def _find_var(self, name):
        for mapping in (
            self.display_vars,
            self.transcription_vars,
            self.translation_vars,
            self.advanced_vars,
        ):
            value = mapping.get(name)
            if isinstance(value, tk.Variable):
                return value
        return None

    def set_var(self, name, value):
        var = self._find_var(name)
        if var is None:
            return {"ok": False, "error": f"unknown var {name!r}"}
        # var.set() fires the real trace_add("write", ...) callback
        # (_update_settings_dirty_state) synchronously, before this returns -
        # Tcl variable traces don't need an event loop to fire.
        var.set(value)
        return {"ok": True, "dirty": bool(self.dirty_ctx["dirty_value"])}

    def set_text(self, name, value):
        widget = self.advanced_vars.get(name)
        if not isinstance(widget, tk.Text):
            return {"ok": False, "error": f"unknown text field {name!r}"}
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        # <<Modified>> is a queued Tk window event, not fired synchronously
        # like a Variable trace - see the module docstring. update_idletasks()
        # is not enough; this needs a real update() to pump it.
        self.var_root.update()
        return {"ok": True, "dirty": bool(self.dirty_ctx["dirty_value"])}

    def options(self):
        """Dropdown option lists for the form - real display strings, same
        source data settings_ui_mixin.py's own comboboxes use."""
        return {
            "stt_source_lang": [name for name, _code in ([("Auto-detect", "auto")] + whisper_language_options())],
            "realtime_stt_final_model": [name for name, _code in REALTIME_STT_FINAL_MODEL_OPTIONS],
            "realtime_stt_realtime_model": [name for name, _code in REALTIME_STT_REALTIME_MODEL_OPTIONS],
            "local_nllb_model_name": [name for name, _code in NLLB_MODEL_NAME_OPTIONS],
            "local_nllb_target_lang": [name for name, _code in nllb_language_options()],
            "logging_mode": [name for name, _code in LOGGING_MODE_OPTIONS],
        }

    def apply(self):
        restart_before = self._realtime_stt_restart_requested
        try:
            self._apply_display_vars(self.display_vars)
            self._apply_transcription_vars(self.transcription_vars)
            self._apply_translation_vars(self.translation_vars)
            self._apply_advanced_vars(self.advanced_vars)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self.dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(self.dirty_ctx)
        self._update_settings_dirty_state(self.dirty_ctx, force=True)
        self.save_settings()
        return {
            "ok": True,
            "dirty": bool(self.dirty_ctx["dirty_value"]),
            "values": self._current_value_dict(),
            "restart_requested": self._realtime_stt_restart_requested,
            "restart_requested_changed": self._realtime_stt_restart_requested != restart_before,
            "settings_path": self.settings_path,
        }

    def _current_value_dict(self):
        return {
            "max_lines": self.max_lines,
            "bg_color": self.bg_color,
            "show_interim_text": self.show_interim_text,
            "stt_device": self.stt_device,
            "clear_display_on_inactivity": self.clear_display_on_inactivity,
            "clear_display_inactivity_seconds": self.clear_display_inactivity_seconds,
            "lock_output_focus": self.lock_output_focus,
            "source_lang": self.source_lang,
            "realtime_stt_final_model": self.realtime_stt_final_model,
            "realtime_stt_realtime_model": self.realtime_stt_realtime_model,
            "realtime_stt_silero_sensitivity": self.realtime_stt_silero_sensitivity,
            "translation_enabled": self.translation_enabled,
            "local_nllb_model_name": self.local_nllb_model_name,
            "local_nllb_device": self.local_nllb_device,
            "local_nllb_target_lang": self.local_nllb_target_lang,
            "local_nllb_max_chars": self.local_nllb_max_chars,
            "logging_mode": self.logging_mode,
            "bad_words_en": sorted(self.bad_words_by_lang.get("en", [])),
            "bad_words_es": sorted(self.bad_words_by_lang.get("es", [])),
            "custom_vocab_en": self.custom_vocabulary_by_lang.get("en", []),
            "custom_vocab_es": self.custom_vocabulary_by_lang.get("es", []),
        }

    def _current_display_dict(self):
        """Display-name form of the mapped-combobox fields (code -> name),
        so the HTML <select> elements can be set to the right option once
        options() has populated them - current_values() returns the
        internal code/value form for everything else."""
        return {
            "source_lang": self._stt_source_lang_rev_map.get(
                self.source_lang or "auto", "Auto-detect"
            ),
            "realtime_stt_final_model": self._final_model_rev_map.get(
                self.realtime_stt_final_model, REALTIME_STT_FINAL_MODEL_OPTIONS[-1][0]
            ),
            "realtime_stt_realtime_model": self._realtime_model_rev_map.get(
                self.realtime_stt_realtime_model, REALTIME_STT_REALTIME_MODEL_OPTIONS[0][0]
            ),
            "local_nllb_model_name": self._nllb_model_name_rev_map.get(
                self.local_nllb_model_name, NLLB_MODEL_NAME_OPTIONS[0][0]
            ),
            "local_nllb_target_lang": self._nllb_target_lang_rev_map.get(
                self.local_nllb_target_lang, "English"
            ),
            "logging_mode": self._logging_mode_rev_map.get(self.logging_mode, "Normal"),
        }

    def current_values(self):
        values = self._current_value_dict()
        values["display"] = self._current_display_dict()
        values["dirty"] = bool(self.dirty_ctx["dirty_value"])
        return values


HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema Options port test</title>
<script>
// A hidden canvas purely so WebMeasurer's measure()/lineHeight() calls (used
// by the real _fit_font_to_lines/_apply_scaled_fonts this page's Apply
// triggers) have something to measure against - see web_output_window.py,
// same functions, unmodified. Nothing here is ever drawn on screen; this
// experiment's visible surface is the form below, not a caption canvas.
const _measureCanvas = document.createElement('canvas')
const _ctx = _measureCanvas.getContext('2d')
function measure(font, text){ _ctx.font = font; return _ctx.measureText(text).width }
function lineHeight(font){
  _ctx.font = font
  const m = _ctx.measureText('Hg')
  return m.fontBoundingBoxAscent + m.fontBoundingBoxDescent
}
</script>
<style>
:root{--bg:#1E2228;--card:#262A33;--text:#E5E7EB;--muted:#9CA3AF;--border:#3A3F4B;--accent:#5B8FF7;--dirty:#E0A458}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
 font:14px/1.5 "Segoe UI","Segoe UI Variable Text",system-ui,sans-serif;padding:24px}
.card{max-width:640px;margin:0 auto 24px;background:var(--card);border:1px solid var(--border);
 border-radius:12px;padding:20px}
h1{font-size:15px;margin:0 0 4px;color:var(--text)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:18px 0 2px}
.sub{color:var(--muted);font-size:12px;margin:0 0 18px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 0;
 border-bottom:1px solid #2F343E}
.row:last-of-type{border-bottom:none}
label{font-size:13px}
input[type=number]{width:80px;background:#14171C;border:1px solid var(--border);color:var(--text);
 border-radius:6px;padding:4px 8px}
input[type=color]{width:40px;height:26px;border:none;background:none;padding:0}
select{background:#14171C;border:1px solid var(--border);color:var(--text);border-radius:6px;padding:4px 8px;
 max-width:280px}
textarea{width:100%;background:#14171C;border:1px solid var(--border);color:var(--text);border-radius:6px;
 padding:6px 8px;font:12px/1.4 monospace;resize:vertical}
input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent)}
#apply{margin-top:16px;width:100%;padding:10px;border-radius:8px;border:none;font-size:13px;font-weight:600;
 background:#3A3F4B;color:#6B7280;cursor:not-allowed}
#apply.dirty{background:var(--dirty);color:#1E2228;cursor:pointer}
#status{margin-top:12px;font-size:12px;color:var(--muted);white-space:pre-wrap}
</style></head><body>
<div class="card">
  <h1>Rhema Options - port test</h1>
  <p class="sub">~30 of ~40 real settings, wired to genuine tk.Variable/tk.Text objects behind an HTML form.
  Translation stays off and Windows-startup/CUDA-directory are left out - see the file's docstring.</p>

  <h2>Display</h2>
  <div class="row"><label>Max caption lines</label><input type="number" id="lines" min="4" max="10"></div>
  <div class="row"><label>Background color</label><input type="color" id="bg"></div>
  <div class="row"><label>Lock output window focus</label><input type="checkbox" id="lockFocus"></div>
  <div class="row"><label>Clear display on inactivity</label><input type="checkbox" id="clear"></div>
  <div class="row"><label>&nbsp;&nbsp;...after N seconds</label><input type="number" id="clearSeconds" min="5" max="3600"></div>

  <h2>Transcription</h2>
  <div class="row"><label>Show live interim text</label><input type="checkbox" id="interim"></div>
  <div class="row"><label>STT device</label>
    <select id="device"><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="auto">Auto</option></select></div>
  <div class="row"><label>Source language</label><select id="sourceLang"></select></div>
  <div class="row"><label>Final model</label><select id="finalModel"></select></div>
  <div class="row"><label>Realtime model</label><select id="realtimeModel"></select></div>
  <div class="row"><label>Voice sensitivity</label><input type="number" id="silero" min="0.1" max="0.9" step="0.05"></div>

  <h2>Translation (Local NLLB) &mdash; stays off in this test</h2>
  <div class="row"><label>Model name</label><select id="nllbModel"></select></div>
  <div class="row"><label>Device</label>
    <select id="nllbDevice"><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="auto">Auto</option></select></div>
  <div class="row"><label>Target language</label><select id="nllbTargetLang"></select></div>
  <div class="row"><label>Max chars per chunk</label><input type="number" id="nllbMaxChars" min="250" max="20000" step="250"></div>

  <h2>Advanced</h2>
  <div class="row"><label>Logging mode</label><select id="loggingMode"></select></div>
  <div class="row"><label>Bad words (English, comma-separated)</label></div>
  <textarea id="badWordsEn" rows="2"></textarea>
  <div class="row"><label>Bad words (Spanish, comma-separated)</label></div>
  <textarea id="badWordsEs" rows="2"></textarea>
  <div class="row"><label>Custom vocabulary (English, comma-separated)</label></div>
  <textarea id="vocabEn" rows="2"></textarea>
  <div class="row"><label>Custom vocabulary (Spanish, comma-separated)</label></div>
  <textarea id="vocabEs" rows="2"></textarea>

  <button id="apply" disabled>Apply</button>
  <div id="status">loading current settings...</div>
</div>
<script>
const applyBtn = document.getElementById('apply')
const statusEl = document.getElementById('status')

// Variable-backed fields: id -> {varName, kind}. checkbox/number/color/select
// all route through set_var(); the four textareas route through set_text()
// instead (see fields2 below) since they back real tk.Text widgets, not
// tk.Variable ones.
const fields = {
  lines: {varName: 'lines_var', kind: 'int'},
  bg: {varName: 'bg_color_var', kind: 'str'},
  lockFocus: {varName: 'lock_output_focus_var', kind: 'bool'},
  clear: {varName: 'clear_display_on_inactivity_var', kind: 'bool'},
  clearSeconds: {varName: 'clear_display_inactivity_seconds_var', kind: 'int'},
  interim: {varName: 'show_interim_text_var', kind: 'bool'},
  device: {varName: 'stt_device_var', kind: 'str'},
  sourceLang: {varName: 'stt_source_lang_var', kind: 'str'},
  finalModel: {varName: 'realtime_stt_final_model_var', kind: 'str'},
  realtimeModel: {varName: 'realtime_stt_realtime_model_var', kind: 'str'},
  silero: {varName: 'realtime_stt_silero_var', kind: 'float'},
  nllbModel: {varName: 'local_nllb_model_name_var', kind: 'str'},
  nllbDevice: {varName: 'local_nllb_device_var', kind: 'str'},
  nllbTargetLang: {varName: 'local_nllb_target_lang_var', kind: 'str'},
  nllbMaxChars: {varName: 'local_nllb_max_chars_var', kind: 'int'},
  loggingMode: {varName: 'logging_mode_var', kind: 'str'},
}
const textFields = {
  badWordsEn: 'bad_words_en_text',
  badWordsEs: 'bad_words_es_text',
  vocabEn: 'custom_vocab_en_text',
  vocabEs: 'custom_vocab_es_text',
}

function elFor(key){ return document.getElementById(key) }

function fieldValue(key, f){
  const el = elFor(key)
  if (el.type === 'checkbox') return el.checked
  if (f.kind === 'int') return parseInt(el.value, 10)
  if (f.kind === 'float') return parseFloat(el.value)
  return el.value
}

function setDirty(isDirty){
  applyBtn.disabled = !isDirty
  applyBtn.classList.toggle('dirty', isDirty)
}

async function onFieldChange(key){
  const f = fields[key]
  const result = await pywebview.api.set_var(f.varName, fieldValue(key, f))
  setDirty(result.dirty)
}

async function onTextChange(key){
  const result = await pywebview.api.set_text(textFields[key], elFor(key).value)
  setDirty(result.dirty)
}

for (const key in fields){
  elFor(key).addEventListener('input', () => onFieldChange(key))
}
for (const key in textFields){
  elFor(key).addEventListener('input', () => onTextChange(key))
}

function fillSelect(id, names, selected){
  const el = elFor(id)
  el.innerHTML = ''
  for (const name of names){
    const opt = document.createElement('option')
    opt.value = name
    opt.textContent = name
    el.appendChild(opt)
  }
  if (selected) el.value = selected
}

applyBtn.addEventListener('click', async () => {
  const result = await pywebview.api.apply()
  if (!result.ok){
    statusEl.textContent = 'Apply failed: ' + result.error
    return
  }
  setDirty(result.dirty)
  const v = result.values
  const lines = [
    'Applied and saved to ' + result.settings_path,
    'max_lines=' + v.max_lines + '  bg_color=' + v.bg_color + '  lock_output_focus=' + v.lock_output_focus,
    'clear_on_inactivity=' + v.clear_display_on_inactivity + ' (' + v.clear_display_inactivity_seconds + 's)',
    'show_interim_text=' + v.show_interim_text + '  stt_device=' + v.stt_device + '  source_lang=' + v.source_lang,
    'final_model=' + v.realtime_stt_final_model + '  realtime_model=' + v.realtime_stt_realtime_model
      + '  silero=' + v.realtime_stt_silero_sensitivity,
    'translation_enabled=' + v.translation_enabled + ' (forced off by this test)'
      + '  nllb_model=' + v.local_nllb_model_name + '  nllb_device=' + v.local_nllb_device
      + '  nllb_target_lang=' + v.local_nllb_target_lang + '  nllb_max_chars=' + v.local_nllb_max_chars,
    'logging_mode=' + v.logging_mode,
    'bad_words_en=[' + v.bad_words_en.join(', ') + ']',
    'bad_words_es=[' + v.bad_words_es.join(', ') + ']',
    'custom_vocab_en=[' + v.custom_vocab_en.join(', ') + ']',
    'custom_vocab_es=[' + v.custom_vocab_es.join(', ') + ']',
  ]
  if (result.restart_requested_changed){
    lines.push('STT restart flag changed -> _realtime_stt_restart_requested is now ' + result.restart_requested
      + ' (the same flag the real Apply button sets; nothing services it here since RealtimeSTT is not running)')
  }
  statusEl.textContent = lines.join('\n')
})

window.addEventListener('pywebviewready', async () => {
  // The real OptionsEngine is built in on_loaded (it needs this window's
  // evaluate_js to exist first, for the font measurer) - pywebviewready can
  // fire a moment before that Python-side construction finishes, so poll
  // briefly rather than fail on a one-shot race.
  let v = null
  for (let i = 0; i < 50 && !v; i++){
    v = await pywebview.api.current_values()
    if (!v) await new Promise(r => setTimeout(r, 50))
  }
  if (!v){ statusEl.textContent = 'Engine did not become ready.'; return }
  const opts = await pywebview.api.options()

  document.getElementById('lines').value = v.max_lines
  document.getElementById('bg').value = v.bg_color
  document.getElementById('lockFocus').checked = v.lock_output_focus
  document.getElementById('clear').checked = v.clear_display_on_inactivity
  document.getElementById('clearSeconds').value = v.clear_display_inactivity_seconds
  document.getElementById('interim').checked = v.show_interim_text
  document.getElementById('device').value = v.stt_device
  document.getElementById('silero').value = v.realtime_stt_silero_sensitivity
  document.getElementById('nllbDevice').value = v.local_nllb_device
  document.getElementById('nllbMaxChars').value = v.local_nllb_max_chars
  document.getElementById('badWordsEn').value = v.bad_words_en.join(', ')
  document.getElementById('badWordsEs').value = v.bad_words_es.join(', ')
  document.getElementById('vocabEn').value = v.custom_vocab_en.join(', ')
  document.getElementById('vocabEs').value = v.custom_vocab_es.join(', ')

  fillSelect('sourceLang', opts.stt_source_lang, v.display.source_lang)
  fillSelect('finalModel', opts.realtime_stt_final_model, v.display.realtime_stt_final_model)
  fillSelect('realtimeModel', opts.realtime_stt_realtime_model, v.display.realtime_stt_realtime_model)
  fillSelect('nllbModel', opts.local_nllb_model_name, v.display.local_nllb_model_name)
  fillSelect('nllbTargetLang', opts.local_nllb_target_lang, v.display.local_nllb_target_lang)
  fillSelect('loggingMode', opts.logging_mode, v.display.logging_mode)

  setDirty(v.dirty)
  statusEl.textContent = 'Loaded current settings from ' + v.settings_path + '. Nothing dirty yet.'
})
</script></body></html>
"""


class Api:
    """Thin proxy registered as js_api before the window (and therefore the
    real OptionsEngine, which needs the window to exist for evaluate_js)
    exists. Delegates to engine once on_loaded builds one."""

    def __init__(self):
        self.engine = None

    def set_var(self, name, value):
        if self.engine is None:
            return {"ok": False, "dirty": False}
        return self.engine.set_var(name, value)

    def set_text(self, name, value):
        if self.engine is None:
            return {"ok": False, "dirty": False}
        return self.engine.set_text(name, value)

    def apply(self):
        if self.engine is None:
            return {"ok": False, "error": "engine not ready yet"}
        return self.engine.apply()

    def current_values(self):
        if self.engine is None:
            return None
        values = self.engine.current_values()
        values["settings_path"] = self.engine.settings_path
        return values

    def options(self):
        if self.engine is None:
            return {}
        return self.engine.options()


def _start_var_root():
    """Owns the hidden Tk() interpreter on its own dedicated thread and runs
    mainloop() on it forever (until the window closes) - required so that
    on_loaded/js_api calls, which pywebview dispatches on other, throwaway
    threads, can touch tk.Variable/tk.Text objects without CPython's
    _tkinter raising "main thread is not in main loop" (see the module
    docstring's thread-affinity finding). Returns the ready-to-use root;
    the caller only ever touches it, never this thread directly."""
    ready = Event()
    box = {}

    def owner():
        root = tk.Tk()
        root.withdraw()
        box["root"] = root
        ready.set()
        root.mainloop()

    Thread(target=owner, daemon=True).start()
    ready.wait()
    return box["root"]


def main():
    var_root = _start_var_root()

    api = Api()
    window = webview.create_window(
        "Rhema Options - port test",
        html=HTML,
        js_api=api,
        width=680,
        height=760,
        background_color="#1E2228",
    )

    def on_loaded():
        canvas = WebCanvas(window, width=1, height=1)
        measurer = WebMeasurer(window, FONT_FAMILY, 50, PIXELS_PER_INCH)
        api.engine = OptionsEngine(lambda p: None, canvas, measurer, PIXELS_PER_INCH, var_root)

    def on_closed():
        try:
            # destroy() ends var_root's own mainloop(), letting its owner
            # thread exit; safe to call cross-thread for the same reason
            # every other var_root call in this file is (see above).
            var_root.destroy()
        except Exception:
            pass

    window.events.loaded += on_loaded
    window.events.closed += on_closed
    webview.start()


if __name__ == "__main__":
    main()
