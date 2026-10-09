"""Emergency stopping must work on every broker, and must not claim success
it has not verified.

Cancellation used to live inside `if hasattr(self._broker, "ib")`. On Alpaca —
which has no `.ib` — the entire step was skipped: no orders cancelled, no
error recorded, `orders_cancelled` left at 0. A caller could not distinguish
"there was nothing to cancel" from "never tried", and the working orders
stayed live at the broker while the positions underneath them were flattened.
A resting entry filling after the stop re-opens the exposure the stop existed
to remove.

The second theme here is that accepted closing orders are not closed
positions. `positions_flattened` counts every non-rejected result, so an
accepted-but-unfilled market order, a partial fill and a venue cancellation
all increment it. Only re-reading the broker answers "is the account out".
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.broker.broker_interface import CancelSweep
from app.services.kill_switch import KillSwitch

pytestmark = pytest.mark.asyncio


def _db():
    s = AsyncMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=False)
    s.add = MagicMock()
    s.commit = AsyncMock()
    return s


def _pos(symbol="AAPL", qty=100):
    return NS(quantity=qty, strike=Decimal("0"), underlying=symbol, symbol=symbol,
              expiration=date.today(), option_type="call")


def _broker(*, sweep=None, first=None, after=None, flatten_status="filled"):
    b = MagicMock()
    b.cancel_all_open_orders = AsyncMock(
        return_value=sweep if sweep is not None else CancelSweep(requested=0, cancelled=0)
    )
    b.get_positions = AsyncMock(side_effect=[
        first if first is not None else [],
        after if after is not None else [],
        after if after is not None else [],
    ])
    b.place_order = AsyncMock(return_value=MagicMock(status=flatten_status, message=""))
    b.place_equity_order = AsyncMock(
        return_value=MagicMock(status=flatten_status, message="")
    )
    return b


async def _engage(broker, reason="test"):
    ks = KillSwitch()
    ks.configure(broker, MagicMock())
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db()):
        return ks, await ks.engage(reason)


# ── Broker neutrality ──────────────────────────────────────────────────────

async def test_cancellation_goes_through_the_neutral_interface_not_dot_ib():
    """The regression guard. Cancellation must not route through `.ib`.

    Asserting `not hasattr(broker, "ib")` would prove nothing — a MagicMock
    conjures any attribute on access, which is part of why the IBKR-only
    branch looked covered. What is checkable is that the IBKR entry point was
    never *called* and the neutral one was.
    """
    broker = _broker(sweep=CancelSweep(requested=3, cancelled=3))

    _, res = await _engage(broker)

    broker.cancel_all_open_orders.assert_awaited_once()
    broker.ib.openOrders.assert_not_called()
    broker.ib.cancelOrder.assert_not_called()
    assert res["orders_cancelled"] == 3
    assert res["orders_requested"] == 3
    assert res["cancel_attempted"] is True


async def test_working_orders_are_cancelled_even_with_no_positions():
    """A resting entry, or a bracket leg whose position already closed, has no
    position to drive a per-symbol loop — and is exactly what re-opens
    exposure after a stop."""
    broker = _broker(sweep=CancelSweep(requested=2, cancelled=2), first=[], after=[])

    _, res = await _engage(broker)

    broker.cancel_all_open_orders.assert_awaited_once()
    assert res["orders_cancelled"] == 2
    assert res["reconciliation"]["flat"] is True


# ── Unresolved cancellations are never hidden ──────────────────────────────

async def test_an_order_that_did_not_cancel_is_reported_not_dropped():
    broker = _broker(sweep=CancelSweep(
        requested=3, cancelled=2, unresolved={"ord-9": "HTTPError: 500"},
    ))

    _, res = await _engage(broker)

    assert res["orders_cancelled"] == 2
    assert res["unresolved_orders"] == {"ord-9": "HTTPError: 500"}
    assert any("ord-9" in e for e in res["errors"])
    # An order still live at the broker means the account is not flat, even
    # though every position closed.
    assert res["reconciliation"]["flat"] is False


async def test_failure_to_even_list_orders_is_not_silent_success():
    broker = _broker(sweep=CancelSweep(enumeration_error="ConnectionError: refused"))

    _, res = await _engage(broker)

    assert res["orders_cancelled"] == 0
    assert any("cancel_enumerate" in e for e in res["errors"]), (
        "an unreadable order book reported as 0 cancelled is indistinguishable "
        "from having had nothing to cancel"
    )


async def test_a_broker_outage_during_cancellation_is_recorded():
    broker = _broker()
    broker.cancel_all_open_orders = AsyncMock(side_effect=ConnectionError("broker down"))

    _, res = await _engage(broker)

    assert res["cancel_attempted"] is False
    assert any("cancel_orders" in e for e in res["errors"])


# ── Reconciliation: flat is verified, never inferred ───────────────────────

async def test_residual_position_means_not_flat_even_though_orders_were_accepted():
    """Every closing order came back accepted; the position is still there."""
    broker = _broker(first=[_pos("AAPL", 100)], after=[_pos("AAPL", 40)],
                     flatten_status="partial")

    _, res = await _engage(broker)

    assert res["positions_flattened"] == 1, "a partial fill still counts as flattened"
    assert res["reconciliation"]["flat"] is False, (
        "accepted closing orders were treated as a closed position"
    )
    assert res["reconciliation"]["residual_positions"] == [
        {"symbol": "AAPL", "quantity": "40"}
    ]


async def test_an_unreadable_broker_leaves_flatness_unknown_not_false():
    """`None` and `False` are different: one means 'still exposed', the other
    means 'nobody knows'. Reporting unknown as flat would be the dangerous
    direction, and reporting it as not-flat would hide that the check failed."""
    broker = _broker(first=[_pos()])
    broker.get_positions = AsyncMock(
        side_effect=[[_pos()], ConnectionError("broker down")]
    )

    _, res = await _engage(broker)

    assert res["reconciliation"]["performed"] is True
    assert res["reconciliation"]["flat"] is None
    assert any("reconcile" in e for e in res["errors"])


async def test_zero_quantity_rows_do_not_count_as_residual():
    broker = _broker(first=[_pos("AAPL", 100)], after=[_pos("AAPL", 0)])

    _, res = await _engage(broker)

    assert res["reconciliation"]["flat"] is True


# ── Repeated engagement ────────────────────────────────────────────────────

async def test_re_engaging_re_verifies_instead_of_reporting_reassurance():
    """Re-engaging is how an operator asks "did it actually work?" after a
    stop that reported errors. Answering `already_engaged` and nothing else
    reads as success while exposure sits untouched."""
    broker = _broker(first=[_pos("AAPL", 100)], after=[_pos("AAPL", 100)])
    ks, first = await _engage(broker)
    assert first["reconciliation"]["flat"] is False

    broker.get_positions = AsyncMock(return_value=[_pos("AAPL", 100)])
    second = await ks.engage("again")

    assert second["status"] == "already_engaged"
    assert second["reconciliation"]["performed"] is True
    assert second["reconciliation"]["flat"] is False
    assert second["reconciliation"]["residual_positions"] == [
        {"symbol": "AAPL", "quantity": "100"}
    ]


async def test_re_engaging_after_a_clean_stop_confirms_flat():
    broker = _broker(first=[_pos()], after=[])
    ks, _ = await _engage(broker)

    broker.get_positions = AsyncMock(return_value=[])
    second = await ks.engage("again")

    assert second["reconciliation"]["flat"] is True


async def test_re_engaging_does_not_resume_trading():
    broker = _broker()
    ks, _ = await _engage(broker)
    await ks.engage("again")
    assert ks.is_engaged is True


# ── Closing orders state that they close ───────────────────────────────────

async def test_option_flatten_legs_are_marked_as_closing():
    """position_intent is not derivable from side: SELL is sell_to_open on a
    new short and sell_to_close on a long being flattened. Alpaca rejects the
    mismatch, so a flatten sent with an opening intent does nothing."""
    option = NS(quantity=2, strike=Decimal("450"), underlying="SPY",
                symbol="SPY450C", expiration=date.today(), option_type="call")
    broker = _broker(first=[option], after=[])

    await _engage(broker)

    broker.place_order.assert_awaited_once()
    order = broker.place_order.await_args.args[0]
    assert [leg.intent for leg in order.legs] == ["close"]
    assert [leg.action for leg in order.legs] == ["SELL"]
