r"""Port test: does an HTML tooltip give help-icon hovers the same behavior
tooltip.py's Tooltip class gives them in Tk?

The real Tooltip is used in exactly one place - _create_help_icon() in
settings_ui_mixin.py, on the small "?" label next to a setting's label.
On <Enter> it schedules a real popup after delay_ms (400ms default); on
<Leave> or any click it cancels/hides. The popup itself is a real,
separate tk.Toplevel (wm_overrideredirect(True), placed at
widget.winfo_rootx()+20 / winfo_rooty()+height+22) rather than something
drawn inside the parent window - likely so it isn't clipped by the
Options dialog's own scrollable content area. It does NOT clamp to the
screen edge - a tooltip near the bottom-right of the window can render
partially off-screen today; nothing in _show() accounts for that.

Unlike every other Tk mechanism this port evaluation has tested (Tcl
threading, tk.Text's <<Modified>>, tk.Menu's native chrome, monitor
placement), there isn't a real "does the platform support this at all"
question here - a hover-delay popup positioned near its trigger is
completely standard browser behavior (no separate window needed - normal
DOM content, absolutely positioned, can already escape a scrolling
container's clipping via position:fixed). So this file is deliberately
smaller than the others: it exists to check REAL, empirically-verifiable
behavior in an actual pywebview/WebView2 window - real timing (does a
400ms JS setTimeout actually behave inside WebView2 the way it would in a
browser tab), and one small deliberate improvement over the original
(viewport-edge clamping, which the Tk version never had) - not to answer
an open design question, because there mostly isn't one here.

Verified via simulated hover (dispatching real mouseenter/mouseleave
events and reading back real elapsed-time-gated state through
evaluate_js), not by eyeballing it:
- the tooltip does NOT appear before delay_ms elapses (checked at a point
  in time before it)
- it DOES appear once delay_ms has elapsed
- leaving the trigger hides it and cancels a still-pending show
- clicking hides it (matching the real class's <ButtonPress> binding)
- a tooltip that would overflow past the window's right/bottom edge gets
  clamped back on-screen instead of rendering off-window, a deliberate
  improvement over the original class's unclamped placement

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_tooltip.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import os
import sys
import time
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402

HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema tooltip port test</title>
<style>
body{margin:0;background:#1E2228;color:#E5E7EB;font:14px sans-serif;padding:24px}
.help{display:inline-block;margin-left:6px;width:16px;height:16px;border-radius:50%;
 background:#3A3F4B;color:#E5E7EB;text-align:center;font-size:11px;font-weight:700;
 line-height:16px;cursor:help;user-select:none}
.row{margin-bottom:16px}
.corner{position:absolute;right:24px;bottom:24px}
.tooltip{position:fixed;background:#111111;color:#fff;border:1px solid #333;
 padding:4px 6px;font-size:12px;max-width:320px;line-height:1.3;z-index:2000;
 pointer-events:none;display:none}
</style></head><body>
<div class="row">Max caption lines <span class="help" data-tip="Maximum number of translated lines kept on screen.">?</span></div>
<div class="row corner">Edge case <span class="help" id="corner-help" data-tip="This tooltip would overflow past the window edge if not clamped - checking that it gets pulled back on-screen instead of rendering off-window.">?</span></div>
<div id="tooltip" class="tooltip"></div>
<script>
const DELAY_MS = 400
const tip = document.getElementById('tooltip')
let showTimer = null

function clampedPosition(x, y, tipEl){
  const vw = window.innerWidth, vh = window.innerHeight
  const w = tipEl.offsetWidth, h = tipEl.offsetHeight
  const cx = Math.min(x, vw - w - 4)
  const cy = Math.min(y, vh - h - 4)
  return {x: Math.max(0, cx), y: Math.max(0, cy)}
}

document.querySelectorAll('.help').forEach(el => {
  el.addEventListener('mouseenter', () => {
    clearTimeout(showTimer)
    showTimer = setTimeout(() => {
      const rect = el.getBoundingClientRect()
      tip.textContent = el.dataset.tip
      tip.style.display = 'block'
      // Same offset the real Tooltip class uses (rootx+20, rooty+height+22),
      // then clamped to stay on-screen - the one deliberate improvement.
      const pos = clampedPosition(rect.left + 20, rect.bottom + 6, tip)
      tip.style.left = pos.x + 'px'
      tip.style.top = pos.y + 'px'
    }, DELAY_MS)
  })
  el.addEventListener('mouseleave', () => {
    clearTimeout(showTimer)
    tip.style.display = 'none'
  })
  el.addEventListener('click', () => {
    clearTimeout(showTimer)
    tip.style.display = 'none'
  })
})

function isTipVisible(){ return tip.style.display === 'block' }
function tipText(){ return tip.textContent }
function tipRect(){
  const r = tip.getBoundingClientRect()
  return {left: r.left, top: r.top, right: r.right, bottom: r.bottom}
}
function hoverStart(id){
  document.getElementById(id).dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}))
}
function hoverEnd(id){
  document.getElementById(id).dispatchEvent(new MouseEvent('mouseleave', {bubbles: true}))
}
function clickEl(id){
  document.getElementById(id).dispatchEvent(new MouseEvent('click', {bubbles: true}))
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

    window = webview.create_window(
        "Rhema - tooltip port test",
        html=HTML,
        width=420,
        height=260,
        background_color="#1E2228",
    )

    def on_loaded():
        window.evaluate_js("document.querySelectorAll('.help')[0].id = 'first-help'")

        window.evaluate_js("hoverStart('first-help')")
        time.sleep(0.15)
        report(
            "tooltip not shown before the 400ms delay elapses",
            not window.evaluate_js("isTipVisible()"),
        )

        time.sleep(0.35)  # total ~0.5s since hover start, past the 400ms delay
        report(
            "tooltip shown once the delay has elapsed",
            window.evaluate_js("isTipVisible()"),
        )
        report(
            "tooltip shows the real help text for that icon",
            "Maximum number of translated lines" in (window.evaluate_js("tipText()") or ""),
        )

        window.evaluate_js("hoverEnd('first-help')")
        time.sleep(0.05)
        report("leaving the trigger hides the tooltip", not window.evaluate_js("isTipVisible()"))

        window.evaluate_js("hoverStart('first-help')")
        time.sleep(0.15)
        window.evaluate_js("hoverEnd('first-help')")
        time.sleep(0.35)
        report(
            "leaving before the delay elapses cancels the pending show entirely",
            not window.evaluate_js("isTipVisible()"),
        )

        window.evaluate_js("hoverStart('first-help')")
        time.sleep(0.5)
        window.evaluate_js("clickEl('first-help')")
        time.sleep(0.05)
        report("a click hides a visible tooltip", not window.evaluate_js("isTipVisible()"))

        # --- Edge-clamping: the deliberate improvement over the original ---
        window.evaluate_js("hoverStart('corner-help')")
        time.sleep(0.5)
        tip_rect = window.evaluate_js("tipRect()")
        win_size = window.evaluate_js("({w: window.innerWidth, h: window.innerHeight})")
        report(
            "a tooltip near the window's corner is clamped fully on-screen (right edge)",
            tip_rect["right"] <= win_size["w"] + 1,
            f"tip_right={tip_rect['right']} window_width={win_size['w']}",
        )
        report(
            "a tooltip near the window's corner is clamped fully on-screen (bottom edge)",
            tip_rect["bottom"] <= win_size["h"] + 1,
            f"tip_bottom={tip_rect['bottom']} window_height={win_size['h']}",
        )

        ok = all(status == "PASS" for status, _label, _detail in log)
        print("\nRESULT: " + ("ALL PASS" if ok else "SOME FAILURES - see above"), flush=True)

        def _close():
            time.sleep(0.5)
            window.destroy()

        Thread(target=_close, daemon=True).start()

    window.events.loaded += on_loaded
    webview.start()


if __name__ == "__main__":
    main()
