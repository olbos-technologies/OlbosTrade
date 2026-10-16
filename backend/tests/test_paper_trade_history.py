"""
GET /api/paper-trade/history — regression coverage for a real production bug:
hold_days computation crashed (TypeError: date - datetime) whenever the
result set included any still-open trade (exit_date=None), silently zeroing
out the entire response (the route's except-all wraps it into
{"trades": [], "total": 0, "error": ...}), which is exactly what made the
"Desk signals" tab in the UI look empty/broken.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.paper_trade import get_trade_history

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _trade(*, status, entry_date, exit_date=None):
    t = MagicMock()
    t.strategy = "equity"
    t.underlying = "AAPL"
    t.status = status
    t.entry_date = entry_date
    t.exit_date = exit_date
    t.expiration = None
    t.short_strike = Decimal("0")
    t.long_strike = Decimal("0")
    t.quantity = 10
    t.credit_received = Decimal("150.0")
    t.cost_to_close = Decimal("0")
    t.pnl = None
    t.pnl_pct = None
    t.mfe = None
    t.mae = None
    t.exit_reason = None
    t.signal_score = Decimal("0.8")
    t.trading_mode_at_entry = "balanced"
    return t


def _session_for(trades: list) -> AsyncMock:
    """Fake AsyncSessionLocal() context manager whose session.execute() is
    called twice by get_trade_history: once for the trade rows, once for the
    count query."""
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = trades

    count_result = MagicMock()
    count_result.scalar.return_value = len(trades)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=[rows_result, count_result])
    return session


@pytest.mark.asyncio
async def test_history_does_not_crash_when_an_open_trade_is_present():
    """The exact bug: an open trade (exit_date=None) previously crashed
    hold_days's `date.today() - t.entry_date` (datetime) subtraction for the
    WHOLE response, not just that row."""
    now = datetime.now(timezone.utc)
    open_trade = _trade(status="open", entry_date=now - timedelta(days=3))
    session = _session_for([open_trade])

    with patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session):
        result = await get_trade_history(_CONN, limit=50, offset=0, status=None)

    assert "error" not in result
    assert result["total"] == 1
    assert result["trades"][0]["hold_days"] == 3
    assert result["trades"][0]["exit_date"] is None


@pytest.mark.asyncio
async def test_history_computes_hold_days_for_closed_trade():
    entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    exit_ = datetime(2026, 1, 8, tzinfo=timezone.utc)
    closed_trade = _trade(status="closed", entry_date=entry, exit_date=exit_)
    session = _session_for([closed_trade])

    with patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session):
        result = await get_trade_history(_CONN, limit=50, offset=0, status=None)

    assert result["trades"][0]["hold_days"] == 7


@pytest.mark.asyncio
async def test_history_mixed_open_and_closed_trades_both_compute_cleanly():
    now = datetime.now(timezone.utc)
    open_trade = _trade(status="open", entry_date=now - timedelta(days=1))
    closed_trade = _trade(
        status="closed",
        entry_date=now - timedelta(days=10),
        exit_date=now - timedelta(days=2),
    )
    session = _session_for([open_trade, closed_trade])

    with patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session):
        result = await get_trade_history(_CONN, limit=50, offset=0, status=None)

    assert "error" not in result
    assert result["total"] == 2
    assert result["trades"][0]["hold_days"] == 1
    assert result["trades"][1]["hold_days"] == 8


@pytest.mark.asyncio
async def test_history_empty_result_returns_cleanly():
    session = _session_for([])
    with patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session):
        result = await get_trade_history(_CONN, limit=50, offset=0, status=None)

    assert result == {"trades": [], "total": 0, "offset": 0, "limit": 50}
