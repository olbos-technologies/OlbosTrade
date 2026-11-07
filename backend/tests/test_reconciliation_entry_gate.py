"""Entry vetoes use fake brokers/snapshots only; no gateway or database."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.broker.ibkr_coordinator import IBKRRequestCoordinator, Priority
from app.services.execution_interlock import capture_entry_permit, submission_scope
from app.services.execution_interlock import EntryHalted
from app.services.kill_switch import KillSwitch
from app.services.reconciliation_entry_gate import ReconciliationBlocked, require_reconciled_entry
from test_ibkr_execution import _make_client, _make_equity_client, _send_entry, _OrderStatus


@pytest.fixture(autouse=True)
def reset_halt(monkeypatch):
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", KillSwitch())


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
@pytest.mark.parametrize("snapshot", [False, RuntimeError("db unavailable"), asyncio.TimeoutError()])
async def test_unresolved_or_unavailable_never_sends(asset, snapshot):
    client = _make_client([])
    check = AsyncMock(side_effect=snapshot) if isinstance(snapshot, Exception) else AsyncMock(return_value=SimpleNamespace(clean=snapshot))
    with patch("app.services.position_reconciler.PositionReconciler.check", check):
        with pytest.raises(ReconciliationBlocked):
            await _send_entry(client, asset)
    assert not client.ib.placed
    check.assert_awaited_once_with(fresh_broker=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["equity", "options"])
async def test_mismatch_after_cancel_blocks_replacement(asset):
    client = _make_client([_OrderStatus()])
    client._await_order = AsyncMock(return_value=("timeout", 0, 0))
    client._cancel_and_confirm = AsyncMock(return_value=("cancelled", 0, 0))
    check = AsyncMock(side_effect=[SimpleNamespace(clean=True), SimpleNamespace(clean=False)])
    with patch("app.services.position_reconciler.PositionReconciler.check", check):
        with pytest.raises(ReconciliationBlocked):
            await _send_entry(client, asset)
    assert len(client.ib.placed) == 1
    client._cancel_and_confirm.assert_awaited_once()


@pytest.mark.asyncio
async def test_mismatch_while_queued_blocks_before_send():
    coordinator = IBKRRequestCoordinator(num_workers=1, num_reserved=1)
    started, release = asyncio.Event(), asyncio.Event()
    async def blocking_read():
        started.set()
        await release.wait()
    read = asyncio.create_task(coordinator.submit(Priority.P0, blocking_read))
    await started.wait()
    client = _make_client([])
    check = AsyncMock(return_value=SimpleNamespace(clean=True))
    with patch("app.services.position_reconciler.PositionReconciler.check", check):
        order = asyncio.create_task(coordinator.submit(
            Priority.P0, lambda: _send_entry(client, "equity"), req_type="PLACE_ORDER"))
        while coordinator._queues[Priority.P0].empty():
            await asyncio.sleep(0)
        check.return_value = SimpleNamespace(clean=False)
        release.set()
        try:
            await read
            with pytest.raises(ReconciliationBlocked):
                await order
        finally:
            await coordinator.stop()
    assert not client.ib.placed


@pytest.mark.asyncio
async def test_held_bracket_is_cancelled_without_transmitting_children():
    client = _make_equity_client(parent_status="PreSubmitted")
    client.ib.cancelOrder = MagicMock(side_effect=lambda order: setattr(client.ib.placed[0].orderStatus, "status", "Cancelled"))
    check = AsyncMock(side_effect=[SimpleNamespace(clean=True), SimpleNamespace(clean=False)])
    with patch("app.services.position_reconciler.PositionReconciler.check", check):
        result = await client.place_equity_order("SPY", 5, "BUY", order_type="market", stop=440, take_profit=460)
    assert len(client.ib.placed) == 1
    assert result.status == "cancelled"
    client.ib.cancelOrder.assert_called_once()


@pytest.mark.asyncio
async def test_close_bypasses_unavailable_reconciliation_and_entry_recovers():
    client = _make_client([_OrderStatus("Filled", 5, 450)])
    check = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.services.position_reconciler.PositionReconciler.check", check):
        with submission_scope(None):
            result = await client.place_equity_order("SPY", 5, "SELL", order_type="market")
        check.assert_not_awaited()
        assert result.status == "filled"
        check.side_effect = None
        check.return_value = SimpleNamespace(clean=True)
        await require_reconciled_entry(client, capture_entry_permit())
        check.assert_awaited_once()


@pytest.mark.asyncio
async def test_broker_cache_is_refreshed_after_database_await():
    from app.services.position_reconciler import PositionReconciler
    from test_position_reconciler import _wire_db, make_position
    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=[[], [make_position("TTWO", "TTWO")]])
    with patch("app.services.position_reconciler.AsyncSessionLocal") as db:
        _wire_db(db, [])
        result = await PositionReconciler(broker, organization_id=None).check(fresh_broker=True)
    assert not result.clean
    assert result.untracked_at_broker == ["TTWO"]
    assert broker.get_positions.await_count == 2


@pytest.mark.asyncio
async def test_halt_during_reconciliation_still_vetoes_send():
    from app.services.kill_switch import kill_switch_service
    permit = capture_entry_permit()
    async def check(**kwargs):
        kill_switch_service._engaged = True
        return SimpleNamespace(clean=True)
    with patch("app.services.position_reconciler.PositionReconciler.check", side_effect=check):
        with pytest.raises(EntryHalted, match="kill_switch"):
            await require_reconciled_entry(MagicMock(), permit)


@pytest.mark.asyncio
async def test_broker_refresh_failure_is_nonclean():
    from app.services.position_reconciler import PositionReconciler
    from test_position_reconciler import _wire_db
    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=[[], RuntimeError("disconnected")])
    with patch("app.services.position_reconciler.AsyncSessionLocal") as db:
        _wire_db(db, [])
        result = await PositionReconciler(broker, organization_id=None).check(fresh_broker=True)
    assert not result.clean
    assert result.warnings[0].startswith("broker_unavailable:")
