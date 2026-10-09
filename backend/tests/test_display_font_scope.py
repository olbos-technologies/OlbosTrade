"""
The editorial display face is for marketing surfaces only.

Playfair Display was adopted for the landing and auth headings because those
are prose at 28-44px. It must not reach the terminal, and the reasons are
measurable rather than aesthetic:

  - Its numerals do not tabulate. index.css carries an explicit `.tnum` rule
    ("Data cells — sans + tabular-nums ... so columns align without using
    --mono"); a serif in a data cell misaligns every column in every table.
  - The terminal renders data at 9-11px. A high-contrast serif is built on
    hairline strokes, which is exactly what vanishes at that size on a dark
    background.
  - Only weight 600 is loaded. Anything wanting 400 or 500 silently gets a
    synthesised face.

WHY THIS IS A PYTHON TEST AND NOT A VITEST ONE
-----------------------------------------------
It was written in vitest first, using the same `import.meta.glob` trick as
vocabulary.test.ts, and it was worthless: vitest stubs CSS, so every .css file
came back as the empty string while .tsx files loaded fine. The scan passed by
reading nothing. `?raw` and `?inline` both return "" — this was verified, not
assumed.

So it lives here, next to test_static_cache_headers.py, which already scans
frontend/docker-entrypoint.sh from the backend suite for the same reason: the
rule spans files that the frontend's own test environment cannot read. Both run
in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# Marketing surfaces, plus the file that defines the token.
ALLOWED = {
    "landing.css",   # landing headings
    "auth.css",      # login / request-access / claim
    "index.css",     # defines --display
}

PATTERN = re.compile(r"var\(--display\)|Playfair")


def _scanned_files() -> list[Path]:
    return [
        p for ext in ("*.css", "*.ts", "*.tsx")
        for p in SRC.rglob(ext)
        if "__tests__" not in p.parts
    ]


def test_the_scan_actually_reads_files():
    """Guards the guard.

    The vitest version of this test silently read nothing. A file count and a
    known-content assertion are what would have caught that, so both are here.
    """
    files = _scanned_files()
    assert len(files) > 50, f"only found {len(files)} files under {SRC}"

    index = (SRC / "index.css").read_text()
    assert "--display" in index, "index.css did not load, or no longer defines --display"


def test_display_font_is_referenced_only_from_marketing_surfaces():
    offenders = []
    for path in _scanned_files():
        if path.name in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if PATTERN.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the display face escaped the marketing surfaces:\n  " + "\n  ".join(offenders)
    )


def test_index_css_defines_the_token_without_applying_it():
    """Defining --display is fine; applying it in the shared stylesheet is not."""
    applied = [
        f"{i}: {line.strip()}"
        for i, line in enumerate((SRC / "index.css").read_text().splitlines(), 1)
        if re.search(r"font-family:\s*var\(--display\)", line)
    ]
    assert not applied, "index.css applies --display, which reaches the terminal:\n  " + "\n  ".join(applied)


def test_tabular_data_cells_still_use_the_sans_face():
    """The specific rule --display would undermine.

    If .tnum ever moves to a serif, the reasoning in this module's docstring
    stops holding and this whole guard needs rethinking rather than updating.
    """
    index = (SRC / "index.css").read_text()
    match = re.search(r"\.tnum\s*\{([^}]*)\}", index)
    assert match, ".tnum rule not found in index.css"
    body = match.group(1)
    assert "var(--sans)" in body, f".tnum no longer uses the sans face: {body.strip()}"
    assert "tabular-nums" in body, f".tnum no longer sets tabular-nums: {body.strip()}"


@pytest.mark.parametrize("surface", ["landing.css", "auth.css"])
def test_marketing_surfaces_do_use_it(surface):
    """The inverse: if the adoption is reverted, this guard should stop claiming
    to protect something that is no longer there."""
    assert "var(--display)" in (SRC / surface).read_text(), (
        f"{surface} no longer uses --display — is the serif adoption still in place?")
