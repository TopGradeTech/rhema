r"""Port test: the Controller-window shell - Preview screenshot section,
cross-window Toggle Fullscreen - assembled alongside a real Output window
for the first time, in two phases (see "Instability finding" below for why
it's phased).

Every prior experiment proved exactly one Controller-related mechanism in
isolation: web_menu_bar.py (the File/About menu), web_startup_overlay.py
(the loading gate + deferred menu attach), web_multi_window.py (a
Controller-style button reaching into a separate Output window's real
state), web_tooltip.py (help-icon hovers, not used directly in the
Controller itself). None of them touched the one real piece of Controller
content nothing has tested yet: _build_preview_section()'s periodic
screenshot thumbnail.

**That screenshot mechanism is the actual open question this file exists
to answer - and the answer flips the real app's own technique.** The real
method captures via `ImageGrab.grab(window=self.root.winfo_id())` -
PrintWindow under the hood on Windows - specifically instead of a plain
screen-bbox BitBlt, because (per that method's own comment) the video
overlay's fast PhotoImage updates can get presented through a
hardware-accelerated DWM path that BitBlt reads back as solid black;
PrintWindow asks the window to render itself and sees the real content.

**Confirmed this does NOT carry over: PrintWindow against a real
WebView2-hosted pywebview window returns a flat, solid-black image**
(grayscale extrema (0, 0) on the decoded capture, regardless of what the
page actually shows) - a known-ish limitation of WebView2/Chromium
Embedded-style browser controls, which render through their own GPU
compositor rather than responding to the classic
WM_PRINT/WM_PRINTCLIENT messages PrintWindow depends on. A plain
screen-coordinate bbox capture (BitBlt) against the SAME window correctly
saw real content instead (extrema != flat) - AT its default window
position (which landed on the primary monitor every time this was tested).
So a real port's Preview section needs the OPPOSITE capture technique from
the current Tk implementation, not the same one carried over -
`_capture_output_png_bytes` below uses bbox capture, not `window=`.

**Second, separate monitor-dependent caveat, also confirmed directly (not
a WebView2 or pywebview issue at all):** on this machine, plain BitBlt
screen capture returns solid black for the non-primary monitors,
regardless of what's actually on screen there - confirmed by grabbing raw
screen regions with no window/pywebview involved at all: a primary-monitor
region came back with real varied pixel data, the identical-sized
secondary-monitor region came back completely flat black. This is a
real, separate hardware/driver-level limitation of legacy GDI BitBlt on
multi-monitor (likely multi-GPU-output) setups - unrelated to WebView2,
and it's why phase 1 below deliberately leaves the Output window at
pywebview's default placement rather than the non-primary monitor phase
2's Toggle Fullscreen check moves it to. A real port's Preview section
built on BitBlt should expect this on some real user machines and may need
a more modern capture API (e.g. Windows.Graphics.Capture / DXGI Desktop
Duplication) for reliable multi-monitor support, rather than assuming
classic BitBlt works on every monitor.

Caveat worth stating plainly on top of that: bbox capture needs the window
to actually be the visible, unoccluded content at that screen position -
unlike PrintWindow's, in-principle, occlusion-independent approach. A real
port's Preview section needs to keep that in mind too, e.g. if another
window ever overlapped the output window's screen position.

**Instability finding - the reason this file runs the capture check in two
phases instead of one:** combining a second, content-rich Controller
window with the Output window's real OutputEngine produced an
intermittent, severe hang - a flood of "[pywebview] Error while processing
...AccessibilityObject..." COM apartment-threading recursion (the same
error web_multi_window.py saw ONCE, harmlessly) that sometimes never
stopped, sometimes resolved within a second, across otherwise-identical
runs of the unchanged file. Ruled out as the cause, one at a time, by
direct testing: the real menu bar (removed - still hung), the drip-reveal
background thread (_meter_display_commit() replaced with the synchronous
_commit_display_piece() - still hung), the capture technique
(PrintWindow vs BitBlt - still hung either way). The common factor across
every hang was simply TWO real, content-rich WebView2 windows coexisting -
this machine had 15-20 other msedgewebview2.exe processes running
(Teams/Slack/etc.) throughout testing, so this looks like resource/
COM-threading contention from concurrent WebView2 usage on a real desktop,
not a deterministic bug in this file's own code. That is itself a real,
important finding for the port: a Controller+Output two-window
architecture may see unpredictable multi-second-to-a-minute stalls under
realistic desktop conditions (several other apps also embedding WebView2),
in ways narrower single- or two-window experiments never surfaced.

Given that, this file runs the actual capture-technique comparison in
**phase 1**, single-window, BEFORE the Controller window exists at all -
a controlled measurement of the real open question, immune to phase 2's
demonstrated instability. **Phase 2** then assembles the fuller
Controller+Output shell (Preview image push, capture liveness, cross-window
Toggle Fullscreen) for whatever additional confidence it's worth, with the
menu bar left out (not because it's implicated - the ablation above ruled
it out - but to keep this phase's own scope to the screenshot/cross-window
mechanisms already un-proven elsewhere).

Reuses OutputEngine/WebCanvas/WebMeasurer from web_output_window.py for
the output side (same pattern as every window experiment since
web_video_overlay.py), fed real caption text via _commit_display_piece()
(passing stage="display_commit" directly - the same default
_meter_display_commit() itself uses) - a synchronous commit, not the
drip-reveal path, per the ablation above. Reuses the exact
move()+resize()+toggle_fullscreen() sequence already proven in
web_multimonitor.py/web_multi_window.py for the Toggle Fullscreen button -
not re-verified in depth here, just assembled, since it already has its
own dedicated experiment.

To avoid disrupting whatever's actually on screen, both windows stay small
and off in a corner except for one brief (~2s) real fullscreen cycle on a
deliberately non-primary monitor.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_controller_window.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import base64
import ctypes
import io
import os
import sys
import time
from ctypes import wintypes
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402
from PIL import ImageGrab  # noqa: E402

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
    return phys, logical


CONTROLLER_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema Controller port test</title>
<style>
body{margin:0;background:#1E2228;color:#E5E7EB;font:14px sans-serif}
#page{padding:16px}
h3{margin:0 0 10px}
.section{border:1px solid #3A3F4B;border-radius:8px;padding:10px;margin-bottom:12px}
.section h4{margin:0 0 6px;font-size:12px;text-transform:uppercase;color:#9CA3AF}
#preview{width:100%;height:160px;object-fit:contain;background:#000;border-radius:4px}
#status,#latency{color:#9CA3AF;font-size:12px;margin:2px 0}
button{background:#5B8FF7;color:#111;border:none;border-radius:6px;padding:7px 14px;
 font-weight:600;cursor:pointer;margin-right:8px}
</style></head><body>
<div id="page">
  <h3>Rhema Controller - port test</h3>
  <div class="section">
    <h4>Preview</h4>
    <img id="preview" alt="output preview">
  </div>
  <div class="section">
    <h4>Status</h4>
    <div id="status">Status: Listening</div>
    <div id="latency">Latency: -- ms</div>
    <button id="pause">Pause</button>
    <button id="toggle">Toggle Fullscreen</button>
  </div>
</div>
<script>
document.getElementById('toggle').addEventListener('click', async () => {
  const r = await pywebview.api.toggle_output_fullscreen()
  document.getElementById('status').textContent = 'Status: fullscreen=' + r.is_fullscreen
})
document.getElementById('pause').addEventListener('click', async () => {
  const r = await pywebview.api.toggle_pause()
  document.getElementById('pause').textContent = r.paused ? 'Resume' : 'Pause'
})
function setPreview(dataUri){ document.getElementById('preview').src = dataUri }
function previewSrc(){ return document.getElementById('preview').src }
</script>
</body></html>
"""


class ControllerApi:
    def __init__(self, output_window, target_phys, target_logical):
        self.output_window = output_window
        self.target_phys = target_phys
        self.target_logical = target_logical
        self.is_fullscreen = False
        self.paused = False

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
        self.paused = not self.paused
        return {"paused": self.paused}


def _capture_via_printwindow(output_hwnd):
    return ImageGrab.grab(window=output_hwnd)


def _capture_via_bitblt(output_hwnd):
    x, y, w, h = _real_window_rect(output_hwnd)
    return ImageGrab.grab(bbox=(x, y, x + w, y + h))


def _capture_output_png_bytes(output_hwnd):
    # BitBlt (bbox), NOT PrintWindow (window=) - see the module docstring's
    # capture-technique finding. Requires the window to actually be the
    # visible, unoccluded content at that screen position, unlike
    # PrintWindow's (unsupported-by-WebView2, but in principle
    # occlusion-independent) approach - a real caveat for any port using
    # this technique, not a flaw specific to this file's test.
    shot = _capture_via_bitblt(output_hwnd)
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    return buf.getvalue()


def main():
    log = []

    def report(label, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label}" + (f" - {detail}" if detail else ""), flush=True)
        log.append((status, label, detail))

    target_phys, target_logical = _target_monitor_logical()

    from web_output_window import HTML as OUTPUT_HTML

    # No explicit x/y here - left at pywebview's default placement (which
    # landed on the primary monitor every time this was tested) rather than
    # the non-primary target monitor phase 2's Toggle Fullscreen check
    # moves it to later. That default placement is what phase 1's capture
    # check actually needs - see the module docstring's BitBlt/monitor
    # finding: this machine's BitBlt capture returns solid black for its
    # non-primary monitors regardless of content, unrelated to WebView2 or
    # anything code-related, and phase 1 must not get caught by it.
    output_window = webview.create_window(
        "Rhema - output (port test)",
        html=OUTPUT_HTML,
        width=480,
        height=300,
        background_color="#000000",
    )

    engine_box = {}

    def on_output_loaded():
        canvas = WebCanvas(output_window, width=1, height=1)
        measurer = WebMeasurer(output_window, FONT_FAMILY, 50, PIXELS_PER_INCH)
        engine = OutputEngine(lambda p: None, canvas, measurer, PIXELS_PER_INCH)
        engine_box["engine"] = engine
        engine.max_lines = 3
        engine._commit_display_piece("First caption for the preview screenshot test.", "display_commit")

        def phase1_isolated_capture_check():
            # Deliberately run BEFORE the Controller window exists at all -
            # a controlled, single-window measurement of the actual capture
            # question this file exists to answer, kept separate from
            # phase 2's full two-window assembly (whose own reliability
            # turned out to be a separate, real finding - see below and the
            # module docstring). Confirmed repeatable in this shape.
            time.sleep(0.5)  # let the real render finish painting first
            output_hwnd = _hwnd_for(output_window)

            printwindow_shot = _capture_via_printwindow(output_hwnd)
            printwindow_extrema = printwindow_shot.convert("L").getextrema()
            report(
                "[isolated] PrintWindow (window=) against a live WebView2 window is flat/blank",
                printwindow_extrema[0] == printwindow_extrema[1],
                f"grayscale extrema={printwindow_extrema}",
            )

            bitblt_shot = _capture_via_bitblt(output_hwnd)
            bitblt_extrema = bitblt_shot.convert("L").getextrema()
            report(
                "[isolated] BitBlt (bbox) against the same window sees real content instead",
                bitblt_extrema[0] != bitblt_extrema[1],
                f"grayscale extrema={bitblt_extrema}",
            )

            phase2_assemble_controller(output_hwnd)

        Thread(target=phase1_isolated_capture_check, daemon=True).start()

    def phase2_assemble_controller(output_hwnd):
        # Deliberately NOT attaching menu=... here. web_menu_bar.py and
        # web_startup_overlay.py already separately prove the real menu bar
        # mechanism in depth; combining one with this file's two-window
        # setup produced a reproducible-but-intermittent hang (see the
        # module docstring's instability finding) - left out to keep this
        # phase's own focus on the screenshot/cross-window mechanisms.
        api = ControllerApi(output_window, target_phys, target_logical)
        controller_window = webview.create_window(
            "Rhema Controller - port test",
            html=CONTROLLER_HTML,
            js_api=api,
            width=420,
            height=440,
            background_color="#1E2228",
        )

        def on_controller_loaded():
            report("Controller window loaded alongside the Output window", True)

            png_1 = _capture_output_png_bytes(output_hwnd)
            data_uri_1 = "data:image/png;base64," + base64.b64encode(png_1).decode("ascii")
            controller_window.evaluate_js(f"setPreview({__import__('json').dumps(data_uri_1)})")
            got_src = controller_window.evaluate_js("previewSrc()")
            report("Controller's preview <img> actually received the captured screenshot", got_src == data_uri_1)

            # --- Liveness: change the real caption, capture again, must differ ---
            engine = engine_box["engine"]
            engine._commit_display_piece("Second, different caption - the capture must change too.", "display_commit")
            time.sleep(0.5)
            png_2 = _capture_output_png_bytes(output_hwnd)
            report(
                "a second capture after new content differs from the first (not a frozen/cached image)",
                png_2 != png_1,
                f"first={len(png_1)} bytes, second={len(png_2)} bytes",
            )

            # --- Cross-window Toggle Fullscreen, integrated into the real shell ---
            result = api.toggle_output_fullscreen()
            time.sleep(0.4)
            actual = _real_window_rect(output_hwnd)
            report(
                "Toggle Fullscreen button (in the full shell) still lands correctly on the target monitor",
                result["is_fullscreen"] and actual == target_phys,
                f"actual={actual} expected={target_phys}",
            )

            def finish():
                time.sleep(1.5)
                api.toggle_output_fullscreen()  # back off, tidy
                ok = all(status == "PASS" for status, _label, _detail in log)
                print("\nRESULT: " + ("ALL PASS" if ok else "SOME FAILURES - see above"), flush=True)
                controller_window.destroy()
                time.sleep(0.3)
                try:
                    output_window.destroy()
                except Exception:
                    pass

            Thread(target=finish, daemon=True).start()

        controller_window.events.loaded += on_controller_loaded

    output_window.events.loaded += on_output_loaded
    webview.start()


if __name__ == "__main__":
    main()
