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

"""Phase 4 of the pywebview port: the video overlay (OBS Virtual Camera /
webcam background + translucent caption bar), adapted from
experiments/web_video_overlay.py's already-proven data-URI approach.

VideoCaptureMixin's device enumeration, capture-thread lifecycle, letterbox
math (_letterbox_fit_frame), and frame-size reporting are already Tk-free
(they never touch text_canvas) and are reused completely unmodified via
subclassing. Its render path is not: the real _render_video_frame/
_update_caption_bar both build a tkinter.PhotoImage and, in the frame case,
mutate its pixels in place with .paste() to avoid reallocating a
canvas-sized image object every tick (409-451 in video_capture_mixin.py) -
a real optimization with no browser equivalent, since PhotoImage IS the
Tk-specific thing here, not a detail sitting on top of one. So this
overrides exactly those two methods, encoding each frame to a JPEG data:
URI (letterboxed to size first, identical math to the methods being
replaced) and pushing it through WebCanvas's create_image/image= support
instead of PhotoImage.paste().

experiments/web_video_overlay.py already measured whether a data-URI-per-
frame approach is fast enough to feel live rather than merely correct (it
is, at 80% JPEG quality) - this reuses that same encoding, not a fresh
guess.
"""

import base64
import io

import cv2
from PIL import Image, ImageColor

from video_capture_mixin import VideoCaptureMixin, _VIDEO_CAPTION_BAR_COLOR

JPEG_QUALITY = 80


class WebVideoCaptureMixin(VideoCaptureMixin):
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

        # The worker stores frames already converted to RGB
        # (_video_capture_worker's own COLOR_BGR2RGB, for the PhotoImage
        # path this method replaces); cv2.imencode treats its input as BGR
        # regardless of what it actually is, so this flips it back rather
        # than teaching the (unmodified) worker a second color convention.
        bgr = cv2.cvtColor(resized, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return
        data_uri = "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")

        try:
            self.text_canvas.coords(self.video_image_item, x, y)
            self.text_canvas.itemconfigure(self.video_image_item, image=data_uri)
            self._update_caption_bar(canvas_width, canvas_height)
            # WebCanvas queues draw ops rather than painting immediately the
            # way a real Tk Canvas does - without this, video frames would
            # only reach the browser whenever some OTHER code path (e.g.
            # render_text() on the next caption change) happened to flush,
            # making video update at caption speed instead of its own
            # capture-tick rate. Proved in experiments/web_video_overlay.py.
            self.text_canvas.flush()
        except Exception:
            return

    def _update_caption_bar(self, canvas_width, canvas_height):
        """Docks a translucent bar (tinted with the Background Color
        setting) to the bottom of the window itself, sized for the caption
        lines - same real behavior as the method this replaces, including
        the rebuild-only-on-change caching (_caption_bar_cache_key): the
        bar's appearance only depends on window size/color/opacity, not on
        per-frame content, so encoding a fresh PNG data: URI on every video
        tick (30-60x/sec) would be pure waste competing with the frame
        itself for the same per-tick time budget."""
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
        bar_image = Image.new("RGBA", (canvas_width, bar_height), (r, g, b, int(round(alpha * 255))))
        buf = io.BytesIO()
        bar_image.save(buf, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        self._caption_bar_cache_key = cache_key
        try:
            self.text_canvas.itemconfigure(self.caption_bar_item, image=data_uri)
        except Exception:
            pass
