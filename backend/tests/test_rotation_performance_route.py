"""Tests for the GET /api/portfolio/rotation-performance route."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _trade(underlying, pnl, regime="normal_mean_revert", exit_reason="position_rotation",
           entry_date=datetime(2026, 8, 1), exit_date=datetime(2026, 8, 3)):
    return NS(
        id=f"{underlying}-{exit_date.isoformat()}", underlying=underlying, pnl=pnl,
        regime=regime, exit_reason=exit_reason, entry_date=entry_date, exit_date=exit_date,
    )


def _trades_session(trades):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: trades)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_zero_rotations_returns_empty_shape():
    from app.api.routes import portfolio as pmod

    with patch("app.core.database.AsyncSessionLocal", return_value=_trades_session([])):
        out = await pmod.portfolio_rotation_performance(_CONN)

    assert out["status"] == "no_rotations_yet"
    assert out["total"] == 0
    assert out["recent"] == []


@pytest.mark.asyncio
async def test_normal_case_computes_correct_aggregates():
    from app.api.routes import portfolio as pmod

    trades = [
        _trade("AAPL", 50.0),
        _trade("MSFT", -20.0),
    ]
    with patch("app.core.database.AsyncSessionLocal", return_value=_trades_session(trades)):
        out = await pmod.portfolio_rotation_performance(_CONN)

    assert out["status"] == "ok"
    assert out["total"] == 2
    assert out["total_pnl"] == 30.0
    assert out["win_rate"] == 0.5
    assert len(out["recent"]) == 2


@pytest.mark.asyncio
async def test_query_filters_on_position_rotation_exit_reason():
    """The mock doesn't apply real SQL WHERE filtering (it returns
    whatever rows it's given regardless of the query), so correctness of
    the exit_reason=='position_rotation' filter has to be asserted
    against the compiled statement itself — a stop_loss-exited trade must
    never reach the ledger in production even though this mock would
    happily hand one back if asked."""
    from app.api.routes import portfolio as pmod

    trades = [_trade("AAPL", 50.0, exit_reason="position_rotation")]
    session = _trades_session(trades)
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        out = await pmod.portfolio_rotation_performance(_CONN)

    assert out["status"] == "ok"
    assert out["total"] == 1
    compiled = session.execute.await_args.args[0].compile(
        compile_kwargs={"literal_binds": True},
    )
    assert "position_rotation" in str(compiled)
    assert "stop_loss" not in str(compiled)


@pytest.mark.asyncio
async def test_unknown_pnl_rows_are_skipped():
    from app.api.routes import portfolio as pmod

    trades = [_trade("AAPL", None), _trade("MSFT", 10.0)]
    with patch("app.core.database.AsyncSessionLocal", return_value=_trades_session(trades)):
        out = await pmod.portfolio_rotation_performance(_CONN)

    assert out["status"] == "ok"
    assert out["total"] == 1
    assert out["total_pnl"] == 10.0


@pytest.mark.asyncio
async def test_db_failure_returns_graceful_error():
    from app.api.routes import portfolio as pmod

    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        out = await pmod.portfolio_rotation_performance(_CONN)

    assert out["status"] == "error"
    assert "db down" in out["error"]
