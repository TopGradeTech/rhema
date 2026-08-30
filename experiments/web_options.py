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
never-shown, never-mainloop'd Tk() interpreter used for NOTHING but that
Tcl-level get/set/trace machinery - no widget, no window, no event loop.
Every actual FORM CONTROL is HTML; its onchange handler calls into Python
(via pywebview's js_api) and does nothing but `var.set(value)`, which fires
the *real*, unmodified trace_add callback synchronously, which is what
flips the *real* dirty flag. Apply calls the *real* section methods
(_apply_display_vars/_apply_transcription_vars) directly - not the full
_apply_settings_vars dispatcher, which also calls enter_fullscreen() and
device-refresh/color-repaint machinery that are output-window chrome
concerns, not Options-mechanics ones, and already the subject of the two
window experiments. That boundary is deliberate, not an oversight - see
"Out of scope" below.

Only 5 settings are wired to the form (max lines, background color, show
live interim text, STT device, clear-on-inactivity) out of the ~40 the real
Options window has - enough to cover every type dirty-tracking has to
handle (numeric, color/string, boolean, mapped-combobox, another boolean)
and one with a real, observable side effect: changing "STT device" sets the
same _realtime_stt_restart_requested flag _apply_transcription_vars sets in
the shipping app, without needing RealtimeSTT actually running to observe
it (the flag is only ever *serviced* by the capture thread, never set
inline - see _request_capture_restart in realtime_stt_mixin.py). The other
~35 settings would wire up exactly the same way; there is no reason to
believe any of them behaves differently, so this proves the mechanism
rather than re-deriving it 40 times.

Out of scope, deliberately: the NLLB download/model-swap side effects in
_apply_translation_vars (translation stays off - translation_vars is {},
which every code path in that method already tolerates) and the Windows-
startup-registry / CUDA-directory side effects in _apply_advanced_vars
(advanced_vars is {} for the same reason) - both write real, persistent
state outside this repo (a 2.5GB model download; HKCU\...\Run) that an
unattended port test should not trigger on its own. enter_fullscreen(),
apply_colors() and _refresh_audio_devices() (the tail of the real
_apply_settings_vars dispatcher) are output-window chrome, not settings
mechanics, and are skipped for the same reason web_video_overlay.py didn't
tackle multi-monitor: a different question than the one this file answers.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_options.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

from web_output_window import (  # noqa: E402
    FONT_FAMILY,
    PIXELS_PER_INCH,
    OutputEngine,
    WebCanvas,
    WebMeasurer,
)


class OptionsEngine(OutputEngine):
    """Real DisplayMixin/SettingsUIMixin/SettingsMixin, unmodified. Adds a
    minimal but real vars-dict (genuine tk.Variable objects) and the real
    dirty-tracking context, then exposes set_var()/apply() for the HTML
    form to call through pywebview's js_api."""

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
        self.translation_enabled = False

        # _apply_display_vars calls these unconditionally; VideoCaptureMixin
        # isn't mixed in here (video is a separate experiment), so they need
        # to exist as no-ops rather than raise AttributeError.
        self.start_video_feed = lambda: None
        self.stop_video_feed = lambda: None

        # The one Tk dependency that doesn't go away: tk.Variable needs a
        # live Tcl interpreter to back .get()/.set()/trace_add. var_root is
        # never shown and mainloop() is never called on it - it exists
        # purely as that interpreter, not as a window.
        v = var_root
        only_monitor_label = "Monitor 1"
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
        }
        self.transcription_vars = {
            "show_interim_text_var": tk.BooleanVar(master=v, value=self.show_interim_text),
            "stt_device_var": tk.StringVar(master=v, value=self.stt_device),
        }

        self.dirty_ctx = self._new_settings_dirty_context()
        self._collect_settings_vars_for_dirty_tracking(self.display_vars, self.dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(self.transcription_vars, self.dirty_ctx)
        self.dirty_ctx["dirty_ready"] = True
        self.dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(self.dirty_ctx)

    # ------------------------------------------------------------------ #
    # js_api surface - called from the HTML form via pywebview.api.*
    # ------------------------------------------------------------------ #

    def set_var(self, name, value):
        var = self.display_vars.get(name) or self.transcription_vars.get(name)
        if var is None:
            return {"ok": False, "error": f"unknown var {name!r}"}
        # var.set() fires the real trace_add("write", ...) callback
        # (_update_settings_dirty_state) synchronously, before this returns -
        # Tcl variable traces don't need an event loop to fire.
        var.set(value)
        return {"ok": True, "dirty": bool(self.dirty_ctx["dirty_value"])}

    def apply(self):
        restart_before = self._realtime_stt_restart_requested
        try:
            self._apply_display_vars(self.display_vars)
            self._apply_transcription_vars(self.transcription_vars)
            self._apply_translation_vars({})
            self._apply_advanced_vars({})
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self.dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(self.dirty_ctx)
        self._update_settings_dirty_state(self.dirty_ctx, force=True)
        self.save_settings()
        return {
            "ok": True,
            "dirty": bool(self.dirty_ctx["dirty_value"]),
            "max_lines": self.max_lines,
            "bg_color": self.bg_color,
            "show_interim_text": self.show_interim_text,
            "stt_device": self.stt_device,
            "clear_display_on_inactivity": self.clear_display_on_inactivity,
            "restart_requested": self._realtime_stt_restart_requested,
            "restart_requested_changed": self._realtime_stt_restart_requested != restart_before,
            "settings_path": self.settings_path,
        }

    def current_values(self):
        return {
            "max_lines": self.max_lines,
            "bg_color": self.bg_color,
            "show_interim_text": self.show_interim_text,
            "stt_device": self.stt_device,
            "clear_display_on_inactivity": self.clear_display_on_inactivity,
            "dirty": bool(self.dirty_ctx["dirty_value"]),
        }


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
.card{max-width:480px;margin:0 auto;background:var(--card);border:1px solid var(--border);
 border-radius:12px;padding:20px}
h1{font-size:15px;margin:0 0 4px;color:var(--text)}
.sub{color:var(--muted);font-size:12px;margin:0 0 18px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 0;
 border-bottom:1px solid #2F343E}
.row:last-of-type{border-bottom:none}
label{font-size:13px}
input[type=number]{width:70px;background:#14171C;border:1px solid var(--border);color:var(--text);
 border-radius:6px;padding:4px 8px}
input[type=color]{width:40px;height:26px;border:none;background:none;padding:0}
select{background:#14171C;border:1px solid var(--border);color:var(--text);border-radius:6px;padding:4px 8px}
input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent)}
#apply{margin-top:16px;width:100%;padding:10px;border-radius:8px;border:none;font-size:13px;font-weight:600;
 background:#3A3F4B;color:#6B7280;cursor:not-allowed}
#apply.dirty{background:var(--dirty);color:#1E2228;cursor:pointer}
#status{margin-top:12px;font-size:12px;color:var(--muted);white-space:pre-wrap}
</style></head><body>
<div class="card">
  <h1>Rhema Options - port test</h1>
  <p class="sub">5 of ~40 real settings, wired to genuine tk.Variable objects behind an HTML form.</p>
  <div class="row"><label>Max caption lines</label><input type="number" id="lines" min="4" max="10"></div>
  <div class="row"><label>Background color</label><input type="color" id="bg"></div>
  <div class="row"><label>Show live interim text</label><input type="checkbox" id="interim"></div>
  <div class="row"><label>STT device</label>
    <select id="device"><option value="cpu">CPU</option><option value="cuda">CUDA</option></select></div>
  <div class="row"><label>Clear display on inactivity</label><input type="checkbox" id="clear"></div>
  <button id="apply" disabled>Apply</button>
  <div id="status">loading current settings...</div>
</div>
<script>
const applyBtn = document.getElementById('apply')
const statusEl = document.getElementById('status')
const fields = {
  lines: {el: document.getElementById('lines'), varName: 'lines_var', kind: 'int'},
  bg: {el: document.getElementById('bg'), varName: 'bg_color_var', kind: 'str'},
  interim: {el: document.getElementById('interim'), varName: 'show_interim_text_var', kind: 'bool'},
  device: {el: document.getElementById('device'), varName: 'stt_device_var', kind: 'str'},
  clear: {el: document.getElementById('clear'), varName: 'clear_display_on_inactivity_var', kind: 'bool'},
}

function fieldValue(f){
  if (f.el.type === 'checkbox') return f.el.checked
  if (f.kind === 'int') return parseInt(f.el.value, 10)
  return f.el.value
}

function setDirty(isDirty){
  applyBtn.disabled = !isDirty
  applyBtn.classList.toggle('dirty', isDirty)
}

async function onFieldChange(key){
  const f = fields[key]
  const result = await pywebview.api.set_var(f.varName, fieldValue(f))
  setDirty(result.dirty)
}

for (const key in fields){
  fields[key].el.addEventListener('input', () => onFieldChange(key))
}

applyBtn.addEventListener('click', async () => {
  const result = await pywebview.api.apply()
  if (!result.ok){
    statusEl.textContent = 'Apply failed: ' + result.error
    return
  }
  setDirty(result.dirty)
  const lines = [
    'Applied and saved to ' + result.settings_path,
    'max_lines=' + result.max_lines + '  bg_color=' + result.bg_color,
    'show_interim_text=' + result.show_interim_text + '  stt_device=' + result.stt_device,
    'clear_display_on_inactivity=' + result.clear_display_on_inactivity,
  ]
  if (result.restart_requested_changed){
    lines.push('STT device changed -> _realtime_stt_restart_requested is now ' + result.restart_requested
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
  document.getElementById('lines').value = v.max_lines
  document.getElementById('bg').value = v.bg_color
  document.getElementById('interim').checked = v.show_interim_text
  document.getElementById('device').value = v.stt_device
  document.getElementById('clear').checked = v.clear_display_on_inactivity
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


def main():
    var_root = tk.Tk()
    var_root.withdraw()

    api = Api()
    window = webview.create_window(
        "Rhema Options - port test",
        html=HTML,
        js_api=api,
        width=560,
        height=560,
        background_color="#1E2228",
    )

    def on_loaded():
        canvas = WebCanvas(window, width=1, height=1)
        measurer = WebMeasurer(window, FONT_FAMILY, 50, PIXELS_PER_INCH)
        api.engine = OptionsEngine(lambda p: None, canvas, measurer, PIXELS_PER_INCH, var_root)

    def on_closed():
        try:
            var_root.destroy()
        except Exception:
            pass

    window.events.loaded += on_loaded
    window.events.closed += on_closed
    webview.start()


if __name__ == "__main__":
    main()
