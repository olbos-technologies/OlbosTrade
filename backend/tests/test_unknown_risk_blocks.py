"""An entry whose risk cannot be determined must be refused, not counted as zero risk.

`estimate_proposed_risk_dollars` turned every missing input into 0, and the gate's
concentration, sector and heat checks all ADD the proposed risk to what is open, so an
unknown looked like a trade with no risk and sailed through. Missing options `max_loss`
and a missing equity entry price both reached the broker that way.

Pinned: each unknown input blocks with `risk_unknown`, the known and derivable cases are
unchanged (including a `net_credit`-only options signal, which the route's own validation
accepts), the derived figure feeds the concentration checks, and through the real
`_execute_signal` the broker is never reached.
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

import pytest

from app.services.execution_portfolio_gate import (
    estimate_proposed_risk_dollars,
    evaluate_portfolio_gates,
    options_max_loss_per_contract,
    proposed_risk_unknown_reason,
)
from test_execution_portfolio_gate import _equity, _options, _portfolio


def _opt(spread, qty=1):
    return {"ticker": "SPY", "asset_type": "options", "strategy": "bull_put_spread",
            "quantity": qty, "spread": spread}


def _blocked(signal, **pf):
    r = evaluate_portfolio_gates(signal, _portfolio(**pf))
    return r


# ── options ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spread", [
    {}, {"max_loss": None}, {"max_loss": 0}, {"max_loss": -10.0}, {"max_loss": "abc"},
    {"max_loss": float("nan")}, {"max_loss": float("inf")},
    {"short_strike": 450, "long_strike": 445},                     # no credit, no max_loss
    {"net_credit": 100.0},                                          # no strikes
    {"short_strike": 450, "long_strike": 445, "net_credit": 500.0},   # credit == width: no loss, contradictory
    {"short_strike": 450, "long_strike": 445, "net_credit": 900.0},   # credit > width
    {"short_strike": "x", "long_strike": 445, "net_credit": 100.0},
])
def test_an_options_signal_with_unknown_risk_is_refused(spread):
    r = _blocked(_opt(spread))
    assert not r.allowed and r.flags == ["risk_unknown"]
    assert "could not be determined" in r.reason and r.proposed_risk_dollars == 0.0


def test_a_missing_spread_is_refused():
    r = _blocked({"ticker": "SPY", "asset_type": "options", "quantity": 1})
    assert not r.allowed and r.flags == ["risk_unknown"]


def test_a_known_max_loss_is_unchanged():
    r = _blocked(_options(qty=2, max_loss=350.0))
    assert r.allowed and r.proposed_risk_dollars == 700.0


def test_a_net_credit_only_signal_is_priced_from_the_strikes_not_treated_as_zero():
    """The route accepts net_credit without max_loss; 450/445 at a $100 credit risks $400."""
    spread = {"short_strike": 450, "long_strike": 445, "net_credit": 100.0}
    assert options_max_loss_per_contract(spread) == 400.0
    r = _blocked(_opt(spread, qty=2))
    assert r.allowed and r.proposed_risk_dollars == 800.0
    assert estimate_proposed_risk_dollars(_opt(spread, qty=2)) == 800.0


def test_the_derived_risk_feeds_the_concentration_check():
    """With the old zero it would have been allowed; $400 on a $1,000 book is 40% of it."""
    spread = {"short_strike": 450, "long_strike": 445, "net_credit": 100.0}
    r = _blocked(_opt(spread), value=1_000.0)
    assert not r.allowed and "concentration" in r.reason.lower() and r.flags != ["risk_unknown"]


def test_max_loss_wins_over_a_derivation_when_both_are_present():
    spread = {"max_loss": 123.0, "short_strike": 450, "long_strike": 445, "net_credit": 100.0}
    assert options_max_loss_per_contract(spread) == 123.0


# ── equity ──────────────────────────────────────────────────────────────────

def _eq(plan):
    return {"ticker": "AAPL", "asset_type": "equity", "trade_plan": plan}


@pytest.mark.parametrize("plan", [
    {"shares": 10, "stop_price": 145.0},                              # no entry
    {"shares": 10, "entry_price": 0, "stop_price": 145.0},
    {"shares": 10, "entry_price": -5.0, "stop_price": 145.0},
    {"shares": 10, "entry_price": float("nan"), "stop_price": 145.0},
    {"shares": 10, "entry_price": "abc"},
    {"entry_price": 150.0, "stop_price": 145.0},                      # no share count
    {"shares": 0, "entry_price": 150.0, "stop_price": 145.0},
    {"shares": -3, "entry_price": 150.0, "stop_price": 145.0},
    {"shares": 10, "entry_price": 150.0, "stop_price": float("inf")},
    {"shares": 10, "entry_price": 150.0, "stop_price": "x"},
])
def test_an_equity_signal_with_unknown_risk_is_refused(plan):
    r = _blocked(_eq(plan))
    assert not r.allowed and r.flags == ["risk_unknown"] and r.proposed_risk_dollars == 0.0


def test_an_equity_signal_with_no_trade_plan_is_refused():
    assert _blocked({"ticker": "AAPL", "asset_type": "equity"}).flags == ["risk_unknown"]


def test_a_known_equity_signal_is_unchanged():
    r = _blocked(_equity())
    assert r.allowed and r.proposed_risk_dollars == 50.0


def test_no_stop_but_a_known_entry_and_size_keeps_the_worst_case_proxy():
    r = _blocked(_eq({"shares": 10, "entry_price": 150.0}))
    assert r.allowed and r.proposed_risk_dollars == 1500.0


def test_unknown_risk_is_reported_even_when_another_limit_would_also_block():
    r = _blocked(_eq({"shares": 10}), open_count=99)
    assert r.flags == ["risk_unknown"]            # checked first, so the cause is never masked


def test_the_reason_helper_says_why():
    assert "entry price" in proposed_risk_unknown_reason(_eq({"shares": 10}))
    assert "share count" in proposed_risk_unknown_reason(_eq({"entry_price": 1.0}))
    assert proposed_risk_unknown_reason(_equity()) is None
    assert math.isfinite(options_max_loss_per_contract({"max_loss": 5}))


# ── through the real order pipeline ─────────────────────────────────────────

from test_unrecorded_entry_marker import _run, _broker, _options_signal  # noqa: E402


@pytest.fixture(autouse=True)
def _market_open():
    """Pin the market open: `_execute_signal` blocks with `market_closed` before the portfolio
    gate, so without this the pipeline tests below pass during the US session and fail after
    16:00 ET, testing the clock instead of the gate."""
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


@pytest.mark.asyncio
async def test_the_pipeline_refuses_an_options_entry_without_risk_and_never_reaches_the_broker(stub_position_claim):
    sig = _options_signal()
    sig["spread"].pop("net_credit")            # no net_credit, no max_loss: risk unknowable
    broker = _broker()
    res = await _run(sig, broker, record_fill=AsyncMock(), approved_by="manual")

    assert res["result"] == "blocked"
    assert "could not be determined" in res["reason"]
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_pipeline_still_submits_an_options_entry_whose_risk_is_known(stub_position_claim):
    broker = _broker()
    res = await _run(_options_signal(), broker, record_fill=AsyncMock(return_value="t1"),
                     approved_by="manual")
    assert res["result"] == "submitted"
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_pipeline_refuses_an_equity_entry_without_a_price_and_never_reaches_the_broker(stub_position_claim):
    from test_unrecorded_entry_marker import _equity_signal

    sig = _equity_signal()
    sig["trade_plan"].pop("entry_price")
    broker = _broker()
    res = await _run(sig, broker, record_fill=AsyncMock())

    assert res["result"] == "blocked" and "could not be determined" in res["reason"]
    broker.place_equity_order.assert_not_awaited()
