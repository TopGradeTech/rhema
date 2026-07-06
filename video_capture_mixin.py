import time
from threading import Thread, Lock, Event

import cv2
from PIL import Image, ImageTk

_VIDEO_RETRY_DELAY = 5.0  # seconds to wait after a failed open before retrying

# OBS Virtual Camera (and some other virtual/software cameras) register with
# Windows only through Media Foundation, not the legacy DirectShow API, so a
# CAP_DSHOW-only open silently fails to see them ("backend is generally
# available but can't be used to capture by index" in the console). Try MSMF
# first and fall back to DSHOW for devices that only support the old API.
_VIDEO_BACKENDS = (cv2.CAP_MSMF, cv2.CAP_DSHOW)

# DirectShow/Media Foundation otherwise often negotiate a low default capture
# mode (e.g. 640x480) even though OBS's Virtual Camera is actually serving
# frames at OBS's configured canvas resolution. Requesting a size comfortably
# bigger than any realistic OBS canvas makes the backend clamp UP to the
# highest native mode the device actually offers, which is a one-time,
# zero-cost negotiation.
#
# Deliberately NOT tied to the output monitor's resolution: if that happened
# to be smaller than OBS's real canvas (e.g. output on a 1080p screen while
# OBS renders at a higher canvas for stream quality), requesting the smaller
# monitor size asks some virtual-camera drivers to scale every frame down to
# fit in real time instead of doing a one-time clamp, which caused visible
# flicker when frame delivery couldn't keep up. The actual "fit to the
# output screen" behavior already happens downstream in _render_video_frame,
# which letterboxes whatever the camera's real decoded frame size is into
# the actual canvas size every frame - independent of what we request here.
_VIDEO_FALLBACK_WIDTH = 7680
_VIDEO_FALLBACK_HEIGHT = 4320

_VIDEO_CAPTION_BAR_COLOR = (60, 60, 60)  # RGB gray
_VIDEO_CAPTION_BAR_ALPHA = 0.5  # default opacity; user-adjustable via the Caption Bar Opacity slider

_VIDEO_MIN_RENDER_INTERVAL_MS = 16  # don't bother redrawing faster than ~60fps


def _open_capture(index, requested_width=_VIDEO_FALLBACK_WIDTH, requested_height=_VIDEO_FALLBACK_HEIGHT):
    for backend in _VIDEO_BACKENDS:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, requested_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, requested_height)
            # Without this the backend's internal frame queue defaults to
            # holding several frames. Our read loop only falls a little
            # behind (GIL contention with the Tk main thread, an occasional
            # slow cvtColor) before that queue is consuming more than one
            # slot, and once it's full the backend starts delivering
            # increasingly stale frames in uneven bursts instead of dropping
            # them - which is why the flicker took roughly a minute to show
            # up rather than being there from the first frame. Forcing a
            # 1-frame buffer makes cap.read() always return the newest
            # frame, dropping anything we didn't get to in time instead of
            # queueing it.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        cap.release()
    return None


class VideoCaptureMixin:
    """Video Feed mode: decodes the OBS Virtual Camera and draws it as a
    background layer on the output canvas, behind the caption text.

    OBS's Virtual Camera mirrors the same composited "Program" frames OBS
    encodes and sends to its stream outputs (YouTube/Facebook Live), as
    long as its Output Type is left on "Program" (the default), so this is
    a pixel-accurate mirror of what's live, not a re-encoded copy.
    """

    def _video_capture_defaults(self):
        self.video_feed_enabled = False
        self.video_device_index = None
        self.video_devices = []
        self.video_status = "Not connected"
        self._video_stop_event = Event()
        self._video_capture_thread = None
        self._video_frame_lock = Lock()
        self._video_latest_frame = None
        self._video_photo_image = None
        self._caption_bar_photo_image = None
        self._caption_bar_cache_key = None
        self._video_render_after_id = None
        # Fallback until the actual capture fps is detected (see
        # _video_capture_worker) - overwritten as soon as the device
        # reports a usable CAP_PROP_FPS, so the render tick can track
        # OBS's real output rate instead of an arbitrary guess.
        self.video_render_interval_ms = 33
        self.video_caption_bar_alpha = _VIDEO_CAPTION_BAR_ALPHA

    # ------------------------------------------------------------------ #
    # Device enumeration                                                    #
    # ------------------------------------------------------------------ #

    def enumerate_video_devices(self, max_probe=5):
        """Probe camera indices for availability. Blocking - callers must
        run this off the Tk thread (opening/closing a camera device that
        doesn't exist can take noticeably longer than a hit, and each index
        is now tried against two backends)."""
        available = []
        for index in range(max_probe):
            cap = _open_capture(index)
            if cap is not None:
                available.append(index)
                cap.release()
        return available

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start_video_feed(self):
        if self._video_capture_thread is not None:
            return
        if not self.video_feed_enabled:
            return
        if self.video_device_index is None:
            return
        self._video_stop_event.clear()
        self._video_capture_thread = Thread(
            target=self._video_capture_worker, args=(self.video_device_index,), daemon=True
        )
        self._video_capture_thread.start()
        self._apply_video_feed_visibility()
        self._schedule_video_render_tick()

    def stop_video_feed(self):
        self._video_stop_event.set()
        outgoing_thread = self._video_capture_thread
        if outgoing_thread is not None:
            # cv2.VideoCapture open/close for virtual cameras (MSMF/DSHOW)
            # can block for a second or more. Clearing the reference here
            # without waiting let start_video_feed()'s "already running"
            # guard see None and spawn a second thread that fights the
            # still-alive first one over the same device - stacking up
            # enough blocking native calls in a row (e.g. from repeated
            # Apply clicks while testing the opacity slider) starved the
            # main process's GIL badly enough that RealtimeSTT's own
            # subprocess pipe polling missed its window and the pipe
            # appeared to die ("Pipe ended" crash). Joining with a bound
            # keeps this from hanging forever if a device is truly stuck.
            outgoing_thread.join(timeout=3.0)
        self._video_capture_thread = None
        if self._video_render_after_id is not None:
            try:
                self.root.after_cancel(self._video_render_after_id)
            except Exception:
                pass
            self._video_render_after_id = None
        with self._video_frame_lock:
            self._video_latest_frame = None
        self._caption_bar_cache_key = None
        self._apply_video_feed_visibility()

    def _apply_video_feed_visibility(self):
        active = self.video_feed_enabled
        state = "normal" if active else "hidden"
        try:
            self.text_canvas.itemconfigure(self.video_image_item, state=state)
            self.text_canvas.itemconfigure(self.caption_bar_item, state=state)
        except Exception:
            pass
        if not active:
            try:
                self.text_canvas.itemconfigure(self.video_image_item, image="")
                self.text_canvas.itemconfigure(self.caption_bar_item, image="")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Worker                                                                #
    # ------------------------------------------------------------------ #

    def _video_capture_worker(self, device_index):
        cap = _open_capture(device_index)
        if cap is None:
            self.video_status = f"Camera {device_index} not found - start OBS Virtual Camera first"
            self.root.after(0, self._update_video_status_ui)
            return
        self.video_status = "Connected - waiting for first frame"
        self.root.after(0, self._update_video_status_ui)
        # Match the render tick to OBS's actual output rate instead of the
        # arbitrary fallback, so frames aren't held back or redrawn more
        # often than new ones actually arrive.
        reported_fps = cap.get(cv2.CAP_PROP_FPS)
        if reported_fps and reported_fps > 1:
            self.video_render_interval_ms = max(
                _VIDEO_MIN_RENDER_INTERVAL_MS, int(round(1000 / reported_fps))
            )
        consecutive_failures = 0
        # cap.get(CAP_PROP_FRAME_WIDTH/HEIGHT) only reports what the driver
        # claims to have negotiated, queried before any frame is decoded -
        # it can be stale or simply wrong for virtual cameras, and won't
        # notice if OBS's canvas resolution changes later. The actual
        # decoded frame size is ground truth for what OBS is really
        # serving, so report that instead, and whenever it changes.
        last_reported_size = None
        try:
            while not self._video_stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures > 30:
                        self.video_status = "Camera feed lost"
                        self.root.after(0, self._update_video_status_ui)
                        break
                    time.sleep(0.1)
                    continue
                consecutive_failures = 0
                frame_height, frame_width = frame.shape[:2]
                actual_size = (frame_width, frame_height)
                if actual_size != last_reported_size:
                    last_reported_size = actual_size
                    self.video_status = f"Connected ({frame_width}x{frame_height})"
                    self.root.after(0, self._update_video_status_ui)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with self._video_frame_lock:
                    self._video_latest_frame = rgb_frame
        finally:
            cap.release()

    def _update_video_status_ui(self):
        if hasattr(self, "video_status_var") and self.video_status_var is not None:
            try:
                self.video_status_var.set(self.video_status)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Render                                                                #
    # ------------------------------------------------------------------ #

    def _schedule_video_render_tick(self):
        self._video_render_after_id = self.root.after(
            self.video_render_interval_ms, self._video_render_tick
        )

    def _video_render_tick(self):
        self._video_render_after_id = None
        if self._video_stop_event.is_set() or self._video_capture_thread is None:
            return
        with self._video_frame_lock:
            frame = self._video_latest_frame
        if frame is not None:
            self._render_video_frame(frame)
        self._schedule_video_render_tick()

    def _render_video_frame(self, frame):
        canvas_width = self.text_canvas.winfo_width()
        canvas_height = self.text_canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        frame_height, frame_width = frame.shape[:2]
        if frame_width <= 0 or frame_height <= 0:
            return
        if frame_width == canvas_width and frame_height == canvas_height:
            # OBS's output resolution already matches the window exactly -
            # pass the frame through untouched rather than resampling it.
            target_width, target_height = frame_width, frame_height
            resized = frame
        else:
            # Fit inside the canvas (letterbox/pillarbox as needed) rather
            # than cropping to cover it - the whole OBS composition must
            # always stay visible, never zoomed/cropped past the window's
            # borders.
            scale = min(canvas_width / frame_width, canvas_height / frame_height)
            target_width = max(1, int(round(frame_width * scale)))
            target_height = max(1, int(round(frame_height * scale)))
            resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        image = Image.fromarray(resized)
        photo = ImageTk.PhotoImage(image)
        x = (canvas_width - target_width) // 2
        y = (canvas_height - target_height) // 2
        self._video_photo_image = photo
        try:
            self.text_canvas.coords(self.video_image_item, x, y)
            self.text_canvas.itemconfigure(self.video_image_item, image=photo)
        except Exception:
            pass
        self._update_caption_bar(canvas_width, canvas_height)

    def _video_caption_bar_height(self, canvas_height, line_height, lines):
        bar_height = int(round(line_height * lines + self.text_padding * (lines + 1)))
        return max(1, min(canvas_height, bar_height))

    def _update_caption_bar(self, canvas_width, canvas_height):
        """Docks a translucent gray bar to the bottom of the window itself
        (not the letterboxed video rect), sized for the caption lines. Drawn
        as its own true-alpha image on top of the video and below the
        caption text, so it reads consistently regardless of the camera's
        aspect ratio or any letterbox bars around it."""
        line_height = self.text_font.metrics("linespace") or 1
        lines = self._effective_max_lines()
        bar_height = self._video_caption_bar_height(canvas_height, line_height, lines)
        alpha = max(0.0, min(1.0, float(self.video_caption_bar_alpha)))
        cache_key = (canvas_width, bar_height, alpha)
        try:
            self.text_canvas.coords(self.caption_bar_item, 0, canvas_height)
        except Exception:
            pass
        if getattr(self, "_caption_bar_cache_key", None) == cache_key:
            return
        # This bar's appearance only changes with window size or the opacity
        # setting, not per-frame - rebuilding the PIL image and PhotoImage
        # from scratch on every single video render tick (30-60x/sec) was
        # pure waste that ate into the same per-tick time budget the video
        # frame itself needs, making it more likely to fall behind under load.
        r, g, b = _VIDEO_CAPTION_BAR_COLOR
        bar_image = Image.new("RGBA", (canvas_width, bar_height), (r, g, b, int(round(alpha * 255))))
        photo = ImageTk.PhotoImage(bar_image)
        self._caption_bar_photo_image = photo
        self._caption_bar_cache_key = cache_key
        try:
            self.text_canvas.itemconfigure(self.caption_bar_item, image=photo)
        except Exception:
            pass
