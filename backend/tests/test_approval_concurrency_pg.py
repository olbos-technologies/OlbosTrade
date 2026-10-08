"""Concurrent Copilot approval must authorise exactly one submission.

WHY THIS FILE NEEDS A REAL POSTGRES. The defect it guards is a property of
transaction isolation, not of Python. `_resolve_pending_approval` used to
SELECT a row with status='pending' and then assign a new status through the
ORM. Under READ COMMITTED that SELECT takes no row lock, so two concurrent
approvals of the same signal both see 'pending', both issue an UPDATE keyed
on the primary key, and both commit. Each caller gets a payload back, and
each goes on to submit an order — one signal, two economic orders.

A mocked session cannot show that. The mock returns whatever the test told it
to return, so a SELECT-then-write helper and an atomic claim look identical
through it; the pre-existing mock tests in test_execution_events_helpers.py
passed throughout the window in which this bug was live. Only a real database
with two real connections decides who wins.

`test_the_old_select_then_write_pattern_double_books` runs the superseded
pattern against the same database and asserts it DOES double-book. It is not
redundant: it is what makes the other tests meaningful, by proving the harness
can observe the failure it claims the fix prevents. If someone reverts
`_resolve_pending_approval` to a SELECT, that test keeps passing and the rest
start failing — which is the signal you want.

Set OLBOS_TEST_DATABASE_URL to an asyncpg URL to run these, e.g.
    postgresql+asyncpg://postgres@/olbos_test?host=/tmp&port=5433
CI provides one via the postgres service in .github/workflows/ci.yml. Without
it the module skips rather than passing vacuously.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.trade_desk import _resolve_pending_approval
from app.models.execution_event import ExecutionEvent

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]


@pytest_asyncio.fixture
async def sessions():
    """A session factory bound to the test database, with a clean table.

    `_resolve_pending_approval` imports AsyncSessionLocal *inside* the
    function body, so patching the module attribute is enough to redirect it —
    no application engine is constructed or connected to.
    """
    engine = create_async_engine(TEST_DB_URL, pool_size=10, max_overflow=10)
    async with engine.begin() as conn:
        await conn.run_sync(ExecutionEvent.__table__.drop, checkfirst=True)
        await conn.run_sync(ExecutionEvent.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    await engine.dispose()


async def _queue(factory, signal_id: str, **payload) -> None:
    async with factory() as s, s.begin():
        s.add(ExecutionEvent(
            kind="pending_approval",
            signal_id=signal_id,
            ticker=payload.get("ticker", "SPY"),
            asset_type="equity",
            status="pending",
            payload={"id": signal_id, "ticker": "SPY", "action": "BUY", **payload},
        ))


async def _status(factory, signal_id: str) -> str | None:
    async with factory() as s:
        row = (await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.signal_id == signal_id)
        )).scalar_one()
        return row.status


async def _row(factory, signal_id: str) -> ExecutionEvent:
    async with factory() as s:
        return (await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.signal_id == signal_id)
        )).scalar_one()


# --------------------------------------------------------------------------
# The property under test
# --------------------------------------------------------------------------

@pytest.mark.parametrize("racers", [2, 4, 8])
async def test_concurrent_approvals_authorize_exactly_one(sessions, racers):
    """Two is a double-tapped button; eight is a retrying client.

    READ THE MODULE DOCSTRING BEFORE TRUSTING THIS AS A REGRESSION GUARD. It
    asserts the right property, but it cannot by itself prove an
    implementation is safe: the window a SELECT-then-write leaves open is
    internal to the function, so whether two callers actually interleave is up
    to the event loop. Measured against the old implementation, 2 and 4 racers
    passed and only 8 reproduced the double-book — the guard that does not
    depend on scheduling is test_the_claim_is_a_conditional_update below.
    """
    sid = f"sig-{uuid.uuid4()}"
    await _queue(sessions, sid)

    results = await asyncio.gather(
        *(_resolve_pending_approval(sid, "approved") for _ in range(racers))
    )

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, (
        f"{len(winners)} callers were authorised to submit one signal; "
        "each non-None result becomes a broker order"
    )
    assert winners[0]["id"] == sid
    assert await _status(sessions, sid) == "approved"


async def test_concurrent_approve_and_reject_produce_one_terminal_decision(sessions):
    sid = f"sig-{uuid.uuid4()}"
    await _queue(sessions, sid)

    approved, rejected = await asyncio.gather(
        _resolve_pending_approval(sid, "approved"),
        _resolve_pending_approval(sid, "rejected"),
    )

    assert (approved is None) != (rejected is None), (
        "approve and reject both claimed the same signal — the order would be "
        "submitted and recorded as rejected"
    )
    final = await _status(sessions, sid)
    assert final in {"approved", "rejected"}
    # The surviving status matches whichever call won.
    assert final == ("approved" if approved is not None else "rejected")


async def test_repeated_approval_cannot_claim_twice(sessions):
    """Sequential retries — a refreshed tab, a replayed request."""
    sid = f"sig-{uuid.uuid4()}"
    await _queue(sessions, sid)

    assert await _resolve_pending_approval(sid, "approved") is not None
    assert await _resolve_pending_approval(sid, "approved") is None
    assert await _resolve_pending_approval(sid, "rejected") is None
    assert await _status(sessions, sid) == "approved"


async def test_unknown_signal_is_not_claimable(sessions):
    assert await _resolve_pending_approval("never-existed", "approved") is None


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

async def test_claim_records_the_actor_who_authorised_it(sessions):
    sid = f"sig-{uuid.uuid4()}"
    await _queue(sessions, sid)

    payload = await _resolve_pending_approval(sid, "approved", actor="user-42")

    assert payload["approval"]["resolved_by"] == "user-42"
    assert payload["approval"]["resolution"] == "approved"
    assert payload["approval"]["resolved_at"]
    # and it is durable, not just returned
    row = await _row(sessions, sid)
    assert row.payload["approval"]["resolved_by"] == "user-42"
    # the signal's own fields survive the merge
    assert row.payload["ticker"] == "SPY"


async def test_absent_actor_is_omitted_rather_than_guessed(sessions):
    sid = f"sig-{uuid.uuid4()}"
    await _queue(sessions, sid)

    payload = await _resolve_pending_approval(sid, "approved")

    assert "resolved_by" not in payload["approval"], (
        "an audit trail that names an actor it does not know is worse than "
        "one that admits it does not"
    )


# --------------------------------------------------------------------------
# The guard that does not depend on scheduling
# --------------------------------------------------------------------------

async def test_the_claim_is_a_conditional_update_not_a_read(sessions):
    """Assert the SHAPE of the statement, because behaviour alone is flaky.

    The concurrency tests above assert the right property but can pass against
    a racy implementation when the event loop happens to serialise them. This
    one cannot: it fails the moment the claim goes back to being a SELECT, or
    loses the `status = 'pending'` predicate that makes the UPDATE conditional.
    Those are the two ways the defect comes back.
    """
    sid = f"sig-{uuid.uuid4()}"
    await _queue(sessions, sid)

    captured = []

    class _Recorder:
        def __init__(self) -> None:
            self._ctx = sessions()

        async def __aenter__(self):
            session = await self._ctx.__aenter__()
            original = session.execute

            async def execute(statement, *args, **kwargs):
                captured.append(statement)
                return await original(statement, *args, **kwargs)

            session.execute = execute
            return session

        async def __aexit__(self, *exc):
            return await self._ctx.__aexit__(*exc)

    with patch("app.core.database.AsyncSessionLocal", _Recorder):
        await _resolve_pending_approval(sid, "approved")

    assert len(captured) == 1, (
        "the claim must be ONE statement; a read followed by a write is the "
        "window two approvals slip through"
    )
    statement = captured[0]
    assert isinstance(statement, update(ExecutionEvent).__class__), (
        f"claim issued a {type(statement).__name__}, not an UPDATE"
    )
    # Compiled without literal_binds: the JSONB cast cannot render as a
    # literal, and the predicate is what matters, not the payload merge.
    where_sql = str(statement.whereclause)
    assert "execution_events.status" in where_sql, (
        "the UPDATE lost its status predicate, so it would re-claim an "
        f"already-resolved approval: {where_sql}"
    )
    bound = {str(v) for v in statement.compile().params.values()}
    assert "pending" in bound, (
        f"the status predicate does not bind 'pending': {bound}"
    )


# --------------------------------------------------------------------------
# The harness proves it can see the failure it guards against
# --------------------------------------------------------------------------

async def test_the_old_select_then_write_pattern_double_books(sessions):
    """The superseded implementation, run against the same database.

    This is the bug, reproduced. If this ever stops double-booking, the
    isolation assumptions behind the fix have changed and the rest of this
    file needs rereading.
    """
    sid = f"sig-{uuid.uuid4()}"
    await _queue(sessions, sid)


    both_read = asyncio.Barrier(2)

    async def legacy_claim():
        async with sessions() as session, session.begin():
            row = (await session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.kind == "pending_approval",
                    ExecutionEvent.signal_id == sid,
                    ExecutionEvent.status == "pending",
                )
            )).scalar_one_or_none()

            # Both transactions have now read 'pending' — the window the old
            # code left open between the read and the write.
            await both_read.wait()
            if row is None:
                return None
            payload = dict(row.payload)
            row.status = "approved"
            return payload

    results = await asyncio.gather(legacy_claim(), legacy_claim())

    assert sum(r is not None for r in results) == 2, (
        "expected the unlocked SELECT-then-write to authorise both callers; "
        "if it did not, this harness can no longer demonstrate the defect"
    )
