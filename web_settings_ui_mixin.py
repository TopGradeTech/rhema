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

"""Phase 5 of the pywebview port: the Controller window - the real
Preview/Status/Latency/audio-level-meter/Pause/Toggle-Fullscreen block that
open_settings() builds in settings_ui_mixin.py, plus the File/About menu
options from _build_menu_bar(). Deliberately NOT the Options dialog
(Phase 6) and NOT the startup loading overlay (Phase 7) - those stay
real-but-unbuilt for this phase, same as Phase 3/4 left the Controller
itself unbuilt.

The menu itself is NOT a native WinForms MenuStrip (pywebview's
set_window_menu) - it started as one, but that path needed a
ProfessionalColorTable subclass, a Paint-event overpaint for a WinForms-
reserved gutter that ignores ColorTable overrides regardless, and still
never looked quite native. Replaced with a plain HTML/CSS/JS dropdown menu
bar (#menuBar in CONTROLLER_HTML) after a gear-icon experiment for Options
alone showed a page element themes with three ordinary CSS rules instead
of fighting WinForms - see _ControllerApi's own menu-action methods for
where each item now calls the same real backend method the old
Menu/MenuAction list called.

Mixed in ahead of SettingsUIMixin AND DisplayMixin in WebTranslationApp's
MRO (see main_webview.py) so these override just the handful of methods
that touch real Controller widgets - update_status/
_set_chunk_latency_label_text/_render_audio_level_meter/toggle_pause all
originate in display_mixin.py, not settings_ui_mixin.py, but they're
Controller-widget code by what they DO, not by which file they happen to
live in. Everything else either mixin defines (Options-dialog building,
NLLB workers, dirty-tracking, the caption-rendering path) is untouched and
simply not exercised by the Controller window itself yet.

toggle_fullscreen was originally a Phase-5-scoped stand-in that called
pywebview's window.toggle_fullscreen() directly, proving the CROSS-WINDOW
PLUMBING (a Controller button reaching into the Output window's own
state) worked before real per-monitor placement existed to call into. As
of Phase 8 it's a genuine port of main.py's own TranslationApp.
toggle_fullscreen body (flip is_fullscreen, call enter_fullscreen()/
exit_fullscreen()) - those two now come from WebMonitorMixin
(web_monitor_mixin.py), which does the real per-monitor placement math
against pywebview windows instead of Tk geometry.

The Preview/Output Snapshot feature is wired up end-to-end (JS polling, an
img tag, a js_api call) - see _capture_output_snapshot_data_uri's own
comment for the real occlusion-independent capture technique (Windows
Graphics Capture, via the windows-capture package) and the two unsafe
techniques (bbox/BitBlt, PrintWindow) it replaces.
"""

import json
import tkinter as tk
import webbrowser

from languages import whisper_language_options, nllb_language_options
from settings_logic_mixin import SettingsLogicMixin
from settings_ui_mixin import DONATE_URL
from webview_bridge import PywebviewGeometryAdapter, TkVariableInterpreter

# The real, current option tables from settings_ui_mixin.py's
# _build_display_controls/_build_transcription_section/
# _build_translation_section (Advanced/Transcription/Translation sections
# respectively) - copied verbatim from that file rather than re-derived,
# so this can never quietly drift from what those sections actually offer.
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

CONTROLLER_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema Controller</title><style>
:root{__THEME_CSS__}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
  font:14px/1.4 "Segoe UI",system-ui,sans-serif;overflow:hidden}
#wrap{display:flex;flex-direction:column;height:100vh;box-sizing:border-box;padding:14px;gap:12px}
#menuBar{display:flex;gap:2px;margin:-4px 0 -6px -6px}
.menuItem{position:relative;padding:5px 10px;border-radius:5px;font-size:13px;color:var(--muted);
  cursor:pointer;user-select:none}
.menuItem:hover,.menuItem.open{background:var(--overlay);color:var(--text)}
.menuDropdown{display:none;position:absolute;top:100%;left:0;margin-top:4px;background:var(--overlay);
  border:1px solid var(--border);border-radius:6px;min-width:190px;padding:4px;
  box-shadow:0 8px 20px rgba(0,0,0,.45);z-index:20}
.menuItem.open .menuDropdown{display:block}
.menuOption{padding:7px 10px;border-radius:4px;font-size:13px;color:var(--text);cursor:pointer;
  white-space:nowrap}
.menuOption:hover{background:var(--accent);color:#fff}
.menuSeparator{height:1px;background:var(--border);margin:4px 2px}
#previewBox{flex:1;min-height:0;border:1px solid var(--border);border-radius:6px;
  background:#000;display:flex;align-items:center;justify-content:center;overflow:hidden}
#preview{max-width:100%;max-height:100%;display:block}
#previewPlaceholder{color:var(--muted);font-size:13px}
#statusSection{border:1px solid var(--border);border-radius:6px;padding:10px 12px;flex:0 0 auto}
#statusSection .label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
#status{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#latency{color:var(--muted);margin-top:4px}
#meterRow{display:flex;align-items:center;gap:8px;margin-top:8px}
#meterTrack{flex:1;height:12px;background:#1A1A1A;border:1px solid #3A3A3A;border-radius:3px;overflow:hidden}
#meterFill{height:100%;width:0;background:var(--accent);transition:width 70ms linear}
#buttonRow{display:flex;gap:10px;margin-top:10px}
#buttonRow button{padding:8px 16px;border:none;border-radius:5px;background:var(--accent);color:#fff;
  font:inherit;font-weight:600;cursor:pointer}
#buttonRow button:hover,#buttonRow button:active{background:var(--accent-hover)}
#startupOverlay{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;
  justify-content:center;flex-direction:column;z-index:1000}
#startupOverlay.hidden{display:none}
.spinner{width:40px;height:40px;border-radius:50%;border:4px solid var(--border);
  border-top-color:var(--accent);animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
#startupText{margin-top:14px;font-size:14px;font-weight:600}
</style></head><body>
<div id="startupOverlay">
  <div class="spinner"></div>
  <div id="startupText">Loading...</div>
</div>
<div id="wrap">
  <div id="menuBar">
    <div class="menuItem">File
      <div class="menuDropdown">
        <div class="menuOption" onclick="pywebview.api.hardware_autodetect_clicked()">Hardware Autodetect</div>
        <div class="menuOption" onclick="pywebview.api.open_options_clicked()">Options</div>
      </div>
    </div>
    <div class="menuItem">About
      <div class="menuDropdown">
        <div class="menuOption" onclick="pywebview.api.about_clicked()">About Rhema</div>
        <div class="menuSeparator"></div>
        <div class="menuOption" onclick="pywebview.api.check_updates_clicked()">Check for Updates</div>
        <div class="menuOption" onclick="pywebview.api.donate_clicked()">Donate</div>
        <div class="menuOption" onclick="pywebview.api.feature_request_clicked()">Feature Request</div>
      </div>
    </div>
  </div>
  <div id="previewBox"><span id="previewPlaceholder">Capturing snapshot...</span><img id="preview" style="display:none"></div>
  <div id="statusSection">
    <div class="label">Status</div>
    <div id="status">Status: starting...</div>
    <div id="latency">Latency: --</div>
    <div id="meterRow">
      <div id="meterTrack"><div id="meterFill"></div></div>
    </div>
    <div id="buttonRow">
      <button id="pauseBtn" onclick="pywebview.api.pause_clicked()">Pause</button>
      <button id="fullscreenBtn" onclick="pywebview.api.toggle_fullscreen_clicked()">Toggle Fullscreen</button>
    </div>
  </div>
</div>
<script>
function setStatus(text){ document.getElementById('status').textContent = text }
function setLatency(text){ document.getElementById('latency').textContent = text }
function setMeter(pct){ document.getElementById('meterFill').style.width = Math.max(0, Math.min(100, pct)) + '%' }
function setPauseButtonText(text){ document.getElementById('pauseBtn').textContent = text }
function hideStartupOverlay(){ document.getElementById('startupOverlay').classList.add('hidden') }
function applyThemeVars(vars){
  const root = document.documentElement
  for (const key in vars){
    if (key === 'colorScheme') { root.style.colorScheme = vars[key]; continue }
    root.style.setProperty(key, vars[key])
  }
}
function setPreview(dataUri){
  const img = document.getElementById('preview')
  const ph = document.getElementById('previewPlaceholder')
  img.src = dataUri
  img.style.display = 'block'
  ph.style.display = 'none'
}

function pollPreview(){
  pywebview.api.get_preview_data_uri().then(function(uri){
    if (uri) setPreview(uri)
  }).catch(function(){})
}
setInterval(pollPreview, 15000)

// File/About menu bar - a plain HTML/CSS dropdown replacing the old
// native WinForms MenuStrip (see this file's own module docstring for
// why). One item open at a time; clicking an option (or anywhere outside
// an open menu) closes it - the option's own onclick fires first, then
// this same bubbled click closes the menu, which is normal menu UX and
// needs no extra code to get right.
document.querySelectorAll('.menuItem').forEach(function(item){
  item.addEventListener('click', function(e){
    e.stopPropagation()
    var wasOpen = item.classList.contains('open')
    document.querySelectorAll('.menuItem.open').forEach(function(m){ m.classList.remove('open') })
    if (!wasOpen) item.classList.add('open')
  })
})
document.addEventListener('click', function(){
  document.querySelectorAll('.menuItem.open').forEach(function(m){ m.classList.remove('open') })
})
setTimeout(pollPreview, 300)

// Same hotkey listener as the Output window's own HTML (main_webview.py) -
// see that copy's comment for why this is deliberately duplicated rather
// than attached once.
document.addEventListener('keydown', (e) => {
  const key = e.key.toLowerCase()
  if (key === 'f11' || (e.ctrlKey && e.altKey && key === 'f') || key === 'escape') {
    e.preventDefault()
    pywebview.api.toggle_fullscreen_clicked()
  } else if (e.ctrlKey && key === 's') {
    e.preventDefault()
    pywebview.api.open_settings_clicked()
  } else if (e.ctrlKey && key === 'q') {
    e.preventDefault()
    pywebview.api.close_app_clicked()
  }
})
</script></body></html>
"""


NLLB_PROGRESS_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Local NLLB</title>
<style>
:root{color-scheme:dark}
html,body{margin:0;background:#1E2228;color:#E5E7EB;
  font:14px/1.4 "Segoe UI",system-ui,sans-serif;overflow:hidden}
#wrap{padding:20px 24px;box-sizing:border-box}
h3{margin:0 0 12px;font-size:13px;font-weight:700;color:#F3F4F6}
#track{height:10px;background:#14171C;border:1px solid #3A3A3A;border-radius:4px;overflow:hidden;
  position:relative}
#fill{position:absolute;top:0;height:100%;width:35%;background:#5B8FF7;border-radius:4px;
  animation:bounce 1.1s ease-in-out infinite}
@keyframes bounce{0%{left:-35%}100%{left:100%}}
#statusText{margin-top:10px;font-size:12px;color:#9CA3AF;white-space:pre-wrap}
</style></head><body>
<div id="wrap">
  <h3>Preparing Local NLLB...</h3>
  <div id="track"><div id="fill"></div></div>
  <div id="statusText"></div>
</div>
<script>
function setNllbStatus(text){ document.getElementById('statusText').textContent = text }
window.addEventListener('pywebviewready', () => setNllbStatus(__INITIAL__))
</script></body></html>
"""


class _ControllerApi:
    """Exposed to the Controller window's JS as `pywebview.api.*` - kept
    small and separate from the real app instance (rather than exposing
    `self` directly) so the Controller's HTML can only reach the handful
    of methods it actually needs, not every public method on
    WebTranslationApp."""

    def __init__(self, app):
        self._app = app

    def pause_clicked(self):
        self._app.toggle_pause()

    def toggle_fullscreen_clicked(self):
        self._app.toggle_fullscreen()

    def get_preview_data_uri(self):
        return self._app._capture_output_snapshot_data_uri()

    def open_settings_clicked(self):
        self._app.focus_controller_window()

    def open_options_clicked(self):
        self._app._show_options_dialog()

    def hardware_autodetect_clicked(self):
        self._app._run_hardware_autodetect_menu_action()

    def about_clicked(self):
        self._app._show_about_popup()

    def check_updates_clicked(self):
        self._app.check_for_updates(manual=True)

    def donate_clicked(self):
        self._app._show_donate_popup()

    def feature_request_clicked(self):
        self._app._open_feature_request_page()

    def close_app_clicked(self):
        self._app.on_closing()


class _OptionsApi:
    """Exposed to the Options window's JS as `pywebview.api.*` - same
    kept-small, delegate-to-the-real-app pattern as _ControllerApi above."""

    def __init__(self, app):
        self._app = app

    def set_var(self, name, value):
        return self._app.options_set_var(name, value)

    def set_text(self, name, value):
        return self._app.options_set_text(name, value)

    def apply(self):
        return self._app.options_apply()

    def options(self):
        return self._app.options_list()

    def current_values(self):
        return self._app.options_current_values()

    def select_audio_device(self, label):
        return self._app.options_select_audio_device(label)

    def select_video_device(self, label):
        return self._app.options_select_video_device(label)

    def refresh_devices(self):
        self._app._refresh_audio_devices()
        # Matches Tk's own startup gating (settings_ui_mixin.py only scans
        # the camera at all if video_feed_enabled is True) - this is
        # called unconditionally from OPTIONS_HTML's pywebviewready
        # handler on EVERY app launch (Options is built hidden-but-eagerly
        # at startup), so without this check the camera got probed even
        # when the video feed was off, wasting the scan's multi-second
        # cost for a control the user hadn't even revealed yet.
        if self._app.video_feed_enabled:
            self._app._refresh_video_devices()
        return {"ok": True}

    def get_video_status(self):
        # self.video_status is updated directly by the capture worker
        # (video_capture_mixin.py) regardless of any UI layer - the real
        # Tk app just happens to also push it into a tk.StringVar
        # (video_status_var) for a live label. This port has no live-var-
        # to-page push mechanism, so the Options page polls this instead,
        # same pattern as the Controller's Preview polling.
        return getattr(self._app, "video_status", "")

    def browse_cuda_directory(self):
        return self._app._browse_cuda_directory()

    def check_nllb_download(self):
        return self._app._check_nllb_download_from_options()

    def test_nllb(self):
        return self._app._test_nllb_from_options()

    def get_nllb_status(self):
        return self._app._nllb_status_for_options()

    def get_translation_summary(self):
        return self._app._translation_summary_lines()

    def select_output_monitor(self, label):
        return self._app._select_output_monitor(label)

    def select_settings_monitor(self, label):
        return self._app._select_settings_monitor(label)

    def show_monitor_ids(self):
        self._app.show_monitor_ids()
        return {"ok": True}

    # Same hotkey targets _ControllerApi/_OutputApi expose (Phase 12) -
    # previously missing here entirely, which is why F11/Ctrl-Alt-F/
    # Escape/Ctrl-S/Ctrl-Q silently did nothing while the Options window
    # had focus (see OPTIONS_HTML's own keydown listener below, added for
    # the same reason CONTROLLER_HTML/main_webview.py's OUTPUT_HTML each
    # carry their own copy - pywebview has no process-wide bind_all
    # equivalent to attach this to just once).
    def toggle_fullscreen_clicked(self):
        self._app.toggle_fullscreen()

    def open_settings_clicked(self):
        self._app.focus_controller_window()

    def close_app_clicked(self):
        self._app.on_closing()


OPTIONS_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema Options</title>
<style>
:root{__THEME_CSS__}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
 font:14px/1.5 "Segoe UI","Segoe UI Variable Text",system-ui,sans-serif;padding:24px 24px 110px;overflow-y:auto}
.card{max-width:640px;margin:0 auto 24px;background:var(--card);border:1px solid var(--border);
 border-radius:12px;padding:20px}
#applyBar{position:fixed;left:0;right:0;bottom:0;background:var(--bg);
 border-top:1px solid var(--border);padding:14px 24px;box-shadow:0 -4px 16px rgba(0,0,0,.2)}
#applyBarInner{max-width:640px;margin:0 auto}
h1{font-size:15px;margin:0 0 4px;color:var(--text)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--accent);font-weight:700;
 margin:28px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--border)}
h2:first-of-type{margin-top:2px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 0;
 border-bottom:1px solid var(--border)}
.row:last-of-type{border-bottom:none}
.row[hidden]{display:none}
label{font-size:13px}
input[type=number]{width:80px;background:var(--input-bg);border:1px solid var(--border);color:var(--text);
 border-radius:6px;padding:4px 8px}
input[type=color]{width:40px;height:26px;border:none;background:none;padding:0}
select{background:var(--input-bg);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:4px 8px;
 max-width:280px}
input[type=text]{background:var(--input-bg);border:1px solid var(--border);color:var(--text);border-radius:6px;
 padding:4px 8px}
textarea{width:100%;background:var(--input-bg);border:1px solid var(--border);color:var(--text);border-radius:6px;
 padding:6px 8px;font:12px/1.4 monospace;resize:vertical}
input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent)}
.smallBtn{padding:5px 12px;border-radius:6px;border:none;font:inherit;font-size:12px;font-weight:600;
 background:var(--accent);color:#fff;cursor:pointer;white-space:nowrap}
.smallBtn:hover{background:var(--accent-hover)}
#apply{width:100%;padding:10px;border-radius:8px;border:none;font-size:13px;font-weight:600;
 background:var(--border);color:var(--muted);cursor:not-allowed}
#apply.dirty{background:var(--dirty);color:#1E2228;cursor:pointer}
#status{margin-top:12px;font-size:12px;color:var(--muted);white-space:pre-wrap}
.help{display:inline-block;margin-left:6px;width:15px;height:15px;border-radius:50%;
 background:var(--border);color:var(--text);text-align:center;font-size:10px;font-weight:700;
 line-height:15px;cursor:help;user-select:none}
#tooltip{position:fixed;background:#111111;color:#fff;border:1px solid #333;
 padding:4px 6px;font-size:12px;max-width:320px;line-height:1.3;z-index:2000;
 pointer-events:none;display:none}
</style></head><body>
<div class="card">
  <h1>Rhema Options</h1>

  <h2>Display</h2>
  <div class="row"><label>Theme<span class="help" data-tip="Switches the Controller and Options windows between light and dark.">?</span></label>
    <select id="theme"><option value="Light">Light</option><option value="Dark">Dark</option></select></div>
  <div class="row" id="linesRow"><label>Max caption lines (no video feed)<span class="help" data-tip="Maximum number of translated lines kept on screen when the video feed below is off.">?</span></label><input type="number" id="lines" min="4" max="10"></div>
  <div class="row"><label>Background color<span class="help" data-tip="Background color for the output overlay and preview. Also tints the caption bar behind the video overlay, if enabled.">?</span></label>
    <span style="display:flex;gap:6px;align-items:center"><input type="color" id="bgSwatch"><input type="text" id="bg" maxlength="32" placeholder="#000000" style="width:90px"></span></div>
  <div class="row"><label>Text color<span class="help" data-tip="Text color for the output overlay and preview.">?</span></label>
    <span style="display:flex;gap:6px;align-items:center"><input type="color" id="textColorSwatch"><input type="text" id="textColor" maxlength="32" placeholder="#ffffff" style="width:90px"></span></div>
  <div class="row"><label>Always keep on top of other apps<span class="help" data-tip="Keeps the fullscreen output window on top of other windows and attempts to focus it. Leave this off to let other apps appear above the output window.">?</span></label><input type="checkbox" id="lockFocus"></div>
  <div class="row"><label>Clear display on inactivity</label><input type="checkbox" id="clear"></div>
  <div class="row" id="clearSecondsRow"><label>&nbsp;&nbsp;...after N seconds</label><input type="number" id="clearSeconds" min="__CLEAR_SECONDS_MIN__" max="__CLEAR_SECONDS_MAX__"></div>
  <div class="row"><label>Show video feed behind captions<span class="help" data-tip="Shows the OBS Virtual Camera behind captions. Start OBS's Virtual Camera first.">?</span></label><input type="checkbox" id="videoEnabled"></div>
  <div class="row" id="videoDeviceRow"><label>Camera device<span class="help" data-tip="Camera index for the OBS Virtual Camera. Click Refresh after starting OBS's Virtual Camera if it isn't listed yet.">?</span></label>
    <span style="display:flex;gap:8px;align-items:center"><select id="videoDevice"></select><button type="button" id="videoRefresh" class="smallBtn">Refresh</button></span></div>
  <div class="row" id="videoStatusRow"><label>&nbsp;&nbsp;Camera status</label><span id="videoStatus" style="color:#9CA3AF;font-size:12px">--</span></div>
  <div class="row" id="videoLinesRow"><label>Max caption lines (with video feed)<span class="help" data-tip="Maximum number of translated lines kept on screen when the video feed is on. Kept lower than the no-video default to leave more of the video visible.">?</span></label><input type="number" id="videoLines" min="1" max="3"></div>
  <div class="row" id="videoAlphaRow"><label>Caption bar opacity (%)<span class="help" data-tip="How solid the bar behind the caption lines looks, using the Background Color above. 0% is fully see-through, 100% is a solid bar.">?</span></label><input type="number" id="videoAlpha" min="0" max="100"></div>
  <div class="row"><label>Output monitor<span class="help" data-tip="Monitor where the translation output appears.">?</span></label><select id="outputMonitor"></select></div>
  <div class="row"><label>Controller monitor<span class="help" data-tip="Monitor where this Controller/Options window opens.">?</span></label><select id="settingsMonitor"></select></div>
  <div class="row"><span></span><button type="button" id="showMonitorIds" class="smallBtn">Show Monitor Numbers</button></div>

  <h2>Audio</h2>
  <div class="row"><label>Microphone<span class="help" data-tip="Input device used for speech capture.">?</span></label><select id="audioDevice"></select></div>

  <h2>Transcription</h2>
  <div class="row"><label>Show live interim text</label><input type="checkbox" id="interim"></div>
  <div class="row"><label>STT device<span class="help" data-tip="Auto uses CUDA when available, otherwise CPU.">?</span></label>
    <select id="device"><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="auto">Auto</option></select></div>
  <div class="row"><label>Source language<span class="help" data-tip="The language your speech is transcribed as. Type to search all supported languages.">?</span></label>
    <input type="text" id="sourceLang" list="sourceLangOptions" autocomplete="off" style="width:220px"><datalist id="sourceLangOptions"></datalist></div>
  <div class="row"><label>Final model<span class="help" data-tip="Accurate faster-whisper model used after each utterance ends. Larger models are more accurate but need more VRAM and take longer per utterance.">?</span></label><select id="finalModel"></select></div>
  <div class="row"><label>Realtime model<span class="help" data-tip="Fast model used internally every ~0.2s to drive dynamic silence detection. Not shown on screen - kept small so it doesn't compete with the final model for GPU time.">?</span></label><select id="realtimeModel"></select></div>
  <div class="row"><label>Voice sensitivity<span class="help" data-tip="How easily speech is detected. Lower catches softer/quieter speech; higher ignores background noise better.">?</span></label><input type="number" id="silero" min="0.1" max="0.9" step="0.05"></div>

  <h2>Translation (Local NLLB)</h2>
  <div class="row"><label>Enable translation</label><input type="checkbox" id="enableTranslation"></div>
  <div id="translationSummary" style="color:#9CA3AF;font-size:12px;margin:2px 0 10px;white-space:pre-line"></div>
  <div class="row"><label>Model name<span class="help" data-tip="Hugging Face model id for local text translation. Larger models translate more accurately but need more VRAM/RAM and disk space, and run slower.">?</span></label><select id="nllbModel"></select></div>
  <div class="row"><label>Device<span class="help" data-tip="Auto uses CUDA when available, otherwise CPU.">?</span></label>
    <select id="nllbDevice"><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="auto">Auto</option></select></div>
  <div class="row"><label>Target language<span class="help" data-tip="Language the translated transcript is produced in. Type to search all 200 languages.">?</span></label>
    <input type="text" id="nllbTargetLang" list="nllbTargetLangOptions" autocomplete="off" style="width:220px"><datalist id="nllbTargetLangOptions"></datalist></div>
  <div class="row"><label>Max chars per chunk<span class="help" data-tip="Long transcripts are split by paragraph, sentence, or length before translation.">?</span></label><input type="number" id="nllbMaxChars" min="250" max="20000" step="250"></div>
  <div class="row"><span></span><span style="display:flex;gap:8px">
    <button type="button" id="nllbDownload" class="smallBtn">Download / Check for Updates</button>
    <button type="button" id="nllbTest" class="smallBtn">Test Local NLLB</button>
  </span></div>
  <div id="nllbStatus" style="color:#9CA3AF;font-size:12px;margin:4px 0 8px">NLLB status: --</div>

  <h2>Advanced</h2>
  <div class="row"><span></span><button type="button" id="advancedToggle" class="smallBtn">Show Advanced Settings</button></div>
  <div id="advancedContent" hidden>
  <div class="row"><label>Logging mode<span class="help" data-tip="Normal keeps status/error and finalized output logs. Debug adds pipeline traces. Evaluation adds raw transcribed/translated comparison logs. Full enables all logs.">?</span></label><select id="loggingMode"></select></div>
  <div class="row"><label>Start app when Windows starts</label><input type="checkbox" id="startWithWindows"></div>
  <div class="row"><label>CUDA directory<span class="help" data-tip="Optional Windows path used to find CUDA Toolkit 12.x and cuDNN 9.x DLLs for local faster-whisper GPU mode. Select the CUDA toolkit folder or its bin folder.">?</span></label>
    <span style="display:flex;gap:8px;align-items:center"><input type="text" id="cudaDirectory" style="width:220px"><button type="button" id="cudaBrowse" class="smallBtn">Browse</button><button type="button" id="cudaClear" class="smallBtn">Clear</button></span></div>
  <div class="row"><label>Bad words filter<span class="help" data-tip="Words to omit from the output.">?</span></label><button type="button" id="badWordsToggle" class="smallBtn">Show list</button></div>
  <div id="badWordsContainer" hidden>
    <div class="row"><label>&nbsp;&nbsp;English (comma-separated)</label></div>
    <textarea id="badWordsEn" rows="2"></textarea>
    <div class="row"><label>&nbsp;&nbsp;Spanish (comma-separated)</label></div>
    <textarea id="badWordsEs" rows="2"></textarea>
  </div>
  <div class="row"><label>Custom vocabulary<span class="help" data-tip="Words or phrases to bias recognition and preserve capitalization.">?</span></label><button type="button" id="vocabToggle" class="smallBtn">Show list</button></div>
  <div id="vocabContainer" hidden>
    <div class="row"><label>&nbsp;&nbsp;English (comma-separated)</label></div>
    <textarea id="vocabEn" rows="2"></textarea>
    <div class="row"><label>&nbsp;&nbsp;Spanish (comma-separated)</label></div>
    <textarea id="vocabEs" rows="2"></textarea>
  </div>
  </div>

</div>
<div id="applyBar">
  <div id="applyBarInner">
    <button id="apply" disabled>Apply</button>
    <div id="status">loading current settings...</div>
  </div>
</div>
<div id="tooltip"></div>
<script>
// Tooltip help icons - inline JS, not a shared Python module (tooltip.py
// stays as-is for the Tk app's own _create_help_icon; this is the "small
// JS-snippet helper" the port plan called for instead of a shared
// abstraction). Proved in experiments/web_tooltip.py: 400ms hover delay
// matching tooltip.py's Tooltip class default, plus one deliberate
// improvement the original never had - viewport-edge clamping, since a
// position:fixed div (unlike Tk's own tipwindow) is trivial to keep
// on-screen.
(function () {
  const DELAY_MS = 400
  const tip = document.getElementById('tooltip')
  let showTimer = null

  function clampedPosition(x, y, tipEl) {
    const vw = window.innerWidth, vh = window.innerHeight
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight
    const cx = Math.min(x, vw - w - 4)
    const cy = Math.min(y, vh - h - 4)
    return {x: Math.max(0, cx), y: Math.max(0, cy)}
  }

  document.querySelectorAll('.help').forEach(el => {
    el.addEventListener('mouseenter', () => {
      clearTimeout(showTimer)
      showTimer = setTimeout(() => {
        const rect = el.getBoundingClientRect()
        tip.textContent = el.dataset.tip
        tip.style.display = 'block'
        const pos = clampedPosition(rect.left + 20, rect.bottom + 6, tip)
        tip.style.left = pos.x + 'px'
        tip.style.top = pos.y + 'px'
      }, DELAY_MS)
    })
    el.addEventListener('mouseleave', () => {
      clearTimeout(showTimer)
      tip.style.display = 'none'
    })
    el.addEventListener('click', () => {
      clearTimeout(showTimer)
      tip.style.display = 'none'
    })
  })
})()
</script>
<script>
const applyBtn = document.getElementById('apply')
const statusEl = document.getElementById('status')

const fields = {
  theme: {varName: 'theme_var', kind: 'str'},
  lines: {varName: 'lines_var', kind: 'int'},
  bg: {varName: 'bg_color_var', kind: 'str'},
  textColor: {varName: 'text_color_var', kind: 'str'},
  lockFocus: {varName: 'lock_output_focus_var', kind: 'bool'},
  clear: {varName: 'clear_display_on_inactivity_var', kind: 'bool'},
  clearSeconds: {varName: 'clear_display_inactivity_seconds_var', kind: 'int'},
  videoEnabled: {varName: 'video_feed_enabled_var', kind: 'bool'},
  videoLines: {varName: 'video_lines_var', kind: 'int'},
  videoAlpha: {varName: 'video_caption_alpha_var', kind: 'float'},
  interim: {varName: 'show_interim_text_var', kind: 'bool'},
  device: {varName: 'stt_device_var', kind: 'str'},
  sourceLang: {varName: 'stt_source_lang_var', kind: 'str'},
  finalModel: {varName: 'realtime_stt_final_model_var', kind: 'str'},
  realtimeModel: {varName: 'realtime_stt_realtime_model_var', kind: 'str'},
  silero: {varName: 'realtime_stt_silero_var', kind: 'float'},
  enableTranslation: {varName: 'enable_translation_var', kind: 'bool'},
  nllbModel: {varName: 'local_nllb_model_name_var', kind: 'str'},
  nllbDevice: {varName: 'local_nllb_device_var', kind: 'str'},
  nllbTargetLang: {varName: 'local_nllb_target_lang_var', kind: 'str'},
  nllbMaxChars: {varName: 'local_nllb_max_chars_var', kind: 'int'},
  loggingMode: {varName: 'logging_mode_var', kind: 'str'},
  startWithWindows: {varName: 'start_with_windows_var', kind: 'bool'},
  cudaDirectory: {varName: 'cuda_directory_var', kind: 'str'},
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

document.getElementById('audioDevice').addEventListener('change', async (e) => {
  await pywebview.api.select_audio_device(e.target.value)
})
document.getElementById('videoDevice').addEventListener('change', async (e) => {
  await pywebview.api.select_video_device(e.target.value)
})
document.getElementById('videoRefresh').addEventListener('click', async () => {
  await pywebview.api.refresh_devices()
})
document.getElementById('cudaBrowse').addEventListener('click', async () => {
  const result = await pywebview.api.browse_cuda_directory()
  if (result.ok){
    document.getElementById('cudaDirectory').value = result.path
    setDirty(result.dirty)
  }
})
document.getElementById('cudaClear').addEventListener('click', () => {
  elFor('cudaDirectory').value = ''
  onFieldChange('cudaDirectory')
})
document.getElementById('nllbDownload').addEventListener('click', () => {
  pywebview.api.check_nllb_download()
})
document.getElementById('nllbTest').addEventListener('click', () => {
  pywebview.api.test_nllb()
})
document.getElementById('outputMonitor').addEventListener('change', async (e) => {
  await pywebview.api.select_output_monitor(e.target.value)
})
document.getElementById('settingsMonitor').addEventListener('change', async (e) => {
  await pywebview.api.select_settings_monitor(e.target.value)
})
document.getElementById('showMonitorIds').addEventListener('click', () => {
  pywebview.api.show_monitor_ids()
})

function makeListToggle(buttonId, containerId){
  const button = document.getElementById(buttonId)
  const container = document.getElementById(containerId)
  button.addEventListener('click', () => {
    container.hidden = !container.hidden
    button.textContent = container.hidden ? 'Show list' : 'Hide list'
  })
}
makeListToggle('badWordsToggle', 'badWordsContainer')
makeListToggle('vocabToggle', 'vocabContainer')
// Same real collapse/relabel behavior as the two list toggles above, just
// wrapping the whole Advanced section instead of one field - matches
// Tk's "Show Advanced Settings" button (settings_ui_mixin.py), which
// keeps Advanced collapsed by default rather than always fully expanded.
document.getElementById('advancedToggle').addEventListener('click', () => {
  const content = document.getElementById('advancedContent')
  const button = document.getElementById('advancedToggle')
  content.hidden = !content.hidden
  button.textContent = content.hidden ? 'Show Advanced Settings' : 'Hide Advanced Settings'
})

function syncClearSecondsVisibility(){
  document.getElementById('clearSecondsRow').hidden = !document.getElementById('clear').checked
}
document.getElementById('clear').addEventListener('change', syncClearSecondsVisibility)

// Matches Tk's on_video_feed_toggle (settings_ui_mixin.py): exactly one
// "Max caption lines" field and one camera-controls block is ever visible
// at a time, based on whether the video feed is enabled - previously all
// of these rows stayed visible/editable regardless, giving no visual cue
// which "Max caption lines" value was actually in effect.
function syncVideoRowsVisibility(){
  const on = document.getElementById('videoEnabled').checked
  document.getElementById('linesRow').hidden = on
  document.getElementById('videoDeviceRow').hidden = !on
  document.getElementById('videoStatusRow').hidden = !on
  document.getElementById('videoLinesRow').hidden = !on
  document.getElementById('videoAlphaRow').hidden = !on
}
document.getElementById('videoEnabled').addEventListener('change', syncVideoRowsVisibility)

// Matches Tk's sync_interim_with_translation (settings_ui_mixin.py):
// translating a still-changing partial produces reordered, inconsistent
// output next to the eventual finalized translation, so translation wins
// the conflict - live interim text is force-unchecked and disabled
// whenever translation is on. Previously this only happened server-side
// at Apply time here, so a user could leave both checked, see "Applied
// and saved", and still have the checkbox showing checked even though
// the feature was now off.
function syncInterimWithTranslation(){
  const enabled = document.getElementById('enableTranslation').checked
  const interim = document.getElementById('interim')
  interim.disabled = enabled
  if (enabled && interim.checked){
    interim.checked = false
    onFieldChange('interim')
  }
}
document.getElementById('enableTranslation').addEventListener('change', syncInterimWithTranslation)

// Lets the user type an arbitrary color string (a named color, 3-digit
// shorthand, or anything else Tk's own free-text Entry accepts) the same
// way Tk's bg/text color Entry does - <input type=color> only accepts a
// strict #rrggbb value, silently ignoring (and rendering black for)
// anything else, and previously had no accompanying text field at all.
// The swatch stays a convenience picker: picking a color there writes the
// resolved hex into the real (tracked) text field, which is what actually
// gets sent to the backend.
function syncSwatchFromHex(hexId, swatchId){
  const val = elFor(hexId).value
  if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(val)){
    elFor(swatchId).value = val.length === 4
      ? '#' + [...val.slice(1)].map((c) => c + c).join('')
      : val
  }
}
document.getElementById('bgSwatch').addEventListener('input', (e) => {
  elFor('bg').value = e.target.value
  onFieldChange('bg')
})
document.getElementById('textColorSwatch').addEventListener('input', (e) => {
  elFor('textColor').value = e.target.value
  onFieldChange('textColor')
})
elFor('bg').addEventListener('input', () => syncSwatchFromHex('bg', 'bgSwatch'))
elFor('textColor').addEventListener('input', () => syncSwatchFromHex('textColor', 'textColorSwatch'))

// Live NLLB status (previously only ever written once at page load and
// once after Apply, so a Download/Test click's whole multi-GB/multi-
// second progress showed the same frozen text the entire time) and the
// translation mode/language summary Tk keeps visible under the Enable
// Translation checkbox (previously missing from this page entirely).
function pollNllbStatus(){
  pywebview.api.get_nllb_status().then((s) => {
    document.getElementById('nllbStatus').textContent = 'NLLB status: ' + s.text
    document.getElementById('nllbDownload').disabled = s.busy
    document.getElementById('nllbTest').disabled = s.busy
  }).catch(() => {})
}
setInterval(pollNllbStatus, 1500)
setTimeout(pollNllbStatus, 300)

function pollTranslationSummary(){
  pywebview.api.get_translation_summary().then((lines) => {
    document.getElementById('translationSummary').textContent = lines.join('\n')
  }).catch(() => {})
}
setInterval(pollTranslationSummary, 2000)
setTimeout(pollTranslationSummary, 300)

// Same hotkey listener as the Output/Controller windows' own HTML
// (main_webview.py / CONTROLLER_HTML above) - previously missing from
// this window entirely, so F11/Ctrl-Alt-F/Escape/Ctrl-S/Ctrl-Q silently
// did nothing while Options had focus. pywebview has no process-wide
// bind_all equivalent, so every real window needs its own copy.
document.addEventListener('keydown', (e) => {
  const key = e.key.toLowerCase()
  if (key === 'f11' || (e.ctrlKey && e.altKey && key === 'f') || key === 'escape') {
    e.preventDefault()
    pywebview.api.toggle_fullscreen_clicked()
  } else if (e.ctrlKey && key === 's') {
    e.preventDefault()
    pywebview.api.open_settings_clicked()
  } else if (e.ctrlKey && key === 'q') {
    e.preventDefault()
    pywebview.api.close_app_clicked()
  }
})

function setVideoRefreshBusy(busy){
  // Matches Tk's own Refresh-button-disable during a camera scan
  // (settings_ui_mixin.py _refresh_video_devices) - cv2's DirectShow
  // backend isn't safe to probe from two threads at once, so this is a
  // real crash-avoidance signal, not just cosmetic feedback.
  const button = document.getElementById('videoRefresh')
  button.disabled = busy
  button.textContent = busy ? 'Scanning...' : 'Refresh'
}

function pollVideoStatus(){
  pywebview.api.get_video_status().then((status) => {
    document.getElementById('videoStatus').textContent = status || '--'
  }).catch(() => {})
}
setInterval(pollVideoStatus, 2000)
setTimeout(pollVideoStatus, 300)

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

function fillDatalist(id, names){
  const el = elFor(id)
  el.innerHTML = ''
  for (const name of names){
    const opt = document.createElement('option')
    opt.value = name
    el.appendChild(opt)
  }
}

function applyThemeVars(vars){
  const root = document.documentElement
  for (const key in vars){
    if (key === 'colorScheme') { root.style.colorScheme = vars[key]; continue }
    root.style.setProperty(key, vars[key])
  }
}

applyBtn.addEventListener('click', async () => {
  const result = await pywebview.api.apply()
  if (!result.ok){
    statusEl.textContent = 'Apply failed: ' + result.error
    return
  }
  setDirty(result.dirty)
  const v = result.values
  document.getElementById('nllbStatus').textContent = 'NLLB status: ' + v.nllb_status
  // Snaps the language fields to whatever actually took effect - typing
  // something that doesn't match a real option falls back to the
  // previous value server-side (_optional_mapped_setting, settings_logic_
  // mixin.py) without erroring, but the input would otherwise keep
  // showing the un-matched text the user typed, which no longer reflects
  // reality once applied.
  document.getElementById('sourceLang').value = v.display.stt_source_lang
  document.getElementById('nllbTargetLang').value = v.display.local_nllb_target_lang
  // Same reasoning for clearSeconds - _apply_display_vars clamps this to
  // 1-60 server-side regardless of what this field's own (wider) min/max
  // allowed the user to type, so the input needs resyncing to the actual
  // clamped value or it would keep showing an un-applied number forever.
  document.getElementById('clearSeconds').value = v.clear_display_inactivity_seconds
  // Translation, when just enabled, forces interim text off server-side
  // too (_apply_translation_vars) - resync in case the live checkbox
  // listener above didn't already catch it (e.g. Enable Translation was
  // toggled via Hardware Autodetect or another path).
  document.getElementById('interim').checked = v.show_interim_text
  syncInterimWithTranslation()
  syncVideoRowsVisibility()
  pollTranslationSummary()
  statusEl.textContent = 'Applied and saved.'
})

window.addEventListener('pywebviewready', async () => {
  let v = null
  for (let i = 0; i < 50 && !v; i++){
    v = await pywebview.api.current_values()
    if (!v) await new Promise(r => setTimeout(r, 50))
  }
  if (!v){ statusEl.textContent = 'Engine did not become ready.'; return }
  const opts = await pywebview.api.options()

  document.getElementById('theme').value = v.ui_theme
  document.getElementById('lines').value = v.max_lines
  document.getElementById('bg').value = v.bg_color
  document.getElementById('textColor').value = v.text_color
  syncSwatchFromHex('bg', 'bgSwatch')
  syncSwatchFromHex('textColor', 'textColorSwatch')
  document.getElementById('lockFocus').checked = v.lock_output_focus
  document.getElementById('clear').checked = v.clear_display_on_inactivity
  document.getElementById('clearSeconds').value = v.clear_display_inactivity_seconds
  syncClearSecondsVisibility()
  document.getElementById('videoEnabled').checked = v.video_feed_enabled
  document.getElementById('videoLines').value = v.video_max_lines
  document.getElementById('videoAlpha').value = v.video_caption_bar_alpha
  syncVideoRowsVisibility()
  document.getElementById('interim').checked = v.show_interim_text
  document.getElementById('device').value = v.stt_device
  document.getElementById('sourceLang').value = v.display.stt_source_lang
  document.getElementById('silero').value = v.realtime_stt_silero_sensitivity
  document.getElementById('enableTranslation').checked = v.translation_enabled
  syncInterimWithTranslation()
  document.getElementById('nllbDevice').value = v.local_nllb_device
  document.getElementById('nllbTargetLang').value = v.display.local_nllb_target_lang
  document.getElementById('nllbMaxChars').value = v.local_nllb_max_chars
  document.getElementById('nllbStatus').textContent = 'NLLB status: ' + v.nllb_status
  document.getElementById('startWithWindows').checked = v.start_with_windows
  document.getElementById('cudaDirectory').value = v.cuda_directory
  document.getElementById('badWordsEn').value = v.bad_words_en.join(', ')
  document.getElementById('badWordsEs').value = v.bad_words_es.join(', ')
  document.getElementById('vocabEn').value = v.custom_vocab_en.join(', ')
  document.getElementById('vocabEs').value = v.custom_vocab_es.join(', ')

  fillSelect('finalModel', opts.realtime_stt_final_model, v.display.realtime_stt_final_model)
  fillSelect('realtimeModel', opts.realtime_stt_realtime_model, v.display.realtime_stt_realtime_model)
  fillSelect('nllbModel', opts.local_nllb_model_name, v.display.local_nllb_model_name)
  fillSelect('loggingMode', opts.logging_mode, v.display.logging_mode)
  fillSelect('audioDevice', opts.audio_device, v.preferred_device_label)
  fillSelect('videoDevice', [], v.video_device_label)
  fillDatalist('sourceLangOptions', opts.stt_source_lang)
  fillDatalist('nllbTargetLangOptions', opts.local_nllb_target_lang)
  fillSelect('outputMonitor', opts.monitor_labels, v.monitor_var)
  fillSelect('settingsMonitor', opts.monitor_labels, v.settings_monitor_var)
  await pywebview.api.refresh_devices()

  setDirty(v.dirty)
  statusEl.textContent = 'Loaded current settings.'
})
</script></body></html>
"""


class WebSettingsUIMixin(SettingsLogicMixin):
    def _restored_window_geometry(self, geometry_string, default_width, default_height):
        """Turns a saved Tk-format "WxH+X+Y" geometry string (or None, on
        a first run) into the width/height/x/y kwargs webview.create_window
        accepts, falling back to the given defaults - the Web-side half of
        real window geometry persistence (the other half is
        PywebviewGeometryAdapter, which .geometry()'s the current live
        window back into that same string format for save_settings to
        write out). Reuses _parse_geometry (monitor_logic_mixin.py) rather
        than writing a second WxH+X+Y parser, since Tk's own geometry
        strings and this one need to agree on the exact same format for
        settings.json to round-trip between the Tk and Web apps."""
        parsed = self._parse_geometry(geometry_string) if geometry_string else None
        if not parsed:
            return {"width": default_width, "height": default_height}
        width, height, x, y = parsed
        kwargs = {
            "width": width or default_width,
            "height": height or default_height,
        }
        if x is not None and y is not None:
            kwargs["x"] = x
            kwargs["y"] = y
        return kwargs

    def build_web_controller(self):
        """Analogous to open_settings() - called once at startup from
        main_webview.py after the Output window is loaded (needs
        self._window to already exist, for the Preview capture and the
        cross-window Toggle Fullscreen call)."""
        import webview

        # File/About are plain HTML (#menuBar in CONTROLLER_HTML), not a
        # native WinForms MenuStrip - see this file's own module docstring
        # for why. That also means the old "attach the menu only once
        # app_startup_ready is true" dance (the real _build_menu_bar()'s
        # own behavior, matched here via a deferred set_window_menu() call
        # in _hide_startup_loading_overlay) needs no equivalent: #menuBar
        # is ordinary content inside #wrap, sitting behind the same full-
        # screen #startupOverlay (z-index 1000) that already blocks
        # interaction with Preview/Status/buttons until hideStartupOverlay()
        # runs - it's gated for free, not specially wired.
        geometry_kwargs = self._restored_window_geometry(self.settings_geometry, 420, 560)
        controller_window = webview.create_window(
            "Rhema Controller",
            html=CONTROLLER_HTML.replace("__THEME_CSS__", self._theme_css_declaration()),
            background_color=self._settings_palette()["window_bg"],
            js_api=_ControllerApi(self),
            **geometry_kwargs,
        )
        self._controller_window = controller_window
        # Duck-types just enough of a Tk Toplevel for save_settings/
        # load_settings (settings_mixin.py) to persist/restore this
        # window's real size, position, and maximized state, exactly like
        # Tk's own settings_window - previously left None for the life of
        # the app, so every resize/move/maximize was silently discarded on
        # close (settings_mixin.py's capture block no-ops whenever this is
        # None).
        self.settings_window = PywebviewGeometryAdapter(controller_window)
        controller_window.events.closing += self.on_closing
        # shown (not immediately after create_window) - window.native's
        # real WinForms Form isn't guaranteed realized until then, and
        # apply_dark_title_bar's hwnd_for() needs a real Handle to Invoke()
        # against.
        controller_window.events.shown += lambda: self.apply_dark_title_bar(
            controller_window, dark=(self.ui_theme == "dark")
        )
        if self.settings_maximized:
            # Same events.shown timing requirement as apply_dark_title_bar
            # right above - window.native's real WinForms Form isn't
            # guaranteed realized before then either.
            def _maximize_controller():
                try:
                    controller_window.maximize()
                except Exception:
                    pass
            controller_window.events.shown += _maximize_controller
        # Real open_settings() (settings_ui_mixin.py) starts the audio
        # level meter's render loop here too - without this call, nothing
        # ever turns audio_level_target (written continuously by the
        # capture thread) into a rendered value, so the meter bar built
        # into CONTROLLER_HTML stays frozen at 0% forever even while the
        # mic is picking up real audio.
        self._start_audio_level_updates()
        self._show_startup_loading_overlay()
        return controller_window

    # ------------------------------------------------------------------ #
    # Phase 7: startup loading gate - blocks Controller interaction until
    # RealtimeSTT, Local NLLB, and the camera scan have all finished their
    # initial load/verify pass, exactly matching what
    # _show_startup_loading_overlay/_poll_startup_overlay_status/
    # _hide_startup_loading_overlay do in settings_ui_mixin.py.
    # _poll_startup_overlay_status itself is NOT overridden - its real body
    # (settings_ui_mixin.py) only checks a sentinel, calls the shared
    # _check_startup_ready() (settings_logic_mixin.py), and reschedules via
    # self.root.after() - no Tk touch at all, so it's reused unmodified via
    # inheritance, same as _fit_font_to_lines/_apply_scaled_fonts already
    # were in Phase 3.
    # ------------------------------------------------------------------ #
    def _show_startup_loading_overlay(self):
        # Real version rescans camera devices behind the overlay so the
        # video device dropdown reflects last run's saved selection
        # instead of a placeholder - only relevant if video was left on.
        # This override's _refresh_video_devices is already synchronous
        # (no worker thread, unlike the real one), so there is no async
        # completion callback to wire - just call it and immediately mark
        # the scan ready, matching the real method's own "camera scan
        # done" semantics.
        if self.video_feed_enabled:
            self._refresh_video_devices()
        self._mark_startup_video_scan_ready()
        self._startup_loading_overlay = True  # sentinel, not a real widget
        self._poll_startup_overlay_status()

    def _hide_startup_loading_overlay(self):
        self._startup_loading_overlay = None
        if self._controller_window is not None:
            try:
                self._controller_window.evaluate_js("hideStartupOverlay()")
            except Exception:
                pass
        # Same first-run/recommendation-changed Hardware Autodetect the
        # real method triggers automatically - self._transcription_vars/
        # self._translation_vars already exist by this point (Options was
        # built hidden-but-eagerly at startup - see main_webview.py).
        if getattr(self, "_transcription_vars", None) is not None and (
            self.is_first_run or self._hardware_recommendation_differs()
        ):
            self.root.after(300, lambda: self._run_hardware_autodetect_from_menu(
                self._transcription_vars, self._translation_vars
            ))

    # ------------------------------------------------------------------ #
    # File menu actions - real as of Phase 6.
    # ------------------------------------------------------------------ #
    def _run_hardware_autodetect_menu_action(self):
        # Real _run_hardware_autodetect_from_menu (settings_logic_mixin.py)
        # needs the real transcription_vars/translation_vars dicts, which
        # only exist once the Options window has been built at least once -
        # build it (silently, without stealing focus, if this is the very
        # first time) rather than requiring the user to open Options first.
        # Guarded on the vars not existing yet (rather than calling
        # build_web_options() unconditionally): main_webview.py already
        # builds Options hidden-but-eagerly at startup, before the File
        # menu is ever clickable, so by the time this runs the vars almost
        # always already exist - and build_web_options()'s own reuse
        # branch unconditionally shows/restores an existing Options window
        # regardless of the `hidden` flag, so calling it here every time
        # made every Hardware Autodetect click pop Options onto the screen
        # as an unwanted side effect.
        if getattr(self, "_transcription_vars", None) is None:
            self.build_web_options(hidden=True)
        self._run_hardware_autodetect_from_menu(self._transcription_vars, self._translation_vars)

    def _show_options_dialog(self):
        self.build_web_options()

    def focus_controller_window(self):
        # Phase 12 QA pass: Ctrl-S's real target (main.py's
        # open_settings_event - "settings_window" is the real app's own
        # name for the Controller, not the Options dialog). The real
        # version opens the Controller if it doesn't exist yet and
        # focuses it if it does; this port's Controller always exists
        # already (built eagerly at startup, unlike Tk's lazy
        # open_settings()), so this only ever needs the focus half.
        if self._controller_window is not None:
            try:
                self._controller_window.show()
                self._controller_window.restore()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # About/Donate popups - the real settings_ui_mixin.py versions build a
    # tk.Toplevel(parent), falling back to `self.root` when
    # self.settings_window is None (always true here, Phase 6 territory) -
    # a real tk.Toplevel needs a genuine Tk interpreter as its master,
    # which FakeRoot is not, so calling those unmodified would crash the
    # instant a user opened either menu item. Overridden here with
    # message-box equivalents carrying the same real text/links, not
    # simplified content.
    # ------------------------------------------------------------------ #
    def _show_about_popup(self):
        self._show_info_dialog(
            "About Rhema",
            "Rhema\n"
            "Ῥῆμα - pronounced REE-mah\n\n"
            "Greek for \"spoken word\" or \"utterance\" - distinct from "
            "logos (λόγος), the broader word for "
            "\"word\" or \"reason.\" Rhema is the word spoken aloud in the "
            "moment, which is what this app carries across languages in "
            "real time.",
        )

    def _show_donate_popup(self):
        message = (
            "“So faith comes from hearing, and hearing through the "
            "word (rhema) of Christ.” (Romans 10:17, ESV)\n\n"
            "If Rhema has helped carry that spoken word across a language "
            "barrier - a sermon, a Bible study, a testimony someone could "
            "finally understand - I'd be grateful if you'd consider "
            "supporting its continued development.\n\n"
            "This app is built and maintained by one person in their free "
            "time. Every contribution, whatever the amount, directly funds "
            "the time it takes to keep improving it and to keep it "
            "available, free of charge, to churches and ministries who "
            "need it.\n\n"
            "Please note that financial contributions are not tax "
            "deductible."
        )
        # kind="donate" gives an independent Donate/Close button pair,
        # matching the real Tk popup: Donate opens the link but leaves the
        # dialog open, only Close (or Escape) dismisses it - unlike a plain
        # Yes/No confirm, where either button closes the dialog the
        # instant it's clicked, so there was no way to open the link and
        # keep reading, or to close without answering.
        handled, _ = self._show_html_message_dialog(
            "Support Rhema", message, "donate", on_action=self._donate_action_clicked
        )
        if handled:
            return
        # Fallback (pre-window/crash-time only - see web_messagebox.py's
        # own docstring): plain Yes/No, since native MessageBoxW has no
        # "keep open" concept either way.
        if self._confirm_yes_no("Support Rhema", message + "\n\nOpen the donation page now?"):
            self._open_donate_link()

    def _donate_action_clicked(self, value):
        if value:
            self._open_donate_link()

    def _open_donate_link(self):
        try:
            webbrowser.open(DONATE_URL)
        except Exception:
            self._show_error_dialog("Can't open link", f"Couldn't open:\n{DONATE_URL}")

    # ------------------------------------------------------------------ #
    # Controller widget overrides (real methods in display_mixin.py this
    # replaces the Tk-widget-touching tail of).
    # ------------------------------------------------------------------ #
    def update_status(self, msg):
        if msg == self.STATUS_LISTENING or msg.startswith("Listening"):
            msg = self._listening_status_message()
        self._log_status(msg)

        def push():
            if self._controller_window is None:
                return
            try:
                self._controller_window.evaluate_js(
                    "setStatus(%s)" % json.dumps(f"Status: {msg}")
                )
            except Exception:
                pass

        self.root.after(0, push)

    def _set_chunk_latency_label_text(self, label_text):
        def push():
            if self._controller_window is None:
                return
            try:
                self._controller_window.evaluate_js("setLatency(%s)" % json.dumps(label_text))
            except Exception:
                pass

        self.root.after(0, push)

    def _render_audio_level_meter(self, level):
        if self._controller_window is None:
            return
        try:
            self._controller_window.evaluate_js("setMeter(%s)" % json.dumps(float(level)))
        except Exception:
            pass

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self._controller_window is not None:
            try:
                self._controller_window.evaluate_js(
                    "setPauseButtonText(%s)"
                    % json.dumps("Resume" if self.is_paused else "Pause")
                )
            except Exception:
                pass
        self.update_status("Paused" if self.is_paused else self.STATUS_LISTENING)

    def _update_dialog_parent(self):
        # Real update_mixin.py version returns self.settings_window (the
        # real Options dialog, Phase 6) or falls back to self.root - here,
        # the closer equivalent of "attach to the Controller window
        # whenever it's open" is the Controller window itself, not the
        # (fullscreen, always-on-top) Output window.
        return getattr(self, "_controller_window", None)

    def toggle_fullscreen(self):
        # Matches the real main.py TranslationApp.toggle_fullscreen exactly
        # (flip is_fullscreen, call enter_fullscreen()/exit_fullscreen()) -
        # not a Tk-Toplevel-touching method there, just a plain method on
        # the app class, so there's nothing to inherit; this is a genuine
        # port of its body, not an override. hide_status()/
        # show_status_temporarily() (main.py) are deliberately not called
        # here - both are about showing/hiding the Output window's OWN
        # native Tk menu bar briefly outside fullscreen, which has no Web
        # analog worth building for a canvas-only Output window.
        if self._window is None:
            return
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.enter_fullscreen()
        else:
            self.exit_fullscreen()
        # Same stale-canvas-size fix as _select_output_monitor above -
        # exiting fullscreen (a windowed size) or re-entering it (possibly
        # onto a different monitor than last time) both change the real
        # window's dimensions.
        self.root.after(300, self._refresh_output_canvas_size)

    # ------------------------------------------------------------------ #
    # Preview / Output Snapshot.
    #
    # Two techniques were tried and rejected before this one - both real,
    # confirmed findings, not theoretical:
    #
    # - bbox/BitBlt (ImageGrab.grab(bbox=...) against the Output window's
    #   real HWND rect) captures whatever is actually visible on screen at
    #   those coordinates, not the specific window the rect was resolved
    #   from. Confirmed twice, independently, against small self-contained
    #   test windows whose HWND/rect were resolved correctly and
    #   unambiguously: both captures returned a DIFFERENT, unrelated real
    #   window that happened to be occluding that screen region at the
    #   moment of capture, once showing real client/business data and once
    #   what looked like the user's own ticketing system - neither of which
    #   this app had any business capturing. Not an edge case to guard
    #   against with a try/except; it's the technique's ordinary behavior
    #   whenever anything else overlaps that screen region, which is
    #   entirely normal on a real desktop.
    # - PrintWindow (the real Tk app's own technique, ImageGrab.grab(
    #   window=self.root.winfo_id())) IS occlusion-independent - it asks a
    #   specific window to render itself rather than reading screen pixels
    #   - but experiments/web_controller_window.py found it returns solid
    #   black against a real WebView2 surface, so it doesn't work here.
    #
    # The actual fix: Windows Graphics Capture (the same per-window,
    # occlusion-independent, DWM-composited capture API OBS/Xbox Game
    # Bar/Snipping Tool's "Window" mode use), via the `windows-capture`
    # package (Rust-backed, targets this exact API - see requirements.txt).
    # Captures by HWND, so it's immune to both failure modes above: no
    # screen-region readback (nothing else can be captured by accident,
    # regardless of what's on top of the Output window) and no WebView2-
    # specific blackout (it reads the DWM-composited surface, not the
    # window's own GDI device context, which is exactly what PrintWindow
    # against a WebView2 surface can't do).
    #
    # start_free_threaded() (not the plain blocking start()) is deliberate:
    # it returns a CaptureControl immediately, before any frame has
    # arrived, so the done.wait() timeout below can always call
    # control.stop() to tear the native capture session down cleanly even
    # if a frame never arrives (window closed mid-capture, capture session
    # failed silently) - the blocking start() only exposes a stop() that's
    # constructed fresh inside on_frame_arrived, so there'd be nothing to
    # call if no frame ever showed up.
    # ------------------------------------------------------------------ #
    def _capture_output_snapshot(self):
        # Real settings_ui_mixin.py version (self.preview_widget-driven,
        # ImageGrab-based) is called by video_capture_mixin.py's render
        # tick ~50ms after the video feed's first frame draws, to refresh
        # the Controller's preview thumbnail immediately after toggling
        # video on rather than waiting for the next 15s poll. This app
        # never builds a real preview_widget (main_webview.py sets it to
        # None and never reassigns it - CONTROLLER_HTML's own <img> is
        # driven by pollPreview()/get_preview_data_uri instead), so the
        # inherited Tk method's own preview_widget-None guard made this a
        # silent no-op - the real, occlusion-independent snapshot path
        # (_capture_output_snapshot_data_uri) was only ever reachable via
        # the 15s poll, not this fast-refresh signal.
        if self._controller_window is None:
            return
        try:
            data_uri = self._capture_output_snapshot_data_uri()
            if data_uri:
                self._controller_window.evaluate_js("setPreview(%s)" % json.dumps(data_uri))
        except Exception:
            pass

    def _capture_output_snapshot_data_uri(self):
        if self._window is None:
            return None
        try:
            from windows_capture import WindowsCapture
            import cv2
            import base64
            import threading

            from webview_bridge import hwnd_for

            hwnd = hwnd_for(self._window)
            if not hwnd:
                return None

            result = {}
            done = threading.Event()

            capture = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                window_hwnd=hwnd,
            )

            @capture.event
            def on_frame_arrived(frame, capture_control):
                result["frame"] = frame.convert_to_bgr()
                capture_control.stop()
                done.set()

            @capture.event
            def on_closed():
                done.set()

            control = capture.start_free_threaded()
            if not done.wait(timeout=3.0):
                control.stop()
                return None

            frame = result.get("frame")
            if frame is None:
                return None

            image = frame.frame_buffer
            # Matches the real Tk app's own _render_output_snapshot_thumbnail
            # (settings_ui_mixin.py) capping at 1.0x native resolution to
            # avoid upscaling blur - only ever shrinks, and only shrinks a
            # real (usually fullscreen-sized) Output frame down to a size
            # that keeps the pywebview JS<->Python IPC payload small for a
            # snapshot polled every 15s (pollPreview, in this file's own
            # CONTROLLER_HTML).
            max_width = 640
            if frame.width > max_width:
                scale = max_width / frame.width
                image = cv2.resize(
                    image, (max_width, max(1, int(frame.height * scale)))
                )

            ok, buf = cv2.imencode(".png", image)
            if not ok:
                return None
            return "data:image/png;base64," + base64.b64encode(buf).decode("ascii")
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Phase 6: chrome hooks the shared SettingsLogicMixin methods call by
    # name (see settings_logic_mixin.py's own module docstring for the
    # full accounting of which real chrome touches survive inside "logic").
    # ------------------------------------------------------------------ #
    def _show_hardware_autodetect_result(self, text):
        self._show_info_dialog("Hardware Autodetect", text)

    # ------------------------------------------------------------------ #
    # Local NLLB progress popup - the real settings_ui_mixin.py versions
    # build a tk.Toplevel(self.settings_window or self.root). Web's
    # self.root is a FakeRoot (no real Tcl interpreter, so tk.Toplevel(
    # FakeRoot) throws) - previously unoverridden, so every download/
    # check/verify's in-progress popup fell through the MRO to those Tk
    # versions and threw inside FakeRoot's own callback runner, which only
    # ever routes exceptions to the error log (see _write_unhandled_
    # exception) - a real user saw the SAME frozen "NLLB status: ..." text
    # for the whole multi-GB operation with no popup and no other feedback
    # that anything was happening. Overridden here with a real, themed,
    # non-modal pywebview window (indeterminate bouncing bar, matching
    # Tk's own indeterminate Progressbar - a determinate one would stall
    # visibly, since neither a download nor a model load has a reliable
    # byte-progress signal for most of its duration).
    # ------------------------------------------------------------------ #
    def _show_local_nllb_progress_popup(self, message):
        import webview

        try:
            popup = webview.create_window(
                "Local NLLB",
                html=NLLB_PROGRESS_HTML.replace("__INITIAL__", json.dumps(message)),
                width=340,
                height=130,
                resizable=False,
                frameless=True,
                on_top=True,
                background_color="#1E2228",
            )
            self._local_nllb_popup = popup
        except Exception:
            self._local_nllb_popup = None

    def _update_local_nllb_progress_popup(self, message):
        popup = getattr(self, "_local_nllb_popup", None)
        if popup is None:
            return
        try:
            popup.evaluate_js("setNllbStatus(%s)" % json.dumps(message))
        except Exception:
            pass

    def _close_local_nllb_progress_popup(self):
        popup = getattr(self, "_local_nllb_popup", None)
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass
        self._local_nllb_popup = None

    def _nllb_status_for_options(self):
        # Polled by OPTIONS_HTML (pollNllbStatus) so the #nllbStatus line
        # updates live while a check/download/load is in progress -
        # previously it was only ever written once at page load and once
        # after Apply, so a user who clicked Download and left Options
        # open saw the same stale text for the whole operation. "busy"
        # mirrors _refresh_local_nllb_runtime_ui's own in_progress check
        # (settings_ui_mixin.py), used to disable both NLLB buttons while
        # true so a second click can't race the worker thread already
        # running.
        busy = bool(self.nllb_download_in_progress or self.nllb_check_in_progress)
        busy = busy or self.nllb_status in ("Checking", "Downloading", "Loading")
        return {
            "text": self._local_nllb_status_message() or self.nllb_status,
            "busy": busy,
        }

    def _test_nllb_from_options(self):
        # Real "Test Local NLLB" button (settings_ui_mixin.py) was entirely
        # absent from the Web Options page - a user had no way to confirm
        # their selected model/device combo actually produces a working
        # translation without enabling translation and speaking live,
        # which conflates "is NLLB working" with "are my mic/STT also
        # working". test_button/test_status_var are passed as None: the
        # real method's own try/except around both already makes that
        # safe (settings_logic_mixin.py's module docstring calls this
        # exact None-guard pattern out as the intended Web-side contract),
        # and the actual result still reaches the user via the same
        # _set_local_nllb_status()/update_status() calls _execute_local_
        # nllb_test already makes - surfaced here through the same
        # #nllbStatus live poll _nllb_status_for_options feeds, matching
        # how Tk's own Test button reuses local_nllb_message_var (the
        # SAME var the main status line shows) rather than a separate one.
        self._run_local_nllb_test_from_vars(
            self._translation_vars["local_nllb_model_name_var"],
            self._translation_vars["local_nllb_device_var"],
            self._translation_vars["local_nllb_max_chars_var"],
            None,
            None,
            model_name_map=self._translation_vars["local_nllb_model_name_map"],
        )
        return {"ok": True}

    def _translation_summary_lines(self):
        # Web-side equivalent of _refresh_translation_toggle_label
        # (settings_ui_mixin.py), which Tk keeps live via three trace_add
        # callbacks under the Enable Translation checkbox. Previously
        # missing from Web entirely - purely informational (every value
        # here is already visible/settable via the raw controls above),
        # so this is computed fresh on each poll rather than needing its
        # own dirty-tracking.
        if self.translation_enabled:
            mode_line = "Current mode: Translation ON (Local NLLB)"
            target = (
                self._nllb_target_lang_rev_map.get(
                    self.local_nllb_target_lang, self.local_nllb_target_lang
                )
                if getattr(self, "_nllb_target_lang_rev_map", None)
                else self.local_nllb_target_lang
            ) or "English"
            output_line = f"Output language: {target}"
        else:
            mode_line = "Current mode: Translation OFF"
            output_line = "Output language: same as input (translation is off)"
        source = (
            self._stt_source_lang_rev_map.get(self.source_lang or "auto", "Auto-detect")
            if getattr(self, "_stt_source_lang_rev_map", None)
            else (self.source_lang or "Auto-detect")
        )
        if source.lower() == "auto-detect":
            detected = (self.auto_detect_lang or "").strip().lower()
            if detected:
                source = f"Auto-detect (currently: {self._language_label(detected)})"
        input_line = f"Input language: {source} (set in Transcription section above)"
        return [mode_line, output_line, input_line]

    def _confirm_local_nllb_download(self, model_name):
        # Real settings_ui_mixin.py version builds a modal tk.Toplevel with
        # grab_set()/wait_window() - a synchronous confirm. ctypes
        # MessageBoxW (web_messagebox.py, Phase 1) is ALSO synchronous/
        # blocking, so this is a genuine drop-in equivalent, not an
        # approximation - the caller gets the same "blocks until the user
        # answers, then returns a bool" contract either way.
        model_name = (model_name or "").strip() or self.LOCAL_NLLB_DEFAULT_MODEL_NAME
        return self._confirm_yes_no(
            "Download Local NLLB Model",
            f"Download the Local NLLB model now?\n\n{model_name}\n\n"
            "This may be several GB and requires an internet connection.",
        )

    # enter_fullscreen/exit_fullscreen used to be Phase-6-scoped no-ops
    # here - real per-monitor-aware versions now come from WebMonitorMixin
    # (web_monitor_mixin.py, Phase 8), mixed in ahead of this class in
    # main_webview.py's MRO.

    def _apply_ui_theme(self):
        """Web-safe override of settings_ui_mixin.py's real version -
        needed as soon as Options exposes a Theme control at all, not
        optional polish. The real version touches self.style (a
        ttkbootstrap Style object) and self.settings_window/self.options_
        window - none of which exist on WebTranslationApp (this port uses
        self._controller_window/self._options_window, real pywebview
        windows, not Tk Toplevels). self.settings_window specifically is
        touched with NO surrounding try/except (unlike the self.style/
        apply_dark_title_bar calls right above it, which are individually
        guarded), so calling the real version unmodified would raise a
        bare AttributeError - and _apply_display_vars (settings_logic_
        mixin.py, shared) calls this FIRST, before applying any other
        Display setting, so that crash would silently abort an entire
        Apply click's worth of changes the moment the theme dropdown
        actually changed, not just fail to re-theme.

        Real visual effect: the OS dark/light title bar on whichever of
        the two real windows exist, plus (via _rebuild_settings_windows
        below) the actual page content re-theming live.
        """
        dark = self.ui_theme == "dark"
        for window in (self._controller_window, self._options_window):
            if window is not None:
                self.apply_dark_title_bar(window, dark=dark)

    def _theme_vars(self):
        """The live palette as CSS custom-property values, shared by both
        the initial page render (build_web_controller/build_web_options
        substitute __THEME_CSS__ with this, joined into a declaration
        string, so first paint is never wrong-themed) and a later live
        re-theme (_rebuild_settings_windows below, via each page's own
        applyThemeVars(vars) JS function). One dict, one source of truth,
        rather than the two staying in sync by hand. _settings_palette()
        (settings_logic_mixin.py) is the SAME palette Tk's real dialogs
        use, not a separate web-only guess.
        """
        palette = self._settings_palette()
        dark = self.ui_theme == "dark"
        return {
            "colorScheme": "dark" if dark else "light",
            # Dark keeps its original relationship (page darkest, card a
            # step lighter). Light deliberately does NOT mirror that the
            # same way Tk's own _settings_palette() intends it (light
            # page, white card) - per explicit feedback on this port's own
            # Controller: the app background should read as white, with
            # the menu dropdown/hover states reading as a distinct grey
            # "overlay" instead. --overlay carries that grey and is what
            # CONTROLLER_HTML's dropdown/hover rules use - OPTIONS_HTML has
            # no such overlay concept (a single bordered card, not a
            # floating popup) so it only ever uses --bg/--card, unaffected
            # by this beyond --bg itself now reading white in light mode.
            "--bg": palette["window_bg"] if dark else palette["section_bg"],
            "--card": palette["section_bg"],
            "--overlay": palette["section_bg"] if dark else palette["window_bg"],
            "--text": palette["text"],
            "--muted": palette["muted_text"],
            "--border": palette["border"],
            "--input-bg": palette["input_bg"],
            "--accent": palette["accent"],
            "--accent-hover": palette["accent_hover"],
            "--dirty": "#22C55E" if dark else "#16A34A",
        }

    def _theme_css_declaration(self):
        vars_dict = self._theme_vars()
        parts = []
        for key, value in vars_dict.items():
            if key == "colorScheme":
                parts.append(f"color-scheme:{value}")
            else:
                parts.append(f"{key}:{value}")
        return ";".join(parts)

    def _rebuild_settings_windows(self):
        # Real version destroys/reopens self.settings_window/self.options_
        # window (Tk Toplevels) so their raw tk widgets pick up the new
        # theme's palette. A pywebview page doesn't need a rebuild to
        # re-theme at all - CONTROLLER_HTML/OPTIONS_HTML's :root custom
        # properties can just be updated live via each page's own
        # applyThemeVars(vars) JS function, genuinely instant with no
        # window teardown (which would also have re-run the startup gate/
        # hardware-autodetect logic build_web_controller triggers on
        # first build - not something a theme change should ever redo).
        try:
            theme_vars = self._theme_vars()
            for window in (self._controller_window, self._options_window):
                if window is not None:
                    window.evaluate_js("applyThemeVars(%s)" % json.dumps(theme_vars))
        except Exception:
            pass

    def _set_settings_dirty_state(self, dirty_ctx, is_dirty, force=False):
        # Real version does save_button.config(...) - dirty_ctx never gets
        # a "save_button" key from build_web_options below, so the real
        # _apply_settings_from_controller/_update_settings_dirty_state
        # calls into this safely no-op today (dirty_ctx.get("save_button")
        # is None-guarded at every call site). Overridden anyway so the
        # Options window's own Apply button can visually reflect dirty
        # state, which the real no-op wouldn't give it.
        if not force and is_dirty == bool(dirty_ctx.get("dirty_value")):
            return
        dirty_ctx["dirty_value"] = bool(is_dirty)
        window = dirty_ctx.get("options_window")
        if window is None:
            return
        try:
            window.evaluate_js("setDirty(%s)" % json.dumps(bool(is_dirty)))
        except Exception:
            pass

    def _refresh_audio_devices(self):
        # Real version rescans devices on a worker thread and repopulates a
        # real tk.OptionMenu. Called from _apply_settings_vars
        # (settings_logic_mixin.py) after every Apply, in case the device
        # list changed - this override does the same rescan, then pushes
        # the refreshed list to the Options window's own dropdown instead
        # of a Tk menu.
        #
        # Real _refresh_audio_devices (settings_ui_mixin.py) additionally
        # re-resolves preferred_device_label against the freshly rescanned
        # list via _resolve_preferred_device_label (name/type-normalized
        # matching, monitor_logic_mixin.py, since a raw index can silently
        # point at a different physical device after Windows re-enumerates
        # them) and re-derives microphone_index from that - previously
        # missing here entirely, so this only ever pushed the RAW, already-
        # possibly-stale preferred_device_label string into the dropdown
        # without ever touching self.microphone_index. Since
        # main_webview.py unconditionally resets microphone_index to 0 at
        # every launch, and this refresh runs at startup (Options is built
        # hidden-but-eagerly before any capture thread starts) as well as
        # after every Apply, the real recording device silently stayed
        # "whatever enumerates at index 0" on every single run regardless
        # of the saved microphone - the dropdown showed the right label the
        # whole time, giving no visible sign anything was wrong.
        if getattr(self, "_options_window", None) is None:
            return
        try:
            self.devices = self.get_audio_devices()
            resolved_label = self._resolve_preferred_device_label(self.preferred_device_label)
            if resolved_label:
                new_index = self.devices.index(resolved_label)
            elif self.preferred_device_label in self.devices:
                resolved_label = self.preferred_device_label
                new_index = self.devices.index(resolved_label)
            elif self.devices:
                resolved_label = self.devices[0]
                new_index = 0
            else:
                resolved_label = ""
                new_index = None
            device_changed = new_index != self.microphone_index
            self.microphone_index = new_index
            if resolved_label:
                self.preferred_device_label = resolved_label
            window = self._options_window
            window.evaluate_js(
                "fillSelect('audioDevice', %s, %s)"
                % (json.dumps(self.devices), json.dumps(self.preferred_device_label))
            )
            # Matches the real _handle_audio_device_change's own device_
            # changed check (settings_ui_mixin.py), which fires on this
            # same re-resolution via its device_var trace - RealtimeSTT/the
            # level meter only read microphone_index at recorder-
            # construction time, so a corrected index needs an explicit
            # restart to actually take effect this session, not just in
            # settings.json for next time.
            if device_changed:
                self._request_capture_restart()
                self._request_audio_level_stream_restart()
        except Exception:
            pass

    def _refresh_video_devices(self):
        # Same real-app trigger point as _refresh_audio_devices (called
        # after Apply); full rewrite per the port plan, same reasoning -
        # the real method interleaves a worker thread with tk._setit/menu
        # manipulation with no Web equivalent.
        #
        # _video_scan_in_progress guards against a REAL crash, not just
        # janky UX: cv2's DirectShow backend is not safe to probe from two
        # threads at once (Tk's own _refresh_video_devices, settings_ui_
        # mixin.py, carries the same guard for the same documented reason -
        # concurrent enumerate_video_devices calls have crashed the process
        # with a native heap-corruption fault). js_api calls dispatch on
        # their own threads, so a user clicking Refresh twice, or changing
        # the camera dropdown while the page-load auto-scan is still
        # running, could otherwise fire two genuinely concurrent probes.
        if getattr(self, "_options_window", None) is None:
            return
        if getattr(self, "_video_scan_in_progress", False):
            return
        self._video_scan_in_progress = True
        try:
            self._options_window.evaluate_js("setVideoRefreshBusy(true)")
        except Exception:
            pass
        try:
            available = self.enumerate_video_devices()
            labels = [self._video_device_label(i) for i in available]
            current = (
                self._video_device_label(self.video_device_index)
                if self.video_device_index is not None
                else ""
            )
            self._options_window.evaluate_js(
                "fillSelect('videoDevice', %s, %s)" % (json.dumps(labels), json.dumps(current))
            )
        except Exception:
            pass
        finally:
            self._video_scan_in_progress = False
            try:
                self._options_window.evaluate_js("setVideoRefreshBusy(false)")
            except Exception:
                pass

    def _browse_cuda_directory(self):
        # Real settings_ui_mixin.py version opens a native folder picker
        # via choose_directory (tkinter.filedialog.askdirectory) - pywebview
        # has its own equivalent, create_file_dialog(FileDialog.FOLDER),
        # a real native Windows folder picker, not an HTML substitute.
        if self._options_window is None:
            return {"ok": False}
        try:
            import os

            import webview

            current = self.cuda_directory or ""
            result = self._options_window.create_file_dialog(
                webview.FileDialog.FOLDER,
                directory=current if os.path.isdir(current) else "",
            )
        except Exception:
            return {"ok": False}
        if not result:
            return {"ok": False}
        path = result[0]
        self._advanced_vars["cuda_directory_var"].set(path)
        return {
            "ok": True,
            "path": path,
            "dirty": bool(self._options_dirty_ctx["dirty_value"]),
        }

    def _check_nllb_download_from_options(self):
        # Real button (settings_ui_mixin.py) relabels itself between
        # "Download Local NLLB model" and "Check for Updates" depending on
        # self.nllb_status, but that relabeling happens through
        # _refresh_local_nllb_runtime_ui's real button.config() calls,
        # already None-guarded (this port never builds a real button
        # object for it - see settings_logic_mixin.py's own module
        # docstring) - so calling the shared decide-download-or-check
        # method directly, with the SAME tk.Variables already backing the
        # visible Model name/Device/Max chars fields, is a safe, complete
        # equivalent without needing this page's own button to relabel.
        self._download_or_check_local_nllb_from_vars(
            self._translation_vars["local_nllb_model_name_var"],
            self._translation_vars["local_nllb_device_var"],
            self._translation_vars["local_nllb_max_chars_var"],
            model_name_map=self._translation_vars["local_nllb_model_name_map"],
        )
        return {"ok": True}

    def _select_output_monitor(self, label):
        # Real on_output_monitor_change (settings_ui_mixin.py) is a
        # trace_add callback that fires LIVE the instant the dropdown
        # value changes, not at Apply time - _apply_display_vars
        # (settings_logic_mixin.py, shared) only re-derives monitor_index
        # from the var at Apply, it never actually moves anything itself.
        # This mirrors that live-change contract via the same js_api
        # pattern already used for the Video Device fix (options_select_
        # video_device) rather than waiting for Apply, and updates
        # monitor_var too so Apply doesn't clobber it back afterward -
        # the exact bug that fix addressed for video device applies here
        # identically.
        #
        # Deliberately no self.save_settings() here (a real, previously
        # fixed bug, not an oversight): every OTHER Display setting is
        # gated behind Apply (options_set_var only calls var.set(), never
        # save_settings() - see options_set_var below), so this live move
        # persisting immediately meant closing Options without Apply still
        # kept the new monitor on next launch, unlike Tk (which also moves
        # the window live for the session but only persists on Apply).
        # _apply_display_vars (settings_logic_mixin.py) already re-derives
        # and saves monitor_index from monitor_var at Apply time, matching
        # Tk exactly.
        monitor_labels = self._display_vars["monitor_labels"]
        if label not in monitor_labels:
            return {"ok": False}
        self.monitor_index = monitor_labels.index(label)
        self.monitor_device, self.monitor_origin = self._monitor_identity_for_index(
            self.monitor_index
        )
        self._display_vars["monitor_var"].set(label)
        if self.is_fullscreen:
            self.enter_fullscreen()
        else:
            self.move_window_to_monitor(self._window, self.monitor_index, keep_size=False)
        # enter_fullscreen()/move_window_to_monitor() resize the real
        # window, but the Output <canvas>'s own cached pixel dimensions
        # (WebCanvas._w/_h) were only ever set once, at startup - without
        # this, switching to a different-resolution/DPI monitor left video
        # letterboxing and caption font/wrap sizing stuck at the OLD
        # monitor's size for the rest of the session. A short delay lets
        # the native resize actually land first, matching _on_window_
        # loaded's own settle delay after its move+resize+fullscreen call.
        self.root.after(300, self._refresh_output_canvas_size)
        return {"ok": True}

    def _select_settings_monitor(self, label):
        # Mirrors on_settings_monitor_change - real _move_settings_window_
        # to_monitor (monitor_mixin.py) additionally normalizes a
        # maximized Tk Toplevel's state before moving it; pywebview
        # windows here don't need that dance, so this calls the shared
        # move_window_to_monitor (WebMonitorMixin's real per-monitor-aware
        # override) directly against _controller_window. No
        # self.save_settings() here either - same Apply-gate reasoning as
        # _select_output_monitor above.
        monitor_labels = self._display_vars["monitor_labels"]
        if label not in monitor_labels:
            return {"ok": False}
        self.settings_monitor_index = monitor_labels.index(label)
        self.settings_monitor_device, self.settings_monitor_origin = (
            self._monitor_identity_for_index(self.settings_monitor_index)
        )
        self._display_vars["settings_monitor_var"].set(label)
        self.move_window_to_monitor(self._controller_window, self.settings_monitor_index, keep_size=True)
        return {"ok": True}

    def _refresh_output_canvas_size(self):
        """Re-measures the real Output window and pushes the new size into
        both the <canvas> element itself (initCanvas(), the same JS the
        constructor calls once at startup) and WebCanvas's own cached
        dimensions (WebCanvas.resize, webview_bridge.py) - without the
        latter, winfo_width()/height() (what the letterbox/font-fitting
        math actually reads) would keep reporting the stale startup size
        even after the on-screen canvas itself resized. Called after any
        runtime change to the Output window's real geometry: switching the
        Output monitor (_select_output_monitor above) or toggling
        fullscreen (toggle_fullscreen below)."""
        if self._window is None or getattr(self, "text_canvas", None) is None:
            return
        try:
            dims = self._window.evaluate_js("initCanvas()")
            width, height = int(dims["w"]), int(dims["h"])
            self.text_canvas.resize(width, height)
            self._apply_scaled_fonts()
            self._fit_font_to_lines()
            self.render_text()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # The real Options window - every real setting wired to a genuine
    # tk.Variable/tk.Text, exactly as experiments/web_options.py already
    # proved (that file's own docstring covers the two real thread-
    # affinity findings this reuses: a dedicated var_root mainloop thread,
    # and a forced var_root.update() after every tk.Text edit so
    # <<Modified>> has already fired by the time set_text() returns).
    # Deliberately still narrow, matching that experiment's own reviewed
    # scope: theme_var (destroys/rebuilds real Tk windows on the other
    # side) and real per-monitor selection (monitor_var/
    # settings_monitor_var) aren't wired - dark/light theme switching and
    # real multi-monitor placement remain open, the same two gaps that
    # experiment already flagged and this phase doesn't newly introduce.
    # ------------------------------------------------------------------ #
    def build_web_options(self, hidden=False):
        import webview

        if getattr(self, "_options_window", None) is not None:
            try:
                self._options_window.show()
                self._options_window.restore()
                return self._options_window
            except Exception:
                pass

        if getattr(self, "_var_interpreter", None) is None:
            self._var_interpreter = TkVariableInterpreter()
        v = self._var_interpreter.root

        final_model_map = dict(REALTIME_STT_FINAL_MODEL_OPTIONS)
        final_model_rev_map = {code: name for name, code in REALTIME_STT_FINAL_MODEL_OPTIONS}
        realtime_model_map = dict(REALTIME_STT_REALTIME_MODEL_OPTIONS)
        realtime_model_rev_map = {code: name for name, code in REALTIME_STT_REALTIME_MODEL_OPTIONS}
        nllb_model_name_map = dict(NLLB_MODEL_NAME_OPTIONS)
        nllb_model_name_rev_map = {code: name for name, code in NLLB_MODEL_NAME_OPTIONS}
        logging_mode_map = dict(LOGGING_MODE_OPTIONS)
        logging_mode_rev_map = {code: name for name, code in LOGGING_MODE_OPTIONS}
        self._logging_mode_rev_map = logging_mode_rev_map
        self._final_model_rev_map = final_model_rev_map
        self._realtime_model_rev_map = realtime_model_rev_map
        self._nllb_model_name_rev_map = nllb_model_name_rev_map

        # Real ~100/200-language lists (languages.py), same source Tk's own
        # _build_searchable_language_combobox uses - previously this page
        # hardcoded 3 and 2 raw-code options respectively, with a tooltip
        # that dishonestly still claimed the full search (see this file's
        # own OPTIONS_HTML). The var holds the DISPLAY NAME the <input
        # list=...> field shows/lets the user type, matching every other
        # mapped field's contract; the map resolves it back to the real
        # code at Apply time (_optional_mapped_setting, settings_logic_
        # mixin.py) exactly like Tk's combobox var/name_to_code pair.
        stt_language_options = [("Auto-detect", "auto")] + whisper_language_options()
        stt_lang_name_to_code = dict(stt_language_options)
        stt_lang_code_to_name = {code: name for name, code in stt_language_options}
        nllb_lang_options = nllb_language_options()
        nllb_lang_name_to_code = dict(nllb_lang_options)
        nllb_lang_code_to_name = {code: name for name, code in nllb_lang_options}
        self._stt_language_options = stt_language_options
        self._nllb_lang_options = nllb_lang_options
        self._stt_source_lang_rev_map = stt_lang_code_to_name
        self._nllb_target_lang_rev_map = nllb_lang_code_to_name

        # Real monitor labels (monitor_logic_mixin.py, shared with Tk and
        # already Tk-free) - previously hardcoded to a single "Monitor 1"
        # placeholder regardless of actual hardware.
        self.monitors = self.get_monitors()
        monitor_labels = self.get_monitor_labels() or ["Monitor 1"]
        current_output_monitor = monitor_labels[
            min(max(self.monitor_index, 0), len(monitor_labels) - 1)
        ]
        current_settings_monitor = monitor_labels[
            min(max(self.settings_monitor_index, 0), len(monitor_labels) - 1)
        ]

        self._display_vars = {
            "theme_var": tk.StringVar(
                master=v, value="Dark" if self.ui_theme == "dark" else "Light"
            ),
            "lines_var": tk.IntVar(master=v, value=self.max_lines),
            "video_lines_var": tk.IntVar(master=v, value=self.video_max_lines),
            "bg_color_var": tk.StringVar(master=v, value=self.bg_color),
            "text_color_var": tk.StringVar(master=v, value=self.text_color),
            "monitor_labels": monitor_labels,
            "monitor_var": tk.StringVar(master=v, value=current_output_monitor),
            "settings_monitor_var": tk.StringVar(master=v, value=current_settings_monitor),
            "clear_display_on_inactivity_var": tk.BooleanVar(
                master=v, value=self.clear_display_on_inactivity
            ),
            "clear_display_inactivity_seconds_var": tk.IntVar(
                master=v, value=self.clear_display_inactivity_seconds
            ),
            "lock_output_focus_var": tk.BooleanVar(master=v, value=self.lock_output_focus),
            "video_feed_enabled_var": tk.BooleanVar(master=v, value=self.video_feed_enabled),
            "video_device_var": tk.StringVar(
                master=v,
                value=(
                    self._video_device_label(self.video_device_index)
                    if self.video_device_index is not None
                    else ""
                ),
            ),
            "video_caption_alpha_var": tk.DoubleVar(
                master=v, value=self.video_caption_bar_alpha * 100
            ),
        }
        self._transcription_vars = {
            "show_interim_text_var": tk.BooleanVar(master=v, value=self.show_interim_text),
            "stt_device_var": tk.StringVar(master=v, value=self.stt_device),
            "stt_source_lang_var": tk.StringVar(
                master=v,
                value=stt_lang_code_to_name.get(self.source_lang or "auto", "Auto-detect"),
            ),
            # Phase 12 QA pass finding: _apply_transcription_vars
            # (settings_logic_mixin.py) only applies this var at all if a
            # companion "stt_source_lang_map" key exists in the dict
            # (_optional_mapped_setting returns the unchanged current
            # value otherwise, silently no-oping the whole field). Now a
            # real display-name-to-code map covering all ~100 languages
            # (languages.py) - OPTIONS_HTML's sourceLang field used to send
            # raw codes directly from a 3-option <select> (a real identity
            # map), but is now an <input list=...> holding a display name
            # the user typed or picked, matching every other mapped field.
            "stt_source_lang_map": stt_lang_name_to_code,
            "realtime_stt_final_model_var": tk.StringVar(
                master=v,
                value=final_model_rev_map.get(self.realtime_stt_final_model, REALTIME_STT_FINAL_MODEL_OPTIONS[-1][0]),
            ),
            "realtime_stt_final_model_map": final_model_map,
            "realtime_stt_realtime_model_var": tk.StringVar(
                master=v,
                value=realtime_model_rev_map.get(
                    self.realtime_stt_realtime_model, REALTIME_STT_REALTIME_MODEL_OPTIONS[0][0]
                ),
            ),
            "realtime_stt_realtime_model_map": realtime_model_map,
            "realtime_stt_silero_var": tk.DoubleVar(
                master=v, value=self.realtime_stt_silero_sensitivity
            ),
        }
        self._translation_vars = {
            "enable_translation_var": tk.BooleanVar(master=v, value=self.translation_enabled),
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
                value=nllb_lang_code_to_name.get(
                    self.local_nllb_target_lang,
                    nllb_lang_options[0][0] if nllb_lang_options else "",
                ),
            ),
            # Same real finding as stt_source_lang_map above -
            # _apply_translation_vars silently no-ops this whole field
            # without a companion map key. Now a real display-name-to-
            # FLORES-code map covering all 200 languages (languages.py) -
            # OPTIONS_HTML's nllbTargetLang field used to send raw codes
            # directly from a 2-option <select> (a real identity map), but
            # is now an <input list=...> holding a display name.
            "local_nllb_target_lang_map": nllb_lang_name_to_code,
            "local_nllb_max_chars_var": tk.IntVar(master=v, value=self.local_nllb_max_chars),
        }
        self._advanced_vars = {
            "logging_mode_var": tk.StringVar(
                master=v, value=logging_mode_rev_map.get(self.logging_mode, "Normal")
            ),
            "logging_mode_map": logging_mode_map,
            "start_with_windows_var": tk.BooleanVar(master=v, value=self.start_with_windows),
            "cuda_directory_var": tk.StringVar(master=v, value=self.cuda_directory),
            "bad_words_en_text": tk.Text(v),
            "bad_words_es_text": tk.Text(v),
            "custom_vocab_en_text": tk.Text(v),
            "custom_vocab_es_text": tk.Text(v),
        }
        self._advanced_vars["bad_words_en_text"].insert(
            "1.0", ", ".join(sorted(self.bad_words_by_lang.get("en", [])))
        )
        self._advanced_vars["bad_words_es_text"].insert(
            "1.0", ", ".join(sorted(self.bad_words_by_lang.get("es", [])))
        )
        self._advanced_vars["custom_vocab_en_text"].insert(
            "1.0", ", ".join(self.custom_vocabulary_by_lang.get("en", []))
        )
        self._advanced_vars["custom_vocab_es_text"].insert(
            "1.0", ", ".join(self.custom_vocabulary_by_lang.get("es", []))
        )
        for widget in (
            self._advanced_vars["bad_words_en_text"],
            self._advanced_vars["bad_words_es_text"],
            self._advanced_vars["custom_vocab_en_text"],
            self._advanced_vars["custom_vocab_es_text"],
        ):
            widget.edit_modified(False)

        self._options_dirty_ctx = self._new_settings_dirty_context()
        self._collect_settings_vars_for_dirty_tracking(self._display_vars, self._options_dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(self._transcription_vars, self._options_dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(self._translation_vars, self._options_dirty_ctx)
        self._collect_settings_vars_for_dirty_tracking(self._advanced_vars, self._options_dirty_ctx)
        self._options_dirty_ctx["dirty_ready"] = True
        self._options_dirty_ctx["applied_snapshot"] = self._capture_settings_snapshot(self._options_dirty_ctx)

        # Same real side effect _build_translation_section's own
        # maybe_start_nllb_prewarm has at construction time (settings_ui_
        # mixin.py) - translation is opt-in, so if it's off there is
        # nothing to check/download, and _mark_startup_translation_ready()
        # must still fire immediately or the startup overlay would wait
        # forever on a check that will never run (the real method's own
        # comment says exactly this). If translation IS on, kick off the
        # real cache-check chain instead, which eventually marks it ready
        # itself (success or failure - _set_local_nllb_status's whole
        # design is a terminal-state gate, not a success gate).
        if self.translation_enabled and self.nllb_status not in (
            "Checking", "Downloading", "Loading", "Ready",
        ):
            self._start_local_nllb_cache_check(
                self._local_nllb_config_from_vars(
                    self._translation_vars["local_nllb_model_name_var"],
                    self._translation_vars["local_nllb_device_var"],
                    self._translation_vars["local_nllb_max_chars_var"],
                    model_name_map=self._translation_vars["local_nllb_model_name_map"],
                ),
                prompt_if_missing=True,
            )
        elif not self.translation_enabled:
            self._set_local_nllb_status(
                "Not selected",
                "Translation is off. Enable it above to check or download the Local NLLB model.",
            )
            self._mark_startup_translation_ready()

        geometry_kwargs = self._restored_window_geometry(self.options_geometry, 680, 760)
        # __CLEAR_SECONDS_MIN__/__CLEAR_SECONDS_MAX__ substituted from the
        # real shared constants (app_constants.py) rather than hardcoded a
        # second time in OPTIONS_HTML's own markup - the field previously
        # declared a hardcoded 5-3600 range with no relationship to the
        # actual 1-60 range _apply_display_vars enforces (settings_logic_
        # mixin.py, via _coerce_int_range/CLEAR_DISPLAY_INACTIVITY_MIN/MAX),
        # so a value inside the field's own stated bounds but outside 1-60
        # got silently clamped on Apply with the input never reflecting it.
        options_html = (
            OPTIONS_HTML
            .replace("__THEME_CSS__", self._theme_css_declaration())
            .replace("__CLEAR_SECONDS_MIN__", str(self.CLEAR_DISPLAY_INACTIVITY_MIN))
            .replace("__CLEAR_SECONDS_MAX__", str(self.CLEAR_DISPLAY_INACTIVITY_MAX))
        )
        window = webview.create_window(
            "Rhema Options",
            html=options_html,
            js_api=_OptionsApi(self),
            background_color=self._settings_palette()["window_bg"],
            hidden=hidden,
            **geometry_kwargs,
        )
        self._options_window = window
        # Same real size/position/maximized-state persistence as the
        # Controller window above (settings_window) - previously left
        # None, so every Options resize/move/maximize was silently
        # discarded on close.
        self.options_window = PywebviewGeometryAdapter(window)
        self._options_dirty_ctx["options_window"] = window
        # Real behavior is settings_ui_mixin.py's
        # options_window.protocol("WM_DELETE_WINDOW", options_window.withdraw)
        # - Options is a sub-dialog reached from Controller > File > Options,
        # not one of the app's two main windows, so closing it should only
        # hide it (build_web_options's own reuse branch above shows/restores
        # the same window next time rather than rebuilding it). Wiring this
        # to self.on_closing like the Controller/Output windows do would
        # tear down the whole app just from closing this dialog.
        # A closing handler must return exactly False to cancel the close
        # (webview/event.py's Event.set() only treats a literal False return
        # as "cancel" - winforms.py's on_closing then sets args.Cancel);
        # window.hide's own return value doesn't qualify, so hide-then-
        # return-False has to be one handler, not window.hide by itself.
        def _hide_options_window():
            window.hide()
            return False

        window.events.closing += _hide_options_window
        window.events.shown += lambda: self.apply_dark_title_bar(
            window, dark=(self.ui_theme == "dark")
        )
        # Same events.shown timing requirement as apply_dark_title_bar
        # right above. Fires even for a hidden window once it's actually
        # shown later (the eager hidden-startup build, or a real File >
        # Options click), not just on first construction.
        if self.options_maximized:
            def _maximize_options():
                try:
                    window.maximize()
                except Exception:
                    pass
            window.events.shown += _maximize_options
        return window

    def _find_options_var(self, name):
        for mapping in (
            self._display_vars, self._transcription_vars,
            self._translation_vars, self._advanced_vars,
        ):
            value = mapping.get(name)
            if isinstance(value, tk.Variable):
                return value
        return None

    def options_set_var(self, name, value):
        var = self._find_options_var(name)
        if var is None:
            return {"ok": False, "error": f"unknown var {name!r}"}
        var.set(value)
        return {"ok": True, "dirty": bool(self._options_dirty_ctx["dirty_value"])}

    def options_set_text(self, name, value):
        widget = self._advanced_vars.get(name)
        if not isinstance(widget, tk.Text):
            return {"ok": False, "error": f"unknown text field {name!r}"}
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        self._var_interpreter.root.update()
        return {"ok": True, "dirty": bool(self._options_dirty_ctx["dirty_value"])}

    def options_list(self):
        return {
            "realtime_stt_final_model": [name for name, _code in REALTIME_STT_FINAL_MODEL_OPTIONS],
            "realtime_stt_realtime_model": [name for name, _code in REALTIME_STT_REALTIME_MODEL_OPTIONS],
            "local_nllb_model_name": [name for name, _code in NLLB_MODEL_NAME_OPTIONS],
            "logging_mode": [name for name, _code in LOGGING_MODE_OPTIONS],
            "stt_source_lang": [name for name, _code in self._stt_language_options],
            "local_nllb_target_lang": [name for name, _code in self._nllb_lang_options],
            "audio_device": list(self.devices),
            "monitor_labels": self._display_vars["monitor_labels"],
        }

    def options_current_values(self):
        return {
            "ui_theme": "Dark" if self.ui_theme == "dark" else "Light",
            "max_lines": self.max_lines,
            "video_max_lines": self.video_max_lines,
            "monitor_var": self._display_vars["monitor_var"].get(),
            "settings_monitor_var": self._display_vars["settings_monitor_var"].get(),
            "bg_color": self.bg_color,
            "text_color": self.text_color,
            "lock_output_focus": self.lock_output_focus,
            "clear_display_on_inactivity": self.clear_display_on_inactivity,
            "clear_display_inactivity_seconds": self.clear_display_inactivity_seconds,
            "video_feed_enabled": self.video_feed_enabled,
            "video_caption_bar_alpha": self.video_caption_bar_alpha * 100,
            "show_interim_text": self.show_interim_text,
            "stt_device": self.stt_device,
            "source_lang": self.source_lang,
            "realtime_stt_final_model": self.realtime_stt_final_model,
            "realtime_stt_realtime_model": self.realtime_stt_realtime_model,
            "realtime_stt_silero_sensitivity": self.realtime_stt_silero_sensitivity,
            "translation_enabled": self.translation_enabled,
            "local_nllb_model_name": self.local_nllb_model_name,
            "local_nllb_device": self.local_nllb_device,
            "local_nllb_target_lang": self.local_nllb_target_lang,
            "local_nllb_max_chars": self.local_nllb_max_chars,
            "nllb_status": self.nllb_status,
            "logging_mode": self.logging_mode,
            "start_with_windows": self.start_with_windows,
            "cuda_directory": self.cuda_directory,
            "bad_words_en": sorted(self.bad_words_by_lang.get("en", [])),
            "bad_words_es": sorted(self.bad_words_by_lang.get("es", [])),
            "custom_vocab_en": self.custom_vocabulary_by_lang.get("en", []),
            "custom_vocab_es": self.custom_vocabulary_by_lang.get("es", []),
            "preferred_device_label": self.preferred_device_label,
            "video_device_label": (
                self._video_device_label(self.video_device_index)
                if self.video_device_index is not None else ""
            ),
            "display": {
                "realtime_stt_final_model": self._final_model_rev_map.get(
                    self.realtime_stt_final_model, REALTIME_STT_FINAL_MODEL_OPTIONS[-1][0]
                ),
                "realtime_stt_realtime_model": self._realtime_model_rev_map.get(
                    self.realtime_stt_realtime_model, REALTIME_STT_REALTIME_MODEL_OPTIONS[0][0]
                ),
                "local_nllb_model_name": self._nllb_model_name_rev_map.get(
                    self.local_nllb_model_name, NLLB_MODEL_NAME_OPTIONS[0][0]
                ),
                "logging_mode": self._logging_mode_rev_map.get(self.logging_mode, "Normal"),
                "stt_source_lang": self._stt_source_lang_rev_map.get(
                    self.source_lang or "auto", "Auto-detect"
                ),
                "local_nllb_target_lang": self._nllb_target_lang_rev_map.get(
                    self.local_nllb_target_lang,
                    self._nllb_lang_options[0][0] if self._nllb_lang_options else "",
                ),
            },
            "dirty": bool(self._options_dirty_ctx["dirty_value"]),
        }

    def options_apply(self):
        try:
            self._apply_settings_from_controller(
                self._display_vars, {}, self._transcription_vars,
                self._translation_vars, self._advanced_vars, self._options_dirty_ctx,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "dirty": bool(self._options_dirty_ctx["dirty_value"]),
            "values": self.options_current_values(),
        }

    def options_select_audio_device(self, label):
        if label not in self.devices:
            return {"ok": False}
        new_index = self.devices.index(label)
        # Matches the real _handle_audio_device_change (settings_ui_
        # mixin.py): without this, picking a different microphone here
        # updated settings.json and the dropdown immediately, but
        # RealtimeSTT/the level meter (which only read microphone_index at
        # recorder-construction time) kept capturing from the PREVIOUS
        # device for the rest of the running session - the switch only
        # ever took effect after a full app restart.
        device_changed = new_index != self.microphone_index
        self.microphone_index = new_index
        self.preferred_device_label = label
        self.save_settings()
        if device_changed:
            self._request_capture_restart()
            self._request_audio_level_stream_restart()
        return {"ok": True}

    def options_select_video_device(self, label):
        # Same crash-avoidance guard as _refresh_video_devices above - this
        # is a second, independent call site into the same not-safe-for-
        # concurrent-probing enumerate_video_devices(), reachable any time
        # the user changes the dropdown while a Refresh scan is in flight.
        if getattr(self, "_video_scan_in_progress", False):
            return {"ok": False}
        try:
            available = self.enumerate_video_devices()
        except Exception:
            available = []
        for index in available:
            if self._video_device_label(index) == label:
                self.video_device_index = index
                self.save_settings()
                # Real bug, not just missing polish: _apply_display_vars
                # (settings_logic_mixin.py, shared with the Tk app) re-
                # derives video_device_index from display_vars["video_
                # device_var"] on EVERY Apply - without updating that
                # StringVar here too, this method's own write above took
                # effect immediately but then got silently reverted back to
                # whatever device was selected when Options was first
                # opened the next time Apply ran (stop_video_feed/
                # start_video_feed still fired, just against the stale
                # index) - live symptom: picking OBS's Virtual Camera here,
                # closing OBS, and still seeing video, because Apply had
                # already reverted the capture back to the previously
                # selected physical webcam.
                if getattr(self, "_display_vars", None) is not None:
                    video_device_var = self._display_vars.get("video_device_var")
                    if video_device_var is not None:
                        video_device_var.set(label)
                return {"ok": True}
        return {"ok": False}
