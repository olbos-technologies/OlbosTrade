"""An unresolved claim is released by what the broker shows — and by nothing else.

IBKR cannot be asked about a client order id, so `reconcile_unresolved` can
never resolve an IBKR claim and an `unknown` one used to wait for a person.
`reconcile_from_evidence` lets the broker's own state settle it, under two
rules and no others:

  * the broker holds the underlying AND an `open` trade row records it;
  * the broker holds nothing, no open/pending row exists, the claim is past the
    settle window, and a fresh network read confirms no position and no
    working order.

These tests are written around the ways that release could be WRONG, because a
claim released too early puts a second order into an account that may already
hold the first. Each negative case asserts the claim is still there and, where
it matters, that the broker was not even asked.

Real PostgreSQL, because settle age is decided by the database clock and the
delete + audit row must land in one transaction.
"""
from __future__ import annotations

import contextlib
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.execution_event import ExecutionEvent
from app.models.position_claim import STATE_PENDING, STATE_SUBMITTED, STATE_UNKNOWN, PositionClaim
from app.services import position_claim

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]

SCOPE = "global"


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS execution_events"))
        await conn.run_sync(PositionClaim.__table__.create)
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
    """A fresh engine, pool and session factory over the same rows: nothing in
    memory carries over, so what survives is only what was persisted."""
    @contextlib.asynccontextmanager
    async def _restart():
        engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        try:
            with patch("app.core.database.AsyncSessionLocal", factory):
                yield factory
        finally:
            await engine.dispose()

    return _restart


def _sym() -> str:
    return f"T{uuid.uuid4().hex[:6].upper()}"


async def _unknown_claim(factory, sym, *, scope=SCOPE, age_seconds=600, state=STATE_UNKNOWN):
    """A claim in the given state whose last activity is `age_seconds` old."""
    claim = await position_claim.try_claim(sym, "equity", scope=scope)
    assert claim is not None
    if state in (STATE_SUBMITTED, STATE_UNKNOWN):
        await position_claim.mark_submitted(claim)
    if state == STATE_UNKNOWN:
        await position_claim.mark_unknown(claim, "submit timed out")
    async with factory() as s, s.begin():
        await s.execute(
            update(PositionClaim)
            .where(PositionClaim.claim_token == claim.token)
            .values(
                claimed_at=text(f"now() - interval '{age_seconds} seconds'"),
                submitted_at=(
                    text(f"now() - interval '{age_seconds} seconds'")
                    if state in (STATE_SUBMITTED, STATE_UNKNOWN) else None
                ),
            )
        )
    return claim


async def _rows(factory):
    async with factory() as s:
        return (await s.execute(select(PositionClaim))).scalars().all()


async def _events(factory):
    async with factory() as s:
        return (await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.kind == "claim_resolved")
        )).scalars().all()


def _reconcile(sym, *, held=(), open_=(), pending=(), flat=None, scope=SCOPE, settle=120):
    check = flat if isinstance(flat, AsyncMock) else AsyncMock(return_value=flat)
    return check, position_claim.reconcile_from_evidence(
        scope=scope, held=set(held), tracked_open=set(open_),
        tracked_pending=set(pending), confirm_flat=check, settle_seconds=settle,
    )


# ── rule 1: the broker holds it and an open row records it ─────────────────

async def test_position_held_and_recorded_open_releases_the_claim(sessions):
    sym = _sym()
    await _unknown_claim(sessions, sym)
    check, coro = _reconcile(sym, held={sym}, open_={sym})
    counts = await coro
    assert counts["released_position_tracked"] == 1
    assert await _rows(sessions) == []
    check.assert_not_awaited()  # no network read needed when a row records it
    (event,) = await _events(sessions)
    assert event.status == "released_by_evidence"
    assert event.payload["evidence"] == position_claim.EVIDENCE_POSITION_TRACKED
    assert event.payload["underlying"] == sym


async def test_position_held_with_no_recording_row_stays_blocked(sessions):
    """Exposure nobody has recorded is the adoption case — the claim is what
    keeps a second entry out while that is sorted out."""
    sym = _sym()
    await _unknown_claim(sessions, sym)
    check, coro = _reconcile(sym, held={sym}, flat=True)
    counts = await coro
    assert counts["still_unresolved"] == 1 and counts["released_broker_flat"] == 0
    assert len(await _rows(sessions)) == 1
    check.assert_not_awaited()
    assert await _events(sessions) == []


async def test_open_row_without_a_broker_position_stays_blocked(sessions):
    """The row says open but the broker shows nothing: the reconciler will book
    that close; releasing now would re-open a position still on the books."""
    sym = _sym()
    await _unknown_claim(sessions, sym)
    check, coro = _reconcile(sym, open_={sym}, flat=True)
    counts = await coro
    assert counts["still_unresolved"] == 1
    assert len(await _rows(sessions)) == 1
    check.assert_not_awaited()


async def test_a_pending_row_is_not_a_recorded_position(sessions):
    """Pending is itself unresolved. A broker-flat reading must not release a
    claim while a pending row is still waiting on its own resolution."""
    sym = _sym()
    await _unknown_claim(sessions, sym)
    check, coro = _reconcile(sym, pending={sym}, flat=True)
    counts = await coro
    assert counts["still_unresolved"] == 1
    assert len(await _rows(sessions)) == 1
    check.assert_not_awaited()


# ── rule 2: nothing held, nothing resting — confirmed, not assumed ─────────

async def test_broker_confirmed_flat_releases_a_settled_claim(sessions):
    sym = _sym()
    await _unknown_claim(sessions, sym, age_seconds=600)
    check, coro = _reconcile(sym, flat=True)
    counts = await coro
    assert counts["released_broker_flat"] == 1
    assert await _rows(sessions) == []
    check.assert_awaited_once_with(sym)
    (event,) = await _events(sessions)
    assert event.payload["evidence"] == position_claim.EVIDENCE_BROKER_FLAT
    assert event.payload["state"] == STATE_UNKNOWN


async def test_a_working_order_or_position_at_the_broker_keeps_the_claim(sessions):
    sym = _sym()
    await _unknown_claim(sessions, sym)
    check, coro = _reconcile(sym, flat=False)
    counts = await coro
    assert counts["still_unresolved"] == 1
    assert len(await _rows(sessions)) == 1
    check.assert_awaited_once()
    assert await _events(sessions) == []


async def test_a_flat_check_that_could_not_be_established_keeps_the_claim(sessions):
    """None means the read failed or the broker cannot answer. It is not flat."""
    sym = _sym()
    await _unknown_claim(sessions, sym)
    _, coro = _reconcile(sym, flat=None)
    counts = await coro
    assert counts["indeterminate"] == 1
    assert len(await _rows(sessions)) == 1


async def test_a_flat_check_that_raises_keeps_the_claim(sessions):
    sym = _sym()
    await _unknown_claim(sessions, sym)
    _, coro = _reconcile(sym, flat=AsyncMock(side_effect=ConnectionError("gateway down")))
    counts = await coro
    assert counts["unreachable"] == 1
    assert len(await _rows(sessions)) == 1


@pytest.mark.parametrize("answer", ["yes", 1, [], object()])
async def test_only_a_literal_true_counts_as_flat(sessions, answer):
    """A truthy-looking answer is not the broker saying True."""
    sym = _sym()
    await _unknown_claim(sessions, sym)
    _, coro = _reconcile(sym, flat=answer)
    counts = await coro
    assert counts["released_broker_flat"] == 0
    assert len(await _rows(sessions)) == 1


async def test_a_claim_inside_the_settle_window_is_not_released(sessions):
    """A just-accepted order can read as absent for a moment. The broker is not
    even asked until the claim is old enough to believe it."""
    sym = _sym()
    await _unknown_claim(sessions, sym, age_seconds=5)
    check, coro = _reconcile(sym, flat=True)
    counts = await coro
    assert counts["too_fresh"] == 1
    assert len(await _rows(sessions)) == 1
    check.assert_not_awaited()


async def test_the_passage_of_time_alone_releases_nothing(sessions):
    """Ten days old, no evidence either way: still blocked. Waiting is not
    evidence."""
    sym = _sym()
    await _unknown_claim(sessions, sym, age_seconds=10 * 24 * 3600)
    _, coro = _reconcile(sym, flat=None)
    await coro
    assert len(await _rows(sessions)) == 1


# ── bounded: it sits inside a time-budgeted poll cycle ─────────────────────

async def test_at_most_the_allowed_number_of_network_checks_are_made_per_call(sessions):
    syms = [_sym() for _ in range(5)]
    for sym in syms:
        await _unknown_claim(sessions, sym)
    check = AsyncMock(return_value=False)  # exposure: nothing is released
    counts = await position_claim.reconcile_from_evidence(
        scope=SCOPE, held=set(), tracked_open=set(), tracked_pending=set(),
        confirm_flat=check, max_flat_checks=2,
    )
    assert check.await_count == 2
    assert counts["still_unresolved"] == 2 and counts["deferred"] == 3
    assert len(await _rows(sessions)) == 5


async def test_deferred_claims_are_picked_up_on_the_next_call_oldest_first(sessions):
    """No claim starves: the oldest are asked first, and a later call reaches
    the ones that were deferred."""
    oldest, middle, newest = _sym(), _sym(), _sym()
    await _unknown_claim(sessions, oldest, age_seconds=900)
    await _unknown_claim(sessions, middle, age_seconds=700)
    await _unknown_claim(sessions, newest, age_seconds=500)
    asked: list[str] = []

    async def record(sym):
        asked.append(sym)
        return True

    first = await position_claim.reconcile_from_evidence(
        scope=SCOPE, held=set(), tracked_open=set(), tracked_pending=set(),
        confirm_flat=record, max_flat_checks=2,
    )
    assert asked == [oldest, middle] and first["deferred"] == 1
    assert [r.underlying for r in await _rows(sessions)] == [newest]

    await position_claim.reconcile_from_evidence(
        scope=SCOPE, held=set(), tracked_open=set(), tracked_pending=set(),
        confirm_flat=record, max_flat_checks=2,
    )
    assert asked == [oldest, middle, newest]
    assert await _rows(sessions) == []


async def test_releases_that_need_no_network_check_are_not_capped(sessions):
    """The cap protects the gateway; a release decided from local state costs
    it nothing and must not be held back by the cap."""
    syms = [_sym() for _ in range(4)]
    for sym in syms:
        await _unknown_claim(sessions, sym)
    check = AsyncMock(return_value=True)
    counts = await position_claim.reconcile_from_evidence(
        scope=SCOPE, held=set(syms), tracked_open=set(syms), tracked_pending=set(),
        confirm_flat=check, max_flat_checks=0,
    )
    assert counts["released_position_tracked"] == 4
    check.assert_not_awaited()


# ── what it must never touch ───────────────────────────────────────────────

async def test_a_claim_still_inside_its_submit_lease_is_never_touched(sessions):
    """`pending` means a live caller is about to submit. Broker-flat is exactly
    what it looks like before the order is sent."""
    sym = _sym()
    await _unknown_claim(sessions, sym, state=STATE_PENDING, age_seconds=600)
    check, coro = _reconcile(sym, flat=True)
    counts = await coro
    assert sum(counts.values()) == 0
    (row,) = await _rows(sessions)
    assert row.state == STATE_PENDING
    check.assert_not_awaited()


async def test_another_owners_claim_is_not_released(sessions):
    sym = _sym()
    await _unknown_claim(sessions, sym, scope="11111111-1111-1111-1111-111111111111")
    check, coro = _reconcile(sym, held={sym}, open_={sym}, flat=True, scope=SCOPE)
    counts = await coro
    assert sum(counts.values()) == 0
    assert len(await _rows(sessions)) == 1
    check.assert_not_awaited()


async def test_other_symbols_are_unaffected(sessions):
    kept, freed = _sym(), _sym()
    await _unknown_claim(sessions, kept)
    await _unknown_claim(sessions, freed)
    check = AsyncMock(side_effect=lambda s: s == freed)
    counts = await position_claim.reconcile_from_evidence(
        scope=SCOPE, held=set(), tracked_open=set(), tracked_pending=set(),
        confirm_flat=check,
    )
    assert counts["released_broker_flat"] == 1 and counts["still_unresolved"] == 1
    assert [r.underlying for r in await _rows(sessions)] == [kept]


# ── restart and races ──────────────────────────────────────────────────────

async def test_a_claim_left_by_a_dead_process_is_resolved_by_the_next_one(sessions, restarted):
    """The timeout happened in one process; the evidence arrives in another.
    Nothing in memory carries over — only the persisted claim does."""
    sym = _sym()
    await _unknown_claim(sessions, sym, age_seconds=600)

    async with restarted() as factory:
        assert len(await _rows(factory)) == 1  # the claim survived the "restart"
        counts = await position_claim.reconcile_from_evidence(
            scope=SCOPE, held=set(), tracked_open=set(), tracked_pending=set(),
            confirm_flat=AsyncMock(return_value=True),
        )
        assert counts["released_broker_flat"] == 1
        assert await _rows(factory) == []
        assert len(await _events(factory)) == 1


async def test_the_release_itself_refuses_a_claim_that_is_not_unresolved(sessions):
    """The selection query already skips `pending` claims; the delete must not
    rely on that. It is the last line between this code and a claim a live
    caller is about to submit under."""
    sym = _sym()
    claim = await position_claim.try_claim(sym, "equity", scope=SCOPE)  # state=pending
    assert claim is not None
    async with sessions() as s:
        row = (await s.execute(select(PositionClaim))).scalars().one()
    assert row.state == STATE_PENDING

    assert await position_claim._release_with_evidence(row, "x", "should refuse") is False
    (still,) = await _rows(sessions)
    assert still.claim_token == claim.token
    assert await _events(sessions) == []


async def test_a_claim_retaken_after_it_was_read_is_not_deleted_or_audited(sessions):
    """Release is keyed on the token and the state. If the row changed hands
    between the read and the delete, nothing is deleted and no audit row says
    otherwise."""
    sym = _sym()
    old = await _unknown_claim(sessions, sym)
    async with sessions() as s:
        stale = (await s.execute(select(PositionClaim))).scalars().one()
    await position_claim.resolve(old)                       # somebody else's resolve
    fresh = await position_claim.try_claim(sym, "equity", scope=SCOPE)  # and a new entry
    assert fresh is not None

    released = await position_claim._release_with_evidence(stale, "x", "stale read")
    assert released is False
    (row,) = await _rows(sessions)
    assert row.claim_token == fresh.token and row.state == STATE_PENDING
    assert await _events(sessions) == []
