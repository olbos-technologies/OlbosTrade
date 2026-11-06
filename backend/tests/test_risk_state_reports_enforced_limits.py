"""/api/risk/portfolio-state must report the loss limits the engine enforces.

2026-11-06: the header strip read "DAILY DD 0.0% / 5.0%" while the Risk page's rule list
and the guardrail engine enforced 2.0%. Two routes, two sources: this one reported the
plain MAX_*_LOSS_PCT settings, which paper-visibility mode ignores, while the engine uses
the effective_* values. The strip therefore named a limit that was not the one in force.

Both routes must now agree, in both modes.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.core.config import settings
from app.services.guardrails import GuardrailEngine

PLAIN = dict(max_daily_loss_pct=0.05, max_weekly_loss_pct=0.10)
VIS = dict(paper_visibility_max_daily_loss_pct=0.02, paper_visibility_max_weekly_loss_pct=0.15)


class _FakeDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        r = MagicMock()
        r.scalar.return_value = 0
        r.scalars.return_value.all.return_value = []
        return r


async def _state(visibility: bool):
    from app.api.routes.risk import get_portfolio_state

    acct = MagicMock(net_liquidation=25_000.0, buying_power=25_000.0, cash_balance=25_000.0)
    patches = [
        patch.object(type(settings), "paper_visibility_active",
                     new_callable=PropertyMock, return_value=visibility),
        patch("app.broker.broker_factory.get_broker", return_value=MagicMock()),
        patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit", new=AsyncMock(return_value=acct)),
        patch("app.api.routes.risk.AsyncSessionLocal", lambda: _FakeDB()),
        patch("app.api.routes.risk.scope_or_401", new=AsyncMock(return_value=None)),
        *[patch.object(settings, k, v) for k, v in {**PLAIN, **VIS}.items()],
    ]
    for p in patches:
        p.start()
    try:
        out = await get_portfolio_state(MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()
    return out["state"]


@pytest.mark.asyncio
async def test_in_visibility_mode_the_strip_reports_the_visibility_limits():
    state = await _state(visibility=True)
    assert state.get("error") is None, state.get("error")
    assert state["max_daily_loss_pct"] == 0.02      # not the plain 0.05
    assert state["max_weekly_loss_pct"] == 0.15     # not the plain 0.10


@pytest.mark.asyncio
async def test_outside_visibility_mode_it_reports_the_plain_limits():
    state = await _state(visibility=False)
    assert state["max_daily_loss_pct"] == 0.05
    assert state["max_weekly_loss_pct"] == 0.10


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", [True, False])
async def test_the_strip_and_the_engine_never_disagree(visibility):
    state = await _state(visibility=visibility)
    with patch.object(type(settings), "paper_visibility_active",
                      new_callable=PropertyMock, return_value=visibility), \
         patch.object(settings, "max_daily_loss_pct", PLAIN["max_daily_loss_pct"]), \
         patch.object(settings, "max_weekly_loss_pct", PLAIN["max_weekly_loss_pct"]), \
         patch.object(settings, "paper_visibility_max_daily_loss_pct", VIS["paper_visibility_max_daily_loss_pct"]), \
         patch.object(settings, "paper_visibility_max_weekly_loss_pct", VIS["paper_visibility_max_weekly_loss_pct"]):
        engine = GuardrailEngine()
    assert state["max_daily_loss_pct"] == engine.max_daily_loss_pct
    assert state["max_weekly_loss_pct"] == engine.max_weekly_loss_pct
