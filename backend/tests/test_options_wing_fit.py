"""Size-aware wing width for credit spreads.

The production numbers behind these tests (2026-11-10): per-trade risk budget about $428, a
$10 wing risking about $768 per contract, so 1,479 of 2,082 scan rejections were "sized to
0 contracts". These tests pin what the wing fit may and may not do: it narrows only when the
usual wing sizes to zero, only from strikes actually listed in the chain, never beyond the
budget the sizer already enforces, and it leaves the quote untouched when it is off.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.main as m
from app.services import options_wing_fit as wf


def leg(strike, bid, ask, *, last=0.0, delta=None, oi=500):
    return SimpleNamespace(strike=strike, bid=bid, ask=ask, last=last, open_interest=oi,
                           greeks=SimpleNamespace(delta=delta, gamma=0.01))


def put_chain():
    """Short strike 100 (mid 2.00). Credits by long strike: 90 -> 1.70, 95 -> 1.15, 97.5 -> 0.65, 99 -> 0.25.
    Max loss per contract: $10 wing 830, $5 wing 385, $2.5 wing 185, $1 wing 75."""
    return [
        leg(100.0, 1.95, 2.05, delta=-0.20), leg(99.0, 1.70, 1.80), leg(97.5, 1.30, 1.40),
        leg(95.0, 0.80, 0.90), leg(90.0, 0.25, 0.35),
    ]


def call_chain():
    """Mirror image for calls: short 100 (mid 2.00), long legs ABOVE it."""
    return [
        leg(100.0, 1.95, 2.05, delta=0.20), leg(101.0, 1.70, 1.80), leg(102.5, 1.30, 1.40),
        leg(105.0, 0.80, 0.90), leg(110.0, 0.25, 0.35),
    ]


def pick(budget, legs=None, opt="put", default_width=10.0, **kw):
    return wf.choose_wing(legs or put_chain(), 100.0, opt, budget, default_width=default_width, short_mid=2.0, **kw)


# ── choose_wing ───────────────────────────────────────────────────────────────

def test_the_production_case_a_428_budget_picks_the_5_dollar_wing():
    c = pick(428.0)
    assert c.width == 5.0 and float(c.long_leg.strike) == 95.0
    assert c.max_loss == 385.0 and round(c.net_credit_ps, 2) == 1.15


def test_the_widest_fitting_wing_wins_and_narrower_ones_follow_as_the_budget_shrinks():
    assert pick(400.0).width == 5.0
    assert pick(300.0).width == 2.5          # $5 wing risks 385
    assert pick(100.0).width == 1.0          # $2.5 wing risks 185
    assert pick(50.0) is None                # even the $1 wing risks 75


def test_a_wing_whose_max_loss_equals_the_budget_fits_and_one_cent_over_does_not():
    assert pick(385.0).width == 5.0
    assert pick(384.99).width == 2.5


def test_calls_take_the_long_leg_above_the_short_strike():
    c = pick(428.0, legs=call_chain(), opt="call")
    assert c.width == 5.0 and float(c.long_leg.strike) == 105.0


def test_only_wings_narrower_than_the_quoted_one_are_tried():
    assert pick(1000.0, default_width=5.0).width == 2.5     # never widens, never repeats the $5 wing
    assert pick(1000.0, default_width=1.0) is None
    assert pick(1000.0, default_width=12.5).width == 10.0    # a sparse chain quoted 12.5: $10 is narrower


def test_a_width_with_no_listed_strike_is_skipped_not_invented():
    chain = [c for c in put_chain() if float(c.strike) != 95.0]
    assert pick(428.0, legs=chain).width == 2.5


def test_a_leg_with_no_usable_quote_is_skipped():
    chain = put_chain()
    chain[3] = leg(95.0, 0.0, 0.0, last=0.0)                  # no bid, ask or last
    assert pick(428.0, legs=chain).width == 2.5
    chain[3] = leg(95.0, 0.0, 0.0, last=0.85)                # last price only: usable
    assert pick(428.0, legs=chain).width == 5.0


def test_a_crossed_quote_is_not_used_as_a_mid():
    chain = put_chain()
    chain[3] = leg(95.0, 0.90, 0.80)                         # ask below bid, no last
    assert pick(428.0, legs=chain).width == 2.5


def test_a_wing_that_pays_no_real_credit_or_too_little_of_its_width_is_skipped():
    chain = put_chain()
    chain[3] = leg(95.0, 1.95, 2.05)                         # long leg as dear as the short: no credit
    assert pick(428.0, legs=chain).width == 2.5
    chain[3] = leg(95.0, 1.75, 1.85)                         # credit 0.20 on a $5 wing = 4% of width
    assert pick(428.0, legs=chain).width == 2.5


def test_no_budget_or_no_short_quote_means_no_wing():
    assert pick(0.0) is None and pick(-5.0) is None
    assert wf.choose_wing(put_chain(), 100.0, "put", 428.0, default_width=10.0, short_mid=0.0) is None


def test_the_fit_rule_is_exactly_the_position_sizers_rule():
    """fits_budget must agree with RiskManager.calculate_position_size for every case the fit meets."""
    from app.services.risk_manager import RiskManager
    rm = RiskManager()
    for budget in (50.0, 99.0, 100.0, 184.99, 185.0, 300.0, 384.99, 385.0, 428.0, 829.99, 830.0, 5000.0):
        for max_loss in (0.5, 75.0, 185.0, 385.0, 768.0, 830.0):
            sized = rm.calculate_position_size(portfolio_value=budget, max_loss_per_spread=max_loss,
                                               risk_pct=1.0, size_multiplier=1.0)
            assert wf.fits_budget(max_loss, budget) == (sized >= 1), (budget, max_loss)


def test_the_module_reads_nothing_it_could_write_with():
    src = inspect.getsource(wf)
    for forbidden in ("place_order", "submit_order", "session.add", ".commit("):
        assert forbidden not in src


# ── _live_spread_quote ────────────────────────────────────────────────────────

def broker(puts=None, calls=None):
    b = MagicMock()
    b.get_options_chain = AsyncMock(return_value=SimpleNamespace(puts=puts or put_chain(), calls=calls or call_chain()))
    return b


@pytest.mark.asyncio
async def test_with_the_fit_off_the_quote_is_exactly_as_before():
    q = await m._live_spread_quote(broker(), "AAPL", "2026-12-18", 100.0, 90.0, "put")
    assert q["long_strike"] == 90.0 and q["net_credit"] == 170.0 and q["width_fitted"] is False


@pytest.mark.asyncio
async def test_a_budget_too_small_for_the_usual_wing_narrows_it_from_the_same_chain():
    b = broker()
    q = await m._live_spread_quote(b, "AAPL", "2026-12-18", 100.0, 90.0, "put", fit_budget=428.0)
    assert q["short_strike"] == 100.0 and q["long_strike"] == 95.0
    assert q["net_credit"] == 115.0 and q["width_fitted"] is True
    assert q["long_bid"] == 0.80 and q["long_ask"] == 0.90 and q["long_open_interest"] == 500
    b.get_options_chain.assert_awaited_once()                  # no second broker request


@pytest.mark.asyncio
async def test_a_wing_that_already_fits_is_left_alone():
    q = await m._live_spread_quote(broker(), "AAPL", "2026-12-18", 100.0, 90.0, "put", fit_budget=1000.0)
    assert q["long_strike"] == 90.0 and q["width_fitted"] is False and q["net_credit"] == 170.0


@pytest.mark.asyncio
async def test_when_nothing_narrower_fits_either_the_usual_quote_is_returned_and_sizes_to_zero_as_before():
    q = await m._live_spread_quote(broker(), "AAPL", "2026-12-18", 100.0, 90.0, "put", fit_budget=50.0)
    assert q["long_strike"] == 90.0 and q["width_fitted"] is False


@pytest.mark.asyncio
async def test_a_narrowed_call_spread_has_its_long_leg_above_the_short_strike():
    q = await m._live_spread_quote(broker(), "AAPL", "2026-12-18", 100.0, 110.0, "call", fit_budget=428.0)
    assert q["long_strike"] == 105.0 and q["width_fitted"] is True


@pytest.mark.asyncio
async def test_the_quoted_leg_keeps_the_keys_the_liquidity_gates_read():
    q = await m._live_spread_quote(broker(), "AAPL", "2026-12-18", 100.0, 90.0, "put", fit_budget=428.0)
    for key in ("short_bid", "short_ask", "long_bid", "long_ask", "short_open_interest",
                "long_open_interest", "short_gamma", "short_delta", "net_credit", "width_fitted"):
        assert key in q


# ── _options_risk_budget ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_budget_is_the_same_product_the_sizing_step_uses():
    from app.services.risk_manager import RiskManager
    from app.services.volatility_sizing import vol_adjusted_multiplier
    tm = SimpleNamespace(config=SimpleNamespace(risk_per_trade_pct=0.02))
    with patch("app.services.account_state.get_account_value", new=AsyncMock(return_value=21400.0)), \
         patch("app.services.trading_mode.trading_mode_manager", tm), patch.object(m, "_current_regime", None):
        budget = await m._options_risk_budget(iv_rank=45.0, vix_pct=18.0)
    assert budget is not None, "the helper swallowed an error: the fit would be silently off in production"
    size_mult = vol_adjusted_multiplier(1.0, 45.0, 18.0)
    expected = 21400.0 * m.settings.capital_pool_share("options") * 0.02 * size_mult
    assert budget == pytest.approx(expected)
    # ...and a spread that fits it sizes to at least one contract in the real sizer, one that does not sizes to zero.
    pool = 21400.0 * m.settings.capital_pool_share("options")
    rm = RiskManager()
    assert rm.calculate_position_size(portfolio_value=pool, max_loss_per_spread=budget - 1.0,
                                      risk_pct=0.02, size_multiplier=size_mult) >= 1
    assert rm.calculate_position_size(portfolio_value=pool, max_loss_per_spread=budget + 1.0,
                                      risk_pct=0.02, size_multiplier=size_mult) == 0


@pytest.mark.asyncio
async def test_the_helper_works_against_the_real_mode_manager_not_only_a_stand_in():
    """Only the broker is faked. A NameError or bad import inside the helper would be swallowed into
    None and switch the fit off silently, so a real value here is the proof the wiring is live."""
    with patch("app.services.account_state.get_account_value", new=AsyncMock(return_value=21400.0)):
        budget = await m._options_risk_budget(iv_rank=45.0, vix_pct=18.0)
    assert budget is not None and budget > 0


@pytest.mark.asyncio
async def test_an_unavailable_budget_means_no_fit_not_a_failed_scan():
    with patch("app.services.account_state.get_account_value", new=AsyncMock(side_effect=RuntimeError("down"))):
        assert await m._options_risk_budget(45.0, 18.0) is None


# ── wiring in the scan ────────────────────────────────────────────────────────

def test_only_credit_spreads_are_eligible_and_only_in_paper():
    assert m.WING_FIT_STRATEGIES == ("bull_put_spread", "bear_call_spread")     # no iron condor, no debit spreads
    src = inspect.getsource(m._run_options_scan)
    assert "strategy_name in WING_FIT_STRATEGIES and settings.is_paper_trading" in src
    assert "fit_budget=fit_budget" in src
    assert '"width_fitted":      width_fitted' in src                              # recorded on the signal's spread


def test_the_fit_is_decided_before_the_ai_scorer_sees_the_spread():
    src = inspect.getsource(m._run_options_scan)
    assert src.index("fit_budget=fit_budget") < src.index("score_async(features)")
