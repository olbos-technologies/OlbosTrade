"""
The favicon set declared in index.html must actually exist, and be the size it
claims.

Both failure modes here are silent. A missing file is a 404 the browser hides
behind its default globe icon; a favicon-32x32.png that is really 16px just
looks slightly soft and nobody notices for months. Neither shows up in vitest,
which never loads index.html or reads public/.

Sizes are read from the PNG IHDR header directly rather than with Pillow, so
this needs nothing that is not already in the backend's requirements.

Lives in the backend suite for the same reason test_static_cache_headers.py
does: it checks frontend files that the frontend's own test environment cannot
see.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
INDEX = FRONTEND / "index.html"
PUBLIC = FRONTEND / "public"

ICON_LINK = re.compile(
    r'<link[^>]*rel="(?:icon|apple-touch-icon)"[^>]*href="/([^"]+)"[^>]*>',
    re.IGNORECASE,
)
SIZES_ATTR = re.compile(r'sizes="(\d+)x(\d+)"')

# Measured, not guessed. With filters correctly reconstructed, the render
# scores 729 distinct colours (quantised to 5 bits per channel) and
# favicon.svg rasterised in Chromium at the same 180px scores 141 -- a 5.2x
# gap. 350 sits roughly 2x from either side, so a re-export at a different
# compression level cannot trip it but swapping one drawing for the other
# does.
PHOTOGRAPHIC_COLOUR_FLOOR = 350

# The render is a thin shaded pearl ring on navy, so near-white is only ~7.6%
# of it. This floor is deliberately well below that: its job is to catch an
# icon with no pearl left at all, NOT to tell the two drawings apart. It
# cannot do the latter -- the flat drawing is a thicker stroke and scores
# ~15%, i.e. HIGHER. Only the colour count separates them.
PEARL_PIXEL_FLOOR = 0.03


def _png_size(path: Path) -> tuple[int, int]:
    """Width/height from the IHDR chunk. No image library needed."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    assert data[12:16] == b"IHDR", f"{path.name} has no IHDR chunk"
    return struct.unpack(">II", data[16:24])


def _png_pixels(path: Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    """Decode a PNG to RGB triples, undoing the per-row filters.

    Written out by hand because Pillow is not in the backend's requirements,
    and skipped filter reconstruction is NOT a shortcut that merely loses
    accuracy -- it yields plausible-looking noise. The first version of this
    guard read the filter byte and then ignored it, and every statistic it
    computed was garbage that happened to fall in a believable range. All
    five filter types appear in these assets, so this has to be complete.

    Rows must be walked in order even when only some are sampled: filters 2-4
    reconstruct against the row above.
    """
    data = path.read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    bit_depth, colour_type = data[24], data[25]
    assert bit_depth == 8, f"{path.name} is not 8-bit; this decoder assumes it"
    bpp = {0: 1, 2: 3, 6: 4}.get(colour_type)
    assert bpp, f"{path.name} has unsupported PNG colour type {colour_type}"

    idat = b""
    i = 8
    while i < len(data):
        length = int.from_bytes(data[i:i + 4], "big")
        if data[i + 4:i + 8] == b"IDAT":
            idat += data[i + 8:i + 8 + length]
        i += 12 + length
    raw = zlib.decompress(idat)

    stride = width * bpp + 1
    assert len(raw) >= stride * height, f"{path.name}: short IDAT"

    pixels: list[tuple[int, int, int]] = []
    prev = bytearray(width * bpp)
    for row in range(height):
        ftype = raw[row * stride]
        line = bytearray(raw[row * stride + 1:(row + 1) * stride])
        for x in range(len(line)):
            left = line[x - bpp] if x >= bpp else 0
            up = prev[x]
            upleft = prev[x - bpp] if x >= bpp else 0
            if ftype == 1:
                line[x] = (line[x] + left) & 0xFF
            elif ftype == 2:
                line[x] = (line[x] + up) & 0xFF
            elif ftype == 3:
                line[x] = (line[x] + (left + up) // 2) & 0xFF
            elif ftype == 4:
                est = left + up - upleft
                da, db, dc = abs(est - left), abs(est - up), abs(est - upleft)
                nearest = left if da <= db and da <= dc else up if db <= dc else upleft
                line[x] = (line[x] + nearest) & 0xFF
            elif ftype != 0:
                raise AssertionError(f"{path.name}: unknown PNG filter {ftype}")
        for x in range(0, len(line), bpp):
            if bpp == 1:
                pixels.append((line[x], line[x], line[x]))
            else:
                pixels.append((line[x], line[x + 1], line[x + 2]))
        prev = line
    return width, height, pixels


def _icon_links() -> list[tuple[str, str]]:
    html = INDEX.read_text()
    return [(m.group(0), m.group(1)) for m in ICON_LINK.finditer(html)]


def test_the_scan_finds_the_icon_links():
    """Guards the guard — a regex that matches nothing would make the rest pass."""
    links = _icon_links()
    assert len(links) >= 4, f"expected the full icon set, found {len(links)}: {links}"


@pytest.mark.parametrize("filename", [
    "favicon.ico", "favicon.svg", "favicon-16x16.png",
    "favicon-32x32.png", "apple-touch-icon.png",
])
def test_declared_icon_exists(filename):
    assert (PUBLIC / filename).is_file(), f"index.html references /{filename}, which is missing"


def test_every_icon_href_resolves():
    missing = [href for _, href in _icon_links() if not (PUBLIC / href).is_file()]
    assert not missing, f"index.html points at files that do not exist: {missing}"


def test_png_icons_are_the_size_they_claim():
    """A 32x32 link pointing at a 16px file is invisible until someone zooms."""
    wrong = []
    for tag, href in _icon_links():
        declared = SIZES_ATTR.search(tag)
        if not declared or not href.endswith(".png"):
            continue
        want = (int(declared.group(1)), int(declared.group(2)))
        got = _png_size(PUBLIC / href)
        if got != want:
            wrong.append(f"{href} declares {want[0]}x{want[1]} but is {got[0]}x{got[1]}")
    assert not wrong, "\n  ".join(wrong)


def test_apple_touch_icon_is_180_and_opaque():
    """iOS composites a transparent icon onto white, which would put a white
    ring around a navy tile. It is flattened onto the tile colour instead."""
    path = PUBLIC / "apple-touch-icon.png"
    assert _png_size(path) == (180, 180)
    # colour type 2 (truecolour) or 0 (greyscale) carry no alpha channel
    colour_type = path.read_bytes()[25]
    assert colour_type in (0, 2), (
        f"apple-touch-icon has alpha (PNG colour type {colour_type}); iOS will "
        "composite it onto white")


def test_the_two_icons_are_the_same_mark_drawn_differently():
    """Both icons are the O now. What must not collapse is that they are
    DIFFERENT DRAWINGS of it, chosen per size.

    apple-touch-icon is the photographic render: pearl body, gold ribbons,
    real depth. It earns its 180px on an iOS home screen and turns to mush
    below ~48px. favicon.svg is a flat two-stroke drawing — pearl outside,
    gold on the inner edge — that stays legible at 16px where the render
    cannot.

    Two opposite mistakes this guards, and each looks like tidying up:
      * regenerating the favicon by downsampling the render (muddy at 16px);
      * flattening the app icon to the two-stroke drawing (throws away the
        only reason to have the render at all).

    Distinct colour count is what separates them: a photograph has hundreds,
    a flat vector has ~140 even with antialiasing. See
    PHOTOGRAPHIC_COLOUR_FLOOR for the measurements behind the threshold.
    """
    _, _, pixels = _png_pixels(PUBLIC / "apple-touch-icon.png")

    colours = {(r >> 3, g >> 3, b >> 3) for r, g, b in pixels}
    near_white = sum(1 for r, g, b in pixels if r > 200 and g > 195 and b > 185)

    assert pixels, "scan read no pixels"
    assert near_white / len(pixels) > PEARL_PIXEL_FLOOR, (
        f"only {near_white / len(pixels):.1%} of the app icon is near-white; "
        "the icon has no pearl left in it at all -- is it still the O?")
    assert len(colours) > PHOTOGRAPHIC_COLOUR_FLOOR, (
        f"the app icon has only {len(colours)} distinct colours, which is a "
        "flat vector rather than the photographic render. Flattening it "
        "removes the whole reason it is a separate drawing from favicon.svg.")


def test_svg_favicon_is_the_flat_two_stroke_drawing():
    """The counterpart: the favicon must stay drawn, not become a render."""
    svg = (PUBLIC / "favicon.svg").read_text()
    assert "#f4efe6" in svg, "the pearl stroke is missing from favicon.svg"
    assert svg.count("ellipse") >= 2, (
        "favicon.svg is no longer the two-stroke O — if it was replaced with a "
        "rasterised or traced render, it will go muddy at 16px")


def test_svg_favicon_carries_the_brand_colours():
    """Catches a blank or placeholder SVG, which would render as nothing at all."""
    svg = (PUBLIC / "favicon.svg").read_text()
    assert "#D4AF37" in svg, "the gold stroke is missing from favicon.svg"
    assert "viewBox" in svg, "favicon.svg has no viewBox, so it will not scale"
