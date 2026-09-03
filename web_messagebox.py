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

"""WebTranslationApp's override of LoggingMixin's _show_error_dialog/
_show_info_dialog/_confirm_yes_no.

_show_html_message_dialog (a themed pywebview popup, DIALOG_HTML below) is
the real path now - used by every ordinary call site (About, Donate, the
Check for Updates flow, the NLLB download confirm, etc.), same motivation
as the File/About menu bar's move off native WinForms chrome
(web_settings_ui_mixin.py's own module docstring): a native dialog has no
CSS hook at all, an HTML one is a handful of ordinary CSS rules.

Plain ctypes MessageBoxW is kept as a FALLBACK, not replaced outright, for
two real reasons that don't go away just because the HTML path now exists:

- The crash hook (LoggingMixin._handle_unhandled_exception) can fire
  before any window exists - sys.excepthook is installed as one of the
  first things __init__ does. _show_html_message_dialog's own
  self._controller_window is None check catches exactly that case and
  returns handled=False, so error/info/confirm all still degrade to a
  dialog that needs no window at all: a NULL owner HWND is a completely
  standard, supported MessageBoxW call and still produces a real, focused,
  modal system dialog.
- An unhandled exception could also fire AFTER the Controller window
  exists, from a genuinely broken state - spinning up a NEW pywebview
  window (webview.create_window, its own JS load, a blocking wait for a
  js_api callback) during exception handling has more moving parts than a
  single MessageBoxW call, any of which could itself fail or hang in a
  way a bare try/except around window creation doesn't fully cover (a
  window that's created but never finishes loading, for instance).
  _show_error_dialog passes a finite timeout into
  _show_html_message_dialog for exactly this reason - info/confirm (never
  called from the crash hook) block indefinitely, matching every dialog's
  real modal-until-dismissed contract, but error has an escape hatch back
  to native if the HTML dialog doesn't resolve in time.

Every existing call site (about a dozen, across web_settings_ui_mixin.py/
update_mixin.py/settings_logic_mixin.py) still just calls
self._show_error_dialog(...)/self._show_info_dialog(...)/
self._confirm_yes_no(...) exactly as before - none of them needed to
change, matching LoggingMixin's own docstring: these three methods exist
specifically so a non-Tk app can swap the dialog backend without touching
call sites.
"""

import ctypes

from webview_bridge import hwnd_for

_MB_OK = 0x00000000
_MB_YESNO = 0x00000004
_MB_ICONERROR = 0x00000010
_MB_ICONQUESTION = 0x00000020
_MB_ICONINFORMATION = 0x00000040
_IDYES = 6

_ACCENT_INFO = "#5B8FF7"
_ACCENT_ERROR = "#E5484D"

DIALOG_WIDTH = 440
# Bounds for the auto-sizing below - not a target to design messages
# against. 160 covers even a one-line message + buttons; 700 is a last-
# resort ceiling (falls back to internal scroll only past it) for some
# future message nobody has written yet, comfortably under any real
# screen's height.
_DIALOG_MIN_HEIGHT = 160
_DIALOG_MAX_HEIGHT = 700

# Static HTML/JS shell - all per-call content (title/message/buttons) is
# pushed in via one get_config() js_api call on 'pywebviewready', same
# pattern CONTROLLER_HTML/OPTIONS_HTML already use (web_settings_ui_mixin.py)
# rather than string-formatting untrusted-ish message text (curly quotes,
# apostrophes, etc. - the real About/Donate text has both) into the HTML
# itself. textContent, not innerHTML, so nothing in a message can inject
# markup.
#
# No fixed/flex height anywhere - #message is a plain block that grows
# with its text, and the window created for it starts hidden and is
# resized (then shown) to document.body.scrollHeight once the real
# content is in the DOM. A short "You're up to date" message and the
# long multi-paragraph Donate text need genuinely different window
# heights to both render without a scrollbar - guessing one fixed size
# for both was exactly the previous version's problem.
DIALOG_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema</title>
<style>
:root{color-scheme:dark}
html,body{margin:0;background:#1E2228;color:#E5E7EB;
  font:14px/1.5 "Segoe UI",system-ui,sans-serif;box-sizing:border-box}
#drag{height:10px;-webkit-app-region:drag}
#wrap{padding:0 22px 20px;box-sizing:border-box}
#accentBar{height:3px;border-radius:2px;margin-bottom:14px}
#title{font-size:15px;font-weight:700;color:#F3F4F6;margin-bottom:10px}
#message{white-space:pre-wrap;font-size:13px;color:#E5E7EB;line-height:1.6}
#buttonRow{display:flex;justify-content:flex-end;gap:10px;margin-top:16px;
  -webkit-app-region:no-drag}
.dlgBtn{padding:8px 18px;border:none;border-radius:5px;font:inherit;font-weight:600;
  cursor:pointer}
.dlgBtn.primary{background:#5B8FF7;color:#fff}
.dlgBtn.primary:hover{background:#4A7FEA}
.dlgBtn.secondary{background:#2A2F38;color:#E5E7EB}
.dlgBtn.secondary:hover{background:#333944}
</style></head><body>
<div id="drag"></div>
<div id="wrap">
  <div id="accentBar"></div>
  <div id="title"></div>
  <div id="message"></div>
  <div id="buttonRow"></div>
</div>
<script>
let escapeValue = null
window.addEventListener('pywebviewready', async () => {
  const cfg = await pywebview.api.get_config()
  document.title = cfg.title
  document.getElementById('title').textContent = cfg.title
  document.getElementById('message').textContent = cfg.message
  document.getElementById('accentBar').style.background = cfg.accent
  escapeValue = cfg.escape_value
  const row = document.getElementById('buttonRow')
  cfg.buttons.forEach((b) => {
    const btn = document.createElement('button')
    btn.className = 'dlgBtn ' + b.style
    btn.textContent = b.label
    btn.onclick = () => b.keepOpen ? pywebview.api.action(b.value) : pywebview.api.respond(b.value)
    row.appendChild(btn)
  })
  // Measure AFTER the buttons are in the DOM (their height counts too).
  // Deliberately NOT requestAnimationFrame (the original approach): a
  // window created hidden=True never gets a compositor/paint cycle while
  // hidden (confirmed live - Chromium suspends rAF entirely for a non-
  // visible surface, a well-known rAF-throttling behavior, not specific
  // to this app), so an rAF-gated call to resize_to_fit()/window.show()
  // NEVER fired - every themed dialog (About/Donate/Check-for-Updates/
  // NLLB-download-confirm/Hardware-Autodetect-result/etc.) silently
  // stayed hidden forever, with the calling thread parked in done.wait()
  // indefinitely. scrollHeight forces a synchronous layout reflow on its
  // own regardless of paint state (a well-documented, different browser
  // mechanism than rAF/painting), so it's accurate to read immediately -
  // no frame to wait for in the first place.
  pywebview.api.resize_to_fit(document.body.scrollHeight)
})
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') pywebview.api.respond(escapeValue)
})
</script></body></html>
"""


class _DialogApi:
    """Exposed to a DIALOG_HTML window's JS as `pywebview.api.*` - one
    fresh instance per dialog, closing over that dialog's own config/
    response callback/window rather than reaching into the app directly.
    window_holder (not the window itself) because the window doesn't
    exist yet when this Api instance has to be constructed - webview.
    create_window's own js_api= argument needs it up front."""

    def __init__(self, config, on_respond, window_holder, on_action=None):
        self._config = config
        self._on_respond = on_respond
        self._window_holder = window_holder
        self._on_action = on_action

    def get_config(self):
        return self._config

    def respond(self, value):
        self._on_respond(value)

    def action(self, value):
        # A button with keepOpen=True in its config (see kind == "donate"
        # below) routes here instead of respond() - fires a side effect
        # (opening the donate link) WITHOUT closing the dialog, unlike
        # every other button, so a user can act on it and still dismiss
        # the dialog afterward (or not) exactly like the real Tk Donate
        # popup's independent Donate/Close button pair.
        if self._on_action is not None:
            self._on_action(value)

    def resize_to_fit(self, height):
        # Created with hidden=True specifically so this is the FIRST time
        # the window becomes visible - resize-then-show means the user
        # never sees the placeholder size it was created at or a visible
        # snap to the real one.
        window = self._window_holder.get("window")
        if window is None:
            return
        clamped_height = max(_DIALOG_MIN_HEIGHT, min(int(height), _DIALOG_MAX_HEIGHT))
        try:
            window.resize(DIALOG_WIDTH, clamped_height)
        except Exception:
            pass
        try:
            window.show()
        except Exception:
            pass


def _hwnd_for_parent(parent):
    """`parent` is whatever a call site passed - None (no owner window,
    always safe), a real pywebview Window (resolved via the same
    Invoke()-marshaled hwnd_for() every other .native touch in this port
    uses), or an already-resolved int HWND. Never raises: a parent that
    can't be resolved just means an unowned (but still fully functional)
    dialog, not a crash inside the dialog code."""
    if parent is None:
        return 0
    if isinstance(parent, int):
        return parent
    try:
        return hwnd_for(parent)
    except Exception:
        return 0


class WebMessageBoxMixin:
    """Mixed into WebTranslationApp ahead of LoggingMixin in the MRO so
    these override LoggingMixin's Tk-default dialog methods without
    LoggingMixin itself needing any pywebview awareness."""

    def _show_html_message_dialog(self, title, message, kind, timeout=None, on_action=None):
        """Returns (handled, value). handled is False whenever the HTML
        dialog couldn't be shown or didn't resolve within `timeout` (None
        = block indefinitely) - callers fall back to native MessageBoxW in
        that case, exactly as if this method didn't exist. value is the
        clicked button's value (True/False for a confirm, None for info/
        error) once handled is True.

        self._controller_window is None is the real, load-bearing pre-
        window-crash guard (see this module's own docstring) - not just an
        optimization, since webview.create_window has no meaningful window
        to attach to (or app_lifecycle to survive) that early.
        """
        if getattr(self, "_controller_window", None) is None:
            return False, None
        try:
            import threading

            import webview

            if kind == "confirm":
                accent = _ACCENT_INFO
                buttons = [
                    {"label": "No", "value": False, "style": "secondary"},
                    {"label": "Yes", "value": True, "style": "primary"},
                ]
                escape_value = False
            elif kind == "donate":
                # Matches the real Tk Donate popup's independent Donate/
                # Close buttons (settings_ui_mixin.py _show_donate_popup):
                # Donate opens the link but leaves the dialog open (so the
                # user can keep reading or click it again); only Close (or
                # Escape) actually dismisses it. keepOpen routes the click
                # through _DialogApi.action() instead of respond().
                accent = _ACCENT_INFO
                buttons = [
                    {"label": "Close", "value": False, "style": "secondary"},
                    {"label": "Donate", "value": True, "style": "primary", "keepOpen": True},
                ]
                escape_value = False
            elif kind == "error":
                accent = _ACCENT_ERROR
                buttons = [{"label": "OK", "value": None, "style": "primary"}]
                escape_value = None
            else:
                accent = _ACCENT_INFO
                buttons = [{"label": "OK", "value": None, "style": "primary"}]
                escape_value = None

            config = {
                "title": str(title),
                "message": str(message),
                "accent": accent,
                "buttons": buttons,
                "escape_value": escape_value,
            }

            done = threading.Event()
            result_holder = {}
            window_holder = {}

            def _on_respond(value):
                result_holder["value"] = value
                done.set()
                try:
                    window_holder["window"].destroy()
                except Exception:
                    pass

            def _on_closing():
                # Only a safety net (frameless + no close button means
                # this shouldn't normally fire) - without it, a dialog
                # closed some other way would leave the caller blocked in
                # done.wait() forever instead of falling through below.
                done.set()

            def _on_dialog_action(value):
                if on_action is not None:
                    on_action(value)

            dialog_window = webview.create_window(
                str(title) or "Rhema",
                html=DIALOG_HTML,
                width=DIALOG_WIDTH,
                height=_DIALOG_MIN_HEIGHT,
                resizable=False,
                frameless=True,
                on_top=True,
                hidden=True,
                background_color="#1E2228",
                js_api=_DialogApi(config, _on_respond, window_holder, on_action=_on_dialog_action),
            )
            window_holder["window"] = dialog_window
            dialog_window.events.closing += _on_closing

            if not done.wait(timeout=timeout):
                try:
                    dialog_window.destroy()
                except Exception:
                    pass
                return False, None
            return True, result_holder.get("value", escape_value)
        except Exception:
            return False, None

    def _show_error_dialog(self, title, message, parent=None):
        # Finite timeout, unlike info/confirm below - see this module's
        # own docstring for why error specifically needs an escape hatch
        # back to native rather than blocking indefinitely.
        handled, _ = self._show_html_message_dialog(title, message, "error", timeout=8.0)
        if handled:
            return
        ctypes.windll.user32.MessageBoxW(
            _hwnd_for_parent(parent), str(message), str(title), _MB_OK | _MB_ICONERROR
        )

    def _show_info_dialog(self, title, message, parent=None):
        handled, _ = self._show_html_message_dialog(title, message, "info")
        if handled:
            return
        ctypes.windll.user32.MessageBoxW(
            _hwnd_for_parent(parent), str(message), str(title), _MB_OK | _MB_ICONINFORMATION
        )

    def _confirm_yes_no(self, title, message, parent=None):
        handled, value = self._show_html_message_dialog(title, message, "confirm")
        if handled:
            return bool(value)
        result = ctypes.windll.user32.MessageBoxW(
            _hwnd_for_parent(parent), str(message), str(title), _MB_YESNO | _MB_ICONQUESTION
        )
        return result == _IDYES
