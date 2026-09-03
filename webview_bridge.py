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

"""Shared pywebview-side infrastructure for the port: the Tk-primitive
shims (WebCanvas/WebMeasurer/FakeRoot) proved in experiments/
web_output_window.py, the Invoke()-marshaling fix proved in experiments/
web_ui_thread_invoke.py, the DPI-aware monitor-placement helpers proved in
experiments/web_multimonitor.py, and a new settings-window geometry
adapter. Consolidated here (instead of staying copy-pasted per experiment)
so a fix to any of these lands once, for every window that uses it -
exactly the drift risk AppLifecycleMixin's own docstring already flags for
on_closing.

Windows-only (ctypes.windll, ctypes.wintypes) - matches every mixin this
already sits alongside (monitor_mixin.py, realtime_stt_mixin.py's Windows-
specific paths, etc.); this whole port targets the same Windows desktop
audience the Tk app does.
"""

import ctypes
import json
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes


def invoke_on_ui_thread(window, func):
    """Runs func() (zero-arg) on window's real WinForms UI thread if the
    caller isn't already on it, and returns its result - the same
    InvokeRequired/Invoke(Func[Type](...)) idiom pywebview's own internal
    BrowserView methods use (platforms/winforms.py), proved in experiments/
    web_ui_thread_invoke.py to resolve an intermittent (sometimes 90+
    second) COM apartment-threading hang that showed up whenever two real
    WebView2 windows coexisted and something touched `window.native`
    directly from a background thread. Every `.native`/HWND-touching call
    in this port must go through this, not call `.native` directly.

    Imports clr/System lazily (not at module import time) so importing this
    module never requires pythonnet to already be installed - only code
    paths that actually touch a real pywebview window's .native need it."""
    import clr  # noqa: F401
    from System import Func, Type

    box = {}

    def _wrapped():
        box["result"] = func()

    native = window.native
    if native.InvokeRequired:
        native.Invoke(Func[Type](_wrapped))
    else:
        _wrapped()
    return box.get("result")


def hwnd_for(window):
    """The real top-level HWND behind a pywebview window, Invoke()-marshaled.
    Not part of the public Window API (platforms/winforms.py sets `.native`
    to the WinForms BrowserView Form itself) - the same class of "reach past
    the wrapper for ground truth" monitor_mixin.py's apply_dark_title_bar
    already does via GetParent(winfo_id())."""
    return invoke_on_ui_thread(window, lambda: int(window.native.Handle.ToInt32()))


# The same 4 registry GUIDs platforms/winforms.py's own _is_chromium() checks
# (Runtime/Beta/Dev/Canary channels), reimplemented independently here rather
# than importing that private, underscore-prefixed function directly - it is
# not part of pywebview's public API and could change shape between
# versions without notice.
_WEBVIEW2_CHANNEL_KEYS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # Runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # Canary
)


def is_webview2_runtime_available():
    """True if ANY WebView2 channel (Runtime/Beta/Dev/Canary) is installed,
    in either HKCU or HKLM.

    **Why this exists, and why it must be checked before webview.start()
    rather than left to pywebview itself**: pywebview's WinForms backend
    (platforms/winforms.py's _is_chromium()) does the exact same registry
    check internally - but if it finds NOTHING installed, it does not raise
    or show any user-visible error. It silently falls back to `mshtml`, the
    deprecated IE11/Trident engine, logging only a `logger.warning()` (never
    surfaced to a user, and this app doesn't enable pywebview's own debug
    logging). That is exactly the invisible-failure class of bug this
    project has already been burned by once (RealtimeSTT silently reaching
    Hugging Face instead of erroring when engine_options was ignored - see
    ARCHITECTURE.md's fork rationale) - and it would be worse here: MSHTML
    cannot run the modern canvas/measureText/evaluate_js machinery this
    entire port is built on, so every window would render broken or blank
    with no diagnostic pointing at the real cause.

    A real port must call this BEFORE webview.create_window()/webview.start()
    and, if it returns False, show a clear, loud error (via
    _show_error_dialog / web_messagebox.py - the same ctypes MessageBoxW
    path the crash hook uses, since this can fail before any window exists
    too) directing the user to install the WebView2 Runtime, rather than
    letting pywebview silently degrade."""
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key_path in (
            r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}",
            r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}",
        ):
            for guid in _WEBVIEW2_CHANNEL_KEYS:
                try:
                    with winreg.OpenKey(hive, key_path.format(key=guid)):
                        return True
                except OSError:
                    continue
    return False


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def real_window_rect(hwnd):
    """Ground truth for where a window ACTUALLY is, independent of what
    pywebview believes it asked for - the same kind of check monitor_mixin.py's
    _verify_fullscreen_position does for the Tk app, since the window manager
    silently reverting a geometry the app itself believed had applied is a
    documented real bug on the Tk side (see project memory: Monitor
    Persistence Bug)."""
    rect = _RECT()
    # ToInt32() (what winforms.py uses for this Handle everywhere) can come
    # back negative for a handle with the high bit set; wintypes.HWND wants
    # an unsigned pointer-sized value.
    ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd & 0xFFFFFFFF), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def monitor_scale(hmonitor):
    """Logical-to-physical scale for a real HMONITOR, via the same Shcore API
    GetDpiForMonitor (pywebview's own `_scale`) is built on - the number a
    real port needs to divide get_monitors()'s physical-pixel rect by before
    handing coordinates to any pywebview placement call (create_window,
    move, resize). Confirmed correct on real 150%-scaled hardware in
    experiments/web_multimonitor.py: no rounding slop needed.

    NOTE: this is for WINDOW/MONITOR PLACEMENT coordinates only. It is
    deliberately NOT used for WebMeasurer/FakeRoot's font-measurement ppi
    (see FakeRoot.winfo_fpixels below) - those are a different DPI concern
    with a different correct answer."""
    dpi_x = wintypes.UINT()
    dpi_y = wintypes.UINT()
    # MDT_EFFECTIVE_DPI = 0
    ctypes.windll.shcore.GetDpiForMonitor(hmonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    return dpi_x.value / 96.0


def real_monitors_with_scale(app):
    """get_monitors()'s real monitor list (physical pixels), plus each one's
    real DPI scale factor - get_monitors() itself doesn't carry this, but
    window-placement code needs it to convert to the logical pixels
    pywebview's placement calls want. `app` is any object with MonitorMixin's
    (or MonitorLogicMixin's) get_monitors()/set_dpi_awareness() already
    available - generalized from experiments/web_multimonitor.py's
    throwaway _MonitorProbe class to take the real running app instance
    instead, since a real port already has one."""
    app.set_dpi_awareness()
    monitors = app.get_monitors()

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
            scale_by_device[info.szDevice] = monitor_scale(h)

    for m in monitors:
        m["scale"] = scale_by_device.get(m.get("device", ""), 1.0)
    return monitors


def physical_to_logical(x, y, width, height, scale):
    """Converts a monitor rect from get_monitors()'s physical pixels to the
    logical pixels pywebview's create_window/move/resize expect, per the
    coordinate-space finding in experiments/web_multimonitor.py."""
    return (
        int(round(x / scale)),
        int(round(y / scale)),
        int(round(width / scale)),
        int(round(height / scale)),
    )


class FakeRoot:
    """Stands in for Tk's root just enough to satisfy `self.root.after(...)`/
    `.after_cancel(...)` calls sprinkled through the mixins for UI-thread
    marshaling, plus `winfo_fpixels("1i")` for `_get_pixels_per_inch()`.
    Verbatim from experiments/web_output_window.py's FakeRoot (proved
    against a real drip-reveal race: independent per-call threading.Timers
    dropped queued caption content the first time this was driven with
    real back-to-back commits; one dedicated thread plus one time-ordered
    heap, callbacks run strictly one at a time in fire-time order, fixed
    it), extended with a real winfo_fpixels.

    NOTE: this has nothing to do with the tk.Variable/tk.Text dirty-
    tracking shim's OWN thread-safety story, despite an earlier planning
    draft conflating the two. FakeRoot has no Tcl interpreter behind it at
    all - it is pure Python - so it cannot provide the thread-affinity
    guarantee a real Tcl interpreter needs. That shim's hidden tk.Tk()
    interpreter is TkVariableInterpreter, below, with its own,
    different-in-a-load-bearing-way marshaling primitive."""

    def __init__(self, on_error=None):
        self._lock = threading.Lock()
        self._heap = []
        self._seq = 0
        self._cancelled = set()
        self._wake = threading.Event()
        self._on_error = on_error
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def after(self, delay_ms, fn, *args):
        import heapq

        fire_time = time.monotonic() + (max(0, delay_ms) / 1000.0)
        with self._lock:
            self._seq += 1
            token = self._seq
            heapq.heappush(self._heap, (fire_time, token, fn, args))
        self._wake.set()
        return token

    def after_cancel(self, token):
        with self._lock:
            self._cancelled.add(token)

    def winfo_fpixels(self, arg):
        """`_get_pixels_per_inch()` (settings_ui_mixin.py) calls this with
        "1i" to get a real-world pixels-per-inch figure for
        _target_line_height_px()'s "how tall should a caption line look
        from 10 feet away" math, which _font_size_for_line_height() then
        satisfies by binary-searching Tk point sizes until
        `self.text_font.metrics("linespace")` (physical pixels, on the Tk
        side) reaches that target.

        On the web side, `self.text_font` is a WebMeasurer, and
        WebMeasurer.metrics("linespace")/._css_font() convert a point size
        to CSS pixels using ITS OWN `_ppi` constant - always 96.0 in every
        proved experiment (web_output_window.py's PIXELS_PER_INCH), because
        a CSS pixel is DEFINED as a fixed 1/96 inch; it is not a
        physical-pixel unit that varies with the monitor's real DPI the way
        Tk's font metrics do. The browser/WebView2 compositor converts CSS
        pixels to real physical pixels itself, automatically, via
        devicePixelRatio - already correct on real per-monitor-DPI-aware
        hardware without either side of this comparison needing to know
        the real DPI (this app already calls set_dpi_awareness() at the
        process level, which is what makes WebView2 report per-monitor
        devicePixelRatio correctly rather than being bitmap-stretched by
        Windows' own DPI virtualization).

        So this must return the SAME fixed 96.0 WebMeasurer uses for its
        own ppi, not a real GetDpiForMonitor() query (unlike
        monitor_scale()/real_monitors_with_scale() above, which genuinely
        do need the real per-monitor value - that's a different DPI
        concern, window PLACEMENT coordinates, not font measurement).
        Returning a real per-monitor DPI here instead would double-apply
        DPI compensation - once via this value inflating target_px, and
        again via the browser's own devicePixelRatio scaling - making
        captions render too large on any monitor above 100% scale."""
        return 96.0

    def config(self, **kwargs):
        """apply_colors() (settings_ui_mixin.py) calls `self.root.config(bg=...)`
        unmodified - there is no real top-level window here to recolor, and
        the page's html/body background is already set once via static CSS
        in the output window's own HTML (see main_webview.py), so this is a
        deliberate no-op rather than a missing feature. WebCanvas.config()
        below is the one that actually matters visually."""
        pass

    def _run(self):
        import heapq

        while True:
            with self._lock:
                due = self._heap[0] if self._heap else None
            if due is None:
                self._wake.wait()
                self._wake.clear()
                continue
            delay = due[0] - time.monotonic()
            if delay > 0:
                self._wake.wait(timeout=delay)
                self._wake.clear()
                continue
            with self._lock:
                if not self._heap or self._heap[0][1] != due[1]:
                    continue  # a new, earlier item beat us to the lock
                _, token, fn, args = heapq.heappop(self._heap)
                cancelled = token in self._cancelled
                self._cancelled.discard(token)
            if cancelled:
                continue
            try:
                fn(*args)
            except Exception:
                # Real Tk surfaces this through report_callback_exception,
                # which logging_mixin.py hooks to _write_unhandled_exception -
                # a silent `pass` here would swallow the exact class of bug
                # this shim exists to catch (a drip/roll-up race, say).
                if self._on_error is not None:
                    self._on_error(*sys.exc_info())
                else:
                    import traceback

                    traceback.print_exc()


class TkVariableInterpreter:
    """Hidden, mainloop'd tk.Tk() interpreter dedicated to backing real
    tk.Variable/tk.Text objects for SettingsLogicMixin's dirty-tracking
    machinery and the NLLB status/message vars, reused unmodified from the
    Tk app. Never shown; builds no real widgets beyond the variables/text
    buffers callers attach to it via `master=interpreter.root`.

    **Load-bearing correction from an earlier planning assumption**,
    confirmed by experiments/web_variable_shim_stress.py: calling this
    root's own `.after(0, fn)` DIRECTLY from a worker thread does NOT
    safely marshal onto its mainloop when that mainloop runs on a
    dedicated (non-process-main) thread, which is what a hidden
    interpreter needs (WebTranslationApp's real process main thread is
    spoken for by pywebview's own event loop). Doing that hit real,
    reproducible failures - "RuntimeError: Calling Tcl from different
    apartment" and "RuntimeError: main thread is not in main loop" - even
    though this exact `self.root.after(0, ...)` shape is completely safe
    in the EXISTING Tk app (main.py), because there `self.root.mainloop()`
    runs on the process's real main thread, and only OTHER (non-mainloop)
    threads ever call `.after()` on it - the standard, supported topology.
    A hidden interpreter on its own dedicated thread is a different
    topology, and needs a different marshaling primitive.

    The primitive that DOES work reliably under real concurrent load
    (same stress test, 16 threads x 500 writes, verified no crash/
    corruption/lost update): a plain thread-safe queue.Queue that worker
    threads push closures onto via run_threadsafe(), drained by a
    self-rescheduling `.after(...)` poll loop that only ever runs ON the
    interpreter's own thread (first scheduled from inside that thread's
    own startup, before mainloop() is entered, then perpetually
    re-scheduling itself from within its own callback - never from
    outside). `.after()` itself is only ever called from the thread
    already running the mainloop it belongs to; the queue is what
    actually crosses the thread boundary, and queue.Queue is safe for
    that by design regardless of Tcl.

    Every write/read of a variable owned by this interpreter, from any
    thread other than this interpreter's own, must go through
    run_threadsafe() - not var.set()/.get() directly, and not
    `interpreter.root.after(...)` directly."""

    def __init__(self, poll_interval_ms=10):
        self._queue = queue.Queue()
        self._poll_interval_ms = poll_interval_ms
        self.root = None
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self._thread.start()
        if not ready.wait(timeout=10):
            raise RuntimeError("TkVariableInterpreter did not start within 10s")

    def _run(self, ready):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.after(self._poll_interval_ms, self._drain)
        ready.set()
        self.root.mainloop()

    def _drain(self):
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                import traceback

                traceback.print_exc()
        self.root.after(self._poll_interval_ms, self._drain)

    def run_threadsafe(self, fn):
        """Schedules fn (zero-arg) to run on this interpreter's own
        thread. Safe to call from any thread, including this
        interpreter's own (queue.Queue needs no special-casing for that)."""
        self._queue.put(fn)

    def shutdown(self):
        self.run_threadsafe(self.root.quit)
        self._thread.join(timeout=5)


def _font_css(font):
    """`font` is whatever DisplayMixin/SettingsUIMixin pass through as the
    canvas item's font - always self.text_font (a WebMeasurer) in practice."""
    if font is None or not hasattr(font, "_css_font"):
        return None
    return font._css_font()


class WebCanvas:
    """The only Tk Canvas surface DisplayMixin/SettingsUIMixin (and, when
    mixed in, VideoCaptureMixin) actually touch: winfo_width/height,
    create_text, create_image, coords, itemconfigure, delete. Backed by a
    real <canvas> in the target window - draw ops are queued and flushed in
    one evaluate_js round trip per render_text() call rather than one per
    item, since a page can hold up to LINES_NO_VIDEO_MAX items. Verbatim
    from experiments/web_output_window.py.

    Item ids are handed out in creation order and the JS side iterates its
    item registry by numeric id, which (like Tk's own stacking order) means
    whatever was created first paints first - so as long as callers create
    background items (video, caption bar) before foreground ones (caption
    text), the stacking comes out right with no separate z-index to manage."""

    def __init__(self, window, width, height):
        self._window = window
        self._w = width
        self._h = height
        self._next_id = 1
        self._ops = []

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def resize(self, width, height):
        """Updates the cached size winfo_width/height report, after the
        real <canvas> has been told (via initCanvas(), re-run by the
        caller) to actually resize itself. Without this, self._w/_h stay
        pinned at whatever _on_window_loaded's one-time initCanvas() call
        saw at startup forever - a real gap, not a hypothetical one:
        _render_video_frame's letterbox math and DisplayMixin's font-
        fitting both read winfo_width()/height() as their ground truth for
        "how big is the Output window right now", so switching to a
        different-resolution/DPI monitor at runtime left video and caption
        sizing stuck at the old monitor's dimensions with no way to
        recover short of restarting the app."""
        self._w = width
        self._h = height

    def config(self, **kwargs):
        """apply_colors() (settings_ui_mixin.py) calls
        `self.text_canvas.config(bg=...)` unmodified - unlike FakeRoot's own
        no-op config(), this one actually matters: it is the real background
        color of the caption canvas. Queued through the same ops list as
        every other draw op (flush() sends it in the next evaluate_js round
        trip) rather than pushed immediately, so a bg change lands in the
        same paint as whatever else render_text() queued in the same tick,
        instead of one extra round trip ahead of it."""
        if "bg" in kwargs:
            self._ops.append({"op": "bg", "color": kwargs["bg"]})

    def create_text(self, x, y, anchor="nw", text="", fill="#ffffff", font=None, **_ignored):
        item_id = self._next_id
        self._next_id += 1
        self._ops.append({
            "op": "create",
            "id": item_id,
            "x": x,
            "y": y,
            "anchor": anchor,
            "text": text,
            "fill": fill,
            "font": _font_css(font),
        })
        return item_id

    def create_image(self, x, y, anchor="nw", state="normal", **_ignored):
        item_id = self._next_id
        self._next_id += 1
        self._ops.append({
            "op": "create_image", "id": item_id, "x": x, "y": y, "anchor": anchor, "state": state,
        })
        return item_id

    def coords(self, item_id, x, y):
        self._ops.append({"op": "coords", "id": item_id, "x": x, "y": y})

    def itemconfigure(self, item_id, **kw):
        entry = {"op": "config", "id": item_id}
        if "text" in kw:
            entry["text"] = kw["text"]
        if "fill" in kw:
            entry["fill"] = kw["fill"]
        if "font" in kw:
            entry["font"] = _font_css(kw["font"])
        if "state" in kw:
            entry["state"] = kw["state"]
        if "image" in kw:
            # A data: URI (or "" to clear) - the real code always passes an
            # ImageTk.PhotoImage here instead, which has no browser
            # equivalent; see web_video_overlay.py for what builds this.
            entry["image"] = kw["image"] or ""
        self._ops.append(entry)

    def delete(self, item_id):
        self._ops.append({"op": "delete", "id": item_id})

    def flush(self):
        if not self._ops:
            return
        ops, self._ops = self._ops, []
        try:
            self._window.evaluate_js("applyCanvasOps(%s)" % json.dumps(ops))
        except Exception:
            pass


class WebMeasurer:
    """The same measurer proved in caption_layout_probe.py/
    web_output_window.py: a browser canvas's measureText() standing in for
    tkinter.font.Font. Cached per (size, text) - _fit_font_to_lines
    binary-searches font sizes by repeating the same handful of probe
    strings, so the cache turns most of that search into dict lookups
    instead of JS round trips.

    `pixels_per_inch` should always be 96.0 in practice (the CSS reference;
    see FakeRoot.winfo_fpixels's docstring for why) - kept as a constructor
    parameter rather than hardcoded only because web_output_window.py's
    original shape already threaded it through this way."""

    def __init__(self, window, family, size, pixels_per_inch):
        self._window = window
        self._family = family
        self._size = size
        self._ppi = pixels_per_inch
        self._cache = {}

    def configure(self, **kwargs):
        if "size" in kwargs:
            self._size = kwargs["size"]

    def cget(self, key):
        return self._size if key == "size" else self._family

    def _css_font(self):
        px = self._size * self._ppi / 72.0
        return "%gpx %s" % (px, self._family)

    def measure(self, text):
        key = (self._size, text)
        if key not in self._cache:
            self._cache[key] = int(round(float(self._window.evaluate_js(
                "measure(%s, %s)" % (json.dumps(self._css_font()), json.dumps(text))
            ))))
        return self._cache[key]

    def metrics(self, key):
        if key != "linespace":
            raise KeyError(key)
        cache_key = (self._size, "\x00linespace")
        if cache_key not in self._cache:
            self._cache[cache_key] = int(round(float(self._window.evaluate_js(
                "lineHeight(%s)" % json.dumps(self._css_font())
            ))))
        return self._cache[cache_key]


class PywebviewGeometryAdapter:
    """Duck-types just enough of a Tk Toplevel (.winfo_exists(), .state(),
    .geometry()) for settings_mixin.py's save_settings/load_settings to run
    completely unmodified against a real pywebview window, per the port
    plan's decision to leave settings_mixin.py untouched rather than edit
    it for a Web-only code path.

    Not itself proved against a running Controller/Options window yet (no
    experiment built one with real persisted geometry) - first real
    validation happens when Phase 5/6 build those windows for real. Flagged
    here rather than silently assumed correct.

    `window.x/.y/.width/.height` are pywebview's own live properties
    (confirmed current in platforms/winforms.py). Geometry read here is in
    LOGICAL pixels (pywebview's own coordinate space) - unlike
    real_monitors_with_scale()'s physical-pixel monitor rects, this is
    exactly what Tk's own .geometry() string already used (Tk's geometry
    strings are also logical/DPI-already-applied on a DPI-aware process),
    so save_settings/load_settings round-trip through the same string
    format with no unit conversion needed here.

    state() queries the real WinForms Form's own WindowState directly
    (Invoke()-marshaled, the same "reach past the wrapper for ground
    truth" real_window_rect already does above) rather than tracking
    `.events.maximized/.minimized/.restored` - confirmed live those don't
    fire for a maximize() called programmatically right at startup (the
    exact case this class exists for: restoring a maximized Controller/
    Options window on launch), which silently made a maximized preference
    read back as "normal" and get overwritten by save_settings on the very
    next close - a real user-visible one-session-only-remembered bug, not
    a hypothetical one."""

    def __init__(self, window):
        self._window = window

    def winfo_exists(self):
        try:
            # No public "is this window still alive" property; touching a
            # live attribute is the same probe pywebview's own code uses to
            # notice a destroyed window (it raises once the .NET Form is
            # disposed).
            _ = self._window.width
            return True
        except Exception:
            return False

    def state(self):
        try:
            window_state = invoke_on_ui_thread(
                self._window, lambda: str(self._window.native.WindowState)
            )
        except Exception:
            return "normal"
        return "zoomed" if window_state == "Maximized" else "normal"

    def geometry(self):
        return "%dx%d+%d+%d" % (
            self._window.width,
            self._window.height,
            self._window.x,
            self._window.y,
        )
