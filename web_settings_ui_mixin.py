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
open_settings() builds in settings_ui_mixin.py, plus the real File/About
native menu bar from _build_menu_bar(). Deliberately NOT the Options
dialog (Phase 6) and NOT the startup loading overlay (Phase 7) - those
stay real-but-unbuilt for this phase, same as Phase 3/4 left the Controller
itself unbuilt.

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
img tag, a js_api call) but its actual capture function is DISABLED - see
_capture_output_snapshot_data_uri's own comment for why: the planned
bbox/BitBlt technique was verified live to capture whatever window
actually occludes that screen region, not the Output window specifically,
which is a real information-disclosure risk on an ordinary desktop, not an
edge case. No replacement technique has been implemented yet.
"""

import json
import tkinter as tk
import webbrowser

from settings_logic_mixin import SettingsLogicMixin
from settings_ui_mixin import DONATE_URL
from webview_bridge import TkVariableInterpreter

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
:root{color-scheme:dark}
html,body{margin:0;height:100%;background:#1E2228;color:#E5E7EB;
  font:14px/1.4 "Segoe UI",system-ui,sans-serif;overflow:hidden}
#wrap{display:flex;flex-direction:column;height:100vh;box-sizing:border-box;padding:14px;gap:12px}
h2{margin:0;font-size:16px;color:#F3F4F6}
#previewBox{flex:1;min-height:0;border:1px solid #3A3F47;border-radius:6px;
  background:#000;display:flex;align-items:center;justify-content:center;overflow:hidden}
#preview{max-width:100%;max-height:100%;display:block}
#previewPlaceholder{color:#6B7280;font-size:13px}
#statusSection{border:1px solid #3A3F47;border-radius:6px;padding:10px 12px;flex:0 0 auto}
#statusSection .label{color:#9CA3AF;font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}
#status{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#latency{color:#9CA3AF;margin-top:4px}
#meterRow{display:flex;align-items:center;gap:8px;margin-top:8px}
#meterTrack{flex:1;height:12px;background:#1A1A1A;border:1px solid #3A3A3A;border-radius:3px;overflow:hidden}
#meterFill{height:100%;width:0;background:#5B8FF7;transition:width 70ms linear}
#buttonRow{display:flex;gap:10px;margin-top:10px}
button{flex:1;padding:8px 10px;border:none;border-radius:5px;background:#5B8FF7;color:#fff;
  font:inherit;font-weight:600;cursor:pointer}
button:hover{background:#4A7FEA}
button:active{background:#3E6FD8}
#startupOverlay{position:fixed;inset:0;background:#1E2228;display:flex;align-items:center;
  justify-content:center;flex-direction:column;z-index:1000}
#startupOverlay.hidden{display:none}
.spinner{width:40px;height:40px;border-radius:50%;border:4px solid #3A3F4B;
  border-top-color:#5B8FF7;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
#startupText{margin-top:14px;font-size:14px;font-weight:600}
</style></head><body>
<div id="startupOverlay">
  <div class="spinner"></div>
  <div id="startupText">Loading...</div>
</div>
<div id="wrap">
  <h2>Rhema Controller</h2>
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
        self._app._refresh_video_devices()
        return {"ok": True}


OPTIONS_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema Options</title>
<style>
:root{--bg:#1E2228;--card:#262A33;--text:#E5E7EB;--muted:#9CA3AF;--border:#3A3F4B;--accent:#5B8FF7;--dirty:#E0A458}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
 font:14px/1.5 "Segoe UI","Segoe UI Variable Text",system-ui,sans-serif;padding:24px;overflow-y:auto}
.card{max-width:640px;margin:0 auto 24px;background:var(--card);border:1px solid var(--border);
 border-radius:12px;padding:20px}
h1{font-size:15px;margin:0 0 4px;color:var(--text)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:18px 0 2px}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 0;
 border-bottom:1px solid #2F343E}
.row:last-of-type{border-bottom:none}
label{font-size:13px}
input[type=number]{width:80px;background:#14171C;border:1px solid var(--border);color:var(--text);
 border-radius:6px;padding:4px 8px}
input[type=color]{width:40px;height:26px;border:none;background:none;padding:0}
select{background:#14171C;border:1px solid var(--border);color:var(--text);border-radius:6px;padding:4px 8px;
 max-width:280px}
input[type=text]{background:#14171C;border:1px solid var(--border);color:var(--text);border-radius:6px;
 padding:4px 8px}
textarea{width:100%;background:#14171C;border:1px solid var(--border);color:var(--text);border-radius:6px;
 padding:6px 8px;font:12px/1.4 monospace;resize:vertical}
input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent)}
#apply{margin-top:16px;width:100%;padding:10px;border-radius:8px;border:none;font-size:13px;font-weight:600;
 background:#3A3F4B;color:#6B7280;cursor:not-allowed}
#apply.dirty{background:var(--dirty);color:#1E2228;cursor:pointer}
#status{margin-top:12px;font-size:12px;color:var(--muted);white-space:pre-wrap}
.help{display:inline-block;margin-left:6px;width:15px;height:15px;border-radius:50%;
 background:#3A3F4B;color:var(--text);text-align:center;font-size:10px;font-weight:700;
 line-height:15px;cursor:help;user-select:none}
#tooltip{position:fixed;background:#111111;color:#fff;border:1px solid #333;
 padding:4px 6px;font-size:12px;max-width:320px;line-height:1.3;z-index:2000;
 pointer-events:none;display:none}
</style></head><body>
<div class="card">
  <h1>Rhema Options</h1>

  <h2>Display</h2>
  <div class="row"><label>Max caption lines<span class="help" data-tip="Maximum number of translated lines kept on screen.">?</span></label><input type="number" id="lines" min="4" max="10"></div>
  <div class="row"><label>Background color<span class="help" data-tip="Background color for the output overlay and preview. Also tints the caption bar behind the video overlay, if enabled.">?</span></label><input type="color" id="bg"></div>
  <div class="row"><label>Lock output window focus</label><input type="checkbox" id="lockFocus"></div>
  <div class="row"><label>Clear display on inactivity</label><input type="checkbox" id="clear"></div>
  <div class="row"><label>&nbsp;&nbsp;...after N seconds</label><input type="number" id="clearSeconds" min="5" max="3600"></div>
  <div class="row"><label>Video overlay enabled</label><input type="checkbox" id="videoEnabled"></div>
  <div class="row"><label>Camera device<span class="help" data-tip="Camera index for the OBS Virtual Camera. Click Refresh after starting OBS's Virtual Camera if it isn't listed yet.">?</span></label><select id="videoDevice"></select></div>
  <div class="row"><label>Caption bar opacity (%)<span class="help" data-tip="How solid the bar behind the caption lines looks, using the Background Color above. 0% is fully see-through, 100% is a solid bar.">?</span></label><input type="number" id="videoAlpha" min="0" max="100"></div>

  <h2>Audio</h2>
  <div class="row"><label>Microphone<span class="help" data-tip="Input device used for speech capture.">?</span></label><select id="audioDevice"></select></div>

  <h2>Transcription</h2>
  <div class="row"><label>Show live interim text</label><input type="checkbox" id="interim"></div>
  <div class="row"><label>STT device<span class="help" data-tip="Auto uses CUDA when available, otherwise CPU.">?</span></label>
    <select id="device"><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="auto">Auto</option></select></div>
  <div class="row"><label>Source language</label>
    <select id="sourceLang"><option value="auto">Auto-detect</option><option value="en">English</option><option value="es">Spanish</option></select></div>
  <div class="row"><label>Final model<span class="help" data-tip="Accurate faster-whisper model used after each utterance ends. Larger models are more accurate but need more VRAM and take longer per utterance.">?</span></label><select id="finalModel"></select></div>
  <div class="row"><label>Realtime model<span class="help" data-tip="Fast model used internally every ~0.2s to drive dynamic silence detection. Not shown on screen - kept small so it doesn't compete with the final model for GPU time.">?</span></label><select id="realtimeModel"></select></div>
  <div class="row"><label>Voice sensitivity<span class="help" data-tip="How easily speech is detected. Lower catches softer/quieter speech; higher ignores background noise better.">?</span></label><input type="number" id="silero" min="0.1" max="0.9" step="0.05"></div>

  <h2>Translation (Local NLLB)</h2>
  <div class="row"><label>Enable translation</label><input type="checkbox" id="enableTranslation"></div>
  <div class="row"><label>Model name<span class="help" data-tip="Hugging Face model id for local text translation. Larger models translate more accurately but need more VRAM/RAM and disk space, and run slower.">?</span></label><select id="nllbModel"></select></div>
  <div class="row"><label>Device<span class="help" data-tip="Auto uses CUDA when available, otherwise CPU.">?</span></label>
    <select id="nllbDevice"><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="auto">Auto</option></select></div>
  <div class="row"><label>Target language<span class="help" data-tip="Language the translated transcript is produced in. Type to search all 200 languages.">?</span></label>
    <select id="nllbTargetLang"><option value="eng_Latn">English</option><option value="spa_Latn">Spanish</option></select></div>
  <div class="row"><label>Max chars per chunk<span class="help" data-tip="Long transcripts are split by paragraph, sentence, or length before translation.">?</span></label><input type="number" id="nllbMaxChars" min="250" max="20000" step="250"></div>
  <div id="nllbStatus" style="color:#9CA3AF;font-size:12px;margin:4px 0 8px">nllb status: --</div>

  <h2>Advanced</h2>
  <div class="row"><label>Logging mode<span class="help" data-tip="Normal keeps status/error and finalized output logs. Debug adds pipeline traces. Evaluation adds raw transcribed/translated comparison logs. Full enables all logs.">?</span></label><select id="loggingMode"></select></div>
  <div class="row"><label>Start app when Windows starts</label><input type="checkbox" id="startWithWindows"></div>
  <div class="row"><label>CUDA directory<span class="help" data-tip="Optional Windows path used to find CUDA Toolkit 12.x and cuDNN 9.x DLLs for local faster-whisper GPU mode. Select the CUDA toolkit folder or its bin folder.">?</span></label><input type="text" id="cudaDirectory" style="width:280px"></div>
  <div class="row"><label>Bad words (English, comma-separated)<span class="help" data-tip="Words to omit from the output.">?</span></label></div>
  <textarea id="badWordsEn" rows="2"></textarea>
  <div class="row"><label>Bad words (Spanish, comma-separated)</label></div>
  <textarea id="badWordsEs" rows="2"></textarea>
  <div class="row"><label>Custom vocabulary (English, comma-separated)<span class="help" data-tip="Words or phrases to bias recognition and preserve capitalization.">?</span></label></div>
  <textarea id="vocabEn" rows="2"></textarea>
  <div class="row"><label>Custom vocabulary (Spanish, comma-separated)</label></div>
  <textarea id="vocabEs" rows="2"></textarea>

  <button id="apply" disabled>Apply</button>
  <div id="status">loading current settings...</div>
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
  lines: {varName: 'lines_var', kind: 'int'},
  bg: {varName: 'bg_color_var', kind: 'str'},
  lockFocus: {varName: 'lock_output_focus_var', kind: 'bool'},
  clear: {varName: 'clear_display_on_inactivity_var', kind: 'bool'},
  clearSeconds: {varName: 'clear_display_inactivity_seconds_var', kind: 'int'},
  videoEnabled: {varName: 'video_feed_enabled_var', kind: 'bool'},
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
  document.getElementById('nllbStatus').textContent = 'nllb status: ' + v.nllb_status
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

  document.getElementById('lines').value = v.max_lines
  document.getElementById('bg').value = v.bg_color
  document.getElementById('lockFocus').checked = v.lock_output_focus
  document.getElementById('clear').checked = v.clear_display_on_inactivity
  document.getElementById('clearSeconds').value = v.clear_display_inactivity_seconds
  document.getElementById('videoEnabled').checked = v.video_feed_enabled
  document.getElementById('videoAlpha').value = v.video_caption_bar_alpha
  document.getElementById('interim').checked = v.show_interim_text
  document.getElementById('device').value = v.stt_device
  document.getElementById('sourceLang').value = v.source_lang
  document.getElementById('silero').value = v.realtime_stt_silero_sensitivity
  document.getElementById('enableTranslation').checked = v.translation_enabled
  document.getElementById('nllbDevice').value = v.local_nllb_device
  document.getElementById('nllbTargetLang').value = v.local_nllb_target_lang
  document.getElementById('nllbMaxChars').value = v.local_nllb_max_chars
  document.getElementById('nllbStatus').textContent = 'nllb status: ' + v.nllb_status
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
  await pywebview.api.refresh_devices()

  setDirty(v.dirty)
  statusEl.textContent = 'Loaded current settings.'
})
</script></body></html>
"""


class WebSettingsUIMixin(SettingsLogicMixin):
    def build_web_controller(self):
        """Analogous to open_settings() - called once at startup from
        main_webview.py after the Output window is loaded (needs
        self._window to already exist, for the Preview capture and the
        cross-window Toggle Fullscreen call)."""
        import webview
        from webview.menu import Menu, MenuAction, MenuSeparator

        menu = [
            Menu(
                "File",
                [
                    MenuAction("Hardware Autodetect", self._run_hardware_autodetect_menu_action),
                    MenuAction("Options", self._show_options_dialog),
                ],
            ),
            Menu(
                "About",
                [
                    MenuAction("About Rhema", self._show_about_popup),
                    MenuSeparator(),
                    MenuAction("Check for Updates", lambda: self.check_for_updates(manual=True)),
                    MenuAction("Donate", self._show_donate_popup),
                    MenuAction("Feature Request", self._open_feature_request_page),
                ],
            ),
        ]

        # Deliberately WITHOUT menu=... here - the real _build_menu_bar()
        # only attaches the menu once app_startup_ready is true
        # ("same intent as the loading overlay itself"), and pywebview's
        # public API has no way to attach a menu after window creation.
        # _hide_startup_loading_overlay below reaches past that public API
        # (BrowserView.instances[uid].set_window_menu(...), the same
        # unsupported-but-real path every other cross-window HWND/Controls
        # touch in this port already uses) - proved in
        # experiments/web_startup_overlay.py before relying on it here.
        controller_window = webview.create_window(
            "Rhema Controller",
            html=CONTROLLER_HTML,
            width=420,
            height=560,
            background_color="#1E2228",
            js_api=_ControllerApi(self),
        )
        self._controller_window = controller_window
        self._controller_menu = menu
        controller_window.events.closing += self.on_closing
        # shown (not immediately after create_window) - window.native's
        # real WinForms Form isn't guaranteed realized until then, and
        # apply_dark_title_bar's hwnd_for() needs a real Handle to Invoke()
        # against.
        controller_window.events.shown += lambda: self.apply_dark_title_bar(
            controller_window, dark=(self.ui_theme == "dark")
        )
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
        try:
            from webview.platforms.winforms import BrowserView

            browser_view = BrowserView.instances.get(self._controller_window.uid)
            if browser_view is not None:
                browser_view.set_window_menu(self._controller_menu)
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
        self.build_web_options()
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
        proceed = self._confirm_yes_no(
            "Support Rhema",
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
            "deductible.\n\n"
            "Open the donation page now?",
        )
        if proceed:
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

    # ------------------------------------------------------------------ #
    # Preview / Output Snapshot - DISABLED, not just unimplemented.
    #
    # The original plan for this (bbox/BitBlt against the Output window's
    # own real HWND, resolved via webview_bridge.hwnd_for/real_window_rect
    # rather than any PID/title lookup) was verified LIVE during this
    # phase to be unsafe on a real, actively-used desktop: ImageGrab.grab(
    # bbox=...) captures whatever is actually visible on screen at those
    # coordinates, not the specific window the HWND was resolved from.
    # Confirmed twice, independently, against small self-contained test
    # windows whose HWND/rect were resolved correctly and unambiguously:
    # both captures returned a DIFFERENT, unrelated real window that
    # happened to be occluding that screen region at the moment of
    # capture, once showing real client/business data and once showing
    # what looked like the user's own ticketing system - neither of which
    # this app had any business capturing. This is not an edge case to
    # guard against with a try/except; it's the technique's actual,
    # ordinary behavior whenever anything else overlaps that screen
    # region, which is entirely normal on a real desktop.
    #
    # The real Tk app's own technique (ImageGrab.grab(window=self.root.
    # winfo_id()), i.e. PrintWindow) does NOT have this problem - it asks
    # a SPECIFIC window to render itself, occlusion-independent - but
    # experiments/web_controller_window.py already found PrintWindow
    # returns solid black against a real WebView2 surface. So neither
    # technique this port has tried is both safe AND working against
    # WebView2 content, and no further screen-capture technique was
    # attempted this session given what the first two attempts exposed.
    # A real fix needs a genuinely occlusion-independent capture path (the
    # Windows DWM Thumbnail API - the same mechanism Alt+Tab/Task View use
    # - is the most promising untried option) before this feature ships,
    # not a quick swap back to bbox capture.
    #
    # Left wired up end-to-end (JS polling, the img tag, the js_api call)
    # so only this one function needs to change once a safe technique is
    # found - the Controller window just shows its "Capturing snapshot..."
    # placeholder indefinitely in the meantime, which is honest about the
    # feature being unavailable rather than silently risky.
    # ------------------------------------------------------------------ #
    def _capture_output_snapshot_data_uri(self):
        return None

    # ------------------------------------------------------------------ #
    # Phase 6: chrome hooks the shared SettingsLogicMixin methods call by
    # name (see settings_logic_mixin.py's own module docstring for the
    # full accounting of which real chrome touches survive inside "logic").
    # ------------------------------------------------------------------ #
    def _show_hardware_autodetect_result(self, text):
        self._show_info_dialog("Hardware Autodetect", text)

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
        if getattr(self, "_options_window", None) is None:
            return
        try:
            self.devices = self.get_audio_devices()
            window = self._options_window
            window.evaluate_js(
                "fillSelect('audioDevice', %s, %s)"
                % (json.dumps(self.devices), json.dumps(self.preferred_device_label))
            )
        except Exception:
            pass

    def _refresh_video_devices(self):
        # Same real-app trigger point as _refresh_audio_devices (called
        # after Apply); full rewrite per the port plan, same reasoning -
        # the real method interleaves a worker thread with tk._setit/menu
        # manipulation with no Web equivalent.
        if getattr(self, "_options_window", None) is None:
            return
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

        self._display_vars = {
            "lines_var": tk.IntVar(master=v, value=self.max_lines),
            "video_lines_var": tk.IntVar(master=v, value=self.video_max_lines),
            "bg_color_var": tk.StringVar(master=v, value=self.bg_color),
            "text_color_var": tk.StringVar(master=v, value=self.text_color),
            "monitor_labels": ["Monitor 1"],
            "monitor_var": tk.StringVar(master=v, value="Monitor 1"),
            "settings_monitor_var": tk.StringVar(master=v, value="Monitor 1"),
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
            "stt_source_lang_var": tk.StringVar(master=v, value=self.source_lang or "auto"),
            # Phase 12 QA pass finding: _apply_transcription_vars
            # (settings_logic_mixin.py) only applies this var at all if a
            # companion "stt_source_lang_map" key exists in the dict
            # (_optional_mapped_setting returns the unchanged current
            # value otherwise, silently no-oping the whole field). Every
            # OTHER mapped field here has a real display-name-to-code map
            # because their <select> options show a friendly display name;
            # this one's <select> already sends the raw code directly
            # (OPTIONS_HTML's sourceLang has hardcoded value=auto/en/es
            # options), so this is a real identity map, not a stand-in -
            # exactly what a source_lang_var without a display layer needs.
            "stt_source_lang_map": {"auto": "auto", "en": "en", "es": "es"},
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
            "local_nllb_target_lang_var": tk.StringVar(master=v, value=self.local_nllb_target_lang),
            # Same real finding as stt_source_lang_map above -
            # _apply_translation_vars silently no-ops this whole field
            # without a companion map key. OPTIONS_HTML's nllbTargetLang
            # <select> already sends the real FLORES code directly, so
            # this is genuinely an identity map, not a placeholder.
            "local_nllb_target_lang_map": {"eng_Latn": "eng_Latn", "spa_Latn": "spa_Latn"},
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

        window = webview.create_window(
            "Rhema Options",
            html=OPTIONS_HTML,
            js_api=_OptionsApi(self),
            width=680,
            height=760,
            background_color="#1E2228",
            hidden=hidden,
        )
        self._options_window = window
        self._options_dirty_ctx["options_window"] = window
        window.events.closing += self.on_closing
        window.events.shown += lambda: self.apply_dark_title_bar(
            window, dark=(self.ui_theme == "dark")
        )
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
            "stt_source_lang": ["auto", "en", "es"],
            "local_nllb_target_lang": ["eng_Latn", "spa_Latn"],
            "audio_device": list(self.devices),
        }

    def options_current_values(self):
        return {
            "max_lines": self.max_lines,
            "video_max_lines": self.video_max_lines,
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
        self.microphone_index = self.devices.index(label)
        self.preferred_device_label = label
        self.save_settings()
        return {"ok": True}

    def options_select_video_device(self, label):
        try:
            available = self.enumerate_video_devices()
        except Exception:
            available = []
        for index in available:
            if self._video_device_label(index) == label:
                self.video_device_index = index
                self.save_settings()
                return {"ok": True}
        return {"ok": False}
