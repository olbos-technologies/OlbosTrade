"""
The OLBOS wordmark is artwork (frontend/src/components/BrandWordmark.tsx), not
text, and these guard the two ways that silently breaks.

The first is the bug that prompted this file. The outlines are generated from
the font with the inked bounding box translated onto the viewBox origin. Get
that translate wrong -- ymax instead of -ymin, which is an easy slip because
both are small numbers near the baseline -- and the artwork renders entirely
outside its own viewBox. The component still mounts, the box is still
reserved, the colour is still right, and every layout assertion still passes.
Only the letters are missing. It shipped past a typecheck and a build before a
screenshot caught it.

The second is drift. Five surfaces draw this wordmark; the moment one goes
back to text, that surface reintroduces the fallback-face flash the artwork
exists to remove and starts rendering a different shape from the other four.

Lives in the backend suite for the reason test_static_cache_headers.py does:
it checks frontend files, and the display-font guard that tried this in vitest
silently read nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
COMPONENT = FRONTEND / "src" / "components" / "BrandWordmark.tsx"
SURFACES = [
    FRONTEND / "src" / "components" / "TerminalLayout.tsx",
    FRONTEND / "src" / "pages" / "Landing.tsx",
    FRONTEND / "src" / "pages" / "authShell.tsx",
]

NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _artwork():
    src = COMPONENT.read_text()
    vb = re.search(r"const VIEWBOX_W = (\d+);\s*const VIEWBOX_H = (\d+);", src)
    tr = re.search(r'transform="translate\((-?\d+) (-?\d+)\)"', src)
    paths = re.findall(r'<path d="([^"]+)"', src)
    assert vb, "could not read VIEWBOX_W/H — did the component's shape change?"
    assert tr, "could not read the <g> translate"
    assert paths, "no <path> data found"
    return int(vb.group(1)), int(vb.group(2)), int(tr.group(1)), int(tr.group(2)), paths


def _bounds(d: str) -> tuple[float, float, float, float]:
    """Exact bounds for the M/L/H/V/Q/Z subset fontTools emits.

    Quadratic extrema are solved rather than approximated by control points:
    a control point can sit outside the curve it steers, so using it would
    inflate the box and make this guard fail on correct artwork.
    """
    xs: list[float] = []
    ys: list[float] = []
    cx = cy = 0.0
    for cmd, args in re.findall(r"([MLHVQZmlhvqz])([^MLHVQZmlhvqz]*)", d):
        n = [float(v) for v in NUM.findall(args)]
        up = cmd.isupper()
        if cmd in "Mm":
            for i in range(0, len(n), 2):
                cx, cy = (n[i], n[i + 1]) if up else (cx + n[i], cy + n[i + 1])
                xs.append(cx); ys.append(cy)
        elif cmd in "Ll":
            for i in range(0, len(n), 2):
                cx, cy = (n[i], n[i + 1]) if up else (cx + n[i], cy + n[i + 1])
                xs.append(cx); ys.append(cy)
        elif cmd in "Hh":
            for v in n:
                cx = v if up else cx + v
                xs.append(cx); ys.append(cy)
        elif cmd in "Vv":
            for v in n:
                cy = v if up else cy + v
                xs.append(cx); ys.append(cy)
        elif cmd in "Qq":
            for i in range(0, len(n), 4):
                x1, y1 = (n[i], n[i + 1]) if up else (cx + n[i], cy + n[i + 1])
                x2, y2 = (n[i + 2], n[i + 3]) if up else (cx + n[i + 2], cy + n[i + 3])
                for p0, p1, p2, acc in ((cx, x1, x2, xs), (cy, y1, y2, ys)):
                    acc.append(p0); acc.append(p2)
                    denom = p0 - 2 * p1 + p2
                    if denom != 0:
                        t = (p0 - p1) / denom
                        if 0 < t < 1:
                            acc.append((1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2)
                cx, cy = x2, y2
    return min(xs), min(ys), max(xs), max(ys)


def test_the_scan_reads_the_component():
    """Guards the guard: a regex that matched nothing would pass everything."""
    w, h, _, _, paths = _artwork()
    assert w > 0 and h > 0
    assert len(paths) == 5, f"OLBOS is five glyphs; found {len(paths)} paths"
    assert sum(len(p) for p in paths) > 500, "path data looks truncated"


def test_wordmark_artwork_lands_inside_its_viewbox():
    """The translate bug: artwork drawn outside the viewBox renders blank.

    Everything else about the component still looks correct when this is
    wrong, which is why it needs asserting rather than eyeballing.
    """
    vw, vh, tx, ty, paths = _artwork()
    xs0, ys0, xs1, ys1 = zip(*(_bounds(p) for p in paths))
    x0, y0 = min(xs0) + tx, min(ys0) + ty
    x1, y1 = max(xs1) + tx, max(ys1) + ty

    assert -1 <= x0 and x1 <= vw + 1, (
        f"wordmark spans x[{x0:.0f},{x1:.0f}] but the viewBox is 0..{vw} — "
        "it will be clipped or invisible")
    assert -1 <= y0 and y1 <= vh + 1, (
        f"wordmark spans y[{y0:.0f},{y1:.0f}] but the viewBox is 0..{vh}. "
        "This is the ymax/-ymin translate slip: the glyphs render outside "
        "their own box and the wordmark disappears while every layout "
        "assertion still passes.")

    # And it must actually FILL the box, not hide in a corner of it.
    assert (x1 - x0) > vw * 0.95 and (y1 - y0) > vh * 0.95, (
        f"artwork {x1-x0:.0f}x{y1-y0:.0f} does not fill its {vw}x{vh} viewBox; "
        "the box and the drawing have drifted apart")


def test_every_wordmark_surface_uses_the_component():
    """No surface may go back to text: that is a fallback-face flash on the
    brand element, and a second shape competing with the artwork."""
    offenders = []
    for path in SURFACES:
        src = path.read_text()
        for m in re.finditer(r">\s*OLBOS\s*<", src):
            line = src[:m.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line}")
    assert not offenders, (
        "the wordmark is rendered as TEXT at " + ", ".join(offenders)
        + " — use <BrandWordmark /> so every surface draws the same outlines")


def test_no_webfont_is_loaded_just_for_the_wordmark():
    """Manrope was removed when the wordmark became artwork. Re-adding it to
    set five letters brings back a fourth family and the FOUT with it."""
    css = (FRONTEND / "src" / "index.css").read_text()
    imports = [l for l in css.splitlines() if l.startswith("@import")]
    assert imports, "the font @import vanished — this scan is reading nothing"
    assert "Manrope" not in imports[0], (
        "Manrope is being downloaded again; the wordmark is outlines now and "
        "needs no webfont")
