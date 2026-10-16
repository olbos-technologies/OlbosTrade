"""Tests for the GET /api/portfolio/correlation route."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()


BASE = datetime(2026, 1, 1)


def _trade(underlying, risk_dollars=1000.0):
    # Shape position_risk_dollars() reads: equity-style so risk = credit*qty.
    return NS(underlying=underlying, quantity=1, spread_type="equity",
              strategy="equity", credit_received=risk_dollars,
              short_strike=0, long_strike=0)


def _bars(n, start_price=100.0, step=1.0, start=BASE):
    return [NS(timestamp=start + timedelta(days=i), close=start_price + step * i) for i in range(n)]


def _trades_session(trades):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: trades)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_zero_positions_returns_insufficient_data_no_fetch():
    from app.api.routes import portfolio as pmod

    with patch("app.core.database.AsyncSessionLocal", return_value=_trades_session([])), \
         patch("app.services.account_state.get_account_value", new=AsyncMock(return_value=100_000.0)), \
         patch("app.main._yf_bars", new=AsyncMock()) as mock_bars:
        out = await pmod.portfolio_correlation(_CONN)

    assert out["status"] == "insufficient_data"
    mock_bars.assert_not_called()


@pytest.mark.asyncio
async def test_one_position_returns_insufficient_data_no_fetch():
    from app.api.routes import portfolio as pmod

    with patch("app.core.database.AsyncSessionLocal", return_value=_trades_session([_trade("AAPL")])), \
         patch("app.services.account_state.get_account_value", new=AsyncMock(return_value=100_000.0)), \
         patch("app.main._yf_bars", new=AsyncMock()) as mock_bars:
        out = await pmod.portfolio_correlation(_CONN)

    assert out["status"] == "insufficient_data"
    assert "1 distinct" in out["reason"]
    mock_bars.assert_not_called()


@pytest.mark.asyncio
async def test_two_correlated_positions_produce_cluster_and_flag():
    from app.api.routes import portfolio as pmod

    trades = [_trade("AAPL", risk_dollars=8000.0), _trade("MSFT", risk_dollars=8000.0)]

    async def fake_bars(ticker, limit=60):
        # Both move in lockstep → perfectly correlated.
        return _bars(40, start_price=100.0 if ticker == "AAPL" else 300.0, step=1.0 if ticker == "AAPL" else 2.0)

    with patch("app.core.database.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch("app.services.account_state.get_account_value", new=AsyncMock(return_value=20_000.0)), \
         patch("app.main._yf_bars", new=AsyncMock(side_effect=fake_bars)):
        out = await pmod.portfolio_correlation(_CONN)

    assert out["status"] == "ok"
    assert out["tickers"] == ["AAPL", "MSFT"]
    assert len(out["clusters"]) == 1
    assert set(out["clusters"][0]["tickers"]) == {"AAPL", "MSFT"}
    assert out["clusters"][0]["combined_risk_dollars"] == 16000.0
    assert any(f.startswith("correlation_concentration:") for f in out["concentration_flags"])


@pytest.mark.asyncio
async def test_one_ticker_fetch_failure_survivors_still_work():
    from app.api.routes import portfolio as pmod

    trades = [_trade("AAPL"), _trade("MSFT"), _trade("BAD")]

    async def fake_bars(ticker, limit=60):
        if ticker == "BAD":
            raise RuntimeError("yfinance timeout")
        return _bars(40, start_price=100.0 if ticker == "AAPL" else 300.0, step=1.0 if ticker == "AAPL" else 2.0)

    with patch("app.core.database.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch("app.services.account_state.get_account_value", new=AsyncMock(return_value=20_000.0)), \
         patch("app.main._yf_bars", new=AsyncMock(side_effect=fake_bars)):
        out = await pmod.portfolio_correlation(_CONN)

    assert out["status"] == "ok"
    assert "AAPL" in out["tickers"] and "MSFT" in out["tickers"]
    assert "BAD" not in out["tickers"]
    bad_reasons = [x for x in out["excluded_symbols"] if x["ticker"] == "BAD"]
    assert len(bad_reasons) == 1
    assert "fetch failed" in bad_reasons[0]["reason"]


@pytest.mark.asyncio
async def test_db_failure_returns_graceful_error():
    from app.api.routes import portfolio as pmod

    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")), \
         patch("app.services.account_state.get_account_value", new=AsyncMock(return_value=100_000.0)):
        out = await pmod.portfolio_correlation(_CONN)

    assert out["status"] == "error"
    assert "db down" in out["error"]
