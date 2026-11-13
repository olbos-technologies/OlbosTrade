"""0DTE debit-spread proposals: what is proposed, and every way it must refuse.

The module fails CLOSED. A test that "refuses" must prove the refusal is for the stated
reason, so each negative case asserts on the reason text, and a good chain is proven to
produce a proposal first (otherwise a broken fixture would make every refusal pass).
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import zero_dte as z

# Wednesday 2026-11-11, 10:30 ET (EST, UTC-5) is inside the entry window.
NOW = datetime(2026, 11, 11, 15, 30, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 11)


def opt(strike, bid, ask):
    return SimpleNamespace(strike=strike, bid=bid, ask=ask, last=(bid + ask) / 2)


def make_chain(*, calls=None, puts=None, spot=600.2, expiration=TODAY, age_s=3, fetched_at=None):
    return SimpleNamespace(
        underlying="SPY", expiration=expiration, underlying_price=spot,
        calls=calls if calls is not None else [
            opt(598, 3.80, 3.84), opt(599, 2.80, 2.84), opt(600, 1.90, 1.94),
            opt(601, 1.30, 1.34), opt(602, 0.84, 0.88), opt(603, 0.50, 0.54), opt(605, 0.10, 0.12)],
        puts=puts if puts is not None else [
            opt(595, 0.05, 0.07), opt(598, 0.60, 0.64), opt(599, 0.95, 0.99),
            opt(600, 1.60, 1.64), opt(601, 2.20, 2.24), opt(602, 2.90, 2.94)],
        fetched_at=fetched_at or (NOW - timedelta(seconds=age_s)),
    )


def propose(chain=None, **kw):
    kw.setdefault("symbol", "SPY")
    kw.setdefault("direction", "bullish")
    kw.setdefault("risk_budget", 500.0)
    kw.setdefault("now", NOW)
    return z.propose_debit_spread(chain or make_chain(), **kw)


def refused(result, text):
    assert not result.ok, f"expected a refusal containing {text!r}, got {result.proposal}"
    assert any(text in r for r in result.reasons), result.reasons


# ── a good chain proposes ────────────────────────────────────────────────────

def test_bullish_proposes_a_bull_call_spread_sized_on_the_worst_fill():
    r = propose()
    assert r.ok, r.reasons
    p = r.proposal
    assert (p.option_type, p.long_strike, p.short_strike, p.width) == ("call", 600.0, 602.0, 2.0)
    assert p.limit_debit == 1.06                      # mid 1.92 - mid 0.86
    assert p.natural_debit == pytest.approx(1.10)     # long ask 1.94 - short bid 0.84
    assert p.contracts == 4                           # 500 // 110 = 4: sized on the NATURAL debit
    assert p.max_loss == pytest.approx(440.0)         # worst fill x 4, never above the budget
    assert p.max_loss <= 500.0
    assert p.max_profit == pytest.approx((2.0 - 1.06) * 100 * 4)
    assert p.breakeven == pytest.approx(601.06)
    assert p.reward_to_risk == pytest.approx(0.94 / 1.06, abs=1e-3)
    assert p.expiration == TODAY
    assert p.expires_at == NOW + timedelta(seconds=z.PROPOSAL_TTL_S)


def test_sizing_uses_the_worst_fill_so_the_budget_holds_if_filled_at_the_natural_price():
    """Budget 430: at the 1.06 limit that is 4 contracts ($424), but a fill at the 1.10 natural price
    would then risk $440. Sized on the natural price it is 3 contracts ($330) and can never exceed 430."""
    p = propose(risk_budget=430.0).proposal
    assert p.contracts == 3
    assert p.max_loss == pytest.approx(330.0) and p.max_loss <= 430.0


def test_bearish_proposes_a_bear_put_spread():
    p = propose(direction="bearish").proposal
    assert (p.option_type, p.long_strike, p.short_strike) == ("put", 600.0, 598.0)
    assert p.limit_debit == 1.0                       # mid 1.62 - mid 0.62
    assert p.breakeven == pytest.approx(599.0)


def test_symbol_is_case_insensitive():
    assert propose(symbol="qqq").ok


def test_falls_back_to_a_narrower_width_when_the_preferred_strike_is_missing():
    calls = [opt(600, 1.90, 1.94), opt(601, 1.30, 1.34)]              # no 602
    p = propose(make_chain(calls=calls)).proposal
    assert p.width == 1.0 and p.short_strike == 601.0


def test_contract_count_is_capped():
    assert propose(risk_budget=1_000_000).proposal.contracts == z.MAX_CONTRACTS


def test_naive_quote_time_is_read_as_utc():
    naive = (NOW - timedelta(seconds=3)).replace(tzinfo=None)
    assert propose(make_chain(fetched_at=naive)).ok


def test_warns_when_the_natural_price_is_far_from_the_limit():
    calls = [opt(600, 1.84, 1.98), opt(602, 0.80, 0.88)]               # legs inside the width limits
    r = propose(make_chain(calls=calls))
    assert r.ok, r.reasons
    assert any("may not fill" in w for w in r.proposal.warnings)


# ── refusals: the request ────────────────────────────────────────────────────

@pytest.mark.parametrize("symbol", ["AAPL", "IWM", "", None])
def test_only_spy_and_qqq(symbol):
    refused(propose(symbol=symbol), "not enabled for 0DTE")


def test_direction_must_be_explicit():
    refused(propose(direction="neutral"), "direction must be")


@pytest.mark.parametrize("budget", [0, -5, None])
def test_needs_a_risk_budget(budget):
    refused(propose(risk_budget=budget), "no risk budget")


def test_budget_too_small_for_one_contract():
    refused(propose(risk_budget=50.0), "over the $50 budget")


# ── refusals: the clock ──────────────────────────────────────────────────────

@pytest.mark.parametrize("when,why", [
    (datetime(2026, 11, 11, 14, 59, tzinfo=timezone.utc), "before 10:00 ET"),          # 09:59 ET
    (datetime(2026, 11, 11, 19, 30, tzinfo=timezone.utc), "after 14:30 ET"),           # 14:30 ET
    (datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc), "weekend"),                   # Saturday
    (datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc), "market holiday"),            # Thanksgiving
    (datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc), "early-close day"),           # 13:00 close
])
def test_refuses_outside_the_entry_window(when, why):
    refused(propose(make_chain(fetched_at=when - timedelta(seconds=2)), now=when), why)


def test_entry_window_is_open_inside_hours():
    assert z.entry_window_problem(NOW) is None
    assert z.entry_window_problem(datetime(2026, 11, 11, 19, 29, tzinfo=timezone.utc)) is None   # 14:29 ET


def test_the_expiry_is_the_eastern_date_not_the_utc_date():
    late_evening_et = datetime(2026, 11, 12, 2, 0, tzinfo=timezone.utc)                # 21:00 ET on Nov 11
    assert z.zero_dte_expiry(late_evening_et) == date(2026, 11, 11)


# ── refusals: the quotes ─────────────────────────────────────────────────────

def test_refuses_a_chain_for_another_expiry():
    refused(propose(make_chain(expiration=TODAY + timedelta(days=1))), "is not today")


def test_refuses_stale_quotes():
    refused(propose(make_chain(age_s=45)), "old")


def test_refuses_a_quote_time_in_the_future():
    refused(propose(make_chain(age_s=-60)), "in the future")


def test_refuses_a_chain_with_no_quote_time():
    c = make_chain()
    c.fetched_at = None
    refused(propose(c), "no quote time")


def test_refuses_without_an_underlying_price():
    refused(propose(make_chain(spot=0)), "no underlying price")


def test_refuses_when_the_window_misses_the_money():
    calls = [opt(620, 0.10, 0.12), opt(622, 0.05, 0.07)]
    refused(propose(make_chain(calls=calls)), "no strike within")


def test_refuses_when_the_chain_has_no_options_on_that_side():
    refused(propose(make_chain(calls=[])), "no calls")


def test_refuses_a_one_sided_long_leg():
    calls = [opt(600, 0.0, 1.94), opt(602, 0.84, 0.88)]
    refused(propose(make_chain(calls=calls)), "no two-sided quote")


def test_refuses_a_crossed_long_leg():
    calls = [opt(600, 1.99, 1.94), opt(602, 0.84, 0.88)]
    refused(propose(make_chain(calls=calls)), "crossed")


def test_refuses_a_wide_long_leg():
    calls = [opt(600, 1.50, 2.30), opt(602, 0.84, 0.88)]
    refused(propose(make_chain(calls=calls)), "too wide")


def test_refuses_a_long_leg_priced_below_intrinsic_value():
    calls = [opt(600, 0.04, 0.06), opt(602, 0.01, 0.02)]               # spot 600.2: intrinsic is 0.20
    refused(propose(make_chain(calls=calls)), "below intrinsic")


def test_refuses_a_one_sided_short_leg():
    calls = [opt(600, 1.90, 1.94), opt(602, 0.0, 0.88)]
    r = propose(make_chain(calls=calls))
    refused(r, "no two-sided quote")
    assert any("$2 wide" in x for x in r.reasons)                      # says WHICH width failed


def test_refuses_a_spread_that_is_implausibly_cheap():
    calls = [opt(600, 1.90, 1.94), opt(602, 1.80, 1.84)]               # debit 0.10 on a $2 width
    refused(propose(make_chain(calls=calls)), "implausibly cheap")


def test_refuses_a_spread_with_no_reward_left():
    calls = [opt(600, 1.90, 1.94), opt(602, 0.10, 0.14)]               # debit 1.80 on a $2 width
    refused(propose(make_chain(calls=calls)), "too little reward")


def test_refuses_a_short_leg_priced_above_the_long_leg():
    calls = [opt(600, 1.90, 1.94), opt(602, 2.00, 2.04)]
    refused(propose(make_chain(calls=calls)), "at or above the long leg")


def test_every_width_failing_lists_every_reason():
    calls = [opt(600, 1.90, 1.94)]                                     # no short strike for any width
    r = propose(make_chain(calls=calls))
    assert not r.ok and len(r.reasons) == len(z.WIDTHS)


def test_a_result_with_a_proposal_has_no_reasons():
    r = propose()
    assert r.ok and r.reasons == ()


def test_the_module_imports_nothing_that_can_reach_a_broker():
    """It must stay a pure function: any import of a broker, coordinator or order path is a regression."""
    import inspect
    imports = [ln for ln in inspect.getsource(z).splitlines() if ln.startswith(("import ", "from "))]
    assert imports, "read no import lines: the guard would pass vacuously"
    for ln in imports:
        assert not any(w in ln for w in ("broker", "coordinator", "order", "execution", "main")), ln
