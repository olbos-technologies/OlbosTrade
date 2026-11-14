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
REGISTRY = FRONTEND / "src" / "brand" / "brands.ts"
PUBLIC = FRONTEND / "public"
LOCKUP = PUBLIC / "olbos-trade-lockup.webp"
MARK = PUBLIC / "olbos-mark.webp"
CIMPHAWNG = PUBLIC / "brands" / "cimphawng"
SURFACES = [
    FRONTEND / "src" / "components" / "TerminalLayout.tsx",
    FRONTEND / "src" / "pages" / "Landing.tsx",
    FRONTEND / "src" / "pages" / "authShell.tsx",
]


def test_brand_assets_are_optimized_webp_files():
    # Every brand's chrome artwork meets the same weight limit.
    for path in (LOCKUP, MARK, CIMPHAWNG / "lockup.webp", CIMPHAWNG / "mark.webp"):
        data = path.read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        assert len(data) < 100_000, f"{path.name} is too heavy for application chrome"


def test_component_reserves_the_lockup_aspect_ratio():
    """The lockup's path, proportions and alt text live in the brand registry.

    BrandWordmark reads them from there, so the header, landing page and sign-in
    shell cannot assemble different logos. The Olbos values are pinned here so a
    change to them is deliberate.
    """
    registry = REGISTRY.read_text()
    assert registry.strip(), "brands.ts did not load - this scan is reading nothing"
    assert 'src: "/olbos-trade-lockup.webp"' in registry
    assert "aspect: 1086 / 280" in registry
    assert 'alt: "Olbos Trade"' in registry
    component = COMPONENT.read_text()
    assert "wordmark.aspect" in component, "width must be reserved from the brand's aspect ratio"
    assert "wordmark.src" in component and "wordmark.alt" in component
    # No brand's artwork path is hard-coded in the component any more.
    assert "olbos-trade-lockup" not in component


def test_every_brand_lockup_asset_exists():
    registry = REGISTRY.read_text()
    srcs = re.findall(r'wordmark: \{ src: "([^"]+)"', registry)
    assert len(srcs) >= 2, "expected a wordmark for each brand"
    for src in srcs:
        assert (PUBLIC / src.lstrip("/")).is_file(), f"missing brand lockup {src}"


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
    # 2026-11: the Google Fonts @import is gone (fonts are self-hosted), so there
    # may legitimately be no @import. The scan must still prove it read the file.
    assert "--mono" in css, "index.css did not load — this scan is reading nothing"
    imports = [line for line in css.splitlines() if line.startswith("@import")]
    assert all("Manrope" not in line for line in imports)
    assert "Manrope" not in css
