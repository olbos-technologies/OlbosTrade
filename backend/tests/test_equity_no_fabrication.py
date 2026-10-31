"""
The equity readout must never substitute a computed value for a broker read.

2026-08-27: the account read failed 100% of the day and the dashboard showed
$245,494.81 — exactly starting_capital (250,000) + DB total_pnl (-4,505.19) —
while IBKR actually held $254,029.86. An $8.5k gap, presented as authoritative,
with no staleness indicator: the timeout surfaced as broker_error="" because
str(asyncio.TimeoutError()) is the empty string, which every downstream
`if (broker_error)` check reads as "no error".

Unknown must read as unknown.

Run with: pytest tests/test_equity_no_fabrication.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _db_ok():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock())
    return session


# ── /api/paper-trade/portfolio ────────────────────────────────────────────────

async def _portfolio_with(broker_exc):
    from app.api.routes.paper_trade import get_portfolio

    broker = MagicMock()
    broker.get_account_summary = AsyncMock(side_effect=broker_exc)
    # paper_trade.py does `from ... import get_broker` at module level, so the
    # name must be patched where it is bound, not at the source module.
    with patch("app.api.routes.paper_trade.get_broker", return_value=broker):
        return (await get_portfolio(_CONN))["portfolio"]


@pytest.mark.asyncio
async def test_timeout_produces_a_non_empty_broker_error():
    """str(TimeoutError()) is '' — a falsy error message hides the failure."""
    p = await _portfolio_with(asyncio.TimeoutError())

    assert p.get("broker_error"), "a timeout must not surface as an empty string"
    assert "TimeoutError" in p["broker_error"]


@pytest.mark.asyncio
async def test_account_value_is_absent_not_fabricated_on_failure():
    p = await _portfolio_with(asyncio.TimeoutError())

    assert p.get("account_value") is None
    # The old code defaulted to starting_capital + total_pnl. Whatever the DB
    # P&L happens to be, no arithmetic stand-in may appear here.
    assert p.get("return_pct") is None


# ── /api/dashboard/summary ────────────────────────────────────────────────────


def _summary_conn():
    """Caller stand-in for `dashboard_summary`.

    Its P&L sums are scoped to the caller; the equity these tests assert on
    is not, so the scope here only has to exist, not to match anything.
    """
    from types import SimpleNamespace as NS
    import uuid
    return NS(state=NS(user={"id": str(uuid.uuid4())}))


def _pinned_scope():
    async def _scope(session, conn):
        return None
    return patch("app.services.trade_scope.request_scope", _scope)


@pytest.mark.asyncio
async def test_total_equity_is_null_when_broker_read_fails():
    from app.main import dashboard_summary

    broker = MagicMock()
    broker._connected = True
    broker.ib = MagicMock()
    broker.ib.isConnected = MagicMock(return_value=True)

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_db_ok()), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit",
               new=AsyncMock(side_effect=asyncio.TimeoutError())), \
         _pinned_scope():
        out = await dashboard_summary(_summary_conn())

    assert out["total_equity"] is None, (
        "a failed broker read must not be replaced by capital + realized P&L"
    )
    assert out["total_equity_source"] == "unavailable"


@pytest.mark.asyncio
async def test_total_equity_uses_the_broker_value_when_available():
    from app.main import dashboard_summary

    broker = MagicMock()
    broker._connected = True
    broker.ib = MagicMock()
    broker.ib.isConnected = MagicMock(return_value=True)
    acct = MagicMock(); acct.net_liquidation = 254029.86

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_db_ok()), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit",
               new=AsyncMock(return_value=acct)), \
         _pinned_scope():
        out = await dashboard_summary(_summary_conn())

    assert out["total_equity"] == 254029.86
    assert out["total_equity_source"] == "broker"


@pytest.mark.asyncio
async def test_no_synthetic_fallback_expression_remains_in_the_summary():
    """Regression guard: the fabrication was a one-line default that read as
    harmless. Assert the summary never assigns capital + P&L to equity."""
    import pathlib, re

    src = (pathlib.Path(__file__).resolve().parent.parent / "app" / "main.py").read_text()
    offenders = [
        line.strip() for line in src.splitlines()
        if re.search(r"total_equity\s*=\s*cap\s*\+", line)
    ]
    assert not offenders, f"synthetic equity fallback reintroduced: {offenders}"
