r"""Does Rhema's roll-up caption layout survive a change of text measurer?

This is the decisive question for the output window, which is the last hard
piece of a port. The Controller is mostly widgets and could be rebuilt any
number of ways, but the fullscreen output does something specific and fragile:
committed text wraps into frozen lines that must never re-wrap while someone
is reading them (see _append_to_display_page). Get the line breaks wrong and
the captions visibly reflow mid-sentence, which is the exact jerkiness that
got the original interim preview removed.

The good news, on reading the code, is that the roll-up algorithm is pure
logic. Its entire dependence on Tkinter is two primitives:

    self.text_font.measure(text)            -> width in pixels
    self.text_font.metrics("linespace")     -> line height in pixels

Everything else - the paging, the roll-up, the reserved interim row, the
binary-searched font fitting - is arithmetic on top of those. So the port does
not need this algorithm rewritten in JavaScript. It needs the measurer swapped.

This probe swaps it. The same unmodified methods run twice, once against a
real Tk font and once against a browser canvas's measureText(), and the
resulting line breaks are diffed. If they agree, the output window can be
ported by reusing this code as-is and only replacing the drawing. If they
disagree, the differences are printed, and each one is a decision that has to
be made deliberately rather than discovered later in front of a congregation.

Run: .venv\Scripts\python.exe experiments\caption_layout_probe.py

Nothing here is imported by the app.
"""

import json
import os
import sys
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview  # noqa: E402

from main import TranslationApp  # noqa: E402  (class-level constants only)
from display_mixin import DisplayMixin  # noqa: E402
from settings_ui_mixin import SettingsUIMixin  # noqa: E402
from text_filter_mixin import TextFilterMixin  # noqa: E402
from transcription_mixin import TranscriptionMixin  # noqa: E402
from settings_mixin import SettingsMixin  # noqa: E402

# A 1080p output window, which is what this is actually used at.
CANVAS_W, CANVAS_H = 1920, 1080
MAX_LINES = 8

# Real translated output, in the shape the display actually receives it:
# finalized commits arriving one after another, appended to a rolling page.
COMMITS = [
    "En el principio creo Dios los cielos y la tierra.",
    "Y la tierra estaba desordenada y vacia,",
    "y las tinieblas estaban sobre la faz del abismo,",
    "y el Espiritu de Dios se movia sobre la faz de las aguas.",
    "Y dijo Dios: Sea la luz; y fue la luz.",
    "Hoy quiero hablarles sobre lo que significa caminar en fe,",
    "incluso cuando el camino por delante es incierto",
    "y no puedes ver donde caera el proximo paso.",
]

INTERIM_SAMPLES = [
    "y no puedes ver donde",
    "y no puedes ver donde caera el proximo paso que vas a dar en este camino",
]


class FakeCanvas:
    """The layout code asks the canvas only for its size."""

    def __init__(self, width, height):
        self._w, self._h = width, height

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h


class TkMeasurer:
    """The baseline: what the app does today."""

    def __init__(self, family, size):
        self._font = tkfont.Font(family=family, size=size)

    def configure(self, **kwargs):
        self._font.configure(**kwargs)

    def cget(self, key):
        return self._font.cget(key)

    def measure(self, text):
        return self._font.measure(text)

    def metrics(self, key):
        return self._font.metrics(key)


class WebMeasurer:
    """The candidate: a browser canvas measuring the same strings.

    Tk font sizes are in points; canvas fonts are in pixels. The conversion
    uses the same pixels-per-inch the app itself reads off its Tk root, so
    the two measurers are being asked about the same physical text rather
    than the same number.

    Every measurement is a synchronous round trip through evaluate_js, which
    is far too slow to do inside a render loop - hence the cache. That cost
    is a property of this probe, not of a port: a real port would run the
    layout in JavaScript beside the canvas, where measureText is a local call.
    """

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
        return "%gpx '%s'" % (px, self._family)

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


class LayoutProbe(
    DisplayMixin,
    SettingsUIMixin,
    TextFilterMixin,
    TranscriptionMixin,
    SettingsMixin,
):
    """The app's real layout methods, with the measurer injected.

    Not one line of the wrapping, paging or font-fitting code is redefined
    here - that is the entire point. Only the two objects it measures
    through are substituted.
    """

    def __init__(self, measurer, pixels_per_inch):
        self.text_font = measurer
        self.text_canvas = FakeCanvas(CANVAS_W, CANVAS_H)
        self._ppi = pixels_per_inch

        self.text_padding = 12
        self.chunk_size = 120
        self.min_chars_per_line = 40
        self.max_lines = MAX_LINES
        self.video_max_lines = 2
        self.video_feed_enabled = False
        self.display_page_lines = []
        self.live_line = ""
        self.translations = []

        # filter_bad_words / custom vocabulary run over every rendered line,
        # so they have to be real here too or the strings being measured
        # would not be the strings the app measures.
        self.bad_words_by_lang = {
            "en": set(self.default_bad_words_en()),
            "es": set(self.default_bad_words_es()),
        }
        self.bad_word_filters_enabled = {"en": True, "es": True}
        self._refresh_bad_words()
        self.custom_vocabulary_by_lang = {
            "en": self.default_biblical_terms(),
            "es": self.default_biblical_terms_es(),
        }
        self.biblical_books = self.default_biblical_books()
        self.translation_enabled = True
        self.source_lang = "en"
        self.target_lang = "es"
        self.auto_switch_translation = False
        self.auto_detect_langs = ["en", "es"]
        self.auto_detect_lang = None

    def _get_pixels_per_inch(self):
        # The real one reads it off the Tk root, which a ported app has not
        # got. Both measurers are handed the same value so the comparison
        # isolates text measurement rather than also varying DPI.
        return self._ppi

    def run(self):
        """Drive the layout exactly as a live session does, and return every
        observable the output window renders from."""
        self._fit_font_to_lines()
        pages = []
        for commit in COMMITS:
            self._append_to_display_page(commit)
            pages.append(list(self.display_page_lines))
        result = {
            "font_size": self.text_font.cget("size"),
            "line_height": self.text_font.metrics("linespace"),
            "wrap_width": self._display_wrap_width(),
            "pages": pages,
            "final_page": list(self.display_page_lines),
        }
        # The reserved interim row, which is the part that must never disturb
        # the frozen lines above it.
        interim = {}
        for sample in INTERIM_SAMPLES:
            self.live_line = sample
            interim[sample] = self._compose_display_lines()
        self.live_line = ""
        result["interim"] = interim
        # And a reflow, which is the one time frozen lines are allowed to move.
        self._reflow_display_page()
        result["after_reflow"] = list(self.display_page_lines)
        return result


# The line-count bounds, regex patterns and vocabulary tables the layout and
# filtering code reads off self. All plain data on TranslationApp's class body,
# reused rather than duplicated - see web_transcription.py, same reasoning.
for _name in dir(TranslationApp):
    if _name.isupper():
        setattr(LayoutProbe, _name, getattr(TranslationApp, _name))


HTML = r"""
<!doctype html><html><head><meta charset="utf-8"></head><body>
<script>
const ctx = document.createElement('canvas').getContext('2d')
function measure(font, text){
  ctx.font = font
  return ctx.measureText(text).width
}
function lineHeight(font){
  ctx.font = font
  const m = ctx.measureText('Hg')
  // Tk's linespace is ascent+descent+leading. fontBoundingBox is the
  // closest browser equivalent; actualBoundingBox would measure the glyphs
  // present rather than the font's own line box.
  return m.fontBoundingBoxAscent + m.fontBoundingBoxDescent
}
</script></body></html>
"""


def diff_results(tk_result, web_result):
    problems = []
    for key in ("font_size", "line_height", "wrap_width"):
        if tk_result[key] != web_result[key]:
            problems.append("%s: tk=%s web=%s" % (key, tk_result[key], web_result[key]))
    for index, (a, b) in enumerate(zip(tk_result["pages"], web_result["pages"])):
        if a != b:
            problems.append("page after commit %d differs:\n    tk : %r\n    web: %r" % (index, a, b))
    for sample in INTERIM_SAMPLES:
        a, b = tk_result["interim"][sample], web_result["interim"][sample]
        if a != b:
            problems.append("interim row for %r differs:\n    tk : %r\n    web: %r" % (sample[:30], a, b))
    if tk_result["after_reflow"] != web_result["after_reflow"]:
        problems.append("after reflow:\n    tk : %r\n    web: %r"
                        % (tk_result["after_reflow"], web_result["after_reflow"]))
    return problems


def main():
    # A hidden Tk root, purely to read the real font family and DPI the app
    # would use, and to provide the baseline measurer.
    root = tk.Tk()
    root.withdraw()
    available = set(tkfont.families())
    family = next(
        (n for n in ("DejaVu Sans", "Liberation Sans", "Arial", "Helvetica") if n in available),
        "TkDefaultFont",
    )
    ppi = float(root.winfo_fpixels("1i"))
    print("font family: %s   |   %.1f pixels/inch   |   canvas %dx%d, %d lines"
          % (family, ppi, CANVAS_W, CANVAS_H, MAX_LINES))

    tk_result = LayoutProbe(TkMeasurer(family, 50), ppi).run()
    root.destroy()

    window = webview.create_window("caption layout probe", html=HTML, width=420, height=200, hidden=True)
    results = {}

    def on_loaded():
        try:
            probe = LayoutProbe(WebMeasurer(window, family, 50, ppi), ppi)
            results["web"] = probe.run()
        except Exception as exc:  # surfaced after the loop stops
            results["error"] = exc
        finally:
            window.destroy()

    window.events.loaded += on_loaded
    webview.start()

    if "error" in results:
        raise results["error"]
    web_result = results["web"]

    print("\n--- Tk (today's behaviour) ---")
    print("font size %s, line height %s, wrap width %s"
          % (tk_result["font_size"], tk_result["line_height"], tk_result["wrap_width"]))
    for line in tk_result["final_page"]:
        print("   |%s" % line)
    print("\n--- Browser canvas measureText ---")
    print("font size %s, line height %s, wrap width %s"
          % (web_result["font_size"], web_result["line_height"], web_result["wrap_width"]))
    for line in web_result["final_page"]:
        print("   |%s" % line)

    problems = diff_results(tk_result, web_result)
    print("\n--- Verdict ---")
    # Called out separately because it is the property that actually matters:
    # if the frozen lines wrap identically, captions do not reflow while being
    # read, and everything else on this list is cosmetic by comparison.
    if tk_result["pages"] == web_result["pages"]:
        print("Frozen page line breaks are IDENTICAL across all %d commits."
              % len(COMMITS))
    else:
        print("Frozen page line breaks DIVERGE - this is the blocking one.")
    if not problems:
        print("Identical. The roll-up layout is measurer-agnostic: a port can")
        print("reuse this algorithm unchanged and replace only the drawing.")
    else:
        print("%d difference(s) - each is a deliberate decision for the port:" % len(problems))
        for problem in problems:
            print("  * %s" % problem)


if __name__ == "__main__":
    main()
