"""A mission card must not shrink inside a height-constrained list.

Guards a real, visible failure on the Copilot Queue. `.mission-list` is a
flex column; `.mission-card` carries `overflow: hidden`. The queue caps itself
at `maxHeight: "55%"`, so with 174 pending signals the default `flex-shrink: 1`
let every card shrink from 115px to 28px, and `overflow: hidden` then clipped
the ticker, the subtitle and the Approve/Reject row. The queue rendered as a
stack of thin strips with no way to act on anything in it.

The second half of the bug is why it could not be scrolled out of: flex shrank
the items to fit the container, so the list never overflowed, so
`overflow-y: auto` had nothing to scroll and showed no scrollbar. Pinning the
card height is what makes the list overflow and therefore scroll — one
property fixes both halves.

Measured in Chromium at 1440x900 and 390x844, 174 cards:

    before   card 28px   title clipped   actions outside the card box
    after    card 115px (desktop) / 158px (mobile), everything inside

WHY THIS IS A PYTHON TEST AND NOT A VITEST ONE
-----------------------------------------------
Two reasons, both from CLAUDE.md. jsdom does no layout, so a vitest test
cannot observe a collapsed card at all — it would pass against the broken
CSS. And vitest stubs CSS, so reading index.css from the frontend suite
returns the empty string and the scan would assert nothing. It therefore
lives here, beside test_display_font_scope.py and test_static_cache_headers.py,
which scan frontend files from the backend suite for the same reason.

What this test CANNOT do is prove the layout renders correctly — only a real
browser can, and that was done by hand when the fix was made. It pins the one
property whose removal brings the bug back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INDEX_CSS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "index.css"
)


@pytest.fixture(scope="module")
def mission_card_rule() -> str:
    """The body of the `.mission-card` rule, so the scan reads something."""
    assert INDEX_CSS.exists(), f"{INDEX_CSS} is missing — this guard reads nothing"
    css = INDEX_CSS.read_text()

    match = re.search(r"^\.mission-card\s*\{(.*?)^\}", css, re.S | re.M)
    assert match, (
        "no `.mission-card` rule found in index.css. If it was renamed, this "
        "guard is now reading nothing and must be updated to match."
    )
    body = match.group(1)
    # The rule must still be the one this test is about: a clipping card.
    assert "overflow" in body, (
        "`.mission-card` no longer sets overflow. If the clipping is gone the "
        "collapse may be harmless now — re-verify in a real browser with a "
        "long queue before deleting this test."
    )
    return body


def test_the_card_cannot_shrink_in_a_flex_list(mission_card_rule):
    """The one property whose absence reproduces the bug."""
    assert re.search(r"flex-shrink:\s*0\b", mission_card_rule), (
        "`.mission-card` lost `flex-shrink: 0`. Inside `.mission-list` (a flex "
        "column) in a height-capped container, cards shrink and `overflow: "
        "hidden` clips their text and action buttons — and the list stops "
        "scrolling, because flex fits the items instead of overflowing. "
        "See this test's docstring for the measurements."
    )


def test_the_queue_still_caps_its_height(mission_card_rule):
    """The constraint that makes the shrink possible is still there.

    If the Copilot queue stops capping its own height, cards have no reason to
    shrink and this guard is protecting nothing — which is worth knowing,
    because a guard that cannot fail is worse than no guard.
    """
    queue = (
        INDEX_CSS.parent / "trade-desk" / "copilot" / "CopilotQueue.tsx"
    ).read_text()
    assert "mission-list" in queue, (
        "CopilotQueue no longer renders a `.mission-list`; this guard no "
        "longer describes it."
    )
    assert re.search(r"maxHeight:\s*[\"']55%[\"']", queue), (
        "the Copilot queue no longer caps its height at 55%. That cap is what "
        "put the cards under pressure. If it is gone deliberately, re-verify "
        "the long-queue layout in a real browser and update this test."
    )
