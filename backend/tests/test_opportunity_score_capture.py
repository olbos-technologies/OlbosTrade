"""
Opportunity-score capture on signal_outcomes.

Why only these three columns: a skill test on the deterministic scores was run
against production on 2026-08-28 and returned AUC 0.4157 with a date-clustered
95% CI of [0.2826, 0.5740] over 12 dates — indistinguishable from a coin flip.
Re-running that test in three months needs a score that is not already in the
table, and most of the Alpha Edge family is:

    alpha_edge_entry_score = round(confidence * 100)          (exact)
    risk_score             = round((1 - confidence) * 100)    (equity: the
                             reward:risk nudge needs rr < 1.0, and equity plans
                             are fixed at 2:1, so it never fires)

AUC is rank-based and invariant to monotone transforms, so persisting either
would guarantee the same 0.4157 forever. Of weighted_score()'s five components,
confidence / EV / reward_risk are likewise confidence-determined for equity;
only liquidity and regime move independently. Those are what get stored.

Run with: pytest tests/test_opportunity_score_capture.py -v
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.signal_outcome_tracker import record_signal


def _signal(**over):
    s = {
        "id": "sig-1",
        "ticker": "AAPL",
        "action": "BUY",
        "confidence": 0.72,
        "regime": "normal_mean_revert",
        "indicators": {"rsi": 55.0, "atr": 3.2, "volume_ratio": 1.4},
        "trade_plan": {"entry_price": 100.0, "stop_price": 96.0,
                       "target_price": 108.0, "target_move_pct": 0.08},
        "opportunity_score": {
            "score": 64,
            "components": {"confidence": 0.72, "ev": 0.86, "reward_risk": 0.6667,
                           "liquidity": 0.7, "regime": 1.0},
        },
    }
    s.update(over)
    return s


class _NoDuplicate:
    """record_signal()'s dedup lookup finding no row for this (ticker, action, day)."""
    def scalar_one_or_none(self): return None


class _Session:
    """Captures the SignalOutcome handed to session.add().

    IT IMPLEMENTS commit(), and that is not decoration. record_signal calls
    it, and a fake without one raised AttributeError that record_signal
    swallowed into a logger.warning — so the function FAILED and returned
    None while every test here passed, because they read `added`, which add()
    had already set before the failure. Raised in review on #80 and
    reproduced before fixing.

    That is the same shape as the bug this PR exists to fix: a fake more
    permissive than the real object certifies a broken function.
    """
    added = None
    commits = 0

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def begin(self): return self
    def add(self, obj): type(self).added = obj
    async def execute(self, *a, **k): return _NoDuplicate()
    async def commit(self): type(self).commits += 1


async def _record(sig):
    """Run record_signal against the fake and return the staged row.

    ASSERTS THE CALL SUCCEEDED rather than trusting `added`. record_signal
    never raises by design, so without this every caller here would go green
    on a function that failed — which is exactly what happened. The return
    value is the only signal that the whole path ran.
    """
    _Session.added = None
    _Session.commits = 0
    with patch("app.core.database.AsyncSessionLocal", _Session):
        result = await record_signal(sig)
    assert result is not None, (
        "record_signal returned None — it raised and swallowed the error. "
        "Reading `added` alone would have hidden that, since add() runs first."
    )
    assert _Session.commits == 1, "the row was staged but never committed"
    return _Session.added


# ── the capture itself ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_composite_and_independent_components_are_stored():
    row = await _record(_signal())
    assert row is not None
    assert row.opportunity_score == 64
    assert row.oppty_liquidity == Decimal("0.7")
    assert row.oppty_regime == Decimal("1.0")


@pytest.mark.asyncio
async def test_redundant_components_are_not_stored():
    """confidence / ev / reward_risk are confidence-determined for equity and
    must not reappear as separate columns — `confidence` already holds that
    information, and duplicating it would make a future skill test look like it
    had more independent inputs than it does."""
    row = await _record(_signal())
    for attr in ("oppty_confidence", "oppty_ev", "oppty_reward_risk",
                 "alpha_edge_entry_score", "alpha_edge_score", "risk_score"):
        assert not hasattr(row, attr), f"{attr} should not be persisted"


@pytest.mark.asyncio
async def test_confidence_is_still_stored_unchanged():
    """The capture must not disturb the column the skill test already uses."""
    row = await _record(_signal())
    assert row.confidence == Decimal("0.72")


# ── absence must read as absence ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_opportunity_score_stores_null_not_zero():
    """A signal recorded before the score exists, or by a caller that does not
    compute it, must leave NULL. Zero would be a real score meaning 'worst
    possible' and would silently poison any later analysis."""
    sig = _signal()
    del sig["opportunity_score"]
    row = await _record(sig)
    assert row.opportunity_score is None
    assert row.oppty_liquidity is None and row.oppty_regime is None


@pytest.mark.asyncio
async def test_malformed_opportunity_score_is_ignored_not_guessed():
    row = await _record(_signal(opportunity_score="not-a-dict"))
    assert row.opportunity_score is None
    assert row.oppty_liquidity is None


@pytest.mark.asyncio
async def test_partial_components_store_what_exists():
    row = await _record(_signal(opportunity_score={
        "score": 40, "components": {"liquidity": 0.25},
    }))
    assert row.opportunity_score == 40
    assert row.oppty_liquidity == Decimal("0.25")
    assert row.oppty_regime is None


@pytest.mark.asyncio
async def test_non_numeric_score_does_not_raise():
    row = await _record(_signal(opportunity_score={"score": None, "components": {}}))
    assert row.opportunity_score is None


# ── the arithmetic claim this whole design rests on ──────────────────────────

def test_alpha_edge_entry_score_is_exactly_confidence_times_100():
    """If this ever stops holding, the redundancy argument above stops holding
    too and the entry score becomes worth persisting on its own."""
    from app.services.alpha_edge_engine import compute_equity_scores

    for conf in (0.0, 0.33, 0.5, 0.7234, 1.0):
        entry, hold, exit_ = compute_equity_scores("BUY", conf, position_direction=None)
        assert entry == round(conf * 100)
        assert hold is None and exit_ is None


def test_risk_score_is_the_confidence_complement_for_equity_plans():
    """Equity trade plans are 2:1 BY DEFAULT, so risk_score's sub-1:1 nudge
    cannot fire and it reduces to (1 - confidence) * 100."""
    from app.services.trade_frequency_controller import risk_score

    for conf in (0.1, 0.5, 0.9):
        sig = {"confidence": conf, "trade_plan": {"risk_reward": 2.0}}
        assert risk_score(sig) == int(round((1.0 - conf) * 100))
