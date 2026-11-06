"""/api/guardrails/status must report the limits that are actually enforced.

The Dashboard and Risk page meters used to compare against fixed numbers (2% / 5% /
10% / 15% / 3 / 5) that drifted from the real limits. They now read these fields, so
this pins what the fields are — including the part that bit the owner on 2026-11-06:
while PAPER_TRADE_VISIBILITY_MODE is on, the plain MAX_*_LOSS_PCT settings are ignored
and the PAPER_VISIBILITY_* ones apply, so editing the plain ones changes nothing.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.core.config import settings
from app.services.guardrails import GuardrailEngine

from test_trading_capital_base import LIVE, _FakeDB, _broker

LIMIT_KEYS = (
    "max_daily_loss_pct", "max_weekly_loss_pct", "max_monthly_loss_pct", "max_drawdown_pct",
    "max_trades_per_day", "max_consecutive_losses", "max_concurrent_positions",
)


async def _status(engine=None, *, mode_cap=None, global_cap=None):
    import app.main as m
    from app.services.trading_mode import trading_mode_manager

    stack = [
        patch("app.broker.broker_factory.get_broker", return_value=_broker(LIVE)),
        patch("app.core.database.AsyncSessionLocal", lambda: _FakeDB()),
        patch.object(m, "_maybe_log_guardrail_event", AsyncMock()),
    ]
    if engine is not None:
        stack.append(patch.object(m, "_guardrail_engine", engine))
    if global_cap is not None:
        stack.append(patch.object(settings, "max_concurrent_positions", global_cap))
    if mode_cap is not None:
        # `config` is a property returning a frozen dataclass: swap in a modified copy.
        import dataclasses
        tightened = dataclasses.replace(trading_mode_manager.config, max_concurrent=mode_cap)
        stack.append(patch.object(type(trading_mode_manager), "config",
                                  new_callable=PropertyMock, return_value=tightened))
    for p in stack:
        p.start()
    try:
        return await m.guardrail_status()
    finally:
        for p in reversed(stack):
            p.stop()


def _engine(**limits):
    eng = GuardrailEngine()
    for k, v in limits.items():
        setattr(eng, k, v)
    return eng


@pytest.mark.asyncio
async def test_every_limit_the_meters_need_is_reported():
    out = await _status()
    for key in LIMIT_KEYS:
        assert isinstance(out.get(key), (int, float)) and out[key] > 0, key


@pytest.mark.asyncio
async def test_the_reported_limits_are_the_engines_not_constants():
    eng = _engine(max_daily_loss_pct=0.05, max_weekly_loss_pct=0.10, max_monthly_loss_pct=0.20,
                  max_drawdown_pct=0.30, max_trades_per_day=30, max_consecutive_losses=4)
    out = await _status(eng)
    assert (out["max_daily_loss_pct"], out["max_weekly_loss_pct"], out["max_monthly_loss_pct"]) == (0.05, 0.10, 0.20)
    assert (out["max_drawdown_pct"], out["max_trades_per_day"], out["max_consecutive_losses"]) == (0.30, 30, 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("global_cap,mode_cap,expected", [(5, 3, 3), (5, 8, 5), (5, 5, 5), (2, 9, 2)])
async def test_open_positions_cap_is_the_lower_of_global_and_mode(global_cap, mode_cap, expected):
    """The execution gate takes min(global, mode); a meter showing the global cap alone
    would overstate headroom whenever the mode is tighter (e.g. Scalper)."""
    out = await _status(global_cap=global_cap, mode_cap=mode_cap)
    assert out["max_concurrent_positions"] == expected


# ── the paper-visibility precedence the owner tripped over ──────────────────

def _engine_with(visibility: bool, **overrides):
    patches = [patch.object(type(settings), "paper_visibility_active", new_callable=PropertyMock, return_value=visibility)]
    patches += [patch.object(settings, k, v) for k, v in overrides.items()]
    for p in patches:
        p.start()
    try:
        return GuardrailEngine()
    finally:
        for p in reversed(patches):
            p.stop()


PLAIN = dict(max_daily_loss_pct=0.05, max_weekly_loss_pct=0.10, max_monthly_loss_pct=0.20, max_consecutive_losses=4)
VIS = dict(paper_visibility_max_daily_loss_pct=0.02, paper_visibility_max_weekly_loss_pct=0.15,
           paper_visibility_max_monthly_loss_pct=0.25, paper_visibility_max_consecutive_losses=8)


def test_with_paper_visibility_on_the_plain_loss_limits_are_inert():
    eng = _engine_with(True, **PLAIN, **VIS)
    assert (eng.max_daily_loss_pct, eng.max_weekly_loss_pct, eng.max_monthly_loss_pct) == (0.02, 0.15, 0.25)
    assert eng.max_consecutive_losses == 8


def test_with_paper_visibility_on_its_own_settings_are_what_apply():
    eng = _engine_with(True, **PLAIN, paper_visibility_max_daily_loss_pct=0.05,
                       paper_visibility_max_weekly_loss_pct=0.10, paper_visibility_max_monthly_loss_pct=0.20,
                       paper_visibility_max_consecutive_losses=4)
    assert (eng.max_daily_loss_pct, eng.max_weekly_loss_pct, eng.max_monthly_loss_pct) == (0.05, 0.10, 0.20)
    assert eng.max_consecutive_losses == 4


def test_with_paper_visibility_off_the_plain_limits_apply():
    eng = _engine_with(False, **PLAIN, **VIS)
    assert (eng.max_daily_loss_pct, eng.max_weekly_loss_pct, eng.max_monthly_loss_pct) == (0.05, 0.10, 0.20)
    assert eng.max_consecutive_losses == 4
