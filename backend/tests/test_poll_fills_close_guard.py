"""A trade missing from the position list is not booked closed on the cache's word.

2026-11-07: AMZN, FER and SPCX were booked closed (`closed_price_unavailable`,
unknown P&L) 23 ms apart while IBKR still held all three. The position list
comes from a local cache that can be empty or incomplete without raising, and
the only existing guard checked that the gateway was *connected*, not that the
list was believable. Booking a close with unknown P&L is the destructive step,
so before it the broker is asked over the network (`confirm_symbol_flat`) and
only an affirmative flat closes the row.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import app.main as m
from app.services.trade_recorder import trade_recorder
from conftest import single_operator_work
from test_poll_fills import _db_session, _fake_broker, _open_trade, _claim_evidence_pass_is_not_under_test_here  # noqa: F401

_LONG_AGO = datetime.now(timezone.utc) - timedelta(days=1)


@pytest.fixture(autouse=True)
def _clean_module_state():
    m._close_pending.clear()
    m._close_held_logged.clear()
    yield
    m._close_pending.clear()
    m._close_held_logged.clear()


def _past_grace(trade):
    """The row has already been missing for longer than the grace window."""
    m._close_pending[str(trade.id)] = datetime.now(timezone.utc) - timedelta(
        seconds=m.CLOSE_GRACE_SECONDS + 60
    )


async def _cycle(trade, flat):
    broker = _fake_broker([])  # hollow list: nothing held according to the cache
    if isinstance(flat, Exception):
        broker.confirm_symbol_flat = AsyncMock(side_effect=flat)
    else:
        broker.confirm_symbol_flat = AsyncMock(return_value=flat)
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_db_session([trade])), \
         patch.object(trade_recorder, "record_close_unknown", new=AsyncMock()) as close_unknown, \
         patch.object(trade_recorder, "record_exit", new=AsyncMock()) as record_exit:
        await m._poll_fills_for_scope(single_operator_work())
    return broker, close_unknown, record_exit


@pytest.mark.asyncio
async def test_broker_still_holding_it_keeps_the_row_open():
    trade = _open_trade("FER", _LONG_AGO)
    _past_grace(trade)
    broker, close_unknown, record_exit = await _cycle(trade, flat=False)
    broker.confirm_symbol_flat.assert_awaited_once_with("FER")
    close_unknown.assert_not_called()
    record_exit.assert_not_called()


@pytest.mark.asyncio
async def test_unanswerable_broker_keeps_the_row_open():
    trade = _open_trade("FER", _LONG_AGO)
    _past_grace(trade)
    _, close_unknown, _ = await _cycle(trade, flat=None)
    close_unknown.assert_not_called()


@pytest.mark.asyncio
async def test_a_failing_confirmation_keeps_the_row_open():
    trade = _open_trade("FER", _LONG_AGO)
    _past_grace(trade)
    _, close_unknown, _ = await _cycle(trade, flat=ConnectionError("gateway down"))
    close_unknown.assert_not_called()


@pytest.mark.asyncio
async def test_a_broker_with_no_way_to_answer_keeps_the_row_open():
    """The BrokerInterface default is None, so an adapter that has not opted in
    can never close on the cache's word either."""
    from app.broker.broker_interface import BrokerInterface

    trade = _open_trade("FER", _LONG_AGO)
    _past_grace(trade)
    broker = _fake_broker([])
    broker.confirm_symbol_flat = lambda s: BrokerInterface.confirm_symbol_flat(broker, s)
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_db_session([trade])), \
         patch.object(trade_recorder, "record_close_unknown", new=AsyncMock()) as close_unknown:
        await m._poll_fills_for_scope(single_operator_work())
    close_unknown.assert_not_called()


@pytest.mark.asyncio
async def test_the_whole_hollow_batch_is_held_not_closed():
    """The 2026-11-07 shape: several open rows, an empty list, all past grace."""
    trades = [_open_trade(s, _LONG_AGO) for s in ("AMZN", "FER", "SPCX")]
    for t in trades:
        _past_grace(t)
    broker = _fake_broker([])
    broker.confirm_symbol_flat = AsyncMock(return_value=False)
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_db_session(trades)), \
         patch.object(trade_recorder, "record_close_unknown", new=AsyncMock()) as close_unknown:
        await m._poll_fills_for_scope(single_operator_work())
    close_unknown.assert_not_called()
    assert broker.confirm_symbol_flat.await_count == 3


@pytest.mark.asyncio
async def test_a_confirmed_flat_still_books_the_close():
    """The guard must not turn reconciliation off: a position the broker affirms
    is gone is still closed, with unknown P&L as before."""
    trade = _open_trade("FER", _LONG_AGO)
    _past_grace(trade)
    _, close_unknown, _ = await _cycle(trade, flat=True)
    close_unknown.assert_awaited_once_with(
        trade_id=str(trade.id), exit_reason="closed_price_unavailable",
    )


@pytest.mark.asyncio
async def test_inside_the_grace_window_the_broker_is_not_even_asked():
    trade = _open_trade("FER", _LONG_AGO)  # first seen missing just now
    broker, close_unknown, _ = await _cycle(trade, flat=True)
    broker.confirm_symbol_flat.assert_not_awaited()
    close_unknown.assert_not_called()


@pytest.mark.asyncio
async def test_a_held_row_is_reported_once_per_episode_and_rechecked_each_cycle():
    trade = _open_trade("FER", _LONG_AGO)
    _past_grace(trade)
    with patch("app.main.logger") as log:
        await _cycle(trade, flat=False)
        await _cycle(trade, flat=False)
    assert log.error.call_count == 1  # not once per poll cycle

    # ...and once the broker does say flat, the episode ends and the row closes.
    _, close_unknown, _ = await _cycle(trade, flat=True)
    close_unknown.assert_awaited_once()
    assert str(trade.id) not in m._close_held_logged
