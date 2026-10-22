"""R2-C: strategy intent must never be treated as broker fill proof."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.spread_leg_mapping import infer_candidate_legs


def trade(**kw):
    values = dict(
        spread_type="bull_put_spread", quantity=2, underlying="SPY",
        short_strike=Decimal("500"), long_strike=Decimal("495"),
        expiration=date(2026, 11, 20),
    )
    values.update(kw)
    return SimpleNamespace(**values)


def test_standard_spread_candidates_are_signed_but_never_verified():
    mapping = infer_candidate_legs(trade())
    assert mapping.verified is False
    assert len(mapping.legs) == 2
    assert mapping.legs[0].quantity == -2
    assert mapping.legs[1].quantity == 2
    assert mapping.reason == "strategy_intent_is_not_fill_evidence"


def test_unknown_or_multi_leg_strategy_fails_without_guessing():
    for kind in ("iron_condor", "equity", "unknown"):
        mapping = infer_candidate_legs(trade(spread_type=kind))
        assert mapping.verified is False and not mapping.legs


def test_missing_or_zero_quantity_returns_no_candidate_proof():
    for qty in (None, 0, -1):
        mapping = infer_candidate_legs(trade(quantity=qty))
        assert not mapping.legs and not mapping.verified


def test_zero_and_equal_strikes_fail_closed():
    for strike in (Decimal("0"), Decimal("495")):
        mapping = infer_candidate_legs(trade(short_strike=strike))
        assert not mapping.verified and not mapping.legs


def test_bear_call_candidate_uses_call_contracts():
    mapping = infer_candidate_legs(trade(spread_type="bear_call_spread"))
    assert [leg.option_type for leg in mapping.legs] == ["call", "call"]
    assert not mapping.verified
