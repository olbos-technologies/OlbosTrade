"""The coordinator's worker tasks must be reaped before their loop closes.

`ibkr_coordinator` is a module-level singleton whose `submit()` calls
`start()`, so every test that submits spawns worker tasks on that test's event
loop. Nothing stopped them, so the loop closed with the tasks pending and each
one printed "Task was destroyed but it is pending!" at garbage-collection — 54
of them in a full run.

The fix is the autouse `_reap_ibkr_coordinator_workers` fixture in conftest.py,
and the thing worth pinning is not that `stop()` works (test_ibkr_coordinator
covers that) but HOW the fixture is wired. The first version was a plain sync
fixture and reaped nothing at all: a sync fixture is set up before
pytest-asyncio creates the loop, so it tears down after that loop is closed,
and a task's cancellation can only be delivered by its own loop. It looked
right, ran on every test, and changed nothing.

CI also greps the backend run's output for the warning, which is the
end-to-end version of this check. This test is the fast one that says why.
"""
from __future__ import annotations

import inspect

import pytest


def _fixture(name: str):
    import conftest

    fn = getattr(conftest, name)
    marker = getattr(fn, "_pytestfixturefunction", None)
    assert marker is not None, f"{name} is not a fixture any more"
    return fn, marker


def test_the_reaper_is_autouse():
    """A test that leaks workers will not remember to ask for the fixture."""
    _, marker = _fixture("_reap_ibkr_coordinator_workers")
    assert marker.autouse is True


def test_the_reaper_is_async():
    """The whole bug: a sync fixture tears down after the loop has closed.

    `asyncio.Task.cancel()` is delivered by the task's own event loop. Once
    that loop is closed it will never run the task again, so cancelling from a
    sync teardown is a no-op and the task is still pending when it is
    collected. The fixture must run while the loop is alive, which means it
    must be an async fixture.
    """
    fn, _ = _fixture("_reap_ibkr_coordinator_workers")
    target = inspect.unwrap(fn)
    assert inspect.isasyncgenfunction(target), (
        "the reaper must be an async fixture; a sync one tears down after "
        "pytest-asyncio has already closed the event loop and reaps nothing"
    )


@pytest.mark.asyncio
async def test_stopping_the_coordinator_leaves_nothing_pending():
    """And the mechanism it relies on does what the fixture assumes."""
    from app.broker.ibkr_coordinator import ibkr_coordinator as coord

    coord.start()
    workers = list(coord._workers or [])
    assert workers, "start() spawned no workers; the fixture would have nothing to do"

    await coord.stop()

    assert coord._workers is None
    assert all(w.done() for w in workers)
