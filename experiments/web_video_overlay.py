r"""Port test: the video overlay (OBS Virtual Camera background + caption
bar) drawn onto the same fullscreen canvas web_output_window.py proved for
captions alone.

Builds directly on that file rather than duplicating it: OutputEngine,
WebCanvas and WebMeasurer are imported from there unmodified, and this file
adds exactly one mixin (VideoCaptureMixin) plus two method overrides.

VideoCaptureMixin's device enumeration, capture-thread lifecycle, letterbox
math and frame-size reporting are already Tk-free (they never touch
text_canvas) and are reused as-is. Its render path is not: _render_video_frame
and _update_caption_bar both build a tkinter.PhotoImage and, in the frame
case, mutate its pixels in place with .paste() to avoid reallocating a
canvas-sized image object every tick - a real optimization, but one with no
browser equivalent. There is no "swap the measurer" move available here the
way there was for text (caption_layout_probe.py) or the canvas primitives
(web_output_window.py) - PhotoImage IS the Tk-specific thing, not a
detail sitting on top of one. So this file overrides those two methods
instead of leaving them alone, encoding each frame to a JPEG data: URI
(letterboxed to size first, exactly like the original) and pushing it
through WebCanvas's new create_image/image= support (added to
web_output_window.py for this file) instead of PhotoImage.paste().

Whether that's fast enough to feel live, rather than merely correct, is the
actual open question this file exists to answer - unlike the text path,
there's no reason to assume a data-URI-per-frame approach is free. It prints
a running average of encoded frame size and per-frame JS push time so the
answer is a number, not a guess.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)
       Start OBS (or any camera) before running, or this proves the
       graceful-no-camera path instead - both are real app behavior.

Run:  .venv\Scripts\python.exe experiments\web_video_overlay.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import base64
import os
import sys
import time
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2  # noqa: E402
from PIL import Image, ImageColor  # noqa: E402
import webview  # noqa: E402

from video_capture_mixin import VideoCaptureMixin, _VIDEO_CAPTION_BAR_COLOR  # noqa: E402
from web_output_window import (  # noqa: E402
    FONT_FAMILY,
    HTML,
    PIXELS_PER_INCH,
    OutputEngine,
    WebCanvas,
    WebMeasurer,
)

JPEG_QUALITY = 80


class FrameStats:
    """Running average of encode size/time and push time, printed
    periodically - the actual verdict this experiment is built to produce."""

    def __init__(self, report_every=5.0):
        self.report_every = report_every
        self.count = 0
        self.encode_ms_total = 0.0
        self.push_ms_total = 0.0
        self.bytes_total = 0
        self.last_report = time.monotonic()

    def record(self, encode_ms, push_ms, nbytes):
        self.count += 1
        self.encode_ms_total += encode_ms
        self.push_ms_total += push_ms
        self.bytes_total += nbytes
        now = time.monotonic()
        if now - self.last_report >= self.report_every and self.count:
            elapsed = now - self.last_report
            print(
                "[video] %.1f fps  avg encode %.1fms  avg push %.1fms  avg size %.0f KB  (%d frames)"
                % (
                    self.count / elapsed,
                    self.encode_ms_total / self.count,
                    self.push_ms_total / self.count,
                    (self.bytes_total / self.count) / 1024.0,
                    self.count,
                ),
                flush=True,
            )
            self.count = 0
            self.encode_ms_total = 0.0
            self.push_ms_total = 0.0
            self.bytes_total = 0
            self.last_report = now


class VideoOutputEngine(VideoCaptureMixin, OutputEngine):
    """OutputEngine (real DisplayMixin/SettingsUIMixin, unmodified) plus real
    VideoCaptureMixin, with only its two PhotoImage-coupled methods replaced.
    Everything else VideoCaptureMixin defines - enumerate_video_devices,
    start_video_feed/stop_video_feed, _video_capture_worker, letterbox
    fitting, frame-size reporting - runs exactly as shipped."""

    def __init__(self, push_debug, canvas, measurer, pixels_per_inch):
        super().__init__(push_debug, canvas, measurer, pixels_per_inch)
        self._video_capture_defaults()
        # main.py creates these right after text_item and before any
        # text_line_items exist, so they get lower ids than the caption
        # lines _ensure_line_items lazily creates on the first render_text()
        # - WebCanvas draws by ascending id, so that ordering alone puts
        # video and the caption bar behind the captions, matching Tk's own
        # stacking without any extra z-index bookkeeping.
        self.video_image_item = self.text_canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.caption_bar_item = self.text_canvas.create_image(0, 0, anchor="sw", state="hidden")
        self.video_status_var = None
        self.stats = FrameStats()

    def _update_video_status_ui(self):
        self.push_debug({"type": "status", "text": self.video_status})

    def _render_video_frame(self, frame):
        canvas_width = self.text_canvas.winfo_width()
        canvas_height = self.text_canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        self._video_canvas_size = (canvas_width, canvas_height)
        frame_height, frame_width = frame.shape[:2]
        if frame_width <= 0 or frame_height <= 0:
            return
        # Same fit-inside-the-canvas math as the real method - the worker
        # normally delivers frames already letterboxed to size, so this
        # resize is usually a no-op (first frames / just-resized only).
        scale = min(canvas_width / frame_width, canvas_height / frame_height)
        target_width = max(1, int(round(frame_width * scale)))
        target_height = max(1, int(round(frame_height * scale)))
        if (target_width, target_height) == (frame_width, frame_height):
            resized = frame
        else:
            resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        x = (canvas_width - target_width) // 2
        y = (canvas_height - target_height) // 2

        t0 = time.monotonic()
        # The worker stores frames already converted to RGB (for the PIL
        # path this method replaces); cv2.imencode treats its input as BGR
        # regardless of what it actually is, so this flips it back rather
        # than teaching the (unmodified) worker a second color convention.
        bgr = cv2.cvtColor(resized, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return
        data_uri = "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")
        t1 = time.monotonic()

        try:
            self.text_canvas.coords(self.video_image_item, x, y)
            self.text_canvas.itemconfigure(self.video_image_item, image=data_uri)
            self._update_caption_bar(canvas_width, canvas_height)
            self.text_canvas.flush()
        except Exception:
            return
        t2 = time.monotonic()
        self.stats.record((t1 - t0) * 1000.0, (t2 - t1) * 1000.0, len(data_uri))

    def _update_caption_bar(self, canvas_width, canvas_height):
        line_height = self.text_font.metrics("linespace") or 1
        lines = self._effective_max_lines()
        bar_height = self._video_caption_bar_height(canvas_height, line_height, lines)
        alpha = max(0.0, min(1.0, float(self.video_caption_bar_alpha)))
        try:
            r, g, b = ImageColor.getrgb(self.bg_color)[:3]
        except Exception:
            r, g, b = _VIDEO_CAPTION_BAR_COLOR
        cache_key = (canvas_width, bar_height, alpha, (r, g, b))
        try:
            self.text_canvas.coords(self.caption_bar_item, 0, canvas_height)
        except Exception:
            pass
        if getattr(self, "_caption_bar_cache_key", None) == cache_key:
            return
        # Same rebuild-only-on-change caching as the real method - the bar's
        # appearance only depends on window size/color/opacity, not on
        # per-frame content, so this only runs on resize or a settings change.
        import io

        bar_image = Image.new("RGBA", (canvas_width, bar_height), (r, g, b, int(round(alpha * 255))))
        buf = io.BytesIO()
        bar_image.save(buf, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        self._caption_bar_cache_key = cache_key
        try:
            self.text_canvas.itemconfigure(self.caption_bar_item, image=data_uri)
        except Exception:
            pass


def main():
    window = webview.create_window(
        "Rhema - video overlay port test",
        html=HTML,
        fullscreen=True,
        background_color="#000000",
    )

    def push_debug(payload):
        try:
            window.evaluate_js(f"applyDebug({__import__('json').dumps(payload)})")
        except Exception:
            pass

    state = {}

    def on_loaded():
        dims = window.evaluate_js("initCanvas()")
        width, height = int(dims["w"]), int(dims["h"])
        canvas = WebCanvas(window, width, height)
        measurer = WebMeasurer(window, FONT_FAMILY, 50, PIXELS_PER_INCH)
        engine = VideoOutputEngine(push_debug, canvas, measurer, PIXELS_PER_INCH)
        state["engine"] = engine
        engine.render_text()

        print("Probing camera devices (start OBS Virtual Camera first for a real feed)...", flush=True)
        available = engine.enumerate_video_devices()
        if available:
            index = available[0]
            print(f"Using {engine._video_device_label(index)}", flush=True)
            engine.video_device_index = index
            engine.video_feed_enabled = True
            engine.start_video_feed()
        else:
            print("No camera found - proving the graceful no-camera path (captions only).", flush=True)
            engine.video_feed_enabled = False

        engine.translation_thread = Thread(target=engine._translation_worker, daemon=True)
        engine.translation_thread.start()
        engine.display_thread = Thread(target=engine._display_worker, daemon=True)
        engine.display_thread.start()
        Thread(target=engine.prewarm_translation, daemon=True).start()
        engine._start_realtime_stt()

    def on_closed():
        engine = state.get("engine")
        if engine is None:
            return
        engine.listening = False
        try:
            engine.stop_video_feed()
        except Exception:
            pass
        try:
            engine._stop_realtime_stt()
        except Exception:
            pass
        try:
            engine._force_kill_realtime_stt_processes()
        except Exception:
            pass

    window.events.loaded += on_loaded
    window.events.closed += on_closed
    webview.start()


if __name__ == "__main__":
    main()
