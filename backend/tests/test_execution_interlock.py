"""Dispatch races use events/fake brokers only; no application startup or I/O."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.broker.ibkr_coordinator import IBKRRequestCoordinator, Priority
from app.services.kill_switch import KillSwitch
from app.services import execution_interlock as interlock


@pytest.fixture
def halt(monkeypatch):
    switch = KillSwitch()
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", switch)
    return switch


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_before_dispatch", [False, True])
async def test_queued_entry_cannot_outlive_a_halt(halt, reset_before_dispatch):
    coordinator = IBKRRequestCoordinator(num_workers=1, num_reserved=1)
    entered, release = asyncio.Event(), asyncio.Event()
    entry = AsyncMock()

    async def blocking_read():
        entered.set()
        await release.wait()

    read = asyncio.create_task(coordinator.submit(Priority.P0, blocking_read))
    await entered.wait()
    order = asyncio.create_task(coordinator.submit(
        Priority.P0, entry, req_type="PLACE_ORDER", increases_risk=True,
    ))
    # Queue insertion is our synchronization point, not a wall-clock sleep.
    while coordinator._queues[Priority.P0].empty():
        await asyncio.sleep(0)
    with patch("app.services.kill_switch.AsyncSessionLocal") as db:
        db.return_value.__aenter__ = AsyncMock(return_value=MagicMock(commit=AsyncMock()))
        db.return_value.__aexit__ = AsyncMock(return_value=False)
        await halt.engage("test")
    if reset_before_dispatch:
        # Model an authorized reset: old permits must remain invalid.
        halt._engaged = False
    release.set()
    try:
        await read
        with pytest.raises(interlock.EntryHalted):
            await order
        entry.assert_not_awaited()
    finally:
        release.set()
        await coordinator.stop()


@pytest.mark.asyncio
async def test_close_and_reads_remain_available_during_halt(halt):
    halt._engaged = True
    coordinator = IBKRRequestCoordinator(num_workers=1, num_reserved=1)

    async def close():
        interlock.check_entry_permit(interlock.current_entry_permit())
        return "closed"

    try:
        assert await coordinator.submit(
            Priority.P0, close, req_type="PLACE_ORDER", increases_risk=False,
        ) == "closed"
        assert await coordinator.submit(Priority.P0, AsyncMock(return_value=42)) == 42
        with pytest.raises(interlock.EntryHalted):
            await coordinator.submit(Priority.P0, AsyncMock(), req_type="PLACE_ORDER")
    finally:
        await coordinator.stop()
