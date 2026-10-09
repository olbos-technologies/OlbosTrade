"""Services that own background tasks must be stopped before their loop closes.

Several services here spawn long-lived asyncio tasks on whatever loop is
running — the request coordinator's worker pool, execution_mode's retry, the
live-data update loop. Nothing stopped them in tests, so each test's loop
closed with tasks pending and every one printed "Task was destroyed but it is
pending!" at garbage-collection: 54 per full run.

What is pinned here is not that `stop()` works (test_ibkr_coordinator covers
that) but the two properties of the fixture that make it a real fix rather
than a cosmetic one:

1. It is ASYNC. The first version was sync, ran on every test, and reaped
   nothing: a sync fixture tears down after pytest-asyncio has closed the
   loop, and a task's cancellation can only be delivered by its own loop.

2. It stops NAMED services through their own shutdown APIs instead of
   cancelling whatever tasks survive. A blanket `asyncio.all_tasks()` sweep
   would silence the same warnings while hiding the defect behind them — a
   service with no shutdown path would look clean in the suite and go on
   leaking in production. That is the difference between fixing a lifecycle
   bug and papering over it.

CI also greps the full run's output for the warning, which is the end-to-end
version of this check. These are the fast tests that say why.
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest

CONFTEST = pathlib.Path(__file__).resolve().parent / "conftest.py"


def _fixture(name: str):
    import conftest

    fn = getattr(conftest, name)
    marker = getattr(fn, "_pytestfixturefunction", None)
    assert marker is not None, f"{name} is not a fixture any more"
    return fn, marker


def test_the_reaper_is_autouse():
    """A test that leaks tasks will not remember to ask for the fixture."""
    _, marker = _fixture("_stop_services_owning_background_tasks")
    assert marker.autouse is True


def test_the_reaper_is_async():
    """The original bug: a sync fixture tears down after the loop has closed."""
    fn, _ = _fixture("_stop_services_owning_background_tasks")
    assert inspect.isasyncgenfunction(inspect.unwrap(fn)), (
        "the fixture must be async; a sync one tears down after pytest-asyncio "
        "has already closed the event loop and reaps nothing"
    )


def test_the_reaper_does_not_blanket_cancel_tasks():
    """Sweeping every surviving task would hide the defect it appears to fix.

    `asyncio.all_tasks()` plus `cancel()` tidies the suite's output while
    leaving a service that has no shutdown path still leaking in production —
    the warning that would have revealed it is gone. Stopping named owners
    through their own APIs keeps a new leaker visible.
    """
    # Comments here discuss the sweep in order to rule it out, so read code
    # only — otherwise this guard fails on its own rationale.
    source = "\n".join(
        line for line in CONFTEST.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    for banned in ("all_tasks", "current_task"):
        assert banned not in source, (
            f"conftest.py reaches for asyncio.{banned} — stop named services "
            f"through their own shutdown APIs instead of sweeping tasks"
        )


def test_every_service_that_spawns_a_loop_is_covered():
    """A new background-task owner must be given a shutdown path, not swept up.

    Scans for modules that create a task AND loop forever — the shape that
    leaks — and requires each to be named in BACKGROUND_TASK_OWNERS. One-shot
    `create_task` calls (a backtest, a scan) are not included: they finish.
    """
    import conftest

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    owners = set(conftest.BACKGROUND_TASK_OWNERS)

    spawners = set()
    for path in root.rglob("*.py"):
        text = path.read_text()
        if not re.search(r"\b(create_task|ensure_future)\b", text):
            continue
        if not re.search(r"while True|start_update_loop|_worker_loop", text):
            continue
        spawners.add(path.stem)

    # main.py starts the scheduler, which the app's own lifespan owns; it is
    # not a service with a singleton to stop, and no test runs it.
    spawners.discard("main")

    uncovered = spawners - owners
    assert not uncovered, (
        f"these modules spawn long-lived tasks but no fixture stops them: "
        f"{sorted(uncovered)}. Give each a shutdown entry point and add it to "
        f"conftest.BACKGROUND_TASK_OWNERS — do not blanket-cancel instead."
    )


def test_the_listed_owners_all_still_exist():
    """An owner that was renamed away would make its entry a silent no-op."""
    import conftest

    assert conftest.BACKGROUND_TASK_OWNERS, "the owner list is empty"

    from app.api.routes import ibkr_live
    from app.broker.ibkr_coordinator import ibkr_coordinator
    from app.services.execution_mode import execution_mode_manager

    assert callable(ibkr_coordinator.stop)
    assert callable(execution_mode_manager._cancel_retry)
    assert callable(ibkr_live.shutdown_ibkr_live)


@pytest.mark.asyncio
async def test_stopping_the_coordinator_leaves_nothing_pending():
    """And the mechanism the fixture relies on does what it assumes."""
    from app.broker.ibkr_coordinator import ibkr_coordinator as coord

    coord.start()
    workers = list(coord._workers or [])
    assert workers, "start() spawned no workers; the fixture would have nothing to do"

    await coord.stop()

    assert coord._workers is None
    assert all(w.done() for w in workers)


#: Classes that spawn a background task PER INSTANCE rather than per process.
#: The shared fixture cannot stop these: there is no singleton to reach, and
#: nothing global holds the instances a test created. Each is cleaned up in its
#: own test module, targeted at the specific coroutine.
PER_INSTANCE_OWNERS = {
    "app/broker/ibkr_client.py": (
        "tests/test_ibkr_client.py",
        "_subscribe_account_updates",
    ),
}


def test_per_instance_task_owners_are_cleaned_up_in_their_own_module():
    """The leaker the scan above does not catch, recorded so it is not re-lost.

    `test_every_service_that_spawns_a_loop_is_covered` looks for `while True`
    and friends. IBKRClient has neither: it spawns
    `_subscribe_account_updates` with `ensure_future` and awaits it with a
    timeout, so it reads as a one-shot. It still leaked — 35 tests' worth —
    because `disconnect()` is what cancels it and the tests never disconnect.

    This is not something the shared fixture can fix, so the requirement is
    recorded here instead of being left to whoever next sees the warning.
    """
    tests_dir = pathlib.Path(__file__).resolve().parent
    backend = tests_dir.parent

    for module, (test_file, coro) in PER_INSTANCE_OWNERS.items():
        source = (backend / module).read_text()
        assert coro in source, (
            f"{module} no longer defines {coro} — update PER_INSTANCE_OWNERS"
        )
        cleanup = (backend / test_file).read_text()
        assert coro in cleanup, (
            f"{test_file} no longer reaps {coro}; its tasks will leak again"
        )
        assert "autouse=True" in cleanup, (
            f"{test_file}'s cleanup must be autouse — a test that leaks will "
            f"not remember to ask for it"
        )
