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
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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
        await conn.run_sync(PositionClaim.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
    await engine.dispose()


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
        return None                     # the broker positively has no such order

    out = await position_claim.reconcile_unresolved(lookup)
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
        return {"status": "filled"}

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

    out = await position_claim.reconcile_unresolved(lookup)
    assert out == {"released": 0, "still_unresolved": 0, "unreachable": 0}


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
