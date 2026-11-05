"""TRADING_CAPITAL_BASE: the desk trades with $25k even when the paper account holds ~$245k.

2026-11: the IBKR paper account holds ~245,880 and the owner wants the desk to behave
like a 25,000 account with positions up to 10% of that. Three things read "the capital":

* sizing (account_state.get_account_value, which the entry gate also uses),
* the guardrail percentages (loss limits divide by STARTING_CAPITAL, drawdown by the peak),
* the persisted all-time peak — which, left at the old ~276k, would read a 25k desk as a
  91% drawdown and halt every entry.

STARTING_CAPITAL=25000 alone is the configuration guardrails.py records as having
suspended bull_put_spread for a month (-$2,927 read as -11.71%). So the cap has to bound
sizing, the percentages AND the peak together; each is pinned below.

Unset (0) must be the old behaviour exactly, and the 2%-risk rule is untouched.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.services.account_state import capped_capital, get_account_value
from app.services.equity_signal_engine import compute_equity_trade_plan
from app.services.guardrails import (
    CAPITAL_BASE_DIVERGENT_FLAG, GuardrailEngine, PortfolioState,
)

LIVE = 245_880.0


@contextmanager
def _base(value: float):
    with patch.object(settings, "trading_capital_base", value):
        yield


def _broker(net_liq):
    b = MagicMock()
    b.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=net_liq))
    return b


# ── the helper ──────────────────────────────────────────────────────────────

def test_unset_leaves_the_live_value_alone():
    with _base(0.0):
        assert capped_capital(LIVE) == LIVE


def test_a_base_below_the_account_caps_it():
    with _base(25_000.0):
        assert capped_capital(LIVE) == 25_000.0


def test_an_account_below_the_base_is_not_raised_to_it():
    with _base(25_000.0):
        assert capped_capital(18_000.0) == 18_000.0       # losses must stay visible


@pytest.mark.parametrize("bad", [0.0, -5.0])
def test_a_failed_read_is_never_turned_into_the_base(bad):
    with _base(25_000.0):
        assert capped_capital(bad) == bad


@pytest.mark.asyncio
async def test_get_account_value_returns_the_capped_figure():
    with _base(25_000.0), patch("app.broker.broker_factory.get_broker", return_value=_broker(LIVE)):
        assert await get_account_value() == 25_000.0


@pytest.mark.asyncio
async def test_get_account_value_unset_is_unchanged():
    with _base(0.0), patch("app.broker.broker_factory.get_broker", return_value=_broker(LIVE)):
        assert await get_account_value() == LIVE


@pytest.mark.asyncio
async def test_fallback_when_the_broker_is_down_is_still_starting_capital():
    with _base(25_000.0), patch("app.broker.broker_factory.get_broker", side_effect=ConnectionError("down")):
        assert await get_account_value() == settings.starting_capital


# ── config validation ───────────────────────────────────────────────────────

def test_a_negative_base_is_refused():
    with pytest.raises(ValidationError):
        Settings(trading_capital_base=-1.0)


@pytest.mark.parametrize("pct", [0.0, -0.1, 0.51, 1.0])
def test_an_unreasonable_position_cap_is_refused(pct):
    with pytest.raises(ValidationError):
        Settings(equity_max_position_pct=pct)


def test_the_defaults_change_nothing():
    s = Settings()
    assert s.trading_capital_base == 0.0 and s.equity_max_position_pct == 0.08


# ── the position cap, through the real sizing function ──────────────────────

def _plan(portfolio_value, **kw):
    # entry 100, ATR 1 -> a 1-dollar stop: 2% risk of 25k is $500 = 500 shares = $50k notional,
    # so the notional cap, not the risk rule, is what binds.
    return compute_equity_trade_plan({"close": 100.0, "atr": 1.0}, "BUY",
                                     portfolio_value=portfolio_value, **kw)


def test_default_cap_is_eight_percent_of_the_capital():
    p = _plan(25_000.0)
    assert p["position_size"] <= 25_000 * 0.08 + 1e-6 and p["shares"] == 20


def test_the_configured_ten_percent_cap_applies():
    with patch.object(settings, "equity_max_position_pct", 0.10):
        p = _plan(25_000.0)
    assert p["position_size"] <= 2_500.0 and p["shares"] == 25


def test_an_explicit_argument_still_overrides_the_setting():
    with patch.object(settings, "equity_max_position_pct", 0.10):
        assert _plan(25_000.0, max_position_pct=0.04)["shares"] == 10


def test_sizing_on_the_capped_capital_is_a_tenth_of_sizing_on_the_account():
    with patch.object(settings, "equity_max_position_pct", 0.10):
        small, big = _plan(25_000.0), _plan(LIVE)
    assert big["shares"] > 9 * small["shares"]            # why the cap on capital matters
    assert small["position_size"] <= 2_500.0


def test_the_two_percent_risk_rule_is_untouched_when_the_cap_does_not_bind():
    p = compute_equity_trade_plan({"close": 20.0, "atr": 5.0}, "BUY", portfolio_value=25_000.0)
    assert p["risk_dollars"] <= 25_000 * 0.02 + 1e-6


# ── the guardrails see the desk's capital, not the account's ────────────────

def _state(current, peak, daily_pnl=0.0, start=25_000.0):
    return PortfolioState(
        current_value=current, starting_capital=start, daily_pnl=Decimal(str(daily_pnl)),
        weekly_pnl=Decimal("0"), monthly_pnl=Decimal("0"), consecutive_losses=0,
        trades_today=0, peak_value=peak,
    )


def test_a_stale_large_peak_would_halt_a_capped_desk_which_is_why_it_is_clamped():
    """Pins the hazard itself: without the clamp this exact state blocks every entry."""
    status = GuardrailEngine().check_all(_state(current=25_000.0, peak=276_000.0))
    assert not status.trading_allowed and "drawdown" in (status.reason or "").lower()


def test_the_clamp_bounds_a_stale_peak_to_the_base():
    from app.main import _clamp_peak_to_capital_base

    with _base(25_000.0):
        assert _clamp_peak_to_capital_base(276_000.0) == 25_000.0
        assert _clamp_peak_to_capital_base(21_000.0) == 21_000.0   # a lower peak is left alone
        assert _clamp_peak_to_capital_base(0.0) == 0.0             # "untracked" stays untracked
    with _base(0.0):
        assert _clamp_peak_to_capital_base(276_000.0) == 276_000.0


def test_a_clamped_peak_lets_a_capped_desk_trade():
    from app.main import _clamp_peak_to_capital_base

    with _base(25_000.0):
        peak = _clamp_peak_to_capital_base(276_000.0)
    status = GuardrailEngine().check_all(_state(current=25_000.0, peak=peak))
    assert status.trading_allowed
    assert CAPITAL_BASE_DIVERGENT_FLAG not in status.flags    # base and capped value agree


def test_a_real_drawdown_on_the_capped_desk_still_blocks():
    from app.main import _clamp_peak_to_capital_base

    with _base(25_000.0):
        peak = _clamp_peak_to_capital_base(276_000.0)
    status = GuardrailEngine().check_all(_state(current=25_000.0 * 0.80, peak=peak))
    assert not status.trading_allowed                          # -20% from the base peak


def test_loss_limits_are_a_share_of_the_desks_capital():
    status = GuardrailEngine().check_all(_state(25_000.0, 25_000.0, daily_pnl=-600.0))
    assert not status.trading_allowed                          # -2.4% of 25k


def test_the_divergence_flag_still_fires_when_the_two_really_diverge():
    status = GuardrailEngine().check_all(_state(current=LIVE, peak=LIVE, start=25_000.0))
    assert CAPITAL_BASE_DIVERGENT_FLAG in status.flags         # i.e. the cap is NOT set


# ── the entry gate's own state builder ──────────────────────────────────────

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


@pytest.mark.asyncio
@pytest.mark.parametrize("base,expected", [(25_000.0, 25_000.0), (0.0, LIVE)])
async def test_the_entry_gate_state_uses_the_capped_capital(base, expected):
    from app.api.routes.trade_desk import _fetch_portfolio_state

    with _base(base), patch("app.broker.broker_factory.get_broker", return_value=_broker(LIVE)), \
         patch("app.core.database.AsyncSessionLocal", lambda: _FakeDB()):
        state = await _fetch_portfolio_state()
    assert state.current_value == expected


# ── the status route the Risk page reads ────────────────────────────────────

@pytest.mark.asyncio
async def test_the_status_route_reads_the_capped_capital_and_the_clamped_peak():
    import app.main as m

    with _base(25_000.0), patch("app.broker.broker_factory.get_broker", return_value=_broker(LIVE)), \
         patch("app.core.database.AsyncSessionLocal", lambda: _FakeDB()), \
         patch.object(m, "_peak_value_cache", 276_000.0), \
         patch.object(m, "_maybe_log_guardrail_event", AsyncMock()):
        out = await m.guardrail_status()
    assert out["capital_pct_remaining"] == pytest.approx(1.0)
    assert out["drawdown_pct"] == pytest.approx(0.0)
    assert out["trading_allowed"] is True, out["reason"]


@pytest.mark.asyncio
async def test_the_status_route_without_the_cap_shows_the_old_distortion():
    """The ~11% 'drawdown' on the Risk page: a 276k stored peak against a 245,880 account."""
    import app.main as m

    with _base(0.0), patch("app.broker.broker_factory.get_broker", return_value=_broker(LIVE)), \
         patch("app.core.database.AsyncSessionLocal", lambda: _FakeDB()), \
         patch.object(m, "_peak_value_cache", 276_000.0), \
         patch.object(m, "_maybe_log_guardrail_event", AsyncMock()):
        out = await m.guardrail_status()
    assert out["drawdown_pct"] == pytest.approx((276_000 - LIVE) / 276_000)
