import time
from threading import Thread, Lock, Event

import cv2
from PIL import Image, ImageColor, ImageTk

# OBS Virtual Camera (and some other virtual/software cameras) register with
# Windows only through Media Foundation, not the legacy DirectShow API, so a
# CAP_DSHOW-only open silently fails to see them ("backend is generally
# available but can't be used to capture by index" in the console). Try MSMF
# first and fall back to DSHOW for devices that only support the old API.
_VIDEO_BACKENDS = (cv2.CAP_MSMF, cv2.CAP_DSHOW)

# DirectShow/Media Foundation otherwise often negotiate a low default capture
# mode (e.g. 640x480) even though OBS's Virtual Camera is actually serving
# frames at OBS's configured canvas resolution, so an explicit size must be
# requested.
#
# Do NOT request a huge "bigger than any canvas" size hoping the backend
# clamps down to the device's highest native mode: MSMF instead SCALES
# frames up to whatever is requested. A 7680x4320 request made it deliver
# genuine 8K frames (~95MB each) from OBS's 60fps output, which capped
# delivery at ~15fps from the copy cost alone (confirmed via the decoded
# frame size in the Video Feed status label, 2026-07-10). 1920x1080
# matches the common OBS canvas, so it's normally a zero-cost passthrough;
# if OBS's canvas differs the driver scales to 1080p, which is still cheap
# and gets letterboxed to the real output size downstream
# (_letterbox_fit_frame on the capture thread, with a main-thread fallback
# in _render_video_frame) like any other decoded size.
_VIDEO_FALLBACK_WIDTH = 1920
_VIDEO_FALLBACK_HEIGHT = 1080

_VIDEO_CAPTION_BAR_COLOR = (60, 60, 60)  # RGB gray fallback if bg_color fails to parse
_VIDEO_CAPTION_BAR_ALPHA = 0.5  # default opacity; user-adjustable via the Caption Bar Opacity slider

_VIDEO_MIN_RENDER_INTERVAL_MS = 16  # don't bother redrawing faster than ~60fps


def _probe_video_device_names():
    """Best-effort {index: friendly name} map via DirectShow enumeration
    (pygrabber), e.g. {0: "Brio 101", 1: "OBS Virtual Camera"}. DirectShow
    and Media Foundation enumerate the same underlying device set in the
    same order, so this lines up with the numeric indices _open_capture
    uses even though it tries MSMF first - a device that fails to open via
    MSMF and falls back to DSHOW is still the same index, not a different
    one. Returns {} on any failure (pygrabber missing, no COM access,
    etc.) so callers just fall back to bare "Camera N" labels."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
        return dict(enumerate(names))
    except Exception:
        return {}


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
        self.video_device_names = {}
        self.video_status = "Not connected"
        self._video_stop_event = Event()
        self._video_capture_thread = None
        self._video_frame_lock = Lock()
        self._video_latest_frame = None
        # Sequence number bumped by the capture worker on every new frame;
        # the render tick compares it against the last-drawn sequence and
        # skips the whole PIL/PhotoImage/canvas pipeline when nothing new
        # arrived. OBS's Virtual Camera reports CAP_PROP_FPS=-1 (measured
        # delivery is ~15fps on this machine), so the tick can't be paced to
        # the real rate up front - instead it polls at ~30Hz cheaply and only
        # pays for a draw when there's actually a fresh frame.
        self._video_frame_seq = 0
        self._video_drawn_seq = 0
        # Last known canvas size, published by the Tk thread for the capture
        # worker so it can letterbox-resize frames off the main thread.
        self._video_canvas_size = (0, 0)
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

    def enumerate_video_devices(self, max_probe=5, on_progress=None):
        """Probe camera indices for availability. Blocking - callers must
        run this off the Tk thread (opening/closing a camera device that
        doesn't exist can take noticeably longer than a hit, and each index
        is now tried against two backends). Also refreshes
        self.video_device_names (friendly names) as a side effect.

        max_probe is a ceiling, not a target: a failed open is not cheap
        (the backend still walks the OS device topology), so the no-camera
        case used to be the *slowest* one - probing all max_probe indices
        against both backends for nothing. pygrabber's friendly-name list
        (_probe_video_device_names) already tells us how many real DirectShow
        devices exist, and its index order is confirmed to match DSHOW's own
        (see _video_device_label's caller), so when it returns anything, the
        probe is bounded to one past that count instead of the full ceiling.
        Falls back to max_probe unbounded if pygrabber returned nothing
        (unavailable, or genuinely no devices - can't tell which, so don't
        narrow the search).

        on_progress, if given, is called after each index is probed as
        on_progress(completed, total), where total reflects this bounded
        count, not the original max_probe argument - this method runs off
        the Tk thread, so callers that touch Tk widgets from the callback
        must marshal onto the main thread themselves (e.g. root.after)."""
        self.video_device_names = _probe_video_device_names()
        if self.video_device_names:
            probe_count = min(max_probe, len(self.video_device_names) + 1)
        else:
            probe_count = max_probe
        available = []
        for index in range(probe_count):
            cap = _open_capture(index)
            if cap is not None:
                available.append(index)
                cap.release()
            if on_progress is not None:
                try:
                    on_progress(index + 1, probe_count)
                except Exception:
                    pass
        return available

    def _video_device_label(self, index):
        name = self.video_device_names.get(index)
        return f"Camera {index}: {name}" if name else f"Camera {index}"

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
        with self._video_frame_lock:
            self._video_frame_seq = 0
            self._video_drawn_seq = 0
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
        # Drop the cached Tk photo: _apply_video_feed_visibility detaches it
        # from the canvas item (image=""), so the paste-reuse path in
        # _render_video_frame must not keep writing into it after a restart
        # - it would be updating pixels nothing displays.
        self._video_photo_image = None
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
        # Match the render tick to the device's reported rate when it gives
        # a usable one. OBS's Virtual Camera reports -1 here (confirmed
        # 2026-07-10), so for it the tick stays at the 33ms fallback and
        # the frame-sequence check in _video_render_tick does the real
        # pacing by skipping ticks that have no new frame.
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
        # OBS serves 60fps but the render tick draws at most ~30fps, so at
        # least every other decoded frame can never reach the screen.
        # Keep cap.read()ing at the full device rate (letting frames expire
        # naturally keeps the buffer fresh), but skip the resize/cvtColor
        # processing for frames that fall between render intervals - they'd
        # be thrown away anyway, and processing them just burns CPU/GIL the
        # draws need. This also makes the frames that ARE published evenly
        # spaced instead of "whichever ones the loop got to", which is what
        # steady pacing on screen requires.
        last_processed = 0.0
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
                last_reported_size = self._report_video_frame_size(
                    (frame_width, frame_height), last_reported_size
                )
                now = time.monotonic()
                if (now - last_processed) * 1000.0 < self.video_render_interval_ms:
                    continue
                last_processed = now
                # Letterbox-resize the raw BGR frame first, THEN color
                # convert - cvtColor on the already-shrunk frame instead of
                # the full 60fps-source resolution.
                rgb_frame = cv2.cvtColor(
                    self._letterbox_fit_frame(frame), cv2.COLOR_BGR2RGB
                )
                with self._video_frame_lock:
                    self._video_latest_frame = rgb_frame
                    self._video_frame_seq += 1
        finally:
            cap.release()

    def _report_video_frame_size(self, actual_size, last_reported_size):
        """Update the status label when the decoded frame size changes.
        The decoded size is ground truth for what OBS is really serving
        (driver-reported CAP_PROP dimensions can be stale or wrong for
        virtual cameras). Returns the size to compare the next frame to."""
        if actual_size != last_reported_size:
            frame_width, frame_height = actual_size
            self.video_status = f"Connected ({frame_width}x{frame_height})"
            self.root.after(0, self._update_video_status_ui)
        return actual_size

    def _letterbox_fit_frame(self, frame):
        """Letterbox-resize a frame (any channel order) to the last known
        canvas size, on the capture thread (cv2.resize releases the GIL).
        The main thread was spending 75-110ms per draw doing
        resize+PIL+PhotoImage inline, which capped actual draws at ~9-12fps
        with visibly uneven pacing. If the cached canvas size is stale
        (window just resized) or not yet known, the frame is returned as-is
        and _render_video_frame re-fits it on the main thread as a fallback
        until the next frame comes through."""
        canvas_width, canvas_height = self._video_canvas_size
        if canvas_width <= 1 or canvas_height <= 1:
            return frame
        frame_height, frame_width = frame.shape[:2]
        scale = min(canvas_width / frame_width, canvas_height / frame_height)
        target_width = max(1, int(round(frame_width * scale)))
        target_height = max(1, int(round(frame_height * scale)))
        if (target_width, target_height) == (frame_width, frame_height):
            return frame
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

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
            frame_seq = self._video_frame_seq
        # Only pay for the PIL/PhotoImage/canvas pipeline when the worker
        # actually published a new frame since the last draw - OBS delivers
        # ~15fps while this tick polls at ~30Hz, so about half the ticks
        # would otherwise redraw an identical frame.
        if frame is not None and frame_seq != self._video_drawn_seq:
            self._render_video_frame(frame)
            self._video_drawn_seq = frame_seq
        self._schedule_video_render_tick()

    def _render_video_frame(self, frame):
        canvas_width = self.text_canvas.winfo_width()
        canvas_height = self.text_canvas.winfo_height()
        if canvas_width <= 1 or canvas_height <= 1:
            return
        # Publish the real canvas size so the capture worker letterboxes
        # future frames off the main thread (_letterbox_fit_frame).
        self._video_canvas_size = (canvas_width, canvas_height)
        frame_height, frame_width = frame.shape[:2]
        if frame_width <= 0 or frame_height <= 0:
            return
        # Fit inside the canvas (letterbox/pillarbox as needed) rather than
        # cropping to cover it - the whole OBS composition must always stay
        # visible, never zoomed/cropped past the window's borders. The
        # worker normally delivers frames already fitted, in which case the
        # computed target equals the frame size and this is a no-op; the
        # resize here only runs for the first frames (canvas size not yet
        # published) or right after a window resize.
        scale = min(canvas_width / frame_width, canvas_height / frame_height)
        target_width = max(1, int(round(frame_width * scale)))
        target_height = max(1, int(round(frame_height * scale)))
        if (target_width, target_height) == (frame_width, frame_height):
            resized = frame
        else:
            resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        image = Image.fromarray(resized)
        x = (canvas_width - target_width) // 2
        y = (canvas_height - target_height) // 2
        photo = self._video_photo_image
        try:
            self.text_canvas.coords(self.video_image_item, x, y)
            if (
                photo is not None
                and (photo.width(), photo.height()) == (target_width, target_height)
            ):
                # Reuse the existing Tk photo and overwrite its pixels in
                # place - allocating a fresh PhotoImage every draw made Tk
                # create/destroy a full-canvas image object 15-30x/sec,
                # a large share of the per-draw main-thread cost. The canvas
                # notices the pixel change on its own; no itemconfigure
                # needed when the object identity is unchanged.
                photo.paste(image)
            else:
                photo = ImageTk.PhotoImage(image)
                self._video_photo_image = photo
                self.text_canvas.itemconfigure(self.video_image_item, image=photo)
        except Exception:
            pass
        self._update_caption_bar(canvas_width, canvas_height)

    def _video_caption_bar_height(self, canvas_height, line_height, lines):
        bar_height = int(round(line_height * lines + self.text_padding * (lines + 1)))
        return max(1, min(canvas_height, bar_height))

    def _update_caption_bar(self, canvas_width, canvas_height):
        """Docks a translucent bar (tinted with the Background Color
        setting) to the bottom of the window itself (not the letterboxed
        video rect), sized for the caption lines. Drawn as its own
        true-alpha image on top of the video and below the caption text,
        so it reads consistently regardless of the camera's aspect ratio
        or any letterbox bars around it."""
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
        # This bar's appearance only changes with window size, color, or the
        # opacity setting, not per-frame - rebuilding the PIL image and
        # PhotoImage from scratch on every single video render tick (30-60x/sec)
        # was pure waste that ate into the same per-tick time budget the
        # video frame itself needs, making it more likely to fall behind
        # under load.
        bar_image = Image.new("RGBA", (canvas_width, bar_height), (r, g, b, int(round(alpha * 255))))
        photo = ImageTk.PhotoImage(bar_image)
        self._caption_bar_photo_image = photo
        self._caption_bar_cache_key = cache_key
        try:
            self.text_canvas.itemconfigure(self.caption_bar_item, image=photo)
        except Exception:
            pass
