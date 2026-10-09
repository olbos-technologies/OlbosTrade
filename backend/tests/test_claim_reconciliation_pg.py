"""Crash, restart, ask the broker, resolve safely — end to end, real database.

This is the scenario the whole claim lifecycle exists for:

    a worker records intent, calls the broker, and dies before it hears back

What it left behind blocks new entries on that position. Recovery has to
establish what became of the order, and the only acceptable error is leaving
the claim blocked for too long — never releasing one whose order is live.

The tests drive the real service against real PostgreSQL. "Restart" means a
new engine, a new connection pool and a new session factory over the same
rows: nothing in memory survives, which is the point.

The late-appearing order is the case a bare timeout gets wrong. A broker that
has accepted an order can answer "no such order" for a short while, so a
sweep that runs inside that window must not take the answer as absence —
otherwise recovery is the thing that creates the duplicate.
"""
from __future__ import annotations

import contextlib
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from unittest.mock import patch

from app.broker.broker_interface import OrderLookup
from app.models.execution_event import ExecutionEvent
from app.models.position_claim import (
    STATE_SUBMITTED, STATE_UNKNOWN, PositionClaim,
)
from app.services import claim_reconciliation, position_claim

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


@contextlib.asynccontextmanager
async def _restarted():
    """A new process over the same rows."""
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        with patch("app.core.database.AsyncSessionLocal", factory):
            yield factory
    finally:
        await engine.dispose()


def _sym() -> str:
    return f"T{uuid.uuid4().hex[:6].upper()}"


class _Broker:
    """A broker whose answer the test controls, counting how often it is asked."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.calls: list[str] = []

    async def find_order_by_client_order_id(self, key):
        self.calls.append(key)
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0]


async def _rows(factory):
    async with factory() as s:
        return list((await s.execute(select(PositionClaim))).scalars().all())


async def _crash_mid_submit(ticker, asset_class="options"):
    """Record intent, then stop — the worker never heard back."""
    claim = await position_claim.try_claim(ticker, asset_class)
    assert claim is not None
    await position_claim.mark_submitted(claim)
    await position_claim.mark_unknown(claim, "worker died mid-submit")
    return claim


# ── crash → restart → lookup → safe resolution ─────────────────────────────

async def test_a_crashed_submit_is_released_when_the_broker_has_no_such_order(sessions):
    """The full recovery path, with the broker giving an authoritative answer."""
    ticker = _sym()
    claim = await _crash_mid_submit(ticker)

    async with _restarted():
        # The claim is still blocking, on persisted state alone.
        assert await position_claim.try_claim(ticker, "options") is None

        broker = _Broker(OrderLookup.NOT_FOUND)
        counts = await claim_reconciliation.reconcile_once(
            broker=broker, settle_seconds=0,
        )

        assert counts["released"] == 1
        assert broker.calls == [claim.idempotency_key]
        # And the position is open for business again.
        regained = await position_claim.try_claim(ticker, "options")
        assert regained is not None


async def test_a_crashed_submit_stays_blocked_when_the_order_is_real(sessions):
    """Recovery must not free a claim whose order exists."""
    ticker = _sym()
    await _crash_mid_submit(ticker)

    async with _restarted():
        counts = await claim_reconciliation.reconcile_once(
            broker=_Broker(OrderLookup.FOUND), settle_seconds=0,
        )

        assert counts["released"] == 0
        assert counts["still_unresolved"] == 1
        assert await position_claim.try_claim(ticker, "options") is None


async def test_a_crashed_submit_stays_blocked_when_the_broker_cannot_answer(sessions):
    """An unreachable broker is not evidence of anything."""
    ticker = _sym()
    await _crash_mid_submit(ticker)

    async with _restarted():
        counts = await claim_reconciliation.reconcile_once(
            broker=_Broker(OrderLookup.UNDETERMINED), settle_seconds=0,
        )

        assert counts["released"] == 0
        assert counts["indeterminate"] >= 1
        assert len(await _rows(sessions)) == 1


async def test_an_equity_crash_is_never_released_by_the_sweep(sessions):
    """No client id was sent, so no lookup can speak to it.

    The claim stays until an operator clears it. That is the correct outcome
    and the reason the audited override exists.
    """
    ticker = _sym()
    await _crash_mid_submit(ticker, asset_class="equity")

    async with _restarted():
        broker = _Broker(OrderLookup.NOT_FOUND)
        counts = await claim_reconciliation.reconcile_once(
            broker=broker, settle_seconds=0,
        )

        assert counts["released"] == 0
        assert broker.calls == [], "the broker was asked about an untransmitted key"
        assert len(await _rows(sessions)) == 1


# ── the order that shows up late ───────────────────────────────────────────

async def test_an_order_that_appears_late_is_not_mistaken_for_absence(sessions):
    """The case a bare timeout gets wrong, and the duplicate it would cause.

    Sweep one runs while the order is not yet visible: the broker says
    NOT_FOUND. If recovery believed it, the claim would be released and the
    next dispatch would open a second position on top of the live order.

    The settle window is what stops that, so the first sweep reports
    `too_fresh` and releases nothing. By the second sweep the order is
    visible, and the claim is correctly held for position reconciliation.
    """
    ticker = _sym()
    await _crash_mid_submit(ticker)

    # Not yet visible, then visible.
    broker = _Broker(OrderLookup.NOT_FOUND, OrderLookup.FOUND)

    async with _restarted():
        first = await claim_reconciliation.reconcile_once(
            broker=broker, settle_seconds=3600,   # claim is seconds old
        )
        assert first["released"] == 0, (
            "a not-yet-visible order was read as absence — this is the "
            "duplicate the settle window exists to prevent"
        )
        assert first["too_fresh"] == 1
        assert await position_claim.try_claim(ticker, "options") is None

        second = await claim_reconciliation.reconcile_once(
            broker=broker, settle_seconds=0,
        )
        assert second["released"] == 0
        assert second["still_unresolved"] == 1
        assert await position_claim.try_claim(ticker, "options") is None


async def test_waiting_alone_never_establishes_absence(sessions):
    """Past the settle window, silence is still silence.

    The window makes an authoritative NOT_FOUND believable. It does not
    convert an unanswered lookup into one.
    """
    ticker = _sym()
    await _crash_mid_submit(ticker)

    async with _restarted():
        counts = await claim_reconciliation.reconcile_once(
            broker=_Broker(OrderLookup.UNDETERMINED),
            settle_seconds=0,            # as if the window had long passed
        )

    assert counts["released"] == 0
    assert len(await _rows(sessions)) == 1


# ── bounded retries ────────────────────────────────────────────────────────

async def test_retries_are_bounded_when_the_broker_never_answers(sessions):
    """It must give up within the tick rather than retry forever."""
    await _crash_mid_submit(_sym())
    broker = _Broker(OrderLookup.UNDETERMINED)

    with patch.object(claim_reconciliation, "RETRY_BASE_SECONDS", 0.0):
        counts = await claim_reconciliation.reconcile_once(
            broker=broker, settle_seconds=0,
        )

    assert counts["attempts"] == claim_reconciliation.MAX_ATTEMPTS
    assert len(broker.calls) == claim_reconciliation.MAX_ATTEMPTS


async def test_a_definite_answer_is_not_retried(sessions):
    """Re-asking a broker that already answered just repeats the answer."""
    await _crash_mid_submit(_sym())
    broker = _Broker(OrderLookup.FOUND)

    with patch.object(claim_reconciliation, "RETRY_BASE_SECONDS", 0.0):
        counts = await claim_reconciliation.reconcile_once(
            broker=broker, settle_seconds=0,
        )

    assert counts["attempts"] == 1
    assert len(broker.calls) == 1


async def test_nothing_unresolved_is_a_cheap_no_op(sessions):
    """The common case: the sweep runs every five minutes and finds nothing."""
    broker = _Broker(OrderLookup.NOT_FOUND)

    counts = await claim_reconciliation.reconcile_once(broker=broker, settle_seconds=0)

    assert counts["attempts"] == 1
    assert broker.calls == []
    assert counts.get("released", 0) == 0


async def test_an_exhausted_sweep_names_what_is_still_blocked(caplog):
    """An operator needs the positions, not just a count."""
    import logging

    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.run_sync(PositionClaim.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        with patch("app.core.database.AsyncSessionLocal", factory):
            ticker = _sym()
            await _crash_mid_submit(ticker)
            with caplog.at_level(logging.CRITICAL), \
                 patch.object(claim_reconciliation, "RETRY_BASE_SECONDS", 0.0):
                await claim_reconciliation.reconcile_once(
                    broker=_Broker(OrderLookup.UNDETERMINED), settle_seconds=0,
                )
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await engine.dispose()

    critical = "\n".join(r.getMessage() for r in caplog.records
                         if r.levelno >= logging.CRITICAL)
    assert ticker in critical, "the blocked position was not named"
    assert "BLOCKED" in critical
    assert "/api/admin/position-claims" in critical, (
        "the log should say how to resolve it"
    )


async def test_a_sweep_failure_does_not_raise(sessions):
    """Reconciliation runs from startup and the scheduler; it must not kill either."""
    await _crash_mid_submit(_sym())

    async def _boom(_claim):
        raise RuntimeError("broker exploded")

    with patch("app.services.claim_lookup.lookup_for", return_value=_boom), \
         patch.object(claim_reconciliation, "RETRY_BASE_SECONDS", 0.0):
        counts = await claim_reconciliation.reconcile_once(settle_seconds=0)

    assert counts["unreachable"] >= 1
    assert counts["released"] == 0
