"""Shared test setup.

Nothing global lived here before; this exists for one reason, described below.
Add to it sparingly — an autouse fixture changes every test in the suite, and
the reason has to be worth that.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_position_claim: exercise the real position-claim service rather "
        "than the always-succeeds stub from the autouse fixture",
    )


@pytest.fixture(autouse=True)
def _position_claim_succeeds(request):
    """Let the duplicate guard's position claim succeed by default.

    `_execute_signal` Stage 3 now claims (underlying, asset class) in the
    database before submitting, which is what closes the window between the
    duplicate read and the trade row being written. The existing tests of that
    function fake `AsyncSessionLocal` to serve one specific query, so the
    claim's own statements raise against the fake — and because Stage 3 fails
    CLOSED, every one of those tests started reporting `blocked` instead of
    what it was actually about.

    Stubbing the claim here keeps those tests testing their own subject. The
    claim's real behaviour — including that two concurrent callers cannot both
    win — is covered against a live PostgreSQL in test_position_claim_pg.py,
    which opts out with the `real_position_claim` marker.
    """
    if request.node.get_closest_marker("real_position_claim"):
        yield
        return
    with patch("app.services.position_claim.try_claim",
               new=AsyncMock(return_value=True)):
        yield
