"""Shared test fixtures.

Nothing global lived here before, and nothing here is autouse. A fixture that
applies to every test changes the meaning of tests nobody is looking at —
including, in this case, the ones that most need to see the real behaviour.
Tests that want the stub ask for it by name.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.position_claim import Claim


@pytest.fixture
def stub_position_claim():
    """Let the duplicate guard's position claim succeed, without a database.

    `_execute_signal` Stage 3 claims (underlying, asset class) before
    submitting — that is what closes the window between the duplicate read and
    the trade row being written. Tests of _execute_signal's OTHER behaviour
    fake `AsyncSessionLocal` to serve one specific query, so the claim's own
    statements raise against the fake, and because Stage 3 fails closed those
    tests report `blocked` instead of their own subject.

    Request this fixture to say "this test is not about the claim". It yields
    the mocks so a test can still assert on them — `claim.try_claim`,
    `claim.mark_submitted` and so on.

    Anything that IS about the claim must not use this: see
    test_position_claim_pg.py, which runs the real service against a real
    PostgreSQL, and test_trade_desk_claim_route.py, which drives the route
    through it.
    """
    held = Claim(
        token=uuid.uuid4(),
        idempotency_key="test-idem-key",
        underlying="TEST",
        asset_class="equity",
    )
    with patch("app.services.position_claim.try_claim",
               new=AsyncMock(return_value=held)) as try_claim, \
         patch("app.services.position_claim.mark_submitted",
               new=AsyncMock(return_value=True)) as mark_submitted, \
         patch("app.services.position_claim.mark_unknown",
               new=AsyncMock()) as mark_unknown, \
         patch("app.services.position_claim.resolve",
               new=AsyncMock()) as resolve:
        yield type("StubbedClaim", (), {
            "claim": held,
            "try_claim": try_claim,
            "mark_submitted": mark_submitted,
            "mark_unknown": mark_unknown,
            "resolve": resolve,
        })
