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


def _png_size(path: Path) -> tuple[int, int]:
    """Width/height from the IHDR chunk. No image library needed."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    assert data[12:16] == b"IHDR", f"{path.name} has no IHDR chunk"
    return struct.unpack(">II", data[16:24])


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


def test_svg_favicon_carries_the_brand_colours():
    """Catches a blank or placeholder SVG, which would render as nothing at all."""
    svg = (PUBLIC / "favicon.svg").read_text()
    assert "#D4AF37" in svg, "the gold stroke is missing from favicon.svg"
    assert "viewBox" in svg, "favicon.svg has no viewBox, so it will not scale"
