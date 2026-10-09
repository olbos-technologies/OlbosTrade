"""
Tests for POST /api/backtest/run-equity — see test_backtester_run_equity.py
for the underlying Backtester.run_equity() walk-forward logic tests.

Route-level: this module transitively imports app.models.backtest_result,
which trips the local py3.9 SQLAlchemy-annotation wall — runs on CI
(py3.11) as the source of truth, matching the rest of this test suite's
established constraint.
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest


def _capturing_create_task(bucket: list):
    """Replaces asyncio.create_task so the test can explicitly await the
    background task instead of guessing how many event-loop turns it needs."""
    def _create(coro):
        task = asyncio.ensure_future(coro)
        bucket.append(task)
        return task
    return _create


def _fake_result(ticker: str):
    from app.services.backtester import BacktestResult, BacktestTrade
    from app.utils.metrics import PerformanceMetrics

    trade = BacktestTrade(
        strategy="equity", underlying=ticker,
        entry_date=date(2024, 1, 3), exit_date=date(2024, 1, 10),
        direction="BUY", entry_price=100.0, exit_price=104.0,
        stop_price=96.0, target_price=108.0,
        pnl=400.0, pnl_pct=0.016, exit_reason="target",
        signal_score=0.75, commission_paid=1.0, contracts=100, hold_days=7,
    )
    metrics = PerformanceMetrics(
        total_return_pct=0.016, annualized_return_pct=0.4, sharpe_ratio=1.1,
        sortino_ratio=1.3, calmar_ratio=1.0, max_drawdown_pct=0.02,
        max_drawdown_duration_days=3, win_rate=1.0, profit_factor=99.0,
        total_trades=1, winning_trades=1, losing_trades=0,
        avg_win=400.0, avg_loss=0.0, avg_hold_days=7.0,
        commission_drag_pct=0.0001, expectancy=400.0,
    )
    bar_log = [
        {"date": "2024-01-03", "close": 100.0, "indicators": None, "action": "HOLD",
         "confidence": None, "trade_fired": False, "position_open": False, "portfolio_value": 25000.0},
        {"date": "2024-01-04", "close": 101.0,
         "indicators": {"rsi": 55.0, "macd": 0.1, "bb_pct_b": 0.6, "atr": 1.2, "volume_ratio": 1.1},
         "action": "BUY", "confidence": 0.8, "trade_fired": True, "position_open": True,
         "portfolio_value": 25000.0},
    ]
    return BacktestResult(
        strategy=f"equity:{ticker}", start_date=date(2024, 1, 1), end_date=date(2024, 6, 30),
        starting_capital=25000.0, ending_capital=25400.0,
        trades=[trade], metrics=metrics, equity_curve=[25000.0, 25400.0],
        bar_log=bar_log,
    )



class _AnonRequest:
    """Enough of an HTTPConnection for the tier clamp on these handlers.

    run_equity_backtest now takes the connection so it can clamp start_date to
    the caller's plan — see app/api/tier_deps.py. These tests call the handler
    directly rather than over HTTP, so they supply the connection themselves.
    Carrying no user means "auth is off", which is the state these tests
    already assumed and the state that applies no limits.
    """

    def __init__(self):
        self.state = type("S", (), {"user": None})()


@pytest.mark.asyncio
async def test_run_equity_route_rejects_blank_ticker():
    from app.api.routes.backtest import run_equity_backtest, EquityBacktestRunRequest
    from fastapi import HTTPException

    req = EquityBacktestRunRequest(ticker="   ", start_date="2024-01-01", end_date="2024-06-30")
    with pytest.raises(HTTPException) as exc_info:
        await run_equity_backtest(req, _AnonRequest())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_run_equity_route_queues_then_completes():
    from app.api.routes.backtest import run_equity_backtest, EquityBacktestRunRequest, _runs

    req = EquityBacktestRunRequest(ticker="aapl", start_date="2024-01-01", end_date="2024-06-30")
    tasks: list = []

    with patch("app.api.routes.backtest._persist_run", new=AsyncMock()), \
         patch("app.api.routes.backtest.asyncio.create_task", new=_capturing_create_task(tasks)), \
         patch("app.broker.broker_factory.get_broker", return_value=object()), \
         patch("app.services.data_fetcher.DataFetcher", return_value=object()), \
         patch("app.services.backtester.Backtester.run_equity",
               new=AsyncMock(return_value=_fake_result("AAPL"))):
        resp = await run_equity_backtest(req, _AnonRequest())

        assert resp["status"] == "queued"
        assert resp["strategy"] == "equity:AAPL"
        assert resp["ticker"] == "AAPL"
        run_id = resp["run_id"]

        assert len(tasks) == 1
        await tasks[0]

        completed = _runs[run_id]
        assert completed["status"] == "completed"
        assert completed["strategy"] == "equity:AAPL"
        assert completed["total_trades"] == 1
        assert len(completed["trades"]) == 1
        t = completed["trades"][0]
        assert t["direction"] == "BUY"
        assert t["entry_price"] == 100.0
        assert t["shares"] == 100
        assert t["exit_reason"] == "target"

        assert len(completed["bar_log"]) == 2
        assert completed["bar_log"][0]["action"] == "HOLD"
        assert completed["bar_log"][1]["trade_fired"] is True
        assert completed["bar_log"][1]["indicators"]["rsi"] == 55.0


@pytest.mark.asyncio
async def test_persist_run_excludes_bar_log_and_curve_data():
    """bar_log follows the same existing, deliberate limitation as
    trades/equity_curve: full detail lives in-memory only, not persisted to
    the DB (lost on backend restart)."""
    from app.api.routes.backtest import _persist_run

    fake_session = AsyncMock()
    fake_session.get = AsyncMock(return_value=None)
    fake_session.commit = AsyncMock()
    fake_session.add = lambda row: added.append(row)
    added: list = []

    class _FakeSessionCtx:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *exc):
            return False

    data = {
        "status": "completed", "strategy": "equity:AAPL",
        "start_date": "2024-01-01", "end_date": "2024-06-30", "starting_capital": 25000.0,
        "trades": [{"id": "t1"}],
        "equity_curve": [25000.0, 25400.0],
        "bar_log": [{"date": "2024-01-03", "close": 100.0}],
    }

    with patch("app.api.routes.backtest.AsyncSessionLocal", return_value=_FakeSessionCtx()):
        await _persist_run("11111111-1111-1111-1111-111111111111", data)

    assert len(added) == 1
    persisted_results = added[0].results
    assert "bar_log" not in persisted_results
    assert "trades" not in persisted_results
    assert "equity_curve" not in persisted_results
    assert persisted_results["status"] == "completed"


@pytest.mark.asyncio
async def test_run_equity_route_marks_failed_on_exception():
    from app.api.routes.backtest import run_equity_backtest, EquityBacktestRunRequest, _runs

    req = EquityBacktestRunRequest(ticker="ZZZZ", start_date="2024-01-01", end_date="2024-06-30")
    tasks: list = []

    with patch("app.api.routes.backtest._persist_run", new=AsyncMock()), \
         patch("app.api.routes.backtest.asyncio.create_task", new=_capturing_create_task(tasks)), \
         patch("app.broker.broker_factory.get_broker", return_value=object()), \
         patch("app.services.data_fetcher.DataFetcher", return_value=object()), \
         patch("app.services.backtester.Backtester.run_equity",
               new=AsyncMock(side_effect=ValueError("No OHLCV data returned for ZZZZ"))):
        resp = await run_equity_backtest(req, _AnonRequest())
        run_id = resp["run_id"]
        await tasks[0]

        assert _runs[run_id]["status"] == "failed"
        assert "ZZZZ" in _runs[run_id]["error"]
