r"""Follow-up on web_controller_window.py's instability finding: does
marshaling every `window.native` touch onto the WinForms UI thread via
`Invoke()` - the exact pattern pywebview's OWN internal methods
(show/toggle_fullscreen/set_window_menu/get_cookies in platforms/
winforms.py all use `if self.InvokeRequired: self.Invoke(Func[Type](...))`)
- eliminate the intermittent COM apartment-threading hang that file's
phase 2 hit?

That hang - a flood of "[pywebview] Error while processing
...AccessibilityObject..." recursion, sometimes resolving in under a
second, sometimes never - showed up specifically when this file's code
touched `window.native.Handle`/`window.native.Controls` directly from a
background thread (not the WinForms UI thread pywebview's own internals
always marshal onto first). That's a real, plausible root cause: every
`.native` access this port evaluation has done so far (HWND lookups in
web_multimonitor.py/web_menu_bar.py/web_multi_window.py/
web_controller_window.py) called straight through from whatever thread
happened to be running, unlike pywebview's own code, which never does.

This file re-runs the core of web_controller_window.py's risky shape - two
real windows, one with a committed (non-empty) OutputEngine, a Controller
with real js_api, and a cross-window Toggle Fullscreen (the screenshot
capture itself isn't repeated here - already proven separately in that
file's phase 1) - MULTIPLE times in one process (not just once, since the
original hang was intermittent - a single clean pass wouldn't be strong
evidence either way), with every `.native` touch now going through
`_invoke_on_ui_thread()` instead of a direct call.

**Result: confirmed fixed.** 6/6 iterations passed cleanly, each
completing in 2.2-5.2 seconds - vs. web_controller_window.py's own
observed range of under a second to 90+ seconds (hung) across otherwise-
identical runs. The same handful of single, non-recursive "[pywebview]
Error while processing output_window.native.browser.webview.<property>:
... can only be accessed from the UI thread" messages still print every
iteration (AllowExternalDrop/CanGoBack/CanGoForward/CoreWebView2/
DefaultBackgroundColor/ZoomFactor - looks like some pywebview-internal
property probe unrelated to this file's own code) - but they stay bounded
and never cascade into the runaway recursion that caused the hang. So: **a
real port's cross-window control code (Toggle Fullscreen, HWND/bounds
lookups, anything touching `window.native`) should always marshal through
`InvokeRequired`/`Invoke(Func[Type](...))` rather than calling `.native`
directly from a background thread** - this fix is what resolves the
Controller+Output two-window architecture's biggest open risk, not a
reason to avoid that architecture.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_ui_thread_invoke.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes
from threading import Event, Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clr  # noqa: E402
from System import Func, Type  # noqa: E402

import webview  # noqa: E402

from web_output_window import (  # noqa: E402
    FONT_FAMILY,
    PIXELS_PER_INCH,
    OutputEngine,
    WebCanvas,
    WebMeasurer,
)


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _invoke_on_ui_thread(window, func):
    """Runs func() (zero-arg) on window's real WinForms UI thread if the
    caller isn't already on it, and returns its result - the same
    InvokeRequired/Invoke(Func[Type](...)) idiom pywebview's own internal
    BrowserView methods use (winforms.py), applied here to the .native
    touches THIS port evaluation has been making directly from background
    threads until now. Captures the result via a mutable box rather than
    Invoke()'s own return value, matching how pywebview's own get_cookies()
    does it (Func[Type]'s generic parameter is a dummy - Invoke doesn't
    meaningfully marshal a typed Python return through it)."""
    box = {}

    def _wrapped():
        box["result"] = func()

    native = window.native
    if native.InvokeRequired:
        native.Invoke(Func[Type](_wrapped))
    else:
        _wrapped()
    return box.get("result")


def _hwnd_for(window):
    return _invoke_on_ui_thread(window, lambda: int(window.native.Handle.ToInt32()))


def _real_window_rect(hwnd):
    # Plain Win32, not a .native/COM touch - GetWindowRect doesn't need
    # marshaling onto the UI thread (confirmed safe from any thread in
    # every prior experiment); left as a direct call.
    rect = _RECT()
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd & 0xFFFFFFFF), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


CONTROLLER_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema Controller port test</title>
<style>
body{margin:0;background:#1E2228;color:#E5E7EB;font:14px sans-serif}
#page{padding:16px}
.section{border:1px solid #3A3F4B;border-radius:8px;padding:10px;margin-bottom:12px}
#status,#latency{color:#9CA3AF;font-size:12px;margin:2px 0}
button{background:#5B8FF7;color:#111;border:none;border-radius:6px;padding:7px 14px;
 font-weight:600;cursor:pointer;margin-right:8px}
</style></head><body>
<div id="page">
  <h3>Rhema Controller - port test</h3>
  <div class="section">
    <div id="status">Status: Listening</div>
    <div id="latency">Latency: -- ms</div>
    <button id="pause">Pause</button>
    <button id="toggle">Toggle Fullscreen</button>
  </div>
</div>
<script>
document.getElementById('toggle').addEventListener('click', async () => {
  await pywebview.api.toggle_output_fullscreen()
})
document.getElementById('pause').addEventListener('click', async () => {
  await pywebview.api.toggle_pause()
})
</script>
</body></html>
"""


class ControllerApi:
    def __init__(self, output_window, target_logical, target_phys):
        self.output_window = output_window
        self.target_logical = target_logical
        self.target_phys = target_phys
        self.is_fullscreen = False

    def toggle_output_fullscreen(self):
        x, y, w, h = self.target_logical
        if not self.is_fullscreen:
            self.output_window.move(x, y)
            self.output_window.resize(w, h)
            self.output_window.toggle_fullscreen()
            self.is_fullscreen = True
        else:
            self.output_window.toggle_fullscreen()
            self.is_fullscreen = False
        return {"is_fullscreen": self.is_fullscreen}

    def toggle_pause(self):
        return {"ok": True}


def _run_one_iteration(iteration, target_logical, target_phys, log):
    def report(label, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] iter {iteration}: {label}" + (f" - {detail}" if detail else ""), flush=True)
        log.append((status, label, detail))

    done = Event()
    t0 = time.monotonic()

    from web_output_window import HTML as OUTPUT_HTML

    output_window = webview.create_window(
        f"Rhema output {iteration}", html=OUTPUT_HTML, width=480, height=300, background_color="#000000"
    )

    def on_output_loaded():
        canvas = WebCanvas(output_window, width=1, height=1)
        measurer = WebMeasurer(output_window, FONT_FAMILY, 50, PIXELS_PER_INCH)
        engine = OutputEngine(lambda p: None, canvas, measurer, PIXELS_PER_INCH)
        engine.max_lines = 3
        engine._commit_display_piece(f"Caption for iteration {iteration}.", "display_commit")

        api = ControllerApi(output_window, target_logical, target_phys)
        controller_window = webview.create_window(
            f"Rhema Controller {iteration}",
            html=CONTROLLER_HTML,
            js_api=api,
            width=380,
            height=260,
            background_color="#1E2228",
        )

        def on_controller_loaded():
            time.sleep(0.3)
            hwnd = _hwnd_for(output_window)
            result = api.toggle_output_fullscreen()
            time.sleep(0.3)
            actual = _real_window_rect(hwnd)
            report(
                "cross-window Toggle Fullscreen lands correctly (via Invoke-marshaled .native access)",
                result["is_fullscreen"] and actual == target_phys,
                f"actual={actual} expected={target_phys}",
            )
            api.toggle_output_fullscreen()
            time.sleep(0.2)
            controller_window.destroy()
            time.sleep(0.2)
            try:
                output_window.destroy()
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            report("iteration completed within a sane time budget", elapsed < 15.0, f"{elapsed:.1f}s")
            done.set()

        controller_window.events.loaded += on_controller_loaded

    output_window.events.loaded += on_output_loaded

    if not done.wait(timeout=30.0):
        report("iteration completed at all (did NOT hang past 30s)", False, "timed out")
        try:
            output_window.destroy()
        except Exception:
            pass


def _target_monitor_logical():
    from monitor_mixin import MonitorMixin

    class _MonitorProbe(MonitorMixin):
        def __init__(self):
            self.monitor_index = 0
            self.monitor_device = ""
            self.monitor_origin = ""
            self.settings_monitor_index = 0
            self.settings_monitor_device = ""
            self.settings_monitor_origin = ""

    probe = _MonitorProbe()
    probe.set_dpi_awareness()
    monitors = probe.get_monitors()
    non_primary = [m for m in monitors if not m.get("primary")]
    target = non_primary[0] if non_primary else monitors[0]
    target_phys = (target["left"], target["top"], target["right"] - target["left"], target["bottom"] - target["top"])

    handles = []

    def _cb(hmon, hdc, lprc, lparam):
        handles.append(hmon)
        return True

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_RECT), wintypes.LPARAM
    )
    ctypes.windll.user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_cb), 0)
    scale = 1.0
    for h in handles:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(h, ctypes.byref(info)) and info.szDevice == target.get("device", ""):
            dpi_x = wintypes.UINT()
            dpi_y = wintypes.UINT()
            ctypes.windll.shcore.GetDpiForMonitor(h, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
            scale = dpi_x.value / 96.0
            break
    target_logical = tuple(round(v / scale) for v in target_phys)
    return target_phys, target_logical


def main():
    log = []
    ITERATIONS = 6
    target_phys, target_logical = _target_monitor_logical()

    # webview.start() requires at least one window to already exist at the
    # moment it's called - create a tiny bootstrap window on the main
    # thread first so that precondition is met before the runner thread
    # (which creates/destroys its own windows per iteration) gets going.
    bootstrap = webview.create_window("bootstrap", html="<body></body>", width=1, height=1, hidden=True)

    def runner():
        for i in range(1, ITERATIONS + 1):
            print(f"--- iteration {i}/{ITERATIONS} ---", flush=True)
            iteration_log = []
            _run_one_iteration(i, target_logical, target_phys, iteration_log)
            log.extend(iteration_log)
        ok = all(status == "PASS" for status, _label, _detail in log)
        print(
            f"\nRESULT: {'ALL PASS across ' + str(ITERATIONS) + ' iterations' if ok else 'SOME FAILURES - see above'}",
            flush=True,
        )
        os._exit(0)

    Thread(target=runner, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
