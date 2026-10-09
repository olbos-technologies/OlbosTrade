"""Two signals for the same position must not both reach the broker.

_execute_signal Stage 3 reads the trades table for an open or pending row on
(underlying, asset class) and skips if it finds one. That read is correct and
insufficient: the row is written only AFTER the broker accepts, so two signals
arriving inside that round trip both read zero rows, both pass, and both
submit — one position, two economic orders. It is the same shape as the
Copilot approval bug, one layer down.

Real PostgreSQL, for the same reason as test_approval_concurrency_pg.py: what
decides the winner is ON CONFLICT against a primary key under contention, and
a mocked session returns whatever the test told it to. A check-then-insert and
an atomic claim are indistinguishable through a mock.

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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.position_claim import PositionClaim
from app.services import position_claim

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    # Opt out of conftest's always-succeeds stub: this module is the one that
    # tests the real thing.
    pytest.mark.real_position_claim,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(PositionClaim.__table__.drop, checkfirst=True)
        await conn.run_sync(PositionClaim.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    # position_claim imports AsyncSessionLocal inside each function, so
    # patching the source module is enough and does not depend on import order.
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.run_sync(PositionClaim.__table__.drop, checkfirst=True)
    await engine.dispose()


async def _rows(factory):
    async with factory() as s:
        return (await s.execute(select(PositionClaim))).scalars().all()


# ── the property under test ────────────────────────────────────────────────

@pytest.mark.parametrize("racers", [2, 4, 8])
async def test_only_one_concurrent_entry_claims_a_position(sessions, racers):
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"

    results = await asyncio.gather(
        *(position_claim.try_claim(ticker, "equity") for _ in range(racers))
    )

    assert sum(results) == 1, (
        f"{sum(results)} entries were cleared to submit for one position; "
        "each True becomes a broker order"
    )
    assert len(await _rows(sessions)) == 1


async def test_equity_and_options_on_the_same_underlying_do_not_block_each_other(sessions):
    """SPY shares and a SPY spread are different positions. Keying them
    together would false-block a legitimate entry."""
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"

    equity, options = await asyncio.gather(
        position_claim.try_claim(ticker, "equity"),
        position_claim.try_claim(ticker, "options"),
    )

    assert equity and options
    assert len(await _rows(sessions)) == 2


async def test_different_underlyings_do_not_block_each_other(sessions):
    a, b = await asyncio.gather(
        position_claim.try_claim("AAA", "equity"),
        position_claim.try_claim("BBB", "equity"),
    )
    assert a and b


async def test_the_key_is_normalised(sessions):
    """position_identity_key upper-cases. 'spy' and 'SPY' are one position."""
    assert await position_claim.try_claim("spy", "equity") is True
    assert await position_claim.try_claim("SPY", "equity") is False


# ── expiry rather than release ─────────────────────────────────────────────

async def test_an_expired_claim_does_not_wedge_the_symbol(sessions):
    """A process killed mid-entry leaves its claim behind. The next attempt
    must reap it, or that symbol is locked out until someone notices."""
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with sessions() as s, s.begin():
        s.add(PositionClaim(underlying=ticker, asset_class="equity",
                            claimed_at=past, expires_at=past))

    assert await position_claim.try_claim(ticker, "equity") is True
    rows = await _rows(sessions)
    assert len(rows) == 1 and rows[0].expires_at > datetime.now(timezone.utc)


async def test_a_live_claim_is_not_reaped_early(sessions):
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"
    assert await position_claim.try_claim(ticker, "equity", ttl_seconds=300) is True
    assert await position_claim.try_claim(ticker, "equity", ttl_seconds=300) is False


async def test_reaping_one_expired_claim_does_not_disturb_a_live_one(sessions):
    live = f"L{uuid.uuid4().hex[:6].upper()}"
    dead = f"D{uuid.uuid4().hex[:6].upper()}"
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    await position_claim.try_claim(live, "equity", ttl_seconds=300)
    async with sessions() as s, s.begin():
        s.add(PositionClaim(underlying=dead, asset_class="equity",
                            claimed_at=past, expires_at=past))

    assert await position_claim.try_claim(dead, "equity") is True
    assert await position_claim.try_claim(live, "equity") is False, (
        "reaping expired claims released a live one"
    )


async def test_release_frees_the_symbol_immediately(sessions):
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"
    assert await position_claim.try_claim(ticker, "equity", ttl_seconds=300) is True
    await position_claim.release(ticker, "equity")
    assert await position_claim.try_claim(ticker, "equity") is True


async def test_releasing_something_unheld_is_not_an_error(sessions):
    await position_claim.release("NOPE", "equity")


# ── the harness can see the failure it guards against ──────────────────────

async def test_check_then_insert_double_books(sessions):
    """The pattern this replaces, against the same database.

    Both callers read, find nothing, and insert — which is precisely what
    Stage 3's read does today across a broker round trip.
    """
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"
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
                "(underlying, asset_class, claimed_at, expires_at) "
                "VALUES (:u, 'equity', now(), now() + interval '120 seconds') "
                "ON CONFLICT DO NOTHING"
            ), {"u": ticker})
            return True

    results = await asyncio.gather(legacy_claim(), legacy_claim())

    assert sum(results) == 2, (
        "expected check-then-insert to clear both callers to submit; if it did "
        "not, this harness can no longer demonstrate the defect"
    )
