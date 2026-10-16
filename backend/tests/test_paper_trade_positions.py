"""
GET /api/paper-trade/positions — regression coverage for a real production bug:
the "tracked broker positions" branch (the normal path — every live position
matched to a broker holding goes through it) never included asset_type,
hold_days, trading_mode, credit_received, mfe_pnl, or mae_pnl, even though all
of that data was already available on the matched DB Trade row. The Trade Desk
Positions table reads all of those fields, so every real position silently
showed wrong defaults: "OPTIONS" for equities, "$0.00" entry credit, blank
MFE/MAE, "—" hold days, and "balanced" regardless of the actual active mode.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.paper_trade import get_positions


def _broker_position(*, symbol, quantity, avg_cost, underlying=None, asset_type="equity"):
    # asset_type mirrors Position.asset_type (Literal["equity", "option"]) —
    # every real broker position sets this explicitly (ibkr_client.py /
    # alpaca_client.py), so tests must too. A bare MagicMock() auto-vivifies
    # `.asset_type` as a Mock object rather than raising AttributeError, so
    # the route's `getattr(pos, "asset_type", "option")` fallback silently
    # never triggers and the id_key/asset_type derivation goes wrong.
    p = MagicMock()
    p.symbol = symbol
    p.underlying = underlying or symbol
    p.quantity = quantity
    p.avg_cost = avg_cost
    p.asset_type = asset_type
    return p


def _db_trade(*, underlying, entry_date, spread_type, strategy="equity",
              trading_mode="aggressive", approved_by="autopilot",
              credit_received=Decimal("362.808"),
              mfe=Decimal("120.5"), mae=Decimal("-63.43")):
    t = MagicMock()
    t.id = "11111111-1111-1111-1111-111111111111"
    t.underlying = underlying
    t.strategy = strategy
    t.spread_type = spread_type
    t.entry_date = entry_date
    t.trading_mode_at_entry = trading_mode
    t.approved_by = approved_by
    t.credit_received = credit_received
    t.mfe = mfe
    t.mae = mae
    return t


def _session_for(db_trades: list) -> AsyncMock:
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = db_trades

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=rows_result)
    return session


@pytest.mark.asyncio
async def test_tracked_equity_position_includes_asset_type_hold_days_mode_and_excursion():
    """The exact bug: a broker position matched to a DB trade previously
    returned none of these fields, so the Trade Desk table showed wrong
    defaults for every real (equity) position."""
    now = datetime.now(timezone.utc)
    entry = now - timedelta(days=5)

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[
        _broker_position(symbol="GOOGL", quantity=55, avg_cost=362.808),
    ])

    db_trade = _db_trade(underlying="GOOGL", entry_date=entry, spread_type="equity_long")
    session = _session_for([db_trade])

    with patch("app.api.routes.paper_trade.get_broker", return_value=broker), \
         patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session), \
         patch("yfinance.Tickers", side_effect=RuntimeError("no network in tests")):
        result = await get_positions(_CONN)

    pos = result["positions"][0]
    assert pos["asset_type"] == "equity"
    assert pos["hold_days"] == 5
    assert pos["trading_mode"] == "aggressive"
    # trading_mode (risk-style: conservative/balanced/aggressive/scalper) and
    # approved_by (who/what approved the fill) are separate columns — a prior
    # bug conflated them, feeding the Trade Desk mode badge "manual"/
    # "autopilot" instead of the actual risk mode.
    assert pos["approved_by"] == "autopilot"
    assert pos["credit_received"] == pytest.approx(362.808)
    assert pos["mfe_pnl"] == pytest.approx(120.5)
    assert pos["mae_pnl"] == pytest.approx(-63.43)
    assert pos["tracked"] is True


@pytest.mark.asyncio
async def test_tracked_options_position_reports_options_asset_type():
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[
        _broker_position(symbol="SPY", quantity=1, avg_cost=5.0, asset_type="option"),
    ])
    db_trade = _db_trade(
        underlying="SPY", entry_date=datetime.now(timezone.utc), spread_type="put_credit_spread",
        strategy="put_credit_spread",
    )
    session = _session_for([db_trade])

    with patch("app.api.routes.paper_trade.get_broker", return_value=broker), \
         patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session), \
         patch("yfinance.Tickers", side_effect=RuntimeError("no network in tests")):
        result = await get_positions(_CONN)

    assert result["positions"][0]["asset_type"] == "options"


@pytest.mark.asyncio
async def test_untracked_broker_position_has_no_db_derived_fields():
    """A broker holding with no matching DB row (ghost/untracked position)
    must not fabricate hold_days/mode/excursion data that doesn't exist."""
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[
        _broker_position(symbol="MSFT", quantity=10, avg_cost=400.0),
    ])
    session = _session_for([])  # no open DB trades at all

    with patch("app.api.routes.paper_trade.get_broker", return_value=broker), \
         patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session), \
         patch("yfinance.Tickers", side_effect=RuntimeError("no network in tests")):
        result = await get_positions(_CONN)

    pos = result["positions"][0]
    assert pos["tracked"] is False
    assert pos["hold_days"] is None
    assert pos["trading_mode"] is None
    assert pos["approved_by"] is None
    assert pos["mfe_pnl"] is None
    assert pos["mae_pnl"] is None
    # No DB row, so asset_type comes from the broker's own report (MSFT is
    # a stock) rather than a derived spread_type — this is the untracked-
    # position asset_type fix, not a hardcoded "no data -> options" default.
    assert pos["asset_type"] == "equity"


@pytest.mark.asyncio
async def test_db_only_fallback_position_includes_asset_type():
    """A DB-open trade with no matching live broker position (the rare
    db_only fallback branch) should also report a correct asset_type."""
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    db_trade = _db_trade(underlying="V", entry_date=datetime.now(timezone.utc), spread_type="equity_long")
    session = _session_for([db_trade])

    with patch("app.api.routes.paper_trade.get_broker", return_value=broker), \
         patch("app.api.routes.paper_trade.AsyncSessionLocal", return_value=session):
        result = await get_positions(_CONN)

    pos = result["positions"][0]
    assert pos["source"] == "db_only"
    assert pos["asset_type"] == "equity"


# ── hold_days must not depend on the host's timezone ─────────────────────────
#
# It used to: Trade.entry_date is DateTime(timezone=True) UTC, but hold_days
# was measured against date.today(), the *system-local* date. The answer came
# out one short for part of every day on any host west of UTC. The 5-day
# assertion above read 4 on a UTC-7 machine while passing in CI at UTC — the
# same code disagreeing with itself depending on where it ran.

from datetime import datetime, timedelta, timezone as _tz

from app.api.routes.paper_trade import _hold_days_utc

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def test_hold_days_counts_utc_calendar_days():
    entry = datetime.now(_tz.utc) - timedelta(days=5)
    assert _hold_days_utc(entry) == 5


def test_hold_days_is_identical_for_the_same_instant_in_any_timezone():
    """The same moment expressed in three offsets must give one answer."""
    instant = datetime.now(_tz.utc) - timedelta(days=3)
    answers = {
        _hold_days_utc(instant),
        _hold_days_utc(instant.astimezone(_tz(timedelta(hours=-7)))),
        _hold_days_utc(instant.astimezone(_tz(timedelta(hours=+9)))),
    }
    assert len(answers) == 1, f"timezone-dependent hold_days: {answers}"


def test_hold_days_treats_a_naive_datetime_as_utc():
    """Older rows and fixtures can be naive. Assuming local for those would
    reintroduce exactly the bug this fixes."""
    naive = (datetime.now(_tz.utc) - timedelta(days=2)).replace(tzinfo=None)
    assert _hold_days_utc(naive) == 2


def test_hold_days_never_negative_and_none_stays_none():
    assert _hold_days_utc(datetime.now(_tz.utc) + timedelta(days=3)) == 0
    assert _hold_days_utc(None) is None
