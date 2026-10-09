"""
The Trade Desk's close button agrees with close_position() about what closes.

This is the bug, not a hypothetical. close_position() accepts equity_long,
equity_short, put and call, routing the options cases through
close_options_trade(). The frontend gate was written when only equities could
be closed and never moved, so an options row rendered a "close via broker"
placeholder and no button at all. It reached us as "hold to close is not
functioning" — an accurate report, since from outside an absent button and a
dead button are the same thing.

Neither side's own tests could catch that. Each was self-consistent and wrong
about the other. Only a test that reads both can, which is why this one lives
here rather than in vitest: the frontend tsconfig has no node types, and
reading across the boundary is already the idiom in this directory (see
test_login_rate_limit_deployment.py, which reads the compose files, the
Dockerfile and the frontend entrypoint).

Both halves are parsed out of source rather than restated. A test that restated
the allowlist would pass while agreeing only with itself — which is exactly the
failure mode being guarded.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROUTE = REPO / "backend" / "app" / "api" / "routes" / "trade_desk.py"
GATE = REPO / "frontend" / "src" / "utils" / "closeablePosition.ts"


def backend_allowlist() -> list[str]:
    """The spread_types close_position() will accept; everything else 400s."""
    src = ROUTE.read_text()
    m = re.search(r"if\s+spread_type\s+not\s+in\s+\(([^)]*)\)\s*:", src)
    assert m, (
        "close_position()'s `if spread_type not in (...)` guard was not found. "
        "If it moved or changed shape this test silently stops checking "
        "anything, so it fails loudly instead."
    )
    return sorted(re.findall(r'"([a-z_]+)"', m.group(1)))


def frontend_allowlist() -> list[str]:
    """CLOSEABLE_SPREAD_TYPES, which the close button gate is built from."""
    src = GATE.read_text()
    m = re.search(
        r"export\s+const\s+CLOSEABLE_SPREAD_TYPES\s*=\s*\[([^\]]*)\]", src
    )
    assert m, (
        "CLOSEABLE_SPREAD_TYPES was not found in "
        "frontend/src/utils/closeablePosition.ts — if the gate stopped being "
        "declared there, this test cannot see the frontend half any more."
    )
    return sorted(re.findall(r'"([a-z_]+)"', m.group(1)))


def test_both_halves_were_actually_found():
    """Guards the guard: two empty lists compare equal."""
    assert backend_allowlist(), "parsed an empty backend allowlist"
    assert frontend_allowlist(), "parsed an empty frontend allowlist"


def test_the_close_button_offers_exactly_what_the_route_accepts():
    backend, frontend = backend_allowlist(), frontend_allowlist()

    missing = sorted(set(backend) - set(frontend))
    assert not missing, (
        f"the backend closes {missing} but the Trade Desk offers no button for "
        f"them. The row renders a placeholder instead, which reads as a broken "
        f"button — this is the exact drift that produced 'hold to close is not "
        f"functioning'."
    )

    extra = sorted(set(frontend) - set(backend))
    assert not extra, (
        f"the Trade Desk offers a close button for {extra}, which "
        f"close_position() rejects with a 400. A button that cannot succeed is "
        f"worse than no button."
    )


def test_options_types_are_in_both():
    """The specific pair that was missing, named so a regression is obvious."""
    for spread_type in ("put", "call"):
        assert spread_type in backend_allowlist(), f"{spread_type} left the route"
        assert spread_type in frontend_allowlist(), f"{spread_type} left the gate"
