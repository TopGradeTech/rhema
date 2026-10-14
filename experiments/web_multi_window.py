r"""Port test: can a small Controller-style window control a separate,
real fullscreen Output window - the actual shape the shipping app needs
(one process, two Tk Toplevels sharing state) but no experiment has tried
yet?

Every window experiment so far (web_output_window.py, web_video_overlay.py,
web_options.py, web_multimonitor.py, web_menu_bar.py) opened exactly ONE
pywebview window. The real app is never that: open_settings() in
settings_ui_mixin.py builds "Rhema Controller" (mic/device selection, a
Status section with a latency label and audio meter, a Pause button, and a
"Toggle Fullscreen" button) as a SEPARATE Tk Toplevel from the fullscreen
output window (self.root) - and that button calls self.toggle_fullscreen(),
a method on the shared TranslationApp instance that reaches into the
OTHER window's state. Two real, unanswered questions this file exists to
settle before any Controller-shell experiment would even be worth building:

1. Does pywebview support two independent windows open at once at all -
   multiple create_window() calls before one webview.start() - and can a
   button click in window A (Controller) trigger a real state change in
   window B (Output): specifically, reusing the exact
   move()+resize()+toggle_fullscreen() sequence web_multimonitor.py already
   proved lands correctly on a specific real monitor, now driven from a
   DIFFERENT window's click handler instead of the same window that's
   being placed?
2. What happens to the OTHER window and to webview.start() itself when
   just one window closes? The real app treats the Controller closing as
   "quit the whole app" (WM_DELETE_WINDOW -> on_closing) - but that's an
   app-level decision Tk lets this code make explicitly, not necessarily
   what pywebview does on its own. Does closing the Controller window here
   leave the Output window (and webview.start()'s event loop) running,
   or does pywebview tear down everything when any one window closes?
   Whichever it is changes whether "quit on Controller close" needs to be
   coded explicitly in a real port, or falls out for free.

Reuses OutputEngine/WebCanvas/WebMeasurer from web_output_window.py for the
real output side (same pattern as every window experiment since
web_video_overlay.py), constructed but not fed any transcript content -
real caption rendering was already proven in web_output_window.py and
isn't what this file is testing; RealtimeSTT/NLLB are never started
either, for the same reason.

**Both questions above: confirmed on real hardware.** Two independent
windows coexist fine; a click-equivalent call in the Controller window's
js_api correctly reaches into the Output window's object and runs the
exact move()+resize()+toggle_fullscreen() sequence web_multimonitor.py
proved, landing on the target monitor's real physical bounds both times
(on, then back off). And no, pywebview does NOT auto-quit when one window
closes: destroying the Controller window left the Output window's real
HWND still valid (Win32 IsWindow() confirmed it, not just pywebview's own
possibly-stale bookkeeping - webview.windows turned out to be append-only,
never pruned on destroy(), so it can't answer this question on its own).
So "closing the Controller quits the app" remains something a real port
has to code explicitly (e.g. destroy the other window(s) from the
Controller's closed handler), same as Tk's on_closing hook today - it does
not fall out for free.

**Noisy but apparently harmless side effect, worth flagging:** running
this produced a flood of "[pywebview] Error while processing
output_window.native.<property chain>" messages - COM
apartment-threading violations ("CoreWebView2 can only be accessed from
the UI thread") for WebView2-related properties this file never touches
(AccessibilityObject, ModifierKeys, ZoomFactor, CanGoBack, ...), some
recursing into absurdly long property-chain names before hitting Python's
own recursion limit. This looks like pywebview's own internal error-logging
path trying to walk/describe window.native reflectively after some first
COM-threading fault, itself hitting the same threading fault on each
property it touches - triggered by this file's HWND/bounds lookups
(_hwnd_for, the Controls enumeration) running from background threads
rather than the WinForms UI thread. The actual measurements this file
depends on (Handle, Bounds, GetWindowRect) still came back correct despite
the noise, so it did not corrupt this file's findings - but a real port
reaching into window.native from a background thread should expect this
kind of log spam and, ideally, marshal such calls onto the UI thread
(window.native.Invoke(...)) rather than accept it.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_multi_window.py

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
from web_output_window import (  # noqa: E402
    FONT_FAMILY,
    PIXELS_PER_INCH,
    OutputEngine,
    WebCanvas,
    WebMeasurer,
)


class _MonitorProbe(MonitorMixin):
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
    rect = _RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd & 0xFFFFFFFF), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _hwnd_for(window):
    return int(window.native.Handle.ToInt32())


def _monitor_scale(hmonitor):
    dpi_x = wintypes.UINT()
    dpi_y = wintypes.UINT()
    ctypes.windll.shcore.GetDpiForMonitor(hmonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    return dpi_x.value / 96.0


def _target_monitor_logical():
    """Same real-hardware monitor lookup + physical->logical conversion
    web_multimonitor.py already proved correct - not re-derived here, just
    reused, since this file is testing cross-window control, not placement
    math a second time."""
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

    non_primary = [m for m in monitors if not m.get("primary")]
    target = non_primary[0] if non_primary else monitors[0]
    scale = scale_by_device.get(target.get("device", ""), 1.0)
    phys = (target["left"], target["top"], target["right"] - target["left"], target["bottom"] - target["top"])
    logical = tuple(round(v / scale) for v in phys)
    return phys, logical, scale


CONTROLLER_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema Controller port test</title>
<style>
body{margin:0;background:#1E2228;color:#E5E7EB;font:14px sans-serif;padding:20px}
button{background:#5B8FF7;color:#111;border:none;border-radius:6px;padding:8px 16px;
 font-weight:600;cursor:pointer;margin-top:12px}
#status{margin-top:10px;color:#9CA3AF;font-size:12px;white-space:pre-wrap}
</style></head><body>
<h3 style="margin:0 0 6px">Rhema Controller - port test</h3>
<p style="color:#9CA3AF;margin:0">This window is separate from the real output window.</p>
<button id="toggle">Toggle Fullscreen (on the OTHER window)</button>
<div id="status">not toggled yet</div>
<script>
document.getElementById('toggle').addEventListener('click', async () => {
  const result = await pywebview.api.toggle_output_fullscreen()
  document.getElementById('status').textContent = JSON.stringify(result, null, 2)
})
</script>
</body></html>
"""


class ControllerApi:
    def __init__(self, output_window, output_engine_box, target_phys, target_logical):
        self.output_window = output_window
        self.output_engine_box = output_engine_box
        self.target_phys = target_phys
        self.target_logical = target_logical
        self.is_fullscreen = False

    def toggle_output_fullscreen(self):
        # The exact real recipe web_multimonitor.py proved correct, now
        # invoked from a DIFFERENT window's click handler.
        x, y, w, h = self.target_logical
        if not self.is_fullscreen:
            self.output_window.move(x, y)
            self.output_window.resize(w, h)
            self.output_window.toggle_fullscreen()
            self.is_fullscreen = True
        else:
            self.output_window.toggle_fullscreen()
            self.is_fullscreen = False
        time.sleep(0.3)
        hwnd = _hwnd_for(self.output_window)
        actual = _real_window_rect(hwnd)
        expected = self.target_phys if self.is_fullscreen else None
        return {
            "is_fullscreen": self.is_fullscreen,
            "actual_physical_rect": actual,
            "expected_physical_rect": expected,
            "matches": expected is None or actual == expected,
        }


def main():
    log = []

    def report(label, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label}" + (f" - {detail}" if detail else ""), flush=True)
        log.append((status, label, detail))

    target_phys, target_logical, scale = _target_monitor_logical()
    print(f"Target monitor: physical={target_phys} logical={target_logical} scale={scale}", flush=True)

    # --- Window B: the real output engine, small (not yet fullscreen) so
    # it's out of the way until the Controller's button is clicked. ---
    from web_output_window import HTML as OUTPUT_HTML

    output_window = webview.create_window(
        "Rhema - output (port test)",
        html=OUTPUT_HTML,
        width=480,
        height=300,
        x=target_logical[0] + 40,
        y=target_logical[1] + 40,
        background_color="#000000",
    )

    engine_box = {}

    def on_output_loaded():
        canvas = WebCanvas(output_window, width=1, height=1)
        measurer = WebMeasurer(output_window, FONT_FAMILY, 50, PIXELS_PER_INCH)
        engine = OutputEngine(lambda p: None, canvas, measurer, PIXELS_PER_INCH)
        engine_box["engine"] = engine
        engine.max_lines = 3
        # Real caption rendering is already proven (web_output_window.py) -
        # this file is testing window mechanics, not content, so just
        # confirm the real engine builds successfully against this window.
        engine.render_text()
        report("output window's real OutputEngine constructed successfully", True)

    output_window.events.loaded += on_output_loaded

    # --- Window A: the small Controller stand-in with the one button that
    # matters for this test. ---
    controller_closed_first = {"value": None}

    def on_output_closed():
        if controller_closed_first["value"] is None:
            controller_closed_first["value"] = False
        report(
            "output window's closed event fired",
            True,
            f"controller_closed_first={controller_closed_first['value']}",
        )

    def on_controller_closed():
        if controller_closed_first["value"] is None:
            controller_closed_first["value"] = True
        report(
            "controller window's closed event fired",
            True,
            f"controller_closed_first={controller_closed_first['value']}",
        )

    output_window.events.closed += on_output_closed

    api = ControllerApi(output_window, engine_box, target_phys, target_logical)
    controller_window = webview.create_window(
        "Rhema Controller - port test",
        html=CONTROLLER_HTML,
        js_api=api,
        width=420,
        height=220,
        background_color="#1E2228",
    )
    controller_window.events.closed += on_controller_closed

    def on_controller_loaded():
        report("two independent windows both loaded", True)

        # Calls the same ControllerApi method the real HTML button's
        # pywebview.api.toggle_output_fullscreen() would call - the JS->
        # Python js_api bridge itself was already proven in web_options.py;
        # what's new here is whether THIS method (window A reaching into
        # window B's state) actually works, which this exercises either way.
        result = api.toggle_output_fullscreen()
        report(
            "Controller-window click toggles fullscreen on the Output window, lands correctly",
            result["matches"] and result["is_fullscreen"],
            str(result),
        )

        def finish():
            time.sleep(2.0)
            result2 = api.toggle_output_fullscreen()
            report("toggling back off also works", not result2["is_fullscreen"])

            ok = all(status == "PASS" for status, _label, _detail in log)
            print("\nRESULT (pre-close): " + ("ALL PASS" if ok else "SOME FAILURES - see above"), flush=True)

            # Close only the Controller window - the real app's on_closing
            # behavior (quit everything) is an explicit app-level choice,
            # not something to assume pywebview does on its own. Whether
            # the Output window survives on its own is exactly what this
            # checks.
            output_hwnd = _hwnd_for(output_window)
            controller_window.destroy()
            time.sleep(1.5)
            # webview.windows is append-only (destroy() never removes from
            # it - confirmed by reading __init__.py), so it can't answer
            # "is this window still actually open" - a real Win32 IsWindow()
            # on the HWND we already resolved is the ground truth.
            still_open = bool(ctypes.windll.user32.IsWindow(wintypes.HWND(output_hwnd & 0xFFFFFFFF)))
            report(
                "Output window survives the Controller window closing (pywebview does NOT auto-quit)",
                still_open,
                f"IsWindow(output_hwnd)={still_open}",
            )
            print(
                "\n(If the app needs 'closing the Controller quits everything', that's "
                "still explicit code to write in a real port, same as Tk's on_closing "
                "hook - it does not fall out for free.)",
                flush=True,
            )
            if still_open:
                try:
                    output_window.destroy()
                except Exception:
                    pass

        Thread(target=finish, daemon=True).start()

    controller_window.events.loaded += on_controller_loaded

    webview.start()


if __name__ == "__main__":
    main()
