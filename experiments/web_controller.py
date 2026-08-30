"""Port test: Rhema's Controller in pywebview, driven by the real engine code.

This is not a port. It is the smallest thing that answers the two questions a
port depends on, using real code and a real microphone rather than a mock:

1. **Can the engine run without Tk?** Rhema's logic lives in mixins that are
   combined into `TranslationApp`, which builds a Tk root in `__init__`. If the
   mixins themselves are Tk-free, they can be driven by any UI. This mixes in
   the two that own audio metering and supplies only the handful of attributes
   they touch - no Tk anywhere in the process.

2. **Can pywebview take live engine state?** The meter updates ~20x a second
   from a capture thread, which is the same shape as every other signal the
   Controller shows.

It deliberately uses the audio meter, because that path is already understood:
`_capture_audio_level_from_raw` -> `_audio_rms`/`_audio_max` -> a dBFS curve.
If the number moves when you speak, real engine code is running under a web UI.

Setup: .venv\Scripts\pip.exe install pywebview   (not in requirements.txt -
       this experiment is not imported by the shipping app, so it is not a
       real dependency yet)

Run:  .venv\\Scripts\\python.exe experiments\\web_controller.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import json
import os
import sys
import threading
import time

# Import the app's own modules, not copies of them.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyaudio  # noqa: E402
import webview  # noqa: E402

from audio_capture_mixin import AudioCaptureMixin  # noqa: E402
from display_mixin import DisplayMixin  # noqa: E402

SAMPLE_RATE = 16000
FRAMES = 1024


class HeadlessMeter(AudioCaptureMixin, DisplayMixin):
    """The real metering code with no Tk underneath it.

    Only the attributes those two methods actually read are provided. That the
    list is this short is itself the finding: the audio path is not entangled
    with the UI, it just happens to live in a class that builds one.
    """

    def __init__(self):
        self.audio_level_target = 0.0
        self.audio_level_last_update = 0.0
        self.audio_level_floor_db = -55.0


class Api:
    """Exposed to the page. Holds no window references - pywebview introspects
    this object, and handing it a Window makes it fail with an unreadable
    serialization error."""

    def __init__(self):
        self.running = True

    def stop(self):
        self.running = False


def capture_loop(window, meter: HeadlessMeter, api: Api):
    pa = pyaudio.PyAudio()
    try:
        info = pa.get_default_input_device_info()
        device_name = str(info.get("name", "unknown"))
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=FRAMES,
        )
    except Exception as e:
        _push(window, {"device": f"no input device: {e}", "level": 0, "peak": 0})
        return

    _push(window, {"device": device_name, "level": 0, "peak": 0})

    try:
        while api.running:
            raw = stream.read(FRAMES, exception_on_overflow=False)
            # The app's own code, unchanged.
            meter._capture_audio_level_from_raw(raw, 2)
            peak = meter._audio_max(raw, 2)
            _push(
                window,
                {
                    "device": device_name,
                    "level": round(meter.audio_level_target, 1),
                    "peak": peak,
                },
            )
    except Exception:
        return
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        pa.terminate()


def _push(window, state):
    try:
        window.evaluate_js(f"applyState({json.dumps(state)})")
    except Exception:
        pass


HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
:root{--bg:#1E2228;--card:#262A33;--text:#E5E7EB;--muted:#9CA3AF;--border:#3A3F4B;--accent:#5B8FF7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);user-select:none;
 font:13px/1.55 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.app{padding:14px;display:flex;flex-direction:column;gap:10px;height:100vh}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px}
.card h2{margin:0 0 10px;font-size:10.5px;font-weight:600;letter-spacing:.09em;
 text-transform:uppercase;color:var(--muted)}
.grid{display:grid;grid-template-columns:auto 1fr;gap:8px 14px;align-items:center;font-size:12px}
.grid .k{color:var(--muted)}
.num{font-variant-numeric:tabular-nums}
.meter{height:10px;background:#14171C;border:1px solid var(--border);border-radius:999px;overflow:hidden}
.meter>div{height:100%;width:0;border-radius:999px;
 background:linear-gradient(90deg,var(--accent),#7AA5FF);transition:width 70ms linear}
.note{color:#6B7280;font-size:11px;line-height:1.5}
code{color:#9BB4E8}
</style></head><body>
<div class="app">
  <div class="card">
    <h2>Live audio — real engine code, no Tkinter</h2>
    <div class="grid">
      <span class="k">Input</span><span id="device">opening…</span>
      <span class="k">Level</span><span class="num" id="level">0.0%</span>
      <span class="k">Raw peak</span><span class="num" id="peak">0</span>
      <span class="k">Meter</span><div class="meter"><div id="bar"></div></div>
    </div>
  </div>
  <div class="card">
    <h2>What this proves</h2>
    <p class="note">
      The number above is produced by <code>DisplayMixin._capture_audio_level_from_raw</code>
      and <code>AudioCaptureMixin._audio_rms</code> — imported from the app, not reimplemented.
      There is no Tk root in this process, so the metering path is not entangled with the UI.
      Speak, and the bar should move.
    </p>
  </div>
</div>
<script>
function applyState(s){
  device.textContent = s.device
  level.textContent = s.level.toFixed(1) + '%'
  peak.textContent = s.peak
  bar.style.width = s.level + '%'
}
</script></body></html>
"""


def main():
    api = Api()
    meter = HeadlessMeter()
    window = webview.create_window(
        "Rhema — pywebview port test",
        html=HTML,
        width=460,
        height=420,
        background_color="#1E2228",
        js_api=api,
    )
    # Started from `loaded`, not before webview.start(): the window does not
    # exist until the GUI loop is running, and pushing into it early fails with
    # an unreadable ObjectDisposedException from the WebView2 layer.
    def on_loaded():
        threading.Thread(
            target=capture_loop, args=(window, meter, api), daemon=True
        ).start()

    window.events.loaded += on_loaded
    webview.start()
    api.running = False


if __name__ == "__main__":
    main()
