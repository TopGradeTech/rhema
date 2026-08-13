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

"""One-off rasterizer: renders the Rhema mark (rho-glyph from the logo
artifact) to a multi-resolution .ico and a .png, using the exact bezier
path data from the approved SVG design. PIL has no bezier stroke support,
so each cubic segment is sampled finely and drawn as a chain of round
circles (radius = stroke_width/2) to fake a round-capped, round-joined
stroke.
"""
from PIL import Image, ImageDraw

INK = (16, 19, 26, 255)      # #10131A
SIGNAL = (91, 143, 247, 255)  # #5B8FF7

# Same three path segments as #rho-glyph in the logo artifact, in the
# original 100x100 viewBox coordinate space.
SEGMENTS = [
    # wave
    [(18, 76), (24, 56), (30, 56), (34, 76)],
    [(34, 76), (37, 90), (40, 90), (44, 76)],
    # stem (as a degenerate cubic = straight line)
    [(44, 14), (44, 14), (44, 76), (44, 76)],
    # bowl
    [(44, 14), (61, 14), (74, 21), (74, 33)],
    [(74, 33), (74, 45), (61, 48), (44, 48)],
]

VIEWBOX = 100
BADGE_RX_FRAC = 22 / 100  # corner radius as a fraction of the box
STROKE_FRAC = 9.5 / 100   # stroke width as a fraction of the box


def cubic_point(p0, p1, p2, p3, t):
    mt = 1 - t
    x = (mt**3) * p0[0] + 3 * (mt**2) * t * p1[0] + 3 * mt * (t**2) * p2[0] + (t**3) * p3[0]
    y = (mt**3) * p0[1] + 3 * (mt**2) * t * p1[1] + 3 * mt * (t**2) * p2[1] + (t**3) * p3[1]
    return x, y


def render(size, ground=INK, glyph=SIGNAL, badge=True):
    supersample = 4
    canvas_size = size * supersample
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    scale = canvas_size / VIEWBOX

    if badge:
        rx = BADGE_RX_FRAC * canvas_size
        draw.rounded_rectangle(
            [2 * scale, 2 * scale, canvas_size - 2 * scale, canvas_size - 2 * scale],
            radius=rx,
            fill=ground,
        )

    stroke_w = STROKE_FRAC * canvas_size
    r = stroke_w / 2
    samples_per_segment = 160

    for p0, p1, p2, p3 in SEGMENTS:
        pts = [cubic_point(p0, p1, p2, p3, t / samples_per_segment) for t in range(samples_per_segment + 1)]
        pts = [(x * scale, y * scale) for x, y in pts]
        draw.line(pts, fill=glyph, width=int(round(stroke_w)), joint="curve")
        for x, y in pts:
            draw.ellipse([x - r, y - r, x + r, y + r], fill=glyph)
        # explicit round caps at the open ends of the stroke
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=glyph)

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    import os

    out_dir = os.path.dirname(os.path.abspath(__file__))

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_images = [render(s) for s in ico_sizes]
    ico_path = os.path.join(out_dir, "rhema.ico")
    ico_images[-1].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_images[:-1],
    )
    print("wrote", ico_path)

    png_path = os.path.join(out_dir, "rhema.png")
    render(512).save(png_path)
    print("wrote", png_path)
