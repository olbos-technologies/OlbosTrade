"""Brand artwork must stay centralized and loadable on every UI surface.

The complete angular mark, OLBOS lettering and TRADE descriptor are one
transparent lockup.  Keeping the proportions in one asset prevents the
landing page, authentication shell and terminal chrome from drifting into
three slightly different logos.
"""

from __future__ import annotations

import re
from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
COMPONENT = FRONTEND / "src" / "components" / "BrandWordmark.tsx"
PUBLIC = FRONTEND / "public"
LOCKUP = PUBLIC / "olbos-trade-lockup.webp"
MARK = PUBLIC / "olbos-mark.webp"
SURFACES = [
    FRONTEND / "src" / "components" / "TerminalLayout.tsx",
    FRONTEND / "src" / "pages" / "Landing.tsx",
    FRONTEND / "src" / "pages" / "authShell.tsx",
]


def test_brand_assets_are_optimized_webp_files():
    for path in (LOCKUP, MARK):
        data = path.read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        assert len(data) < 100_000, f"{path.name} is too heavy for application chrome"


def test_component_reserves_the_lockup_aspect_ratio():
    src = COMPONENT.read_text()
    assert 'src="/olbos-trade-lockup.webp"' in src
    assert "const LOCKUP_W = 1086" in src
    assert "const LOCKUP_H = 280" in src
    assert 'alt="Olbos Trade"' in src


def test_every_brand_surface_uses_the_shared_component():
    missing = [path.name for path in SURFACES if "<BrandWordmark" not in path.read_text()]
    assert not missing, f"surfaces bypass the shared lockup: {missing}"


def test_old_pearl_mark_is_not_referenced_by_the_ui():
    offenders = []
    for path in SURFACES:
        src = path.read_text()
        if re.search(r"olbos-o(?:-sm)?\.webp", src):
            offenders.append(path.name)
    assert not offenders, f"old pearl-O asset still used by: {offenders}"


def test_no_webfont_is_loaded_just_for_the_wordmark():
    css = (FRONTEND / "src" / "index.css").read_text()
    imports = [line for line in css.splitlines() if line.startswith("@import")]
    assert imports, "the font @import vanished — this scan is reading nothing"
    assert "Manrope" not in imports[0]
