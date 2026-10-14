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

import tkinter as tk
import os

from monitor_logic_mixin import MonitorLogicMixin


class MonitorMixin(MonitorLogicMixin):

    def apply_dark_title_bar(self, window, dark=True):
        # DWMWA_USE_IMMERSIVE_DARK_MODE (20 on Windows 10 20H1+; older
        # builds before that used 19) is the one part of window chrome
        # with a real, documented API for following dark mode - unlike
        # the classic Win32 menu bar, which has no supported equivalent
        # (see the dark-mode prototype discussion this session).
        if os.name != "nt":
            return
        try:
            import ctypes

            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
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
        self.monitors = self.get_monitors()
        if not self.monitors:
            return
        # Close any existing overlays.
        for win in tuple(self.monitor_id_windows):
            try:
                win.destroy()
            except Exception:
                pass
        self.monitor_id_windows = []

        for i, monitor in enumerate(self.monitors):
            width = max(1, monitor["right"] - monitor["left"])
            height = max(1, monitor["bottom"] - monitor["top"])
            x = monitor["left"]
            y = monitor["top"]
            overlay = tk.Toplevel(self.root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.configure(bg="#000000")
            overlay.geometry(f"{width}x{height}+{x}+{y}")
            label = tk.Label(
                overlay,
                text=str(i + 1),
                fg="#ffffff",
                bg="#000000",
                font=(self.font_family, max(80, int(min(width, height) * 0.2)), "bold"),
            )
            label.place(relx=0.5, rely=0.5, anchor="center")
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
        self.monitors = self.get_monitors()
        if not self.monitors or window is None:
            return
        idx = max(0, min(monitor_index, len(self.monitors) - 1))
        monitor = self.monitors[idx]
        width = monitor["right"] - monitor["left"]
        height = monitor["bottom"] - monitor["top"]
        x = monitor["left"]
        y = monitor["top"]

        if keep_size:
            try:
                window.update_idletasks()
                w = window.winfo_width()
                h = window.winfo_height()
            except Exception:
                w, h = width, height
            x = monitor["left"] + max(0, (width - w) // 2)
            y = monitor["top"] + max(0, (height - h) // 2)
            window.geometry(f"{w}x{h}+{x}+{y}")
        else:
            window.geometry(f"{width}x{height}+{x}+{y}")


    def _move_settings_window_to_monitor(self, monitor_index):
        if self.settings_window is None or not self.settings_window.winfo_exists():
            return
        window = self.settings_window
        prev_state = self._safe_window_state(window)
        if prev_state in ("zoomed", "maximized"):
            self._normalize_settings_window_before_move(window)
            self._schedule_settings_window_move(window, monitor_index)
            return

        self.move_window_to_monitor(window, monitor_index, keep_size=True)


    def _safe_window_state(self, window):
        try:
            return window.state()
        except Exception:
            return None


    def _normalize_settings_window_before_move(self, window):
        try:
            window.state("normal")
            window.update_idletasks()
        except Exception:
            pass


    def _schedule_settings_window_move(self, window, monitor_index):
        window.after(60, lambda: self._finish_settings_window_move(window, monitor_index))


    def _finish_settings_window_move(self, window, monitor_index):
        if not window.winfo_exists():
            return
        self.move_window_to_monitor(window, monitor_index, keep_size=True)
        window.after(80, lambda: self._restore_settings_window_zoom(window))


    def _restore_settings_window_zoom(self, window):
        if not window.winfo_exists():
            return
        try:
            window.state("zoomed")
        except Exception:
            pass


    # The output window intermittently ends up on the wrong monitor at
    # startup even though monitor_index/device resolve correctly from
    # settings and enter_fullscreen runs with the right values (confirmed
    # via instrumentation 2026-07-10) - i.e. Windows/Tk sometimes ignores
    # or later reverts the geometry for the overrideredirect window,
    # the same failure class the "apply twice" workaround in
    # _apply_custom_fullscreen already papers over. Verify the result at
    # staged delays and reassert when wrong; the mismatch log records
    # whether it was wrong immediately (geometry call ignored) or only at
    # a later check (reverted afterward).
    _FULLSCREEN_VERIFY_DELAYS_MS = (300, 1500, 4000)


    def enter_fullscreen(self):
        if not self.is_fullscreen:
            return
        if self.prev_geometry is None:
            self.prev_geometry = self.root.geometry()
        if self.use_custom_fullscreen:
            self._prepare_custom_fullscreen_state()
            self._apply_custom_fullscreen()
        else:
            self._prepare_borderless_fullscreen_state()
            self._apply_standard_fullscreen()
        self._apply_canvas_padding()
        self._schedule_fullscreen_position_checks()


    def _schedule_fullscreen_position_checks(self):
        # Cancel any checks still pending from a previous enter_fullscreen
        # (e.g. Apply clicked twice) so reasserts can't stack or fire against
        # a monitor selection that just changed.
        for after_id in getattr(self, "_fullscreen_verify_after_ids", ()):
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._fullscreen_verify_after_ids = [
            self.root.after(delay, lambda d=delay: self._verify_fullscreen_position(d))
            for delay in self._FULLSCREEN_VERIFY_DELAYS_MS
        ]


    def _verify_fullscreen_position(self, delay_ms):
        if not self.is_fullscreen or not self.monitors:
            return
        idx = max(0, min(self.monitor_index, len(self.monitors) - 1))
        monitor = self.monitors[idx]
        try:
            self.root.update_idletasks()
            actual = (
                self.root.winfo_x(),
                self.root.winfo_y(),
                self.root.winfo_width(),
                self.root.winfo_height(),
            )
        except Exception:
            return
        expected = (
            monitor["left"],
            monitor["top"],
            monitor["right"] - monitor["left"],
            monitor["bottom"] - monitor["top"],
        )
        if actual == expected:
            return
        self._log_status(
            "fullscreen position wrong at +%sms: actual=%s expected=%s "
            "(monitor_index=%s device=%r) - reasserting"
            % (delay_ms, actual, expected, idx, monitor.get("device", ""))
        )
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)


    def _prepare_borderless_fullscreen_state(self):
        if self.prev_overrideredirect is None:
            try:
                self.prev_overrideredirect = bool(self.root.overrideredirect())
            except Exception:
                self.prev_overrideredirect = False
        try:
            self.root.overrideredirect(True)
        except Exception:
            pass


    def _prepare_custom_fullscreen_state(self):
        if self.prev_overrideredirect is None:
            try:
                self.prev_overrideredirect = bool(self.root.overrideredirect())
            except Exception:
                self.prev_overrideredirect = False
        if self.prev_topmost is None:
            try:
                self.prev_topmost = bool(self.root.attributes("-topmost"))
            except Exception:
                self.prev_topmost = False
        try:
            self.root.state("normal")
        except Exception:
            pass


    def _apply_custom_fullscreen(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.lock_output_focus))
        self.root.attributes("-fullscreen", False)
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        self.root.update_idletasks()
        # Apply twice to avoid position being offset by window manager.
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        if self.lock_output_focus:
            try:
                self.root.lift()
            except Exception:
                pass
            try:
                self.root.focus_force()
            except Exception:
                pass


    def _apply_standard_fullscreen(self):
        # Some window managers ignore geometry changes while fullscreen is active.
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        self.root.update_idletasks()
        self.root.attributes("-fullscreen", True)


    def exit_fullscreen(self):
        if self.use_custom_fullscreen:
            self.root.attributes("-fullscreen", False)
            if self.prev_topmost is not None:
                try:
                    self.root.attributes("-topmost", self.prev_topmost)
                except Exception:
                    pass
        else:
            self.root.attributes("-fullscreen", False)
        if self.prev_overrideredirect is not None:
            try:
                self.root.overrideredirect(self.prev_overrideredirect)
            except Exception:
                pass
        if self.prev_geometry:
            self.root.geometry(self.prev_geometry)
        self.prev_geometry = None
        self.prev_overrideredirect = None
        self.prev_topmost = None
        self._apply_canvas_padding()
