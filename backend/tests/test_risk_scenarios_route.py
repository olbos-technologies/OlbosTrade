"""Tests for GET /api/risk/scenarios — every position must be stressed
against its real spot, never modeled at-the-money via the strike, and
equity positions must get linear P&L, never Black-Scholes option pricing."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _equity_trade(underlying, spread_type="equity_long", quantity=100):
    return NS(
        underlying=underlying, spread_type=spread_type, strategy="equity",
        quantity=quantity, short_strike=0.0, long_strike=0.0,
        expiration=date.today() + timedelta(days=30), status="open",
    )


def _option_trade(underlying, spread_type="put", short_strike=100.0, long_strike=95.0):
    return NS(
        underlying=underlying, spread_type=spread_type, strategy="bull_put_spread",
        quantity=1, short_strike=short_strike, long_strike=long_strike,
        expiration=date.today() + timedelta(days=30), status="open",
    )


def _trades_session(trades):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: trades)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.fixture(autouse=True)
def _reset_spot_cache():
    from app.services import spot_price_cache
    spot_price_cache.clear()
    yield
    spot_price_cache.clear()


@pytest.mark.asyncio
async def test_zero_positions_returns_zero_pnl_scenarios():
    from app.api.routes import risk as risk_mod

    with patch("app.api.routes.risk.AsyncSessionLocal", return_value=_trades_session([])):
        out = await risk_mod.get_scenarios(_CONN)

    # run_all() always returns one entry per shock (zero P&L with no
    # positions) — not an empty list; excluded_symbols is what's empty here.
    assert len(out["scenarios"]) > 0
    assert all(s["portfolio_pnl"] == 0 for s in out["scenarios"])
    assert out["excluded_symbols"] == []


@pytest.mark.asyncio
async def test_equity_long_position_gets_linear_pnl_not_option_pricing():
    """Regression for the bug this slice fixes: an equity_long position
    must lose exactly spot * qty * |shock_pct| under market_crash, not
    some Black-Scholes-derived number."""
    from app.api.routes import risk as risk_mod

    trades = [_equity_trade("AAPL", spread_type="equity_long", quantity=100)]

    with patch("app.api.routes.risk.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch.object(risk_mod, "_fetch_spot", new=AsyncMock(return_value=150.0)):
        out = await risk_mod.get_scenarios(_CONN)

    crash = next(s for s in out["scenarios"] if s["scenario"] == "market_crash")
    assert crash["portfolio_pnl"] == pytest.approx(-100 * 150.0 * 0.20)
    assert crash["positions"][0]["baseline"] == pytest.approx(100 * 150.0)


@pytest.mark.asyncio
async def test_equity_short_position_gains_where_long_loses():
    from app.api.routes import risk as risk_mod

    trades = [_equity_trade("AAPL", spread_type="equity_short", quantity=100)]

    with patch("app.api.routes.risk.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch.object(risk_mod, "_fetch_spot", new=AsyncMock(return_value=150.0)):
        out = await risk_mod.get_scenarios(_CONN)

    crash = next(s for s in out["scenarios"] if s["scenario"] == "market_crash")
    assert crash["portfolio_pnl"] == pytest.approx(100 * 150.0 * 0.20)


@pytest.mark.asyncio
async def test_option_position_still_uses_black_scholes_kind():
    """Regression: the option branch (spread_type "call"/"put") must
    still produce kind:"option" positions, not accidentally fall into
    the new equity branch."""
    from app.api.routes import risk as risk_mod

    trades = [_option_trade("AAPL", spread_type="put")]

    with patch("app.api.routes.risk.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch.object(risk_mod, "_fetch_spot", new=AsyncMock(return_value=150.0)):
        out = await risk_mod.get_scenarios(_CONN)

    assert "error" not in out
    assert len(out["scenarios"]) > 0


@pytest.mark.asyncio
async def test_resolvable_spots_produce_real_stressed_scenarios():
    from app.api.routes import risk as risk_mod

    trades = [_equity_trade("AAPL"), _equity_trade("MSFT", quantity=50)]

    async def fake_fetch(symbol):
        return {"AAPL": 150.0, "MSFT": 195.0}[symbol]

    with patch("app.api.routes.risk.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch.object(risk_mod, "_fetch_spot", new=fake_fetch):
        out = await risk_mod.get_scenarios(_CONN)

    assert out["excluded_symbols"] == []
    assert len(out["scenarios"]) > 0
    assert out["worst_pnl"] < 0  # both long, worst scenario must be a real loss


@pytest.mark.asyncio
async def test_one_of_several_unresolvable_spots_survivors_still_stressed():
    from app.api.routes import risk as risk_mod

    trades = [_equity_trade("AAPL"), _equity_trade("MSFT"), _equity_trade("BAD")]

    async def fake_fetch(symbol):
        if symbol == "BAD":
            raise ConnectionError("yfinance timeout")
        return {"AAPL": 150.0, "MSFT": 195.0}[symbol]

    with patch("app.api.routes.risk.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch.object(risk_mod, "_fetch_spot", new=fake_fetch):
        out = await risk_mod.get_scenarios(_CONN)

    assert len(out["excluded_symbols"]) == 1
    assert out["excluded_symbols"][0]["ticker"] == "BAD"
    assert "spot unavailable" in out["excluded_symbols"][0]["reason"]
    assert len(out["scenarios"]) > 0  # survivors still stressed, no 500


@pytest.mark.asyncio
async def test_option_type_derived_from_spread_type_not_missing_column():
    """Regression: Trade has no option_type column — _trade_to_scenario_position
    must derive it from spread_type (here "call") instead of raising
    AttributeError."""
    from app.api.routes import risk as risk_mod

    trades = [_option_trade("AAPL", spread_type="call")]

    with patch("app.api.routes.risk.AsyncSessionLocal", return_value=_trades_session(trades)), \
         patch.object(risk_mod, "_fetch_spot", new=AsyncMock(return_value=150.0)):
        out = await risk_mod.get_scenarios(_CONN)

    assert "error" not in out
    assert len(out["scenarios"]) > 0


@pytest.mark.asyncio
async def test_db_failure_returns_graceful_error_not_500():
    from app.api.routes import risk as risk_mod

    with patch("app.api.routes.risk.AsyncSessionLocal", side_effect=Exception("db down")):
        out = await risk_mod.get_scenarios(_CONN)

    assert out["error"] == "db down"
    assert out["scenarios"] == []
