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
_show_info_dialog/_confirm_yes_no, backed by plain ctypes MessageBoxW
instead of tkinter.messagebox.

Deliberately NOT pywebview-based (no HTML dialog, no evaluate_js). The
crash hook (LoggingMixin._handle_unhandled_exception) can fire before any
window exists - sys.excepthook is installed as one of the first things
__init__ does - so the dialog backend it calls through _show_error_dialog
must not depend on a pywebview window already being up. MessageBoxW needs
none: a NULL owner HWND is a completely standard, supported call and still
produces a real, focused, modal system dialog. It also gives the same
synchronous, blocking-until-dismissed semantics tkinter.messagebox already
had, which callers (e.g. _prompt_update_available reading a return value
before proceeding) depend on.
"""

import ctypes

from webview_bridge import hwnd_for

_MB_OK = 0x00000000
_MB_YESNO = 0x00000004
_MB_ICONERROR = 0x00000010
_MB_ICONQUESTION = 0x00000020
_MB_ICONINFORMATION = 0x00000040
_IDYES = 6


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

    def _show_error_dialog(self, title, message, parent=None):
        ctypes.windll.user32.MessageBoxW(
            _hwnd_for_parent(parent), str(message), str(title), _MB_OK | _MB_ICONERROR
        )

    def _show_info_dialog(self, title, message, parent=None):
        ctypes.windll.user32.MessageBoxW(
            _hwnd_for_parent(parent), str(message), str(title), _MB_OK | _MB_ICONINFORMATION
        )

    def _confirm_yes_no(self, title, message, parent=None):
        result = ctypes.windll.user32.MessageBoxW(
            _hwnd_for_parent(parent), str(message), str(title), _MB_YESNO | _MB_ICONQUESTION
        )
        return result == _IDYES
