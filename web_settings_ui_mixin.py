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

toggle_fullscreen here is a deliberate Phase-5-scoped stand-in, not the
real thing: the actual enter_fullscreen/exit_fullscreen (monitor_mixin.py)
do real per-monitor placement math against Tk window geometry with no
analog built yet - that's Phase 8's job. This override exists to prove the
CROSS-WINDOW PLUMBING (a Controller button reaching into the Output
window's own state) works, which is this phase's actual scope - it calls
pywebview's own window.toggle_fullscreen(), confirmed (platforms/
winforms.py) to already do its own internal InvokeRequired/Invoke()
marshaling, so no extra wrapping is needed here.

The Preview/Output Snapshot feature is wired up end-to-end (JS polling, an
img tag, a js_api call) but its actual capture function is DISABLED - see
_capture_output_snapshot_data_uri's own comment for why: the planned
bbox/BitBlt technique was verified live to capture whatever window
actually occludes that screen region, not the Output window specifically,
which is a real information-disclosure risk on an ordinary desktop, not an
edge case. No replacement technique has been implemented yet.
"""

import json
import webbrowser

from settings_ui_mixin import DONATE_URL

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
</style></head><body>
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


class WebSettingsUIMixin:
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
                    # Real Hardware Autodetect/Options wiring is Phase 6's
                    # job (needs the Options dialog/its vars dicts, which
                    # don't exist yet) - stubbed rather than silently
                    # absent, so the menu structure matches the real app
                    # now and only the ACTION swaps in later.
                    MenuAction("Hardware Autodetect", self._menu_stub_autodetect),
                    MenuAction("Options", self._menu_stub_options),
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

        controller_window = webview.create_window(
            "Rhema Controller",
            html=CONTROLLER_HTML,
            width=420,
            height=560,
            background_color="#1E2228",
            menu=menu,
            js_api=_ControllerApi(self),
        )
        self._controller_window = controller_window
        controller_window.events.closing += self.on_closing
        return controller_window

    # ------------------------------------------------------------------ #
    # Menu stubs - Phase 6 replaces these with the real thing.
    # ------------------------------------------------------------------ #
    def _menu_stub_autodetect(self):
        self._log_status("Hardware Autodetect isn't wired up yet in this preview build.")

    def _menu_stub_options(self):
        self._log_status("The Options window isn't built yet in this preview build.")

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
        if self._window is None:
            return
        # window.toggle_fullscreen() already does its own InvokeRequired/
        # Invoke() marshaling internally (platforms/winforms.py) - unlike
        # a raw .native touch, this one is safe to call directly from any
        # thread, including the fresh thread pywebview spawns per menu/
        # button callback.
        self._window.toggle_fullscreen()

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
