"""Shared test fixtures.

Nothing global lived here before, and nothing here is autouse. A fixture that
applies to every test changes the meaning of tests nobody is looking at —
including, in this case, the ones that most need to see the real behaviour.
Tests that want the stub ask for it by name.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.services.position_claim import Claim


@pytest.fixture
def stub_position_claim():
    """Let the duplicate guard's position claim succeed, without a database.

    `_execute_signal` Stage 3 claims (underlying, asset class) before
    submitting — that is what closes the window between the duplicate read and
    the trade row being written. Tests of _execute_signal's OTHER behaviour
    fake `AsyncSessionLocal` to serve one specific query, so the claim's own
    statements raise against the fake, and because Stage 3 fails closed those
    tests report `blocked` instead of their own subject.

    Request this fixture to say "this test is not about the claim". It yields
    the mocks so a test can still assert on them — `claim.try_claim`,
    `claim.mark_submitted` and so on.

    Anything that IS about the claim must not use this: see
    test_position_claim_pg.py, which runs the real service against a real
    PostgreSQL, and test_trade_desk_claim_route.py, which drives the route
    through it.
    """
    held = Claim(
        token=uuid.uuid4(),
        idempotency_key="test-idem-key",
        underlying="TEST",
        asset_class="equity",
    )
    with patch("app.services.position_claim.try_claim",
               new=AsyncMock(return_value=held)) as try_claim, \
         patch("app.services.position_claim.mark_submitted",
               new=AsyncMock(return_value=True)) as mark_submitted, \
         patch("app.services.position_claim.mark_unknown",
               new=AsyncMock()) as mark_unknown, \
         patch("app.services.position_claim.resolve",
               new=AsyncMock()) as resolve:
        yield type("StubbedClaim", (), {
            "claim": held,
            "try_claim": try_claim,
            "mark_submitted": mark_submitted,
            "mark_unknown": mark_unknown,
            "resolve": resolve,
        })


#: The services in this app that own long-lived background tasks, and the
#: entry point each one exposes to stop its own.
#:
#: Enumerated on purpose. The alternative — sweeping `asyncio.all_tasks()` and
#: cancelling whatever is left — would also silence the warnings, and that is
#: exactly what makes it the wrong fix: it cancels tasks belonging to services
#: that have no shutdown path at all, so a service that leaks forever in
#: production looks clean in the test suite. Stopping named services through
#: their own lifecycle APIs means a NEW leaker shows up as a warning and has
#: to be given a real shutdown path, which is the defect being surfaced rather
#: than hidden.
#:
#: `test_no_leaked_coordinator_workers.py` asserts this list stays in step with
#: the services that actually spawn loops, and that no blanket sweep creeps in.
BACKGROUND_TASK_OWNERS = (
    "ibkr_coordinator",
    "execution_mode",
    "ibkr_live",
)


async def _stop_background_task_owners() -> None:
    """Stop each owner through its own API. Never a blanket cancel.

    A stale reference here — a renamed singleton, a moved module — is allowed
    to raise. It was not, originally: a blanket `except Exception: pass` meant
    an import of the wrong name (`execution_mode` rather than
    `execution_mode_manager`) silently stopped nothing, and the fixture went on
    looking like it covered three services while covering one. An ImportError
    or AttributeError here is a broken fixture and should say so.
    """
    # The request coordinator: submit() calls start(), so any test that
    # submits spawns worker loops on that test's event loop.
    from app.broker.ibkr_coordinator import ibkr_coordinator
    if ibkr_coordinator._workers:
        await ibkr_coordinator.stop()

    # execution_mode's retry task, which keeps trying to record an unrecorded
    # safety reduction. Bounded, so it ends on its own eventually — but
    # "eventually" is after the loop has closed.
    from app.services.execution_mode import execution_mode_manager
    execution_mode_manager._cancel_retry()

    # The live-data broker's update loop.
    from app.api.routes import ibkr_live
    if getattr(ibkr_live._live_broker, "update_task", None):
        await ibkr_live.shutdown_ibkr_live()


@pytest_asyncio.fixture(autouse=True)
async def _stop_services_owning_background_tasks():
    """Stop services that own background tasks, before their loop is torn down.

    Several services here spawn long-lived asyncio tasks on whatever loop
    happens to be running. Nothing stopped them in tests, so each test's loop
    closed with tasks still pending and every one printed

        Task was destroyed but it is pending!

    at garbage-collection — 54 per full run.

    Two things about the shape of this fixture matter more than the warning:

    1. It must be ASYNC. A sync fixture is set up before pytest-asyncio
       creates the loop, so it tears down after that loop is closed, and
       cancellation is delivered by a task's own loop — a closed loop never
       runs them. The first version of this was sync and reaped nothing.

    2. It stops NAMED services through their own shutdown APIs rather than
       cancelling every surviving task. A blanket sweep hides the defect it
       appears to fix: a service with no shutdown path would be tidied up by
       the test suite while still leaking in production. See
       BACKGROUND_TASK_OWNERS.

    It IS autouse, unlike `stub_position_claim` above, and the distinction is
    deliberate: it changes no behaviour a test can observe. It runs after the
    test body and asserts nothing.
    """
    yield
    await _stop_background_task_owners()
