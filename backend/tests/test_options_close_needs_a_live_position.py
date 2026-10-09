"""
close_options_trade() refuses to "close" a position the broker is not holding.

Everything it submits is read from the DB Trade row — strikes, expiration,
quantity. A closing combo for a position that does not exist is an OPENING
trade in the opposite direction: it CREATES exposure instead of removing it.

That is reachable. paper_trade.py emits DB-only rows (source="db_only",
tracked=True) for open Trade rows with no matching broker position, and they
carry a valid id and a valid spread_type, so they look exactly like a closeable
position to every caller.

close_equity_trade() has guarded this since 2026-08-26 — it sources side and
size from get_equity_positions() and raises "already flat at the broker" when
the symbol is not held, after DB/broker quantity drift was root-caused in
production. The options path had no equivalent until PR #64. These tests pin
the asymmetry closed.

The guard is deliberately COARSE: any live, non-zero option position on the
underlying. It catches "there is nothing here", which is the hazard, without
matching leg-by-leg — strike and expiration types vary across broker adapters,
and an over-strict comparison would refuse legitimate risk-REDUCING closes,
which is its own harm. These tests assert that looseness on purpose.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.position_rotation import close_options_trade


def _trade():
    return SimpleNamespace(
        id=uuid4(), underlying="SPY", spread_type="put", quantity=1,
        short_strike=Decimal("400"), long_strike=Decimal("395"),
        expiration=date(2026, 12, 18), strategy="bull_put",
    )


def _option(underlying="SPY", qty=-1, asset_type="option", strike=Decimal("400")):
    return SimpleNamespace(
        symbol=f"{underlying}   261218P00400000", underlying=underlying,
        strike=strike, expiration=date(2026, 12, 18), option_type="put",
        quantity=qty, avg_cost=Decimal("1.0"), asset_type=asset_type,
    )


def _broker(positions):
    b = SimpleNamespace()
    b.get_positions = AsyncMock(return_value=positions)
    b.cancel_open_orders = AsyncMock(return_value=0)
    b.place_order = AsyncMock(return_value=SimpleNamespace(
        status="filled", fill_price=Decimal("1.0"), order_id="x",
    ))
    return b


@pytest.mark.asyncio
async def test_it_refuses_when_the_broker_holds_nothing():
    """The db_only case: an open DB row, a flat broker."""
    broker = _broker([])

    with pytest.raises(RuntimeError, match="not fully live"):
        await close_options_trade(_trade(), broker=broker, closed_by="manual")

    # The point of the guard: nothing was sent.
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_it_refuses_when_the_only_position_is_a_different_underlying():
    broker = _broker([_option(underlying="QQQ")])

    with pytest.raises(RuntimeError, match="not fully live"):
        await close_options_trade(_trade(), broker=broker, closed_by="manual")
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_zero_quantity_row_does_not_count_as_held():
    """IBKR returns zero-quantity rows; they are not positions."""
    broker = _broker([_option(qty=0)])

    with pytest.raises(RuntimeError, match="not fully live"):
        await close_options_trade(_trade(), broker=broker, closed_by="manual")
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_equity_row_on_the_same_underlying_does_not_count():
    """Holding SPY shares is not holding a SPY spread."""
    broker = _broker([_option(asset_type="equity")])

    with pytest.raises(RuntimeError, match="not fully live"):
        await close_options_trade(_trade(), broker=broker, closed_by="manual")
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_it_proceeds_when_both_legs_are_actually_held():
    """The guard must not block a real close — that would be its own harm."""
    broker = _broker([_option(qty=-1), _option(strike=Decimal("395"), qty=1)])

    await close_options_trade(_trade(), broker=broker, closed_by="manual")

    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_position_at_another_strike_is_not_this_spread():
    """Replaces an earlier test that asserted the opposite.

    The first version of this guard checked only that SOME option on the
    underlying was held, and this test asserted that looseness as deliberate —
    the reasoning being that strict leg matching might refuse legitimate
    closes. Review pointed out what that missed: the combo size still came from
    trade.quantity, so "something is held" permits a 2-lot close against a
    1-lot position, which opens exposure in the opposite direction. Refusing is
    recoverable; reversing into a new position is not.
    """
    other_strike = _option()
    other_strike.strike = Decimal("380")
    broker = _broker([other_strike])

    with pytest.raises(RuntimeError, match="not fully live"):
        await close_options_trade(_trade(), broker=broker, closed_by="manual")
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_live_leg_is_not_enough():
    """A combo needs both legs; one alone is not a close of anything."""
    broker = _broker([_option()])  # only the 400 short leg

    with pytest.raises(RuntimeError, match="not fully live"):
        await close_options_trade(_trade(), broker=broker, closed_by="manual")
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_it_closes_the_LIVE_size_not_the_DB_size():
    """The drift case: DB says 2, broker holds 1.

    Submitting 2 would close the one held and OPEN one the other way. This is
    the same failure close_equity_trade() was hardened against on 2026-08-26.
    """
    trade = _trade()
    trade.quantity = 2
    broker = _broker([_option(qty=-1), _option(strike=Decimal("395"), qty=1)])

    await close_options_trade(trade, broker=broker, closed_by="manual")

    order = broker.place_order.await_args.args[0]
    assert [l.quantity for l in order.legs] == [1, 1], (
        f"submitted {[l.quantity for l in order.legs]} against a 1-lot live "
        f"position — an oversized close reverses into new exposure"
    )


@pytest.mark.asyncio
async def test_mismatched_legs_close_the_smaller_side():
    """short=2 long=1 closes 1: the residual is naked, but bounded."""
    trade = _trade()
    trade.quantity = 2
    broker = _broker([_option(qty=-2), _option(strike=Decimal("395"), qty=1)])

    await close_options_trade(trade, broker=broker, closed_by="manual")

    order = broker.place_order.await_args.args[0]
    assert [l.quantity for l in order.legs] == [1, 1]
