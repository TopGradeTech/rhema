r"""Port test: does pywebview's own cross-platform Menu API give the
Controller window a real, working native menu bar in place of Tk's
tk.Menu?

settings_ui_mixin.py's _build_menu_bar() builds a real Win32 menu bar
(tk.Menu, attached via settings_window.config(menu=menu_bar)) with two
cascades: File (Hardware Autodetect, Options) and About (About Rhema,
separator, Check for Updates, Donate, Feature Request) - see that
function's own comment on why it's a real menu bar and not a Menubutton
dropdown (the latter's posted popup was getting dismissed by this app's
own global click-outside handler on the very click that opened it).

pywebview ships a public, cross-platform webview.Menu/MenuAction/
MenuSeparator API (webview/menu.py) and a `menu=` kwarg on create_window() -
found while reading platforms/winforms.py for the multi-monitor experiment
(it imports Menu/MenuAction/MenuSeparator right next to the WinForms
imports). This file builds the REAL File/About structure above with that
API and checks whether it's actually a working replacement, not just
"the objects got constructed":

1. Real native chrome exists at all: on Windows, set_window_menu() builds
   an actual WinForms.MenuStrip and adds it to the Form's Controls. That is
   real native chrome - but NOT the same thing Tk's tk.Menu is. Tk's
   menu bar is drawn by Windows itself in the window's NON-CLIENT frame
   (attached via the real Win32 SetMenu() the same tk.Menu.config(menu=...)
   call reaches for). pywebview's MenuStrip is a WinForms CLIENT-AREA
   control docked at the top, sharing space with the browser control, not
   OS frame chrome. Visually similar, not the same thing - worth knowing
   before assuming it looks/behaves identically (theming, non-client-area
   accessibility hooks, etc. all differ).
2. Given it's a client-area control sharing space with the WebView2 host,
   does the WebView2 control actually get pushed down to make room, or
   does it end up overlapping/covering the menu strip? Nothing in
   set_window_menu() explicitly repositions the browser control - it's
   relying entirely on stock WinForms Dock-layout behavior (MenuStrip
   defaults to Dock=Top; whatever's added after with Dock=Fill takes the
   remaining space) actually applying here. Checked by inspecting the real
   WinForms Controls collection via window.native (the same "reach past
   the wrapper for ground truth" trick the multi-monitor experiment used
   for HWND) rather than assuming from reading the layout code.
3. Does a REAL simulated click on a leaf menu item's real
   ToolStripMenuItem.PerformClick() actually reach the Python callback?
   set_window_menu()'s own code spawns a fresh throwaway thread per click
   to run it (threading.Thread(target=menu_line_item.function).start()) -
   consistent with every other pywebview callback dispatch found so far
   (window.events.loaded, js_api calls) never running on the thread that
   created the window. Any callback here that needed to touch the kind of
   hidden-Tk()-interpreter machinery web_options.py built would need that
   same dedicated-mainloop-thread fix - not re-tested here, just noted as
   the same finding applying again.

Setup: .venv\Scripts\pip.exe install pywebview   (see web_transcription.py)

Run:  .venv\Scripts\python.exe experiments\web_menu_bar.py

Nothing here is imported by the app. Delete the folder and Rhema is unchanged.
"""

import os
import sys
import time
from threading import Event, Thread

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import webview  # noqa: E402
from webview.menu import Menu, MenuAction, MenuSeparator  # noqa: E402

HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Rhema menu bar port test</title></head>
<body style="margin:0;background:#1E2228;color:#E5E7EB;font:14px sans-serif;padding:20px">
<h3 style="margin:0 0 8px">Rhema - menu bar port test</h3>
<p style="color:#9CA3AF">Real File/About menu built with pywebview's own Menu API.
Check results in the terminal.</p>
</body></html>
"""


def main():
    log = []

    def report(label, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        line = f"[{status}] {label}" + (f" - {detail}" if detail else "")
        print(line, flush=True)
        log.append((status, label, detail))

    fired = {}

    def make_action(name):
        event = Event()
        fired[name] = event

        def _run():
            print(f"  (menu action fired: {name!r}, on thread {__import__('threading').current_thread().name})", flush=True)
            event.set()

        return _run

    # The exact File/About structure _build_menu_bar() builds, using
    # pywebview's own real cross-platform API instead of tk.Menu.
    menu = [
        Menu(
            "File",
            [
                MenuAction("Hardware Autodetect", make_action("Hardware Autodetect")),
                MenuAction("Options", make_action("Options")),
            ],
        ),
        Menu(
            "About",
            [
                MenuAction("About Rhema", make_action("About Rhema")),
                MenuSeparator(),
                MenuAction("Check for Updates", make_action("Check for Updates")),
                MenuAction("Donate", make_action("Donate")),
                MenuAction("Feature Request", make_action("Feature Request")),
            ],
        ),
    ]

    window = webview.create_window(
        "Rhema - menu bar port test",
        html=HTML,
        width=520,
        height=360,
        background_color="#1E2228",
        menu=menu,
    )

    def on_loaded():
        native = window.native
        controls = list(native.Controls)
        print("Real WinForms Controls on the window, in Z-order:", flush=True)
        menu_strip = None
        browser_control = None
        for c in controls:
            type_name = c.GetType().Name
            print(f"  {type_name}: Dock={c.Dock} Bounds={c.Bounds}", flush=True)
            if type_name == "MenuStrip":
                menu_strip = c
            # The browser host is the biggest non-MenuStrip control - avoids
            # hardcoding a specific WebView2 wrapper class name that could
            # differ across pywebview/runtime versions.
            elif browser_control is None or (
                c.Bounds.Width * c.Bounds.Height
                > browser_control.Bounds.Width * browser_control.Bounds.Height
            ):
                browser_control = c

        report("a real WinForms.MenuStrip control was actually added", menu_strip is not None)
        if menu_strip is not None:
            report(
                "MenuStrip has both real top-level items (File, About)",
                [item.Text for item in menu_strip.Items] == ["File", "About"],
                str([item.Text for item in menu_strip.Items]),
            )

        if menu_strip is not None and browser_control is not None:
            menu_h = menu_strip.Bounds.Height
            browser_top = browser_control.Bounds.Top
            report(
                "the browser control is pushed down below the menu strip, not overlapping it",
                browser_top >= menu_h,
                f"menu_strip height={menu_h} browser control top={browser_top} "
                f"(type={browser_control.GetType().Name})",
            )
        else:
            report("could identify both a MenuStrip and a browser control to compare", False)

        # --- Real simulated clicks: PerformClick() on the actual
        # ToolStripMenuItem objects pywebview built, not a synthetic
        # stand-in - proves the whole real path (Menu API -> WinForms
        # MenuStrip -> click -> thread -> Python callback) end to end. ---
        if menu_strip is not None:

            def click_path(*titles):
                node = menu_strip
                items = node.Items
                for title in titles:
                    match = next((i for i in items if i.Text == title), None)
                    if match is None:
                        return None
                    node = match
                    items = node.DropDownItems
                return node

            for path, expect_name in (
                (("File", "Hardware Autodetect"), "Hardware Autodetect"),
                (("File", "Options"), "Options"),
                (("About", "About Rhema"), "About Rhema"),
                (("About", "Check for Updates"), "Check for Updates"),
                (("About", "Donate"), "Donate"),
                (("About", "Feature Request"), "Feature Request"),
            ):
                item = click_path(*path)
                if item is None:
                    report(f"found real menu item {path!r} to click", False)
                    continue
                fired[expect_name].clear()
                item.PerformClick()
                got_it = fired[expect_name].wait(timeout=2.0)
                report(
                    f"PerformClick() on {' > '.join(path)} reaches the real Python callback",
                    got_it,
                )

        ok = all(status == "PASS" for status, _label, _detail in log)
        print("\nRESULT: " + ("ALL PASS" if ok else "SOME FAILURES - see above"), flush=True)

        def _close():
            time.sleep(1.0)
            window.destroy()

        Thread(target=_close, daemon=True).start()

    window.events.loaded += on_loaded
    webview.start()


if __name__ == "__main__":
    main()
