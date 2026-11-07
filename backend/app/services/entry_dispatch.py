"""Run an entry as its own task, so the scan that started it cannot cancel it.

THE FAILURE THIS EXISTS FOR
---------------------------
Every scan runs under a hard timeout (`_guarded`, e.g. equity_scan 90 s), and a
timeout does not wait: it CANCELS the scan wherever it is. The scan placed its
entries inline (`await handle_signal(sig)`), so a scan that ran long cancelled an
entry that was already waiting on the broker.

An entry in that state is the worst place to be cancelled:

  * the order is already at IBKR - the coordinator shields the broker job on
    purpose, so it is sent and can fill whether or not anyone is still waiting;
  * the entry's bookkeeping (trade row, execution log, claim resolution) is still
    ahead of it and is simply skipped, because cancellation is not an ordinary
    exception and the entry's error handling never sees it;
  * the position claim is left in `submitted` for good.

The desk then holds a position it has no record of opening, and the reconciler
later "adopts" it as if it had been found at the broker. That is how a position
the app itself placed ends up labelled "adopted_untracked", with no execution
record and a claim that nothing resolves.

This module removes the cause: an entry becomes a task of its own, and the scan
only WAITS on it. If the scan is cancelled the wait is abandoned, and the entry
carries on to its own completion under its own, much longer, limits.

It is not the only defence. `_execute_signal` also settles an entry that is
cancelled after it was sent (trade_desk._settle_interrupted_entry), because an
entry can be cancelled by things other than a scan: a request that disconnects,
a shutdown.
"""
from __future__ import annotations

import asyncio

from app.services.observability import observability
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: Strong references. The event loop keeps only weak ones, so a task nobody holds
#: can be collected mid-flight - which would silently drop the entry again.
_ENTRY_TASKS: set[asyncio.Task] = set()


def entries_in_flight() -> int:
    return len(_ENTRY_TASKS)


def _label(signal: dict) -> str:
    return str(signal.get("ticker") or signal.get("id") or "?")


def _finished(task: asyncio.Task, label: str) -> None:
    """Runs when the entry ends, whether or not anything is still awaiting it.

    Reading the exception here matters: if the scan was cancelled, nobody else
    will, and the failure of an order-placing task must not vanish into a
    "Task exception was never retrieved" at garbage-collection time.
    """
    _ENTRY_TASKS.discard(task)
    if task.cancelled():
        logger.warning("Entry for %s was cancelled before it finished", label)
    elif task.exception() is not None:
        logger.error("Entry for %s failed: %s", label, task.exception())


async def dispatch_entry(signal: dict) -> None:
    """Run `handle_signal(signal)` so that cancelling THIS coroutine does not
    cancel it. Behaves like `await handle_signal(signal)` otherwise: it returns
    when the entry returns and raises what the entry raises."""
    from app.api.routes.trade_desk import handle_signal

    label = _label(signal)
    task = asyncio.ensure_future(handle_signal(signal))
    _ENTRY_TASKS.add(task)
    task.add_done_callback(lambda t: _finished(t, label))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():
            observability.incr("entry.outlived_its_scan")
            logger.warning(
                "The scan was cancelled while the entry for %s was in flight; "
                "the entry continues on its own and will be recorded.", label,
            )
        raise
