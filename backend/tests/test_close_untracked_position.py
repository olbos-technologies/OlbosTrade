"""
POST /api/trade-desk/close-untracked-position — closes a live broker equity
position that has no matching DB Trade row (e.g. a fill lost to the
order-placement timeout bug: asyncio.shield lets the real IBKR request keep
running after the coordinator's wait_for gives up, so it can fill after the
app already treated it as failed and never recorded a Trade).

Sourced entirely from the broker's live position — never a client-supplied
quantity/side — since that's the only trusted source of truth for a symbol
with no DB row.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.routes.trade_desk as td
from app.api.routes.trade_desk import (

    CloseUntrackedPositionRequest, close_untracked_position,
)

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()


def _no_tracked_trade_session():
    result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    return session


def _tracked_trade_session(trade_id="22222222-2222-2222-2222-222222222222"):
    trade = MagicMock()
    trade.id = trade_id
    result = MagicMock(scalar_one_or_none=MagicMock(return_value=trade))
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    return session


def _broker_position(symbol="MRVL", quantity=93, asset_type="equity"):
    return MagicMock(symbol=symbol, underlying=symbol, quantity=quantity, asset_type=asset_type)


@pytest.mark.asyncio
async def test_tracked_trade_already_open_raises_409():
    session = _tracked_trade_session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        with pytest.raises(Exception):
            await close_untracked_position(CloseUntrackedPositionRequest(symbol="MRVL"), _CONN)


@pytest.mark.asyncio
async def test_no_broker_position_raises_404():
    session = _no_tracked_trade_session()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        with pytest.raises(Exception):
            await close_untracked_position(CloseUntrackedPositionRequest(symbol="MRVL"), _CONN)


@pytest.mark.asyncio
async def test_zero_quantity_position_raises_404():
    session = _no_tracked_trade_session()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_broker_position(quantity=0)])
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        with pytest.raises(Exception):
            await close_untracked_position(CloseUntrackedPositionRequest(symbol="MRVL"), _CONN)


@pytest.mark.asyncio
async def test_options_position_raises_400():
    session = _no_tracked_trade_session()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_broker_position(asset_type="option")])
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        with pytest.raises(Exception):
            await close_untracked_position(CloseUntrackedPositionRequest(symbol="MRVL"), _CONN)


@pytest.mark.asyncio
async def test_long_position_submits_sell_and_cancels_bracket():
    session = _no_tracked_trade_session()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_broker_position(quantity=93)])
    broker.cancel_open_orders = AsyncMock(return_value=1)
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="filled", order_id="ord-9", fill_price=284.5,
    ))
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td, "_log_execution", new=AsyncMock()) as log_mock:
        out = await close_untracked_position(CloseUntrackedPositionRequest(symbol="mrvl"), _CONN)

    broker.cancel_open_orders.assert_awaited_once_with("MRVL")
    broker.place_equity_order.assert_awaited_once()
    assert broker.place_equity_order.await_args.kwargs["side"] == "SELL"
    assert broker.place_equity_order.await_args.kwargs["qty"] == 93
    assert broker.place_equity_order.await_args.kwargs["ticker"] == "MRVL"
    assert out["action"] == "SELL"
    assert out["trade_id"] is None
    assert out["closed_by"] == "manual_untracked"
    log_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_short_position_submits_buy():
    session = _no_tracked_trade_session()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_broker_position(quantity=-20)])
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="submitted", order_id="ord-10", fill_price=None,
    ))
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td, "_log_execution", new=AsyncMock()):
        out = await close_untracked_position(CloseUntrackedPositionRequest(symbol="MRVL"), _CONN)

    assert broker.place_equity_order.await_args.kwargs["side"] == "BUY"
    assert broker.place_equity_order.await_args.kwargs["qty"] == 20
    assert out["status"] == "submitted"


@pytest.mark.asyncio
async def test_broker_rejection_raises_502():
    session = _no_tracked_trade_session()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_broker_position()])
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="rejected", order_id=None, fill_price=None,
    ))
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        with pytest.raises(Exception):
            await close_untracked_position(CloseUntrackedPositionRequest(symbol="MRVL"), _CONN)
