"""Two signals for the same position must not both reach the broker — and an
expired lease must not be mistaken for evidence that no order exists.

`_execute_signal` Stage 3 reads the trades table for an open or pending row on
(underlying, asset class) and skips if it finds one. That read is correct and
insufficient: the row is written only AFTER the broker accepts, so two signals
arriving inside that round trip both read zero rows, both pass, and both
submit. Same shape as the Copilot approval bug, one layer down.

Real PostgreSQL throughout, because what decides a winner is ON CONFLICT
against a primary key under contention, and lease decisions compare database
time with database time. A mocked session returns whatever the test told it
to, so a check-then-insert and an atomic claim are indistinguishable through
one.

`test_check_then_insert_double_books` runs the pattern this replaces against
the same database and asserts it DOES let both through, so the harness is
shown capable of observing the failure it claims to prevent.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.execution_event import ExecutionEvent
from app.models.position_claim import (
    STATE_PENDING, STATE_SUBMITTED, STATE_UNKNOWN, PositionClaim,
)
from app.services import position_claim

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
    # NullPool: pooled asyncpg connections outlive a test's event loop, and the
    # next test then finds one bound to a dead loop.
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS execution_events"))
        await conn.run_sync(PositionClaim.__table__.create)
        # force_release writes its audit row here, in the same transaction as
        # the delete, so the table has to be real for that to be observable.
        await conn.run_sync(ExecutionEvent.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS execution_events"))
    await engine.dispose()


@pytest_asyncio.fixture
async def restarted():
    """Run a block as if a fresh process had taken over the same database.

    A restart is the case the claim lifecycle exists for, and it cannot be
    faked by calling the service again: the point is that NOTHING in memory
    carries over. This builds a new engine, a new connection pool and a new
    session factory over the same rows, and rebinds the service to them, so a
    claim that still blocks does so purely on what is persisted.
    """
    @contextlib.asynccontextmanager
    async def _restart():
        engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
        factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, autoflush=False,
        )
        try:
            with patch("app.core.database.AsyncSessionLocal", factory):
                yield factory
        finally:
            await engine.dispose()

    return _restart


def _sym() -> str:
    return f"T{uuid.uuid4().hex[:6].upper()}"


async def _rows(factory):
    async with factory() as s:
        return (await s.execute(select(PositionClaim))).scalars().all()


async def _age_lease(factory, token, seconds: int):
    """Push a claim's lease into the past using DATABASE time."""
    async with factory() as s, s.begin():
        await s.execute(
            update(PositionClaim)
            .where(PositionClaim.claim_token == token)
            .values(lease_expires_at=text(f"now() - interval '{seconds} seconds'"))
        )


# ── exclusivity ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("racers", [2, 4, 8])
async def test_only_one_concurrent_entry_claims_a_position(sessions, racers):
    ticker = _sym()
    results = await asyncio.gather(
        *(position_claim.try_claim(ticker, "equity") for _ in range(racers))
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, (
        f"{len(winners)} entries were cleared to submit for one position"
    )
    assert len(await _rows(sessions)) == 1


async def test_equity_and_options_on_one_underlying_do_not_block_each_other(sessions):
    ticker = _sym()
    equity, options = await asyncio.gather(
        position_claim.try_claim(ticker, "equity"),
        position_claim.try_claim(ticker, "options"),
    )
    assert equity is not None and options is not None


async def test_different_underlyings_do_not_block_each_other(sessions):
    a, b = await asyncio.gather(
        position_claim.try_claim("AAA", "equity"),
        position_claim.try_claim("BBB", "equity"),
    )
    assert a is not None and b is not None


async def test_the_key_is_normalised(sessions):
    assert await position_claim.try_claim("spy", "equity") is not None
    assert await position_claim.try_claim("SPY", "equity") is None


# ── the lease governs only the part of the window with nothing sent ────────

async def test_an_expired_pending_claim_is_reclaimed(sessions):
    """A worker that died BEFORE sending anything left no order, so its claim
    is safe to reclaim."""
    ticker = _sym()
    first = await position_claim.try_claim(ticker, "equity")
    await _age_lease(sessions, first.token, 5)

    second = await position_claim.try_claim(ticker, "equity")
    assert second is not None and second.token != first.token


async def test_an_expired_SUBMITTED_claim_is_NOT_reclaimed(sessions):
    """THE POINT OF THE STATE. An order is at the broker and the lease ran
    out. A timer expiring is not evidence that no order exists, and handing
    the position to a second entry here is how an account ends up with two."""
    ticker = _sym()
    first = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(first)
    await _age_lease(sessions, first.token, 10_000)

    assert await position_claim.try_claim(ticker, "equity") is None
    rows = await _rows(sessions)
    assert len(rows) == 1 and rows[0].state == STATE_SUBMITTED


async def test_an_expired_UNKNOWN_claim_is_NOT_reclaimed(sessions):
    ticker = _sym()
    first = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(first)
    await position_claim.mark_unknown(first, "submit timed out")
    await _age_lease(sessions, first.token, 10_000)

    assert await position_claim.try_claim(ticker, "equity") is None
    assert (await _rows(sessions))[0].state == STATE_UNKNOWN


async def test_reclaiming_one_expired_claim_does_not_disturb_a_live_one(sessions):
    live, dead = _sym(), _sym()
    held = await position_claim.try_claim(live, "equity")
    stale = await position_claim.try_claim(dead, "equity")
    await _age_lease(sessions, stale.token, 5)

    assert await position_claim.try_claim(dead, "equity") is not None
    assert await position_claim.try_claim(live, "equity") is None, (
        "reclaiming an expired claim released a live one"
    )
    assert held is not None


async def test_lease_decisions_use_database_time(sessions):
    """A worker with a skewed clock must not reclaim a live claim or hold a
    dead one. The lease is written as `now() + interval` by the database and
    compared against `now()` by the database; no timestamp travels from
    Python, which is why the module imports no clock at all."""
    import inspect
    import app.services.position_claim as mod

    source = inspect.getsource(mod)
    assert "datetime.now" not in source and "utcnow" not in source, (
        "a local clock crept into the claim service; lease comparisons must "
        "stay database-side"
    )

    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity", lease_seconds=60)
    assert held is not None
    async with sessions() as s:
        row = (await s.execute(select(PositionClaim))).scalar_one()
        db_now = (await s.execute(select(text("now()")))).scalar_one()
    # Written by the database, relative to the database's own clock.
    assert row.lease_expires_at > db_now
    assert row.claimed_at <= db_now


# ── ownership is by token, never by symbol ─────────────────────────────────

async def test_a_stale_worker_cannot_resolve_the_current_holders_claim(sessions):
    """The reason resolve() takes a token. A worker whose pending claim was
    reclaimed must not be able to release whoever holds the position now —
    deleting by symbol would do exactly that."""
    ticker = _sym()
    stale = await position_claim.try_claim(ticker, "equity")
    await _age_lease(sessions, stale.token, 5)
    current = await position_claim.try_claim(ticker, "equity")
    assert current is not None and current.token != stale.token

    await position_claim.resolve(stale)          # the old worker waking up

    rows = await _rows(sessions)
    assert len(rows) == 1 and rows[0].claim_token == current.token, (
        "a stale worker released the current holder's claim"
    )


async def test_a_stale_worker_cannot_mark_the_current_holders_claim_submitted(sessions):
    ticker = _sym()
    stale = await position_claim.try_claim(ticker, "equity")
    await _age_lease(sessions, stale.token, 5)
    current = await position_claim.try_claim(ticker, "equity")

    assert await position_claim.mark_submitted(stale) is False, (
        "a stale worker was cleared to submit"
    )
    rows = await _rows(sessions)
    assert len(rows) == 1 and rows[0].state == STATE_PENDING
    assert current is not None


async def test_resolve_frees_the_position(sessions):
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.resolve(held)
    assert await position_claim.try_claim(ticker, "equity") is not None


# ── reconciliation ─────────────────────────────────────────────────────────

async def test_reconcile_releases_a_claim_the_broker_has_no_order_for(sessions):
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "timed out")

    async def lookup(_key):
        # Authoritative: this broker states no such order exists.
        return position_claim.BrokerVerdict.ABSENT

    # settle_seconds=0: this claim is seconds old, and the settle window is
    # what stops a not-yet-visible order being read as an absent one. Its own
    # test below pins that; here it would only mask the release.
    out = await position_claim.reconcile_unresolved(lookup, settle_seconds=0)
    assert out["released"] == 1
    assert await _rows(sessions) == []


async def test_reconcile_leaves_a_claim_whose_order_the_broker_knows_about(sessions):
    """A real order means a possible position. Releasing would re-open the
    symbol to a second entry while the first is live."""
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "timed out")

    async def lookup(_key):
        return position_claim.BrokerVerdict.PRESENT

    out = await position_claim.reconcile_unresolved(lookup)
    assert out["still_unresolved"] == 1
    assert len(await _rows(sessions)) == 1


async def test_a_failed_lookup_resolves_nothing(sessions):
    """"I could not ask" and "there is nothing there" are different answers."""
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "timed out")

    async def lookup(_key):
        raise ConnectionError("broker unreachable")

    out = await position_claim.reconcile_unresolved(lookup)
    assert out["unreachable"] == 1 and out["released"] == 0
    assert len(await _rows(sessions)) == 1


async def test_reconcile_ignores_claims_that_are_merely_pending(sessions):
    """A pending claim has sent nothing; it is the lease's business, not
    reconciliation's."""
    await position_claim.try_claim(_sym(), "equity")

    async def lookup(_key):
        raise AssertionError("reconciliation asked about a pending claim")

    out = await position_claim.reconcile_unresolved(lookup, settle_seconds=0)
    assert out["released"] == 0
    assert out["still_unresolved"] == 0
    assert out["unreachable"] == 0


async def test_unresolved_lists_what_blocks_re_entry(sessions):
    a, b = _sym(), _sym()
    held_a = await position_claim.try_claim(a, "equity")
    await position_claim.mark_submitted(held_a)
    await position_claim.try_claim(b, "equity")          # pending, not listed

    blocked = await position_claim.unresolved()
    assert [c.underlying for c in blocked] == [a]


# ── the harness can see the failure it guards against ──────────────────────

async def test_check_then_insert_double_books(sessions):
    """The pattern this replaces, against the same database. Both callers
    read, find nothing, and insert — which is what Stage 3's read does across
    a broker round trip."""
    ticker = _sym()
    both_read = asyncio.Barrier(2)

    async def legacy_claim() -> bool:
        async with sessions() as s, s.begin():
            found = (await s.execute(
                select(PositionClaim).where(
                    PositionClaim.underlying == ticker,
                    PositionClaim.asset_class == "equity",
                )
            )).scalar_one_or_none()
            await both_read.wait()          # both have now read "nothing there"
            if found is not None:
                return False
            await s.execute(text(
                "INSERT INTO position_claims "
                "(scope, underlying, asset_class, claim_token, state, "
                " idempotency_key, lease_expires_at) "
                "VALUES ('global', :u, 'equity', gen_random_uuid(), 'pending', "
                "        :k, now() + interval '120 seconds') "
                "ON CONFLICT DO NOTHING"
            ), {"u": ticker, "k": uuid.uuid4().hex[:24]})
            return True

    results = await asyncio.gather(legacy_claim(), legacy_claim())

    assert sum(results) == 2, (
        "expected check-then-insert to clear both callers to submit; if it did "
        "not, this harness can no longer demonstrate the defect"
    )


async def test_an_absent_verdict_does_not_release_a_claim_inside_the_settle_window(sessions):
    """"Not visible yet" is not "not there".

    A broker that has accepted an order can still answer "no such order" for a
    short while. Believing that reading releases the claim and re-opens the
    position to a second entry on a live order — the exact duplicate this
    module exists to prevent. Inside the settle window even an authoritative
    ABSENT is held.
    """
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "timed out")

    async def lookup(_key):
        return position_claim.BrokerVerdict.ABSENT

    out = await position_claim.reconcile_unresolved(lookup, settle_seconds=3600)
    assert out["too_fresh"] == 1
    assert out["released"] == 0
    assert len(await _rows(sessions)) == 1


async def test_an_indeterminate_verdict_is_not_treated_as_absence(sessions):
    """The distinction the Optional[order] contract could not express."""
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "timed out")

    async def lookup(_key):
        return position_claim.BrokerVerdict.INDETERMINATE

    out = await position_claim.reconcile_unresolved(lookup, settle_seconds=0)
    assert out["indeterminate"] == 1
    assert out["released"] == 0
    assert len(await _rows(sessions)) == 1


async def test_an_unrecognised_verdict_is_treated_as_indeterminate(sessions):
    """A lookup that returns something else must not release anything.

    The old contract released on None, so any lookup that returned a falsy
    "nothing to report" sentinel resolved a claim. Anything that is not
    positively ABSENT is now the cautious branch.
    """
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "timed out")

    async def lookup(_key):
        return None                     # what the previous contract released on

    out = await position_claim.reconcile_unresolved(lookup, settle_seconds=0)
    assert out["released"] == 0
    assert out["indeterminate"] == 1
    assert len(await _rows(sessions)) == 1


async def test_the_settle_window_is_measured_by_database_time(sessions):
    """Like every other time comparison here, not by the worker's clock."""
    import inspect

    src = inspect.getsource(position_claim.reconcile_unresolved)
    assert "func.now()" in src and 'func.extract(' in src
    assert "datetime.now" not in src and "utcnow" not in src


# ── restart recovery ───────────────────────────────────────────────────────

async def test_a_submitted_claim_survives_a_process_restart(sessions, restarted):
    """The claim is durable state, not worker memory.

    The whole guarantee rests on this: a worker that dies between recording
    intent and hearing back from the broker must leave the position blocked.
    If the claim lived in the process, the restart would clear it and the next
    dispatch would submit a second order on a position that may already exist.
    """
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)

    # A genuinely new engine, session factory and service state — the next
    # process, not the next function call.
    async with restarted():
        again = await position_claim.try_claim(ticker, "equity")
        assert again is None, "a restart handed out a claim on a submitted position"

        blocking = await position_claim.describe(ticker, "equity")
        assert blocking is not None
        assert blocking.state == position_claim.STATE_SUBMITTED


async def test_a_restart_does_not_resurrect_the_dead_workers_token(sessions, restarted):
    """The old token is not usable after the restart, but the row still blocks.

    A restarted worker holds no claim: it must go through try_claim like
    anyone else, and be refused. The pre-restart Claim object must not be a
    back door to advancing or clearing the row.
    """
    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "worker died mid-submit")

    async with restarted():
        # The stale token can still address its own row — it is the same row —
        # but nothing about the restart grants a fresh entry.
        assert await position_claim.try_claim(ticker, "equity") is None
        still = await position_claim.describe(ticker, "equity")
        assert still.state == position_claim.STATE_UNKNOWN
        assert still.unresolved_reason == "worker died mid-submit"


async def test_an_expired_pending_claim_is_still_reclaimable_after_a_restart(sessions, restarted):
    """Restart recovery must not block what the lease is meant to release.

    The counterpart to the two tests above: a claim that never submitted
    anything is the lease's business, and a restart does not promote it to
    something that needs reconciliation.
    """
    ticker = _sym()
    first = await position_claim.try_claim(ticker, "equity")
    await _age_lease(sessions, first.token, 600)   # pending, lease long gone

    async with restarted():
        taken = await position_claim.try_claim(ticker, "equity")
        assert taken is not None, "an expired pending claim outlived its lease"


# ── audited operator override ──────────────────────────────────────────────

async def test_force_release_clears_a_stuck_claim_and_audits_it(sessions):
    """A claim the broker will never answer for needs a way out that leaves a trace."""
    from app.models.execution_event import ExecutionEvent

    ticker = _sym()
    held = await position_claim.try_claim(ticker, "equity")
    await position_claim.mark_submitted(held)
    await position_claim.mark_unknown(held, "venue lost the key")

    ok = await position_claim.force_release(
        held.token, operator="ops@olbostrade", reason="venue confirmed no fill by phone",
    )
    assert ok is True
    assert await _rows(sessions) == []

    async with sessions() as s:
        events = list((await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.kind == "claim_override")
        )).scalars().all())
    assert len(events) == 1
    ev = events[0]
    assert ev.status == "force_released"
    assert ev.ticker == ticker
    assert ev.payload["operator"] == "ops@olbostrade"
    assert ev.payload["reason"] == "venue confirmed no fill by phone"
    assert ev.payload["state"] == position_claim.STATE_UNKNOWN
    assert ev.payload["unresolved_reason"] == "venue lost the key"
    assert ev.payload["claim_token"] == str(held.token)

    # And the position is genuinely open for business again.
    assert await position_claim.try_claim(ticker, "equity") is not None


async def test_force_release_of_an_unknown_token_audits_nothing(sessions):
    """An override that overrode nothing must not leave a record saying it did."""
    import uuid as _uuid

    from app.models.execution_event import ExecutionEvent

    ok = await position_claim.force_release(
        _uuid.uuid4(), operator="ops@olbostrade", reason="typo",
    )
    assert ok is False
    async with sessions() as s:
        events = (await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.kind == "claim_override")
        )).scalars().all()
    assert list(events) == []


async def test_force_release_writes_the_audit_row_in_the_same_transaction(sessions):
    """Structural: the delete and the audit row cannot be separated.

    If the audit write were its own transaction, a crash between the two would
    release a claim with no record of who did it — the one thing an override
    path must never allow.
    """
    import inspect

    src = inspect.getsource(position_claim.force_release)
    body = src[src.index("async with AsyncSessionLocal"):]
    # one session, one begin(), with both the event and the delete inside it
    assert body.count("AsyncSessionLocal()") == 1
    assert body.count("session.begin()") == 1
    assert body.index("ExecutionEvent(") < body.index("delete(PositionClaim)")
