r"""Phase 2 packaging spike (port plan): does a minimal pywebview app survive
being frozen by PyInstaller and run from a real installed-style location -
not just `.venv\Scripts\python.exe experiments\...`? Pulled forward in the
plan ahead of any real feature work because packaging is the one genuinely
unresearched area, and a blocker found here is far cheaper than one found
after 9 phases of feature work.

Deliberately as small as possible: one window, static HTML, no js_api, no
RealtimeSTT/NLLB. The goal is isolating PACKAGING risk (missing native
DLLs, WebView2 runtime presence, user-data-folder writability) from
everything main.spec already handles for the Tk build.

Not imported by the app. Not part of the shipped requirements (pywebview/
pythonnet already are, per Phase 1's requirements.txt).
"""
import os
import sys

import webview

APP_DATA_DIR_NAME = "RhemaWebviewSpike"


def _app_data_dir():
    r"""Mirrors LoggingMixin._get_app_data_dir's real logic: prefer the
    frozen exe's own directory if writable (matches this app's real
    per-user install target - see installer.iss's PrivilegesRequired=lowest
    note), falling back to %APPDATA%\Rhema-style per-user storage
    otherwise."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if os.path.isdir(exe_dir) and os.access(exe_dir, os.W_OK):
            return exe_dir
        appdata_root = os.getenv("APPDATA") or os.path.expanduser("~")
        fallback_dir = os.path.join(appdata_root, APP_DATA_DIR_NAME)
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir
    return os.path.dirname(os.path.abspath(__file__))


def main():
    data_dir = _app_data_dir()
    storage_path = os.path.join(data_dir, "webview_data")

    marker_path = os.path.join(data_dir, "spike_ran.txt")
    with open(marker_path, "w", encoding="utf-8") as f:
        f.write("frozen=%s\nexe=%s\ndata_dir=%s\nstorage_path=%s\n" % (
            getattr(sys, "frozen", False), sys.executable, data_dir, storage_path
        ))

    window = webview.create_window(
        "Rhema Webview Packaging Spike",
        html="<html><body style='background:#1e1e1e;color:#eee;"
             "font-family:sans-serif;padding:40px'>"
             "<h1>It rendered.</h1>"
             "<p id='status'>checking...</p>"
             "</body></html>",
        width=640,
        height=400,
    )

    def on_loaded():
        try:
            window.evaluate_js(
                "document.getElementById('status').innerText = "
                "'evaluate_js round trip OK'"
            )
            with open(os.path.join(data_dir, "spike_loaded.txt"), "w", encoding="utf-8") as f:
                f.write("loaded+evaluate_js OK\n")
        except Exception as exc:
            with open(os.path.join(data_dir, "spike_error.txt"), "w", encoding="utf-8") as f:
                f.write(repr(exc))

    window.events.loaded += on_loaded

    webview.start(storage_path=storage_path, private_mode=False, debug=False)

    with open(os.path.join(data_dir, "spike_closed.txt"), "w", encoding="utf-8") as f:
        f.write("webview.start() returned - window closed cleanly\n")
        f.write("storage_path exists after close: %s\n" % os.path.isdir(storage_path))


if __name__ == "__main__":
    main()
