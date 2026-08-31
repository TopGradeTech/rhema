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

"""Phase 8 of the pywebview port: real monitor placement.
WebMonitorMixin(MonitorLogicMixin) provides pywebview-flavored versions of
the five real chrome methods monitor_mixin.py's MonitorMixin keeps
(apply_dark_title_bar/show_monitor_ids/enter_fullscreen/exit_fullscreen/
move_window_to_monitor), reusing the already-proven Invoke() + DPI-aware
physical-to-logical conversion from webview_bridge.py (itself adapted from
experiments/web_multimonitor.py/web_multi_window.py).

Two things are simpler here than the Tk versions, not just different:

- apply_dark_title_bar needs no GetParent() call. Tk's winfo_id() returns
  a CHILD hwnd (the drawing surface), so monitor_mixin.py's version has to
  walk up to the real top-level window first. pywebview's window.native.
  Handle (resolved via webview_bridge.hwnd_for, Invoke()-marshaled) is
  already the real top-level HWND.
- enter_fullscreen needs no retry/reassert loop. Tk's version schedules
  position-verification checks at three staged delays because Windows
  sometimes silently reverts an overrideredirect window's geometry after
  the fact (monitor_mixin.py's own _verify_fullscreen_position, a real,
  confirmed, documented bug - see project memory: Monitor Persistence
  Bug). experiments/web_multimonitor.py proved move()+resize()+
  toggle_fullscreen() lands exactly on the target monitor's real physical
  bounds in ONE shot, no workaround, no staged reassert - confirmed
  against real 150%-scaled multi-monitor hardware. So this port doesn't
  carry that workaround forward; if a real equivalent bug is ever found
  here, it needs its own new investigation, not an assumed port of Tk's.

move_window_to_monitor/enter_fullscreen/exit_fullscreen all operate on
whichever pywebview Window is passed in - main_webview.py calls them
against self._window (the Output window). The Controller/Options windows
never move programmatically in this port (the user drags them like any
other window), so nothing here targets them.
"""

import os

from monitor_logic_mixin import MonitorLogicMixin
from webview_bridge import hwnd_for, physical_to_logical, real_monitors_with_scale


class WebMonitorMixin(MonitorLogicMixin):
    def apply_dark_title_bar(self, window, dark=True):
        if os.name != "nt":
            return
        try:
            import ctypes

            hwnd = hwnd_for(window)
            value = ctypes.c_int(1 if dark else 0)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                )
                if result == 0:
                    break
        except Exception:
            pass

    def show_monitor_ids(self, duration_ms=2000):
        import webview

        monitors = real_monitors_with_scale(self)
        self.monitors = self.get_monitors()
        if not monitors:
            return
        for win in tuple(getattr(self, "monitor_id_windows", ())):
            try:
                win.destroy()
            except Exception:
                pass
        self.monitor_id_windows = []

        for i, monitor in enumerate(monitors):
            x, y, w, h = physical_to_logical(
                monitor["left"], monitor["top"],
                monitor["right"] - monitor["left"], monitor["bottom"] - monitor["top"],
                monitor["scale"],
            )
            font_px = max(80, int(min(w, h) * 0.2))
            overlay = webview.create_window(
                f"Rhema monitor ID {i + 1}",
                html=(
                    "<!doctype html><html><body style='margin:0;background:#000;"
                    "display:flex;align-items:center;justify-content:center;height:100vh'>"
                    f"<span style='color:#fff;font-size:{font_px}px;font-weight:bold;"
                    "font-family:sans-serif'>%d</span></body></html>" % (i + 1)
                ),
                x=x, y=y, width=w, height=h,
                frameless=True, on_top=True, resizable=False,
                background_color="#000000",
            )
            self.monitor_id_windows.append(overlay)

        def close_overlays():
            for win in tuple(self.monitor_id_windows):
                try:
                    win.destroy()
                except Exception:
                    pass
            self.monitor_id_windows = []

        self.root.after(duration_ms, close_overlays)

    def move_window_to_monitor(self, window, monitor_index, keep_size=True):
        if window is None:
            return
        monitors = real_monitors_with_scale(self)
        self.monitors = self.get_monitors()
        if not monitors:
            return
        idx = max(0, min(monitor_index, len(monitors) - 1))
        monitor = monitors[idx]
        x, y, w, h = physical_to_logical(
            monitor["left"], monitor["top"],
            monitor["right"] - monitor["left"], monitor["bottom"] - monitor["top"],
            monitor["scale"],
        )
        if keep_size:
            try:
                cur_w, cur_h = window.width, window.height
            except Exception:
                cur_w, cur_h = w, h
            x = x + max(0, (w - cur_w) // 2)
            y = y + max(0, (h - cur_h) // 2)
            window.move(x, y)
        else:
            window.move(x, y)
            window.resize(w, h)

    def enter_fullscreen(self):
        if not self.is_fullscreen:
            return
        window = self._window
        if window is None:
            return
        # toggle_fullscreen() fullscreens onto whatever monitor the window
        # is CURRENTLY sitting on (platforms/winforms.py:
        # WinForms.Screen.FromControl(self).Bounds) - so the right monitor
        # has to be selected BEFORE toggling, in windowed state, exactly
        # the order experiments/web_multimonitor.py proved works in one
        # shot. Exit fullscreen first if already in it, since moving/
        # resizing while WinForms is managing fullscreen Bounds is not
        # the state that technique was proved against.
        if getattr(window, "_rhema_is_fullscreen", False):
            window.toggle_fullscreen()
            window._rhema_is_fullscreen = False
        self.move_window_to_monitor(window, self.monitor_index, keep_size=False)
        window.toggle_fullscreen()
        window._rhema_is_fullscreen = True
        # Real parity gap: monitor_mixin.py's own _apply_custom_fullscreen
        # sets self.root.attributes("-topmost", bool(self.lock_output_
        # focus)) at exactly this point (entering fullscreen) - nothing
        # here ever read self.lock_output_focus at all, so "Lock output
        # focus" was fully wired through Options (form, persistence,
        # _apply_display_vars) but never actually applied to the real
        # window. pywebview's window.on_top is a real public property
        # (window.py), not a reach-past-the-API call - its setter marshals
        # to platforms/winforms.py's set_on_top(), which sets the real
        # Form's TopMost. Matches Tk's own contract too: like
        # _apply_custom_fullscreen, this only takes effect when (re-)
        # entering fullscreen, not from a live Apply while already
        # fullscreen - toggling the checkbox needs a fullscreen exit/
        # re-enter (Escape/F11 twice) to take effect there too.
        try:
            window.on_top = bool(self.lock_output_focus)
        except Exception:
            pass

    def exit_fullscreen(self):
        window = self._window
        if window is None:
            return
        if getattr(window, "_rhema_is_fullscreen", False):
            window.toggle_fullscreen()
            window._rhema_is_fullscreen = False
        # Mirrors monitor_mixin.py's exit_fullscreen restoring prev_topmost
        # - the Output window shouldn't stay pinned above every other
        # window (Controller/Options included) once it's not fullscreen.
        try:
            window.on_top = False
        except Exception:
            pass
