"""Tests for TradeRecorder — fill recording, exit P&L, unknown-close."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.trade_recorder import TradeRecorder


def _session(trade=None):
    """AsyncSessionLocal mock supporting `async with`, .begin(), execute, get."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=session)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: trade))
    session.get = AsyncMock(return_value=trade)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


class _Trade:
    """Capture object standing in for a Trade row."""
    def __init__(self, **kw):
        self.cost_to_close = self.pnl = self.pnl_pct = None
        self.status = self.exit_date = self.exit_reason = None
        for k, v in kw.items():
            setattr(self, k, v)


# ── record_fill ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_record_fill_success_returns_trade_id():
    session = _session(trade=None)   # no existing dispatch_id
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        tid = await rec.record_fill(
            organization_id=None,   # single-operator scope
            strategy="bull_put_spread", underlying="SPY", option_type="put",
            short_strike=450, long_strike=445, expiration=date(2025, 6, 20),
            entry_credit=1.50, quantity=1, signal_score=0.8, iv_rank=40,
            regime="neutral", approved_by="autopilot", dispatch_id="D-1",
        )
    assert tid and len(tid) == 36          # uuid string
    assert session.add.call_count == 2     # Trade + JournalEntry


@pytest.mark.asyncio
async def test_record_fill_tags_real_risk_mode_not_approver():
    """
    trading_mode_at_entry must hold the actual risk-style mode
    (conservative|balanced|aggressive|scalper) read live from
    trading_mode_manager — NOT the approved_by value ("manual"/"autopilot"),
    which is a separate concept stored in its own column. These two were
    previously conflated: record_fill stored approved_by into
    trading_mode_at_entry, silently feeding ModeAnalyticsEngine and the
    Trade Desk mode badge the wrong data for every trade ever recorded.
    """
    session = _session(trade=None)
    rec = TradeRecorder()
    fake_manager = MagicMock()
    fake_manager.current.active_mode.value = "aggressive"
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.trading_mode.trading_mode_manager", fake_manager):
        tid = await rec.record_fill(
            organization_id=None,   # single-operator scope
            strategy="bull_put_spread", underlying="SPY", option_type="put",
            short_strike=450, long_strike=445, expiration=date(2025, 6, 20),
            entry_credit=1.50, quantity=1, signal_score=0.8, iv_rank=40,
            regime="neutral", approved_by="manual", dispatch_id="D-3",
        )
    assert tid
    trade_arg = session.add.call_args_list[0].args[0]
    assert trade_arg.trading_mode_at_entry == "aggressive"
    assert trade_arg.approved_by == "manual"


@pytest.mark.asyncio
async def test_record_fill_db_failure_returns_none():
    session = _session(trade=None)
    session.execute = AsyncMock(side_effect=Exception("db down"))
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        tid = await rec.record_fill(
            organization_id=None,   # single-operator scope
            strategy="equity", underlying="AAPL", option_type="equity_long",
            short_strike=0, long_strike=0, expiration=date(2025, 1, 1),
            entry_credit=150, quantity=10, signal_score=0.7, iv_rank=0,
            regime="x", approved_by="autopilot", dispatch_id="D-2",
        )
    assert tid is None


# ── record_exit ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_record_exit_equity_long():
    t = _Trade(credit_received=Decimal("100"), quantity=1,
               spread_type="equity_long", strategy="equity")
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.record_exit(trade_id=str(uuid.uuid4()), cost_to_close=110.0,
                                   exit_reason="target")
    assert ok and float(t.pnl) == 10.0 and t.status == "closed"   # (110-100)*1


@pytest.mark.asyncio
async def test_record_exit_equity_short():
    t = _Trade(credit_received=Decimal("100"), quantity=1,
               spread_type="equity_short", strategy="equity")
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        await rec.record_exit(trade_id=str(uuid.uuid4()), cost_to_close=90.0, exit_reason="cover")
    assert float(t.pnl) == 10.0          # short: entry - exit = 100 - 90


@pytest.mark.asyncio
async def test_record_exit_options_multiplier_100():
    t = _Trade(credit_received=Decimal("1.50"), quantity=2, spread_type="put", strategy="bull_put_spread")
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        await rec.record_exit(trade_id=str(uuid.uuid4()), cost_to_close=0.50, exit_reason="profit")
    assert float(t.pnl) == 200.0         # (1.5-0.5)*2*100


@pytest.mark.asyncio
async def test_record_exit_trade_not_found():
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=None)):
        ok = await rec.record_exit(trade_id=str(uuid.uuid4()), cost_to_close=1.0, exit_reason="x")
    assert ok is False


@pytest.mark.asyncio
async def test_record_exit_handles_exception():
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("boom")):
        ok = await rec.record_exit(trade_id=str(uuid.uuid4()), cost_to_close=1.0, exit_reason="x")
    assert ok is False


# ── record_close_unknown ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_record_close_unknown_sets_null_pnl():
    t = _Trade(status="open")
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.record_close_unknown(trade_id=str(uuid.uuid4()),
                                            exit_reason="closed_price_unavailable")
    assert ok and t.status == "closed" and t.pnl is None


@pytest.mark.asyncio
async def test_record_close_unknown_not_found():
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=None)):
        ok = await rec.record_close_unknown(trade_id=str(uuid.uuid4()), exit_reason="x")
    assert ok is False


@pytest.mark.asyncio
async def test_record_close_unknown_handles_exception():
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("boom")):
        ok = await rec.record_close_unknown(trade_id=str(uuid.uuid4()), exit_reason="x")
    assert ok is False


# ── pending entry status + reconciliation (confirm_fill / cancel_pending) ──────
@pytest.mark.asyncio
async def test_record_fill_pending_status_persisted():
    """A working (unfilled) order is recorded as pending, not open."""
    session = _session(trade=None)
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        tid = await rec.record_fill(
            organization_id=None,   # single-operator scope
            strategy="bull_put_spread", underlying="QQQ", option_type="put",
            short_strike=400, long_strike=395, expiration=date(2025, 6, 20),
            entry_credit=2.98, quantity=4, signal_score=0.8, iv_rank=40,
            regime="neutral", approved_by="autopilot", dispatch_id="D-P1",
            status="pending",
        )
    assert tid
    trade_arg = session.add.call_args_list[0].args[0]   # first add() = Trade row
    assert trade_arg.status == "pending"


@pytest.mark.asyncio
async def test_confirm_fill_promotes_pending_to_open():
    t = _Trade(status="pending", credit_received=Decimal("2.98"))
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.confirm_fill(trade_id=str(uuid.uuid4()))
    assert ok is True and t.status == "open"


@pytest.mark.asyncio
async def test_confirm_fill_overwrites_credit_with_real_fill():
    t = _Trade(status="pending", credit_received=Decimal("2.98"))
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.confirm_fill(trade_id=str(uuid.uuid4()), net_fill_price=3.10)
    assert ok and t.status == "open" and float(t.credit_received) == 3.10


@pytest.mark.asyncio
async def test_confirm_fill_corrects_quantity_to_broker_reality():
    """The exact bug this covers: a pending trade's quantity used to stay
    frozen at whatever was planned at signal time forever, even once the
    real fill (or a broker holding built from prior activity) differed —
    confirmed in production as MU/SNDK/MRVL each under-reporting their
    broker quantity by 2-7x. confirm_fill() must now correct it, the same
    way it already corrects credit_received from net_fill_price."""
    t = _Trade(status="pending", credit_received=Decimal("2.98"), quantity=21)
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.confirm_fill(trade_id=str(uuid.uuid4()), quantity=63.0)
    assert ok and t.status == "open" and t.quantity == 63


@pytest.mark.asyncio
async def test_confirm_fill_without_quantity_leaves_it_unchanged():
    """No quantity passed (e.g. the underlying wasn't found in live_qty) —
    don't clobber a legitimate value with None."""
    t = _Trade(status="pending", credit_received=Decimal("2.98"), quantity=21)
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.confirm_fill(trade_id=str(uuid.uuid4()))
    assert ok and t.quantity == 21


@pytest.mark.asyncio
async def test_confirm_fill_ignores_non_pending():
    t = _Trade(status="open")
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.confirm_fill(trade_id=str(uuid.uuid4()))
    assert ok is False and t.status == "open"


@pytest.mark.asyncio
async def test_confirm_fill_not_found_returns_false():
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=None)):
        ok = await rec.confirm_fill(trade_id=str(uuid.uuid4()))
    assert ok is False


@pytest.mark.asyncio
async def test_cancel_pending_marks_cancelled():
    t = _Trade(status="pending")
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.cancel_pending(trade_id=str(uuid.uuid4()), reason="order_unfilled_timeout")
    assert ok and t.status == "cancelled" and t.exit_reason == "order_unfilled_timeout"


@pytest.mark.asyncio
async def test_cancel_pending_ignores_non_pending():
    t = _Trade(status="open")
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.cancel_pending(trade_id=str(uuid.uuid4()), reason="x")
    assert ok is False and t.status == "open"


@pytest.mark.asyncio
async def test_cancel_pending_handles_exception():
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("boom")):
        ok = await rec.cancel_pending(trade_id=str(uuid.uuid4()), reason="x")
    assert ok is False


# ── update_excursion (MFE/MAE) ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_update_excursion_initialises_both():
    t = _Trade(status="open", mfe=None, mae=None)
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.update_excursion(trade_id=str(uuid.uuid4()), unrealized_pnl=50.0)
    assert ok and float(t.mfe) == 50.0 and float(t.mae) == 50.0


@pytest.mark.asyncio
async def test_update_excursion_advances_high_water():
    t = _Trade(status="open", mfe=Decimal("50"), mae=Decimal("50"))
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.update_excursion(trade_id=str(uuid.uuid4()), unrealized_pnl=80.0)
    assert ok and float(t.mfe) == 80.0 and float(t.mae) == 50.0


@pytest.mark.asyncio
async def test_update_excursion_advances_low_water():
    t = _Trade(status="open", mfe=Decimal("80"), mae=Decimal("50"))
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.update_excursion(trade_id=str(uuid.uuid4()), unrealized_pnl=-30.0)
    assert ok and float(t.mae) == -30.0 and float(t.mfe) == 80.0


@pytest.mark.asyncio
async def test_update_excursion_no_change_within_band():
    t = _Trade(status="open", mfe=Decimal("80"), mae=Decimal("-30"))
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.update_excursion(trade_id=str(uuid.uuid4()), unrealized_pnl=10.0)
    assert ok is False


@pytest.mark.asyncio
async def test_update_excursion_ignores_non_open():
    t = _Trade(status="closed", mfe=None, mae=None)
    rec = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(trade=t)):
        ok = await rec.update_excursion(trade_id=str(uuid.uuid4()), unrealized_pnl=10.0)
    assert ok is False
