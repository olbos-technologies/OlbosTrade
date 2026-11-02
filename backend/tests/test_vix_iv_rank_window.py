"""The regime's IV rank must be a real 1-year rank, not a 60-session one.

2026-11-02: the app reported CRISIS, entries paused, with `IV_rank=90.1 >= 85.0`
as the ONLY flag and `VIX=19.4` — against an absolute CRISIS trigger of 40. The
regime job ranked today's VIX within the last 60 sessions. After a calm stretch
an ordinary VIX is the top of that narrow range, so rank ~90, and the classifier
treats `iv_rank >= 85` as CRISIS on its own. `IVSurfaceSnapshot.iv_rank` is
documented as a 1-year rank; the job never provided one.

What must stay true: the real CRISIS triggers are untouched. Only the measure of
"where VIX sits in its range" changes, and it is not allowed to be computed from
too little history (that is the bug), so it returns None instead.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest

from app.services import regime_classifier as rc
from app.services.iv_surface import IVSurfaceSnapshot
from app.services.regime_classifier import (
    NEUTRAL_IV_RANK, VIX_RANK_MIN_BARS, VIX_RANK_WINDOW_BARS, RegimeClassifier,
    RegimeType, vix_iv_rank,
)


def _year_with_calm_last_60(now=19.4):
    """A year ranging ~12.5-35 whose LAST 60 sessions are calm (15-19.4) and end at `now`."""
    early = list(np.linspace(13.0, 35.0, 90)) + list(np.linspace(35.0, 12.5, 102))
    calm = list(np.linspace(15.0, now, 59)) + [now]
    series = early + calm
    assert len(series) == 252
    return series


def _old_rank(closes):
    """What the job computed before: the same formula over only the last 60 sessions."""
    w = closes[-60:]
    return (w[-1] - min(w)) / max(max(w) - min(w), 0.01) * 100


# ── the function ────────────────────────────────────────────────────────────

def test_the_reported_situation_old_rank_is_crisis_territory_new_rank_is_not():
    s = _year_with_calm_last_60(19.4)
    assert _old_rank(s) > 85                       # the bug: ordinary VIX looks extreme
    new = vix_iv_rank(s)
    assert new is not None and new < 40            # a real 1-year rank: middling
    assert new < RegimeClassifier.CRISIS_IV_RANK_MIN


def test_a_vix_at_its_one_year_high_still_ranks_at_the_top():
    s = list(np.linspace(13, 20, 251)) + [45.0]
    assert vix_iv_rank(s) == pytest.approx(100.0)
    s2 = list(np.linspace(30, 20, 251)) + [11.0]
    assert vix_iv_rank(s2) == pytest.approx(0.0)


def test_only_the_last_252_closes_count():
    old_spike = [80.0] + [15.0] * 10 + list(np.linspace(14, 18, 251))
    assert len(old_spike) == 262
    # the 80 is older than the window, so it must not compress the rank
    assert vix_iv_rank(old_spike) > 90


def test_too_little_history_is_none_not_a_short_window_rank():
    assert vix_iv_rank([]) is None
    assert vix_iv_rank([15, 16, 17, 18]) is None
    assert vix_iv_rank(list(np.linspace(15, 19.4, 60))) is None          # the old window
    assert vix_iv_rank(list(np.linspace(15, 19.4, VIX_RANK_MIN_BARS - 1))) is None
    assert vix_iv_rank(list(np.linspace(15, 19.4, VIX_RANK_MIN_BARS))) is not None


def test_bad_values_are_ignored_not_trusted():
    s = _year_with_calm_last_60()
    noisy = s[:50] + [float("nan"), None, "x", -3.0, 0.0, float("inf")] + s[50:]
    assert vix_iv_rank(noisy) == pytest.approx(vix_iv_rank(s))
    assert vix_iv_rank([float("nan")] * 300) is None


def test_a_flat_series_does_not_divide_by_zero():
    assert vix_iv_rank([15.0] * 252) == 0.0


def test_constants_match_the_documented_definition():
    assert VIX_RANK_WINDOW_BARS == 252 and VIX_RANK_MIN_BARS == 126
    assert NEUTRAL_IV_RANK == 30.0


# ── through the real classifier: CRISIS goes away, real CRISIS does not ──────

def _surface(vix, iv_rank):
    return IVSurfaceSnapshot(
        underlying="SPY", spot_price=700.0, fetched_at=datetime.now(timezone.utc),
        iv_rank=iv_rank, iv_percentile=iv_rank, atm_iv=vix / 100.0,
        realized_vol_20d=0.15, vrp=0.02, front_skew=None, back_skew=None, skew_trend=0.0,
        term_structure=None, data_quality="partial", warnings=[],
    )


def _classify(vix, iv_rank):
    rng = np.random.default_rng(1)
    returns = pd.Series(rng.normal(0, 0.007, 59))
    return RegimeClassifier().classify(
        _surface(vix, iv_rank), rsi=34.3, adx=26.3, spy_returns=returns,
        flow_sentiment_score=0.5, flow_large_sweep_bullish_count=0).regime


def test_vix_19_with_a_short_window_rank_was_crisis_and_a_real_rank_is_not():
    s = _year_with_calm_last_60(19.4)
    assert _classify(19.4, _old_rank(s)) == RegimeType.CRISIS
    assert _classify(19.4, vix_iv_rank(s)) != RegimeType.CRISIS


@pytest.mark.parametrize("vix,rank", [(40.5, 20.0), (19.4, 90.0), (55.0, 95.0)])
def test_the_real_crisis_triggers_are_untouched(vix, rank):
    """Absolute VIX >= 40 and a genuinely extreme 1-year rank still mean CRISIS."""
    assert _classify(vix, rank) == RegimeType.CRISIS


# ── through the real job ────────────────────────────────────────────────────

def _bars(closes):
    from app.broker.broker_interface import Bar
    return [Bar(timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc), open=Decimal(str(c)),
                high=Decimal(str(c)), low=Decimal(str(c)), close=Decimal(str(c)), volume=1)
            for c in closes]


def _spy_bars(n=60):
    from app.broker.broker_interface import Bar
    rng = np.random.default_rng(2)
    px = 700.0
    out = []
    for i in range(n):
        px *= 1 + rng.normal(0, 0.007)
        out.append(Bar(timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
                       open=Decimal(str(round(px, 2))), high=Decimal(str(round(px * 1.004, 2))),
                       low=Decimal(str(round(px * 0.996, 2))), close=Decimal(str(round(px, 2))), volume=1))
    return out


def _run_job(vix_closes):
    import app.main as m
    asked = {}

    async def fake_yf(ticker, limit=60):
        asked[ticker] = limit
        return _bars(vix_closes) if ticker == "VIX" else _spy_bars(limit)

    m._current_regime = None
    with patch.object(m, "_yf_bars", side_effect=fake_yf), \
         patch("app.services.regime_classifier.compute_flow_features",
               new=AsyncMock(return_value={"flow_sentiment_score": 0.5,
                                           "flow_large_sweep_bullish_count": 0})):
        asyncio.run(m._reclassify_regime())
    return m._current_regime, asked


def test_the_job_asks_for_a_year_of_vix_and_no_longer_calls_crisis_at_vix_19(caplog):
    regime, asked = _run_job(_year_with_calm_last_60(19.4))
    assert asked["VIX"] == VIX_RANK_WINDOW_BARS and asked["SPY"] == 60   # SPY window unchanged
    assert regime is not None and regime.regime != RegimeType.CRISIS
    assert regime.equity_allowed


def test_with_too_little_vix_history_the_job_uses_the_neutral_rank_and_says_so(caplog):
    caplog.set_level(logging.WARNING)
    regime, _ = _run_job(list(np.linspace(15, 19.4, 60)))          # the old 60-bar world
    assert regime is not None and regime.regime != RegimeType.CRISIS
    assert any("need 126 for a 1-year IV rank" in r.getMessage() for r in caplog.records)


def test_a_genuine_vix_spike_still_triggers_crisis_through_the_job():
    spike = list(np.linspace(14, 18, 250)) + [30.0, 44.0]
    regime, _ = _run_job(spike)
    assert regime is not None and regime.regime == RegimeType.CRISIS
