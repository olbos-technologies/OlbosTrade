"""Tests for /api/analytics/signal-score-impact's regime dimension
(Phase 2 track 2B) — bucketing itself is exercised via real ModeTrade rows,
mocking only the DB load."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.api.routes import analytics
from app.services.mode_analytics import ModeTrade

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _trade(pnl, score, regime=None, ticker="AAPL"):
    return ModeTrade(
        trade_id=ticker, mode="balanced", strategy="bull_put_spread",
        pnl=pnl, pnl_pct=0.01, entry_date=date(2026, 1, 1), exit_date=date(2026, 1, 2),
        hold_days=1, signal_score=score, exit_reason="profit_target", regime=regime,
    )


def _trades(n=10, regime=None):
    return [_trade(50.0 if i % 2 == 0 else -20.0, 0.85, regime=regime) for i in range(n)]


@pytest.mark.asyncio
async def test_signal_score_impact_includes_by_regime_when_unfiltered():
    # >=10 trades per regime — the route's own minimum-sample-size floor
    # applies before any bucketing, filtered or not.
    trades = _trades(10, regime="low_vol_trending") + _trades(10, regime="high_vol")
    with patch("app.api.routes.analytics._load_trades_from_db", return_value=trades):
        result = await analytics.get_signal_score_impact(_CONN, regime=None)

    assert "by_regime" in result
    assert set(result["by_regime"].keys()) == {"low_vol_trending", "high_vol"}


@pytest.mark.asyncio
async def test_signal_score_impact_regime_filter_scopes_trades():
    trades = _trades(10, regime="low_vol_trending") + _trades(10, regime="high_vol")
    with patch("app.api.routes.analytics._load_trades_from_db", return_value=trades):
        result = await analytics.get_signal_score_impact(_CONN, regime="low_vol_trending")

    # Filtered result must not recurse into another by_regime breakdown.
    assert "by_regime" not in result
    total_bucketed = sum(b["trade_count"] for b in result["by_score_bucket"].values())
    assert total_bucketed == 10


@pytest.mark.asyncio
async def test_signal_score_impact_regime_filter_below_minimum_returns_message():
    trades = _trades(3, regime="crisis")
    with patch("app.api.routes.analytics._load_trades_from_db", return_value=trades):
        result = await analytics.get_signal_score_impact(_CONN, regime="crisis")

    assert "message" in result
    assert result["correlation"] is None
