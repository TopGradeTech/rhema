r"""Port test: does an HTML click-eating overlay + a post-startup menu bar
attach give the Controller window the same startup-loading gate
_show_startup_loading_overlay() gives it in Tk?

That real method places a tk.Frame over the WHOLE Controller window
(.place(relx=0,rely=0,relwidth=1,relheight=1), .lift(), and a
<Button-1> binding that returns "break" to swallow clicks) with a
"Loading..." label and an indeterminate ttkb.Progressbar, polled every
500ms via root.after until RealtimeSTT/Local NLLB/the camera scan all
report ready, then destroyed. Crucially, _build_menu_bar()'s own comment
says the menu bar is not just grayed out during this - it's kept entirely
off the window (settings_window.config(menu=menu_bar) only runs
`if self.app_startup_ready`) - "same intent as the loading overlay itself".

The HTML overlay side of this is not really in question - an absolutely-
positioned, full-size, pointer-events-blocking <div> is a completely
standard, low-risk browser pattern (verified here anyway, for the same
"check it, don't assume it" reason every other experiment does). The real
open question is the menu bar half: web_menu_bar.py proved pywebview's
public API (create_window(menu=[...])) attaches a real menu bar, but only
ever at WINDOW CREATION time - there is no public Window.set_menu() or
similar for attaching one later. Reading platforms/winforms.py further:
set_window_menu() is a real method, but only ever defined on the internal
BrowserView class, with no module-level wrapper function the way
get_position()/get_size() have one for reaching a window by uid from
outside its own instance. So "build the menu now, attach it only once
startup finishes" - the real app's exact behavior - has no supported public
API path in pywebview. This file tries the unsupported-but-real path
anyway (BrowserView.instances[window.uid].set_window_menu(...), the same
kind of "reach past the wrapper" already used for HWND/Controls lookups in
web_multimonitor.py and web_menu_bar.py) and reports honestly whether it
works, since if it doesn't, a real port needs a different design (e.g.
building the menu at creation time but disabling its top-level items via
their real .Enabled property, using the same native item traversal
web_menu_bar.py already proved) rather than assuming this one does.

**Confirmed on real hardware: both halves work, and the exact real-app
behavior is achievable.** The overlay div genuinely blocks a real click
(checked via document.elementFromPoint at the button's actual screen
position, not just "the overlay element exists somewhere") while up, and
stops blocking once removed - a real click reaches the real button handler
only after. And the unsupported BrowserView.instances[uid].set_window_menu()
path DOES work: called with no menu=... at create_window() time, then
invoked after the window loaded, it attached a real MenuStrip control
where none existed before (confirmed via the same native Controls
enumeration web_menu_bar.py used) - so a real port CAN replicate "the menu
bar stays off the window entirely until startup finishes" exactly, not
just an approximation of it, if it's willing to reach past pywebview's
public API for this one thing the way this file does.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_startup_overlay.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import os
import sys
import time
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402
from webview.menu import Menu, MenuAction  # noqa: E402

HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema startup overlay port test</title>
<style>
body{margin:0;background:#1E2228;color:#E5E7EB;font:14px sans-serif}
#page{padding:20px}
#probe{margin-top:16px;padding:10px 16px;border:none;border-radius:6px;background:#5B8FF7;
 color:#111;font-weight:600;cursor:pointer}
#clicks{margin-top:10px;color:#9CA3AF;font-size:12px}
#overlay{position:fixed;inset:0;background:#1E2228;display:flex;align-items:center;
 justify-content:center;flex-direction:column;z-index:1000}
#overlay.hidden{display:none}
.spinner{width:48px;height:48px;border-radius:50%;border:4px solid #3A3F4B;
 border-top-color:#5B8FF7;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
#loadingtext{margin-top:14px;font-size:15px;font-weight:600}
</style></head><body>
<div id="page">
  <h3 style="margin:0 0 8px">Rhema Controller - startup overlay port test</h3>
  <button id="probe">Click me (should be blocked while loading)</button>
  <div id="clicks">clicks received: 0</div>
</div>
<div id="overlay">
  <div class="spinner"></div>
  <div id="loadingtext">Loading...</div>
</div>
<script>
let clickCount = 0
document.getElementById('probe').addEventListener('click', () => {
  clickCount += 1
  document.getElementById('clicks').textContent = 'clicks received: ' + clickCount
})
function hideOverlay(){ document.getElementById('overlay').classList.add('hidden') }
function clickCountNow(){ return clickCount }
function simulateClickOnProbe(){
  const el = document.getElementById('probe')
  const rect = el.getBoundingClientRect()
  const cx = rect.left + rect.width / 2
  const cy = rect.top + rect.height / 2
  // elementFromPoint tells us what would ACTUALLY receive a real click at
  // that screen position right now - the overlay (if still up and covering
  // it) or the real button underneath. Dispatching the synthetic click on
  // THAT element (not unconditionally on the button itself) is what makes
  // this a real simulation of "what happens if a click lands here" rather
  // than a dispatchEvent() call that would fire the button's own listener
  // regardless of what's actually on top of it.
  const hit = document.elementFromPoint(cx, cy)
  const blocked = hit !== el
  if (hit) hit.dispatchEvent(new MouseEvent('click', {bubbles: true}))
  return {blocked_by_overlay: blocked, hit_id: hit ? hit.id : null}
}
</script>
</body></html>
"""


def main():
    log = []

    def report(label, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label}" + (f" - {detail}" if detail else ""), flush=True)
        log.append((status, label, detail))

    ready = {"value": False}

    def hardware_autodetect(): ...

    def options_action(): ...

    menu = [Menu("File", [MenuAction("Hardware Autodetect", hardware_autodetect), MenuAction("Options", options_action)])]

    window = webview.create_window(
        "Rhema Controller - startup overlay port test",
        html=HTML,
        width=480,
        height=320,
        background_color="#1E2228",
        # Deliberately WITHOUT menu=... here - see if set_window_menu can
        # attach one dynamically later, matching the real app's "menu bar
        # stays off entirely until startup finishes" behavior.
    )

    def on_loaded():
        # --- Overlay actually blocks a real click while up ---
        hit = window.evaluate_js("simulateClickOnProbe()")
        report(
            "overlay blocks a real click at the button's screen position while loading",
            hit["blocked_by_overlay"],
            str(hit),
        )
        clicks = window.evaluate_js("clickCountNow()")
        report("blocked click did not reach the real button handler", clicks == 0, f"clicks={clicks}")

        # --- Dynamic menu attach: the real, unsupported-but-real path ---
        try:
            from webview.platforms.winforms import BrowserView

            browser_view = BrowserView.instances.get(window.uid)
            if browser_view is None:
                report("found the real BrowserView instance for this window", False, "not in BrowserView.instances")
            else:
                browser_view.set_window_menu(menu)
                # set_window_menu marshals onto the UI thread internally
                # (self.Invoke(...) if InvokeRequired) - give it a moment.
                time.sleep(0.3)
                native = window.native
                menu_strip = next(
                    (c for c in native.Controls if c.GetType().Name == "MenuStrip"), None
                )
                report(
                    "set_window_menu() attached a real MenuStrip AFTER window creation "
                    "(undocumented path, not part of pywebview's public API)",
                    menu_strip is not None,
                    f"Controls now: {[c.GetType().Name for c in native.Controls]}",
                )
        except Exception as exc:
            report("dynamic menu attach via BrowserView.instances", False, repr(exc))

        # --- Simulated startup work, then hide the overlay ---
        def finish_startup():
            time.sleep(1.5)
            ready["value"] = True
            window.evaluate_js("hideOverlay()")
            time.sleep(0.3)
            hit2 = window.evaluate_js("simulateClickOnProbe()")
            report(
                "overlay no longer blocks the click once startup finishes",
                not hit2["blocked_by_overlay"],
                str(hit2),
            )
            clicks2 = window.evaluate_js("clickCountNow()")
            report("post-overlay click DID reach the real button handler", clicks2 == 1, f"clicks={clicks2}")

            ok = all(status == "PASS" for status, _label, _detail in log)
            print("\nRESULT: " + ("ALL PASS" if ok else "SOME FAILURES - see above"), flush=True)
            window.destroy()

        Thread(target=finish_startup, daemon=True).start()

    window.events.loaded += on_loaded
    webview.start()


if __name__ == "__main__":
    main()
