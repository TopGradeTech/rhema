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


def _open_capture(index):
    for backend in _VIDEO_BACKENDS:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
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
        self._video_render_after_id = None
        self.video_render_interval_ms = 66  # ~15fps canvas redraw cap

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
        self._video_capture_thread = None
        if self._video_render_after_id is not None:
            try:
                self.root.after_cancel(self._video_render_after_id)
            except Exception:
                pass
            self._video_render_after_id = None
        with self._video_frame_lock:
            self._video_latest_frame = None
        self._apply_video_feed_visibility()

    def _apply_video_feed_visibility(self):
        active = self.video_feed_enabled
        state = "normal" if active else "hidden"
        try:
            self.text_canvas.itemconfigure(self.video_image_item, state=state)
        except Exception:
            pass
        if not active:
            try:
                self.text_canvas.itemconfigure(self.video_image_item, image="")
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
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        self.video_status = f"Connected ({width}x{height})" if width and height else "Connected"
        self.root.after(0, self._update_video_status_ui)
        consecutive_failures = 0
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
        scale = min(canvas_width / frame_width, canvas_height / frame_height)
        target_width = max(1, int(frame_width * scale))
        target_height = max(1, int(frame_height * scale))
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
