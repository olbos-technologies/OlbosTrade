"""
Broker execution-safety tests (Batch 2) for IBKRClient.place_order and the
kill-switch flatten path.

These use a lightweight fake `ib` object so we can assert exactly what order
objects / combo contracts get constructed, without a live IB Gateway. ib_insync's
real LimitOrder / MarketOrder / ComboLeg / Contract classes are used so the
assertions reflect real attribute names.

Run with: pytest tests/test_ibkr_execution.py -v
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import asyncio

from app.broker.broker_interface import SpreadLeg, SpreadOrder
from app.broker.ibkr_client import IBKRClient


# ── Fakes ──────────────────────────────────────────────────────────────────────

class _OrderStatus:
    def __init__(self, status="Submitted", filled=0, avg=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avg


class _Event:
    """Stand-in for ib_insync Event — ignores handler registration."""
    def __iadd__(self, _fn):
        return self


class _Trade:
    def __init__(self, order, order_status):
        self.order = order
        self.orderStatus = order_status
        self.statusEvent = _Event()
        self.fillEvent = _Event()


class FakeIB:
    """Minimal ib_insync.IB replacement for place_order tests."""
    def __init__(self, status_script):
        # status_script: list of _OrderStatus, one returned per placeOrder call
        self._script = list(status_script)
        self.placed = []      # list of (contract, order)
        self.cancelled = []
        self.trades = []
        self._conid = 1000

    def isConnected(self):
        return True

    async def qualifyContractsAsync(self, opt):
        self._conid += 1
        opt.conId = self._conid
        return [opt]

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        scripted = self._script.pop(0) if self._script else _OrderStatus()
        st = _OrderStatus(scripted.status, scripted.filled, scripted.avgFillPrice)
        order.orderId = len(self.placed)
        trade = _Trade(order, st)
        self.trades.append(trade)
        return trade

    def cancelOrder(self, order):
        self.cancelled.append(order)
        # Default fake acknowledges cancellation. Race tests explicitly
        # control _await_order when acknowledgement is delayed or missing.
        for trade in self.trades:
            if trade.order is order:
                trade.orderStatus.status = "Cancelled"


def _make_client(status_script):
    c = IBKRClient()
    c._connected = True
    c.ib = FakeIB(status_script)
    return c


def _credit_spread(limit=Decimal("1.50"), order_type="LMT", q_short=1, q_long=1):
    return SpreadOrder(
        strategy="bull_put_spread",
        underlying="SPY",
        legs=[
            SpreadLeg(symbol="SPY", strike=Decimal("450"), expiration=date(2025, 1, 17),
                      option_type="put", action="SELL", quantity=q_short),
            SpreadLeg(symbol="SPY", strike=Decimal("445"), expiration=date(2025, 1, 17),
                      option_type="put", action="BUY", quantity=q_long),
        ],
        limit_price=limit,
        order_type=order_type,
        time_in_force="DAY",
    )


# Patch out the real 1-2s sleeps so tests run instantly.
@pytest.fixture(autouse=True)
def _clean_account_for_order_mechanics():
    # These tests exercise order construction/fill/halt mechanics. Reconciliation
    # vetoes are driven separately with the real gate and controlled snapshots.
    from types import SimpleNamespace
    with patch("app.services.position_reconciler.PositionReconciler.check",
               new=AsyncMock(return_value=SimpleNamespace(clean=True))):
        yield


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("app.broker.ibkr_client.asyncio.sleep", new_callable=AsyncMock):
        yield


@pytest.fixture
def entry_halt(monkeypatch):
    from app.services.kill_switch import KillSwitch
    switch = KillSwitch()
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", switch)
    return switch


async def _send_entry(client, asset):
    if asset == "options":
        return await client.place_order(_credit_spread(q_short=5, q_long=5))
    return await client.place_equity_order(
        "SPY", 5, "BUY", order_type="limit", limit_price=450,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_halt_during_qualification_prevents_first_send(asset, entry_halt):
    from app.services.execution_interlock import EntryHalted
    client = _make_client([])
    qualify = client.ib.qualifyContractsAsync

    async def qualify_then_halt(contract):
        result = await qualify(contract)
        entry_halt._engaged = True
        return result

    client.ib.qualifyContractsAsync = qualify_then_halt
    with pytest.raises(EntryHalted):
        await _send_entry(client, asset)
    assert not client.ib.placed


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_replacement_waits_for_cancel_ack(asset, entry_halt):
    client = _make_client([_OrderStatus(), _OrderStatus("Filled", 5, 1.5)])
    cancelling, acknowledge = asyncio.Event(), asyncio.Event()
    calls = 0

    async def await_order(trade, qty, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "timeout", 0, 0
        if calls == 2:
            cancelling.set()
            await acknowledge.wait()
            return "cancelled", 0, 0
        return "filled", 5, 1.5

    client._await_order = await_order
    task = asyncio.create_task(_send_entry(client, asset))
    try:
        await asyncio.wait_for(cancelling.wait(), 1)
        assert len(client.ib.placed) == 1
        acknowledge.set()
        result = await task
        assert result.status == "filled"
        assert len(client.ib.placed) == 2
    finally:
        acknowledge.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
@pytest.mark.parametrize("cancel_result", ["timeout", "filled", "partial"])
@pytest.mark.parametrize("initial_filled", [0, 2])
async def test_unknown_or_filled_cancel_never_replaces(asset, cancel_result, initial_filled, entry_halt):
    client = _make_client([_OrderStatus()])
    fills = {"timeout": 0, "filled": 5, "partial": 2}
    client._await_order = AsyncMock(side_effect=[
        ("timeout", initial_filled, 1.5), (cancel_result, max(initial_filled, fills[cancel_result]), 1.5),
    ])
    result = await _send_entry(client, asset)
    assert len(client.ib.placed) == 1
    assert len(client.ib.cancelled) == 1
    if cancel_result == "timeout":
        assert result.status == "pending"
    elif asset == "options":
        assert result.filled_quantity == max(initial_filled, fills[cancel_result])


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_halt_after_cancel_ack_prevents_retry(asset, entry_halt):
    from app.services.execution_interlock import EntryHalted
    client = _make_client([_OrderStatus()])
    calls = 0

    async def await_order(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "timeout", 0, 0
        entry_halt._engaged = True
        return "cancelled", 0, 0

    client._await_order = await_order
    with pytest.raises(EntryHalted):
        await _send_entry(client, asset)
    assert len(client.ib.placed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_inactive_cancel_is_unknown_not_permission_to_retry(asset, entry_halt):
    client = _make_client([_OrderStatus("Inactive")])
    client.ib.cancelOrder = lambda order: client.ib.cancelled.append(order)
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
         patch("app.broker.ibkr_client.CANCEL_CONFIRM_TIMEOUT_SECONDS", 0):
        result = await _send_entry(client, asset)
    assert result.status == "pending"
    assert len(client.ib.placed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_real_status_fill_during_cancel_prevents_retry(asset, entry_halt):
    client = _make_client([_OrderStatus()])

    def cancel_then_fill(order):
        client.ib.cancelled.append(order)
        status = client.ib.trades[0].orderStatus
        status.status, status.filled, status.avgFillPrice = "Filled", 5, 1.5

    client.ib.cancelOrder = cancel_then_fill
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
        result = await _send_entry(client, asset)
    assert result.status == "filled"
    assert len(client.ib.placed) == 1


# ── Combo sizing (task 4) ───────────────────────────────────────────────────────

class TestComboSizing:
    def test_equal_quantities_ratio_one(self):
        legs = _credit_spread(q_short=5, q_long=5).legs
        qty, ratios = IBKRClient._combo_sizing(legs)
        assert qty == 5
        assert ratios == [1, 1]

    def test_ratio_spread_normalised(self):
        legs = _credit_spread(q_short=5, q_long=10).legs
        qty, ratios = IBKRClient._combo_sizing(legs)
        assert qty == 5
        assert ratios == [1, 2]

    def test_single_contract(self):
        legs = _credit_spread(q_short=1, q_long=1).legs
        qty, ratios = IBKRClient._combo_sizing(legs)
        assert qty == 1
        assert ratios == [1, 1]


# ── MKT vs LMT + single combo BAG (tasks 1, 3, 4) ────────────────────────────────

class TestPlaceOrderConstruction:
    @pytest.mark.asyncio
    async def test_multileg_sent_as_single_bag_combo(self):
        client = _make_client([_OrderStatus("Filled", 1, 1.50)])
        await client.place_order(_credit_spread())

        # Exactly ONE order placed — never legged out into separate orders.
        assert len(client.ib.placed) == 1
        contract, order = client.ib.placed[0]
        assert contract.secType == "BAG"
        assert len(contract.comboLegs) == 2

    @pytest.mark.asyncio
    async def test_lmt_credit_uses_negative_net_price(self):
        client = _make_client([_OrderStatus("Filled", 1, 1.50)])
        await client.place_order(_credit_spread(limit=Decimal("1.50")))

        _, order = client.ib.placed[0]
        assert order.orderType == "LMT"
        assert order.action == "BUY"
        # Credit of 1.50 → BUY combo at a net price of -1.50 (we receive credit).
        assert order.lmtPrice == -1.50
        assert order.totalQuantity == 1

    @pytest.mark.asyncio
    async def test_mkt_builds_market_order(self):
        client = _make_client([_OrderStatus("Filled", 1, 1.50)])
        await client.place_order(_credit_spread(order_type="MKT"))

        _, order = client.ib.placed[0]
        assert order.orderType == "MKT"
        # Market order must not carry a real limit price — ib_insync leaves it as
        # the UNSET sentinel (a huge double), never a meaningful number.
        assert order.lmtPrice >= 1e307

    @pytest.mark.asyncio
    async def test_combo_ratio_sizing_applied(self):
        client = _make_client([_OrderStatus("Filled", 5, 1.50)])
        await client.place_order(_credit_spread(q_short=5, q_long=10))

        contract, order = client.ib.placed[0]
        assert order.totalQuantity == 5
        assert [leg.ratio for leg in contract.comboLegs] == [1, 2]

    @pytest.mark.asyncio
    async def test_single_leg_uses_plain_option_not_bag(self):
        client = _make_client([_OrderStatus("Filled", 1, 2.00)])
        single = SpreadOrder(
            strategy="kill_switch_flatten", underlying="SPY",
            legs=[SpreadLeg(symbol="SPY", strike=Decimal("450"),
                            expiration=date(2025, 1, 17), option_type="put",
                            action="BUY", quantity=1)],
            limit_price=Decimal("2.00"), order_type="LMT", time_in_force="DAY",
        )
        await client.place_order(single)

        contract, order = client.ib.placed[0]
        assert contract.secType == "OPT"          # plain option, not a BAG
        assert order.action == "BUY"              # uses the leg's own action
        assert order.lmtPrice == 2.00             # single-leg price used as-is


# ── Partial fills (task 5) ───────────────────────────────────────────────────────

class TestPartialFills:
    @pytest.mark.asyncio
    async def test_full_fill_reports_filled(self):
        client = _make_client([_OrderStatus("Filled", 5, 1.50)])
        result = await client.place_order(_credit_spread(q_short=5, q_long=5))
        assert result.status == "filled"
        assert result.filled_quantity == 5
        assert result.remaining_quantity == 0

    @pytest.mark.asyncio
    async def test_partial_fill_reported_not_filled(self):
        # Order shows 3 of 5 filled but still "Submitted" → must be PARTIAL,
        # never reported as a clean fill.
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
            client = _make_client([_OrderStatus("Submitted", 3, 1.48)])
            result = await client.place_order(_credit_spread(q_short=5, q_long=5))
        assert result.status == "partial"
        assert result.filled_quantity == 3
        assert result.remaining_quantity == 2
        # The residual must be cancelled so it does not keep working.
        assert len(client.ib.cancelled) == 1

    @pytest.mark.asyncio
    async def test_cancelled_with_fill_is_partial(self):
        client = _make_client([_OrderStatus("Cancelled", 2, 1.45)])
        result = await client.place_order(_credit_spread(q_short=5, q_long=5))
        assert result.status == "partial"
        assert result.filled_quantity == 2

    @pytest.mark.asyncio
    async def test_rejected_no_fill(self):
        client = _make_client([_OrderStatus("Rejected", 0, 0.0)])
        result = await client.place_order(_credit_spread())
        assert result.status == "rejected"
        assert result.filled_quantity == 0

    @pytest.mark.asyncio
    async def test_lmt_timeout_retries_then_cancels(self):
        # Every attempt times out with no fill → should retry (LMT) then cancel.
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
             patch("app.broker.ibkr_client._max_retries", return_value=2):
            client = _make_client([_OrderStatus("Submitted", 0, 0.0)] * 3)
            result = await client.place_order(_credit_spread())
        assert result.status == "cancelled"
        assert len(client.ib.placed) == 3       # initial + 2 retries
        assert len(client.ib.cancelled) == 3

    @pytest.mark.asyncio
    async def test_mkt_no_fill_stays_working(self):
        # A market flatten that has not filled must be left working, not cancelled.
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
            client = _make_client([_OrderStatus("Submitted", 0, 0.0)])
            result = await client.place_order(_credit_spread(order_type="MKT"))
        assert result.status == "submitted"
        assert len(client.ib.cancelled) == 0
        assert len(client.ib.placed) == 1       # MKT does not retry


# ── Equity bracket orders: parent-ack race (Error 135) ──────────────────────────
#
# Regression coverage for a live production bug: the take-profit/stop-loss
# children reference the parent order via parentId, but nothing waited for
# IBKR to actually register the parent before the children were submitted --
# so the Gateway would reject both children with "Error 135: Can't find order
# with id" a few hundred ms later, leaving the entry order dangling with no
# protective exits attached. Confirmed twice in production logs on the same day
# (same failure signature both times: children cancelled ~150-300ms after
# PendingSubmit, parent timed out at 60s unfilled).
#
# Unlike FakeIB above (which reassigns orderId on every placeOrder call --
# harmless for the single-order combo path it was built for, but it would
# clobber the explicit parentId linkage this test needs to verify), this fake
# preserves whatever orderId the caller set beforehand, matching real
# ib_insync/IBKR semantics for pre-assigned order IDs.

class _EquityFakeIB:
    def __init__(self, parent_status: str):
        self.placed: list = []
        self._parent_status = parent_status
        self.client = MagicMock()
        self.client.getReqId.side_effect = iter(range(100, 200))

    def isConnected(self):
        return True

    async def qualifyContractsAsync(self, contract):
        contract.conId = 999
        return [contract]

    def placeOrder(self, contract, order):
        # Preserve the caller-assigned orderId (real ib_insync does the same)
        # instead of reassigning it, so parentId linkage stays verifiable.
        is_parent = not getattr(order, "parentId", 0)
        status = self._parent_status if is_parent else "Submitted"
        trade = _Trade(order, _OrderStatus(status, 0, 0.0))
        self.placed.append(trade)
        return trade


def _make_equity_client(parent_status: str) -> IBKRClient:
    c = IBKRClient()
    c._connected = True
    c.ib = _EquityFakeIB(parent_status)
    return c


@pytest.mark.asyncio
async def test_halt_during_bracket_ack_does_not_transmit_children(entry_halt):
    client = _make_equity_client(parent_status="")

    def cancel(order):
        client.ib.placed[0].orderStatus.status = "Cancelled"

    async def acknowledge_then_halt(*args):
        client.ib.placed[0].orderStatus.status = "PreSubmitted"
        entry_halt._engaged = True

    client.ib.cancelOrder = MagicMock(side_effect=cancel)
    with patch("app.broker.ibkr_client.asyncio.sleep", side_effect=acknowledge_then_halt):
        result = await client.place_equity_order(
            "SPY", 5, "BUY", order_type="market", stop=440, take_profit=460,
        )
    assert len(client.ib.placed) == 1
    client.ib.cancelOrder.assert_called_once()
    assert result.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("child_ack", [False, True])
async def test_bracket_replacement_requires_child_cancellation(entry_halt, child_ack):
    client = _make_equity_client(parent_status="PreSubmitted")
    send = client.ib.placeOrder

    def place(contract, order):
        if len(client.ib.placed) == 3:
            assert all(t.orderStatus.status == "Cancelled" for t in client.ib.placed)
        return send(contract, order)

    def cancel(order):
        for trade in client.ib.placed:
            if trade.order is order:
                trade.orderStatus.status = "Cancelled" if child_ack or not order.parentId else "PendingCancel"

    client.ib.placeOrder = place
    client.ib.cancelOrder = MagicMock(side_effect=cancel)
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
         patch("app.broker.ibkr_client.CANCEL_CONFIRM_TIMEOUT_SECONDS", 0), \
         patch("app.broker.ibkr_client._max_retries", return_value=1):
        result = await client.place_equity_order(
            "SPY", 5, "BUY", order_type="limit", limit_price=450, stop=440, take_profit=460,
        )
    assert len(client.ib.placed) == (6 if child_ack else 3)
    assert result.status == ("cancelled" if child_ack else "pending")


@pytest.mark.asyncio
async def test_late_bracket_fill_keeps_protective_children(entry_halt):
    client = _make_equity_client(parent_status="PreSubmitted")

    def cancel_then_fill(order):
        parent = client.ib.placed[0]
        parent.orderStatus.status = "Filled"
        parent.orderStatus.filled = 5
        parent.orderStatus.avgFillPrice = 450

    client.ib.cancelOrder = MagicMock(side_effect=cancel_then_fill)
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
        result = await client.place_equity_order(
            "SPY", 5, "BUY", order_type="limit", limit_price=450, stop=440, take_profit=460,
        )
    assert result.status == "filled"
    assert len(client.ib.placed) == 3
    client.ib.cancelOrder.assert_called_once_with(client.ib.placed[0].order)
    assert all(child.orderStatus.status == "Submitted" for child in client.ib.placed[1:])


class TestEquityBracketAck:
    @pytest.mark.asyncio
    async def test_children_wait_for_parent_ack_before_sending(self):
        # Parent acknowledges immediately (non-empty status) -- children should
        # be sent right away, correctly linked via parentId.
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
            client = _make_equity_client(parent_status="PreSubmitted")
            await client.place_equity_order(
                "NVDA", 10, "BUY", order_type="market",
                stop=190.0, take_profit=210.0,
            )
        placed = client.ib.placed
        assert len(placed) == 3   # parent + take-profit + stop-loss
        parent = placed[0].order
        tp, sl = placed[1].order, placed[2].order
        assert parent.parentId in (0, None)
        assert tp.parentId == parent.orderId
        assert sl.parentId == parent.orderId

    @pytest.mark.asyncio
    async def test_unacknowledged_parent_times_out_then_still_sends_children(self):
        # Parent never acknowledges (status stays empty) -- must not hang
        # forever; bounded by BRACKET_ACK_TIMEOUT_SECONDS, then proceeds anyway.
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
             patch("app.broker.ibkr_client.BRACKET_ACK_TIMEOUT_SECONDS", 0.05):
            client = _make_equity_client(parent_status="")
            await client.place_equity_order(
                "NVDA", 10, "BUY", order_type="market",
                stop=190.0, take_profit=210.0,
            )
        # Children still get sent (matching current behaviour: log + proceed
        # rather than silently dropping the bracket's protective exits).
        assert len(client.ib.placed) == 3


# ── Equity LMT retry-and-reprice (mirrors options' place_order behavior) ────────
#
# Regression coverage for a real production bug: equity LMT orders that timed
# out unfilled were given up on after a single attempt and left "working" at
# IBKR indefinitely, with only a DB-side 30-minute grace period marking the
# trade cancelled (never telling the broker to cancel the resting order).
# Confirmed in production: AMZN/NVDA/QQQ timed out on effectively every
# attempt for days (IBKR's free delayed 15-min data feed means the "live"
# quote used to set the limit is itself already stale, and faster-moving
# names drift past a stale limit within that window). place_equity_order now
# retries like place_order already does: cancel, reprice more aggressively,
# resubmit, up to _max_retries() times.

class TestEquityLimitRetry:
    @pytest.mark.asyncio
    async def test_lmt_timeout_retries_then_cancels(self):
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
             patch("app.broker.ibkr_client._max_retries", return_value=2):
            client = _make_client([_OrderStatus("Submitted", 0, 0.0)] * 3)
            result = await client.place_equity_order(
                "AMZN", 74, "BUY", order_type="limit", limit_price=268.00,
            )
        assert result.status == "cancelled"
        assert len(client.ib.placed) == 3        # initial + 2 retries
        assert len(client.ib.cancelled) == 3

    @pytest.mark.asyncio
    async def test_buy_reprices_upward_on_each_retry(self):
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
             patch("app.broker.ibkr_client._max_retries", return_value=2):
            client = _make_client([_OrderStatus("Submitted", 0, 0.0)] * 3)
            await client.place_equity_order(
                "AMZN", 74, "BUY", order_type="limit", limit_price=268.00,
            )
        prices = [order.lmtPrice for _, order in client.ib.placed]
        # BUY retries get more aggressive (higher) each attempt — more likely
        # to be marketable against a price that has drifted upward.
        assert prices[0] == 268.00
        assert prices[1] > prices[0]
        assert prices[2] > prices[1]

    @pytest.mark.asyncio
    async def test_sell_reprices_downward_on_each_retry(self):
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
             patch("app.broker.ibkr_client._max_retries", return_value=2):
            client = _make_client([_OrderStatus("Submitted", 0, 0.0)] * 3)
            await client.place_equity_order(
                "AMZN", 74, "SELL", order_type="limit", limit_price=268.00,
            )
        prices = [order.lmtPrice for _, order in client.ib.placed]
        assert prices[0] == 268.00
        assert prices[1] < prices[0]
        assert prices[2] < prices[1]

    @pytest.mark.asyncio
    async def test_mkt_no_fill_stays_working_not_cancelled(self):
        with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
            client = _make_client([_OrderStatus("Submitted", 0, 0.0)])
            result = await client.place_equity_order(
                "AMZN", 74, "BUY", order_type="market",
            )
        assert result.status == "submitted"
        assert len(client.ib.cancelled) == 0
        assert len(client.ib.placed) == 1        # MKT does not retry

    @pytest.mark.asyncio
    async def test_rejected_no_fill_not_retried(self):
        client = _make_client([_OrderStatus("Rejected", 0, 0.0)])
        result = await client.place_equity_order(
            "AMZN", 74, "BUY", order_type="limit", limit_price=268.00,
        )
        assert result.status == "rejected"
        assert len(client.ib.placed) == 1

    @pytest.mark.asyncio
    async def test_partial_fill_reported_as_filled_not_cancelled(self):
        # Terminated with a real, non-zero fill — must report status="filled"
        # (EquityOrderResult has no "partial" status) rather than "cancelled",
        # which would wrongly suggest nothing happened at the broker.
        client = _make_client([_OrderStatus("Cancelled", 40, 268.50)])
        result = await client.place_equity_order(
            "AMZN", 74, "BUY", order_type="limit", limit_price=268.00,
        )
        assert result.status == "filled"
        assert result.fill_price == Decimal("268.5")


# ── Terminal partial fill, and the broker's own flat-check ──────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_cancel_after_partial_fill_never_replaces_the_filled_portion(asset, entry_halt):
    """The order fills 2 of 5, then the cancel is acknowledged. The filled
    portion exists at the broker, so no replacement may be sent: it would
    double it. How the result is REPORTED differs by path and is pinned here
    so a change is deliberate:

      * options: status "partial" with the real filled/remaining counts;
      * equity: EquityOrderResult has no partial status or quantity field, so
        it is reported "filled" with the real counts only in the message. That
        is a known accounting gap (the trade row is written at the planned
        quantity), not something this change fixes.
    """
    client = _make_client([_OrderStatus()])

    def partial_then_cancelled(order):
        client.ib.cancelled.append(order)
        status = client.ib.trades[0].orderStatus
        status.status, status.filled, status.avgFillPrice = "Cancelled", 2, 1.5

    client.ib.cancelOrder = partial_then_cancelled
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
        result = await _send_entry(client, asset)

    assert len(client.ib.placed) == 1, "a replacement would double the filled portion"
    if asset == "options":
        assert result.status == "partial"
        assert result.filled_quantity == 2 and result.remaining_quantity == 3
    else:
        assert result.status == "filled"
        assert "2/5" in (result.message or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_replacement_is_sent_only_after_a_zero_fill_cancel_acknowledgement(asset, entry_halt):
    """The positive control for the rule above: with an acknowledged, zero-fill
    cancel the limit IS replaced (at a better price). Without this, the
    'never replaces' tests could pass because nothing ever replaces."""
    client = _make_client([_OrderStatus(), _OrderStatus("Filled", 5, 1.4)])
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
         patch("app.broker.ibkr_client._max_retries", return_value=1):
        result = await _send_entry(client, asset)
    assert len(client.ib.placed) == 2
    assert result.status == "filled"
    assert len(client.ib.cancelled) >= 1


class _FakeContract:
    def __init__(self, symbol):
        self.symbol = symbol


class _FakePosition:
    def __init__(self, symbol, position):
        self.contract = _FakeContract(symbol)
        self.position = position


class _FlatIB:
    """Just enough of ib_insync.IB for confirm_symbol_flat."""
    def __init__(self, positions=(), open_symbols=(), fail=None):
        self._positions = list(positions)
        self._open = [MagicMock(contract=_FakeContract(s)) for s in open_symbols]
        self._fail = fail
        self.positions_requested = 0
        self.open_orders_requested = 0

    def isConnected(self):
        return True

    async def reqPositionsAsync(self):
        self.positions_requested += 1
        if self._fail == "positions":
            raise asyncio.TimeoutError()
        return self._positions

    async def reqAllOpenOrdersAsync(self):
        self.open_orders_requested += 1
        if self._fail == "orders":
            raise ConnectionError("gateway dropped")

    def openTrades(self):
        return self._open


def _flat_client(**kw):
    c = IBKRClient()
    c._connected = True
    c.ib = _FlatIB(**kw)
    return c


@pytest.mark.asyncio
async def test_flat_only_when_both_network_reads_succeed_and_are_empty():
    c = _flat_client()
    assert await c.confirm_symbol_flat("AAPL") is True
    # It asked the broker; it did not trust a local cache.
    assert c.ib.positions_requested == 1 and c.ib.open_orders_requested == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    dict(positions=[_FakePosition("AAPL", 10)]),
    dict(positions=[_FakePosition("AAPL", -3)]),   # short is exposure too
    dict(open_symbols=["AAPL"]),                   # resting order, no position
], ids=["long", "short", "working-order"])
async def test_exposure_or_a_working_order_is_not_flat(kwargs):
    assert await _flat_client(**kwargs).confirm_symbol_flat("aapl") is False


@pytest.mark.asyncio
async def test_other_symbols_and_zero_rows_do_not_count_as_exposure():
    c = _flat_client(positions=[_FakePosition("AAPL", 0), _FakePosition("MSFT", 50)],
                     open_symbols=["MSFT"])
    assert await c.confirm_symbol_flat("AAPL") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["positions", "orders"])
async def test_a_failed_or_timed_out_read_is_not_flat(fail):
    """An empty answer from a read that failed is the dangerous one."""
    assert await _flat_client(fail=fail).confirm_symbol_flat("AAPL") is None


@pytest.mark.asyncio
async def test_not_connected_or_no_symbol_is_not_flat():
    c = _flat_client()
    c._connected = False
    assert await c.confirm_symbol_flat("AAPL") is None
    assert await _flat_client().confirm_symbol_flat("") is None


@pytest.mark.asyncio
async def test_a_broker_that_cannot_answer_says_none_by_default():
    from app.broker.broker_interface import BrokerInterface
    assert await BrokerInterface.confirm_symbol_flat(object(), "AAPL") is None


# ── The reconciliation gate at the real send sites ──────────────────────────────
#
# These drive the actual adapter with a fake IB and a stubbed verdict, so what is
# asserted is whether an ORDER REACHES THE BROKER, which is the only thing the
# gate exists to decide.

from app.services.reconciliation_entry_gate import GateAssessment, ReconciliationBlocked

_CLEAN = GateAssessment(available=True)
_UNRESOLVED = GateAssessment(available=True, findings=(("direction_mismatch", "SPY"),))
_UNAVAILABLE = GateAssessment(available=False, unavailable_cause="broker_unavailable")


@pytest.fixture
def gate_verdicts(monkeypatch):
    """Set the gate's mode and the verdict(s) its evaluation returns, in order
    (the last repeats). Returns the list of evaluations made and the record spy."""
    from app.core.config import settings
    from app.services import reconciliation_entry_gate as gate

    def configure(mode, *verdicts):
        sequence = list(verdicts)
        evaluations: list = []

        async def evaluate(broker):
            evaluations.append(broker)
            return sequence.pop(0) if len(sequence) > 1 else sequence[0]

        record = AsyncMock()
        monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", mode)
        monkeypatch.setattr(gate, "evaluate_entry_reconciliation", evaluate)
        monkeypatch.setattr(gate, "_record", record)
        return evaluations, record

    return configure


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
@pytest.mark.parametrize("verdict, reason", [
    (_UNRESOLVED, "reconciliation_unresolved"),
    (_UNAVAILABLE, "reconciliation_unavailable"),
])
async def test_enforce_sends_nothing_when_reconciliation_is_not_clean(
    asset, verdict, reason, gate_verdicts, entry_halt,
):
    gate_verdicts("enforce", verdict)
    client = _make_client([_OrderStatus("Filled", 5, 1.5)])
    with pytest.raises(ReconciliationBlocked) as exc:
        await _send_entry(client, asset)
    assert str(exc.value) == reason
    assert client.ib.placed == [], "an order reached the broker past a veto"


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
@pytest.mark.parametrize("verdict", [_UNRESOLVED, _UNAVAILABLE], ids=["unresolved", "unavailable"])
async def test_observe_sends_the_order_anyway_and_records_what_it_would_have_done(
    asset, verdict, gate_verdicts, entry_halt,
):
    evaluations, record = gate_verdicts("observe", verdict)
    client = _make_client([_OrderStatus("Filled", 5, 1.5)])
    result = await _send_entry(client, asset)
    assert result.status == "filled"
    assert len(client.ib.placed) == 1, "observe changed what the entry does"
    assert len(evaluations) == 1
    status = record.await_args.args[1]
    assert status == ("would_veto" if verdict is _UNRESOLVED else "unavailable")
    assert record.await_args.args[3] == "SPY"                 # the ticker is recorded
    assert record.await_args.args[4] == asset


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
@pytest.mark.parametrize("mode_name", ["observe", "enforce"])
async def test_a_clean_book_sends_in_every_mode(asset, mode_name, gate_verdicts, entry_halt):
    evaluations, record = gate_verdicts(mode_name, _CLEAN)
    client = _make_client([_OrderStatus("Filled", 5, 1.5)])
    assert (await _send_entry(client, asset)).status == "filled"
    assert len(client.ib.placed) == 1 and len(evaluations) == 1
    record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_off_neither_evaluates_nor_blocks(asset, gate_verdicts, entry_halt):
    evaluations, _ = gate_verdicts("off", _UNRESOLVED)
    client = _make_client([_OrderStatus("Filled", 5, 1.5)])
    assert (await _send_entry(client, asset)).status == "filled"
    assert evaluations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_a_replacement_attempt_is_gated_again_before_it_is_sent(
    asset, gate_verdicts, entry_halt,
):
    """First send is clean and times out; the cancel is acknowledged with no
    fill; the replacement is a NEW entry on a book that may have changed, so it
    is evaluated again and, here, vetoed."""
    evaluations, _ = gate_verdicts("enforce", _CLEAN, _UNRESOLVED)
    client = _make_client([_OrderStatus(), _OrderStatus("Filled", 5, 1.4)])
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
         patch("app.broker.ibkr_client._max_retries", return_value=1):
        with pytest.raises(ReconciliationBlocked):
            await _send_entry(client, asset)
    assert len(evaluations) == 2
    assert len(client.ib.placed) == 1, "the vetoed replacement was still sent"
    assert len(client.ib.cancelled) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_a_clean_replacement_is_sent_after_a_second_evaluation(
    asset, gate_verdicts, entry_halt,
):
    """The positive control for the test above."""
    evaluations, _ = gate_verdicts("enforce", _CLEAN)
    client = _make_client([_OrderStatus(), _OrderStatus("Filled", 5, 1.4)])
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0), \
         patch("app.broker.ibkr_client._max_retries", return_value=1):
        result = await _send_entry(client, asset)
    assert result.status == "filled" and len(client.ib.placed) == 2
    assert len(evaluations) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_an_exit_is_never_evaluated_or_blocked_even_when_enforcing_a_bad_book(
    asset, gate_verdicts, entry_halt,
):
    """A close under submission_scope(None) is how the reviewed exit paths and
    emergency flattening call the adapter."""
    from app.services.execution_interlock import submission_scope
    evaluations, record = gate_verdicts("enforce", _UNRESOLVED)
    client = _make_client([_OrderStatus("Filled", 5, 1.5)])
    with submission_scope(None):
        if asset == "options":
            result = await client.place_order(_credit_spread(q_short=5, q_long=5))
        else:
            result = await client.place_equity_order("SPY", 5, "SELL", order_type="market")
    assert result.status == "filled" and len(client.ib.placed) == 1
    assert evaluations == []
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bracket_is_evaluated_once_for_the_parent_not_again_for_its_children(
    gate_verdicts, entry_halt,
):
    evaluations, _ = gate_verdicts("enforce", _CLEAN)
    client = _make_equity_client(parent_status="PreSubmitted")
    with patch("app.broker.ibkr_client._fill_timeout", return_value=0):
        await client.place_equity_order(
            "NVDA", 10, "BUY", order_type="market", stop=190.0, take_profit=210.0,
        )
    assert len(client.ib.placed) == 3          # parent + take-profit + stop-loss
    assert len(evaluations) == 1


@pytest.mark.asyncio
async def test_a_vetoed_bracket_sends_neither_parent_nor_children(gate_verdicts, entry_halt):
    gate_verdicts("enforce", _UNRESOLVED)
    client = _make_equity_client(parent_status="PreSubmitted")
    with pytest.raises(ReconciliationBlocked):
        await client.place_equity_order(
            "NVDA", 10, "BUY", order_type="market", stop=190.0, take_profit=210.0,
        )
    assert client.ib.placed == []
