r"""Port test: does pywebview's window-placement API give the output window
the same real multi-monitor placement/fullscreen behavior monitor_mixin.py
gets from Tk?

monitor_mixin.py's own monitor enumeration (get_monitors(), real Win32
EnumDisplayMonitors via ctypes) is already Tk-free and already reused
unmodified by every prior experiment - that was never in question. What IS
in question is the OTHER half: move_window_to_monitor() calls Tk's
window.geometry(f"{w}x{h}+{x}+{y}"), and enter_fullscreen() builds on Tk's
overrideredirect()/attributes("-fullscreen")/state(), plus a documented
flaky-placement workaround (apply the geometry twice, then verify at staged
delays and reassert if Windows/Tk reverted it - see
_verify_fullscreen_position's comment). None of that has a Tk-free
equivalent yet; every prior window experiment (web_output_window.py,
web_video_overlay.py, web_options.py) is deliberately fixed-size for one
session on whatever monitor pywebview happens to open on.

pywebview's Window exposes move(x, y)/resize(w, h)/toggle_fullscreen(),
create_window(x=, y=, width=, height=), live x/y properties, and
events.resized/events.moved - on paper, enough surface to reimplement
move_window_to_monitor/enter_fullscreen.

**Coordinate-space finding #1, confirmed by reading pywebview's own Windows
backend (platforms/winforms.py) before writing a line of this test, then
verified against real 3-monitor/150%-scale hardware:** GetMonitorInfoW
(what get_monitors() calls) reports monitor rects in PHYSICAL pixels.
pywebview's window-placement surface - create_window(x=,y=), move()/
resize(), the live x/y properties, events.resized/moved - works in LOGICAL
pixels instead, converting to/from physical internally via a per-window
`_scale` property (GetDpiForWindow). So a real port's
move_window_to_monitor equivalent MUST divide get_monitors()'s physical
rect by that monitor's DPI scale (_monitor_scale here, via
GetDpiForMonitor - the same API pywebview's own `_scale` uses) before
calling any pywebview PLACEMENT API. Confirmed correct on real hardware at
a genuine 1.5x scale factor (not just a 1.0 no-op): create_window(x=,y=),
move()+resize()+toggle_fullscreen(), and events.resized all landed/reported
EXACTLY right against real Win32 GetWindowRect ground truth - no rounding
slop even needed.

**Coordinate-space finding #2, NOT expected going in:** webview.screens
(pywebview's own monitor-enumeration property, NOT the same code path as
window placement) is UNRELIABLE on this real scaled hardware - it reports
PHYSICAL pixel geometry with scale=1.0 for every monitor, both before any
window exists and again after a real window/event loop is running (checked
both ways to rule out a timing/ordering explanation - same wrong answer
either time). So while pywebview's PER-WINDOW placement calls correctly
know about real per-monitor DPI, its MONITOR ENUMERATION does not. A real
port must keep using get_monitors() (real Win32 EnumDisplayMonitors,
already Tk-free, already reused unmodified since the first experiment) plus
its own GetDpiForMonitor query as the source of truth for monitor layout -
webview.screens should not be trusted for this.

(Note also: webview.screens is a `@module_property`, accessed as a plain
attribute - `webview.screens()` looks right but is wrong, and fails with a
confusing "'list' object is not callable" rather than a clear type error,
since the property itself already returns the list before the stray `()`
tries to call it.)

**Confirmed on real hardware, independent of DPI:** move()+resize()+
toggle_fullscreen() reliably places a window on a specific, non-primary
monitor in ONE shot - no "apply twice" workaround, no staged reassert -
unlike Tk's version, which needs both (see _verify_fullscreen_position's
comment: the window manager can silently revert a geometry Tk itself
believed had applied). Verified against actual Win32 GetWindowRect on the
real HWND, immediately and again ~2.5s later, not just pywebview's own
(self-reported) x/y properties.

This machine has 3 real physical monitors, all at 150% scale (144 DPI) -
confirmed via the same EnumDisplayMonitors/GetDpiForMonitor calls
get_monitors() and _monitor_scale make. Genuine scaled hardware, not a
simulated guess - but still uniform-DPI (every monitor the same 150%), so a
true MIXED-DPI setup (e.g. a 100% monitor next to a 150% one) remains
untested; .NET WinForms has a documented history of DPI-virtualization
quirks specifically for that case, and webview.screens' unreliability above
is exactly the kind of surprise that warrants not assuming mixed-DPI "just
works" without testing it for real.

To avoid disrupting whatever's actually on screen, this test keeps every
window small and off in a corner except for one brief (~3s), self-closing
real fullscreen cycle on a deliberately NON-primary monitor, and never
calls lock_output_focus's topmost/focus_force path.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_multimonitor.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

from monitor_mixin import MonitorMixin  # noqa: E402


class _MonitorProbe(MonitorMixin):
    """Just enough of main.py's real __init__ state for the unmodified
    MonitorMixin.get_monitors()/set_dpi_awareness() to run standalone,
    without building a whole OutputEngine (this test never renders
    anything, so it doesn't need one)."""

    def __init__(self):
        self.monitor_index = 0
        self.monitor_device = ""
        self.monitor_origin = ""
        self.settings_monitor_index = 0
        self.settings_monitor_device = ""
        self.settings_monitor_origin = ""


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _real_window_rect(hwnd):
    """Ground truth for where a window ACTUALLY is, independent of what
    pywebview (or Tk) believes it asked for - the same kind of check
    monitor_mixin.py's _verify_fullscreen_position does for the real app,
    since the documented Tk bug this app already hit was the window
    manager silently reverting a geometry Tk itself thought had applied."""
    rect = _RECT()
    # ToInt32() (what pywebview's own winforms.py uses for this Handle
    # everywhere) can come back negative for a handle with the high bit
    # set; wintypes.HWND wants an unsigned pointer-sized value.
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd & 0xFFFFFFFF), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _hwnd_for(window):
    # pywebview's Windows backend sets pywebview_window.native to the real
    # WinForms BrowserView Form itself (see platforms/winforms.py's own
    # self.Handle.ToInt32() usage) - not documented in the public Window
    # API, but this is the same class of "reach past the wrapper for
    # ground truth" already used elsewhere in this repo (e.g.
    # apply_dark_title_bar's GetParent(winfo_id()) in monitor_mixin.py).
    return int(window.native.Handle.ToInt32())


def _monitor_scale(hmonitor):
    """Logical-to-physical scale for a real HMONITOR, via the same
    Shcore API GetDpiForWindow (pywebview's own _scale) is built on -
    the number a real port needs to divide get_monitors()'s physical rect
    by before handing coordinates to any pywebview placement call."""
    dpi_x = wintypes.UINT()
    dpi_y = wintypes.UINT()
    # MDT_EFFECTIVE_DPI = 0
    ctypes.windll.shcore.GetDpiForMonitor(hmonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    return dpi_x.value / 96.0


def _real_monitors_with_scale():
    """get_monitors()'s real monitor list, plus each one's real DPI scale -
    get_monitors() itself doesn't carry this, but a real port's placement
    code needs it (see the module docstring's coordinate-space finding)."""
    probe = _MonitorProbe()
    probe.set_dpi_awareness()
    monitors = probe.get_monitors()

    handles = []

    def _cb(hmon, hdc, lprc, lparam):
        handles.append(hmon)
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_RECT), wintypes.LPARAM
    )
    ctypes.windll.user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_cb), 0)

    # Match handles back to get_monitors()'s dicts by device name, since
    # get_monitors() already resolved that name via the same enumeration.
    scale_by_device = {}
    for h in handles:
        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32),
            ]

        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(h, ctypes.byref(info)):
            scale_by_device[info.szDevice] = _monitor_scale(h)

    for m in monitors:
        m["scale"] = scale_by_device.get(m.get("device", ""), 1.0)
    return monitors


def main():
    log = []

    def report(label, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        line = f"[{status}] {label}" + (f" - {detail}" if detail else "")
        print(line, flush=True)
        log.append((status, label, detail))

    real_monitors = _real_monitors_with_scale()
    print(f"get_monitors() (real Win32 EnumDisplayMonitors), with real DPI scale: {len(real_monitors)} monitor(s)", flush=True)
    for i, m in enumerate(real_monitors):
        print(f"  [{i}] {m}", flush=True)
    report("this machine has real multi-monitor hardware", len(real_monitors) >= 2, f"{len(real_monitors)} found")
    all_100_percent = all(abs(m["scale"] - 1.0) < 1e-6 for m in real_monitors)
    if all_100_percent:
        print(
            "NOTE: every real monitor here is 100% scale - the physical/logical "
            "pixel conversion below runs but is a no-op at this factor. A mixed-DPI "
            "monitor would exercise it for real; none is available on this machine.",
            flush=True,
        )

    try:
        # webview.screens is a @module_property (proxy_tools) - accessed as
        # a plain attribute, NOT called; webview.screens() would first
        # resolve the property (running the real lookup) and then try to
        # call ITS result, raising "'list' object is not callable".
        pywebview_screens = webview.screens
    except Exception as exc:
        pywebview_screens = []
        report("webview.screens() callable before any window exists", False, repr(exc))
    else:
        print(f"webview.screens() (pywebview's own enumeration, logical pixels): {len(pywebview_screens)} screen(s)", flush=True)
        for i, s in enumerate(pywebview_screens):
            print(f"  [{i}] x={s.x} y={s.y} w={s.width} h={s.height} scale={s.scale}", flush=True)
        report(
            "webview.screens() count matches get_monitors()",
            len(pywebview_screens) == len(real_monitors),
        )
        # get_monitors() is physical pixels; webview.screens() is logical.
        # Compare AFTER converting get_monitors()'s rects by each monitor's
        # own real scale - the correct comparison, not a raw physical vs
        # logical one (which would only coincidentally match at 100% scale).
        real_logical_set = {
            (
                round(m["left"] / m["scale"]),
                round(m["top"] / m["scale"]),
                round((m["right"] - m["left"]) / m["scale"]),
                round((m["bottom"] - m["top"]) / m["scale"]),
            )
            for m in real_monitors
        }
        web_set = {(s.x, s.y, s.width, s.height) for s in pywebview_screens}
        report(
            "webview.screens() geometry matches get_monitors() once both are in logical pixels",
            real_logical_set == web_set,
            f"real(logical)={real_logical_set} web={web_set}",
        )

    if len(real_monitors) < 2:
        print("Only one monitor - skipping the cross-monitor placement checks.", flush=True)
        return

    # Deliberately the monitor that ISN'T primary, so this doesn't land on
    # whatever the user is actively looking at in the common
    # one-secondary-monitor case.
    non_primary = [i for i, m in enumerate(real_monitors) if not m.get("primary")]
    target_idx = non_primary[0] if non_primary else 1
    target = real_monitors[target_idx]
    scale = target["scale"]
    target_w_phys = target["right"] - target["left"]
    target_h_phys = target["bottom"] - target["top"]
    # What we actually hand to pywebview - logical pixels, per the
    # coordinate-space finding above.
    target_x_log = round(target["left"] / scale)
    target_y_log = round(target["top"] / scale)
    target_w_log = round(target_w_phys / scale)
    target_h_log = round(target_h_phys / scale)
    print(
        f"Target monitor for placement checks: index {target_idx} = {target} "
        f"-> logical=({target_x_log},{target_y_log},{target_w_log}x{target_h_log})",
        flush=True,
    )

    def close_to(actual, expected, tolerance=2):
        return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))

    # --- Check 1: does create_window(x=, y=) at the target monitor's
    # LOGICAL origin land the window at the right PHYSICAL position, in
    # ONE shot, no workaround? ---
    small_x_log = target_x_log + 40
    small_y_log = target_y_log + 40
    expected_small_phys = (
        round(small_x_log * scale),
        round(small_y_log * scale),
    )
    window = webview.create_window(
        "Rhema - multi-monitor port test",
        html="<body style='background:#1E2228;color:#eee;font:14px sans-serif;padding:16px'>multi-monitor probe</body>",
        x=small_x_log,
        y=small_y_log,
        width=480,
        height=320,
        background_color="#1E2228",
    )

    def on_loaded():
        # Re-check webview.screens now that a real window/event loop exists,
        # to tell apart "wrong because queried too early" from "wrong
        # regardless of timing" for the mismatch just reported above.
        try:
            screens_after_window = webview.screens
            print(
                "webview.screens, queried again from inside on_loaded (a real window/event loop now exists):",
                flush=True,
            )
            for i, s in enumerate(screens_after_window):
                print(f"  [{i}] x={s.x} y={s.y} w={s.width} h={s.height} scale={s.scale}", flush=True)
            real_logical_set_now = {
                (
                    round(m["left"] / m["scale"]),
                    round(m["top"] / m["scale"]),
                    round((m["right"] - m["left"]) / m["scale"]),
                    round((m["bottom"] - m["top"]) / m["scale"]),
                )
                for m in real_monitors
            }
            web_set_now = {(s.x, s.y, s.width, s.height) for s in screens_after_window}
            report(
                "webview.screens matches get_monitors() (logical) once a window/event loop exists",
                real_logical_set_now == web_set_now,
                f"real(logical)={real_logical_set_now} web={web_set_now}",
            )
        except Exception as exc:
            report("webview.screens re-check from on_loaded", False, repr(exc))

        try:
            hwnd = _hwnd_for(window)
        except Exception as exc:
            report("could resolve real HWND from pywebview window", False, repr(exc))
            window.destroy()
            return
        report("could resolve real HWND from pywebview window", True, hex(hwnd))

        actual_x, actual_y, actual_w, actual_h = _real_window_rect(hwnd)
        report(
            "create_window(x=,y=) in logical pixels lands at the right PHYSICAL position on a non-primary monitor, no workaround",
            close_to((actual_x, actual_y), expected_small_phys),
            f"asked(logical)=({small_x_log},{small_y_log}) expected(physical)={expected_small_phys} "
            f"actual(physical)=({actual_x},{actual_y}) size=({actual_w}x{actual_h})",
        )
        report(
            "pywebview's own window.x/window.y (logical) match actual physical / scale",
            close_to((round(window.x * scale), round(window.y * scale)), (actual_x, actual_y)),
            f"pywebview(logical)=({window.x},{window.y}) actual(physical)=({actual_x},{actual_y}) scale={scale}",
        )

        # --- Check 2: move()+resize()+toggle_fullscreen() onto the full
        # target monitor bounds (logical in, physical verified out), in one
        # shot (no "apply twice", no staged reassert) - the real question
        # behind Tk's documented workaround. ---
        window.move(target_x_log, target_y_log)
        window.resize(target_w_log, target_h_log)
        window.toggle_fullscreen()
        # A brief settle delay is fair (Tk's own workaround waits similarly
        # between its two geometry applications), but no retry loop - the
        # point is whether ONE attempt is enough, unlike Tk's.
        time.sleep(0.5)
        fx, fy, fw, fh = _real_window_rect(hwnd)
        expected_full_phys = (target["left"], target["top"], target_w_phys, target_h_phys)
        report(
            "move+resize+toggle_fullscreen lands exactly on the target monitor's real physical bounds, first try",
            close_to((fx, fy, fw, fh), expected_full_phys),
            f"expected(physical)={expected_full_phys} actual(physical)={(fx, fy, fw, fh)}",
        )

        # --- Check 3: does a SECOND geometry check shortly after (Tk's own
        # staged-reassert delays were 300/1500/4000ms) still hold, or does
        # something revert it the way Tk's comment describes? ---
        def verify_later():
            time.sleep(2.0)
            lx, ly, lw, lh = _real_window_rect(hwnd)
            report(
                "fullscreen position still correct ~2.5s later (Tk needed reasserts here)",
                close_to((lx, ly, lw, lh), expected_full_phys),
                f"actual(physical)={(lx, ly, lw, lh)}",
            )

            # --- Check 4: events.resized/events.moved actually fire with
            # real new (logical) dimensions - the hook a future resize-aware
            # canvas would need, unexercised by every fixed-size window so
            # far. ---
            resized_seen = {}

            def on_resized(width, height):
                resized_seen["wh"] = (width, height)

            window.events.resized += on_resized
            new_w_log, new_h_log = 500, 350
            window.toggle_fullscreen()  # exit fullscreen first
            time.sleep(0.3)
            window.resize(new_w_log, new_h_log)
            time.sleep(0.5)
            report(
                "events.resized fires with the real new (logical) size",
                resized_seen.get("wh") == (new_w_log, new_h_log),
                f"got={resized_seen.get('wh')}",
            )

            ok = all(status == "PASS" for status, _label, _detail in log)
            print("\nRESULT: " + ("ALL PASS" if ok else "SOME FAILURES - see above"), flush=True)
            window.destroy()

        Thread(target=verify_later, daemon=True).start()

    window.events.loaded += on_loaded
    webview.start()


if __name__ == "__main__":
    main()
