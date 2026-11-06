"""The Copilot approval queue must not grow without bound.

2026-11: the Command Center announced "5,480 approvals awaiting review". `_queue_pending_approval`
appended a row for every qualifying scan while the desk was in Copilot (the same ~60 tickers,
every 15-30 minutes, for days) and nothing ever retired one, so the list the operator was meant
to review was thousands of stale copies of old signals, each describing a price and spread that
no longer existed. (`/pending` also returned all of them on every poll.)

Now a newer scan of the same decision (ticker + asset class + action + strategy) supersedes the
older pending one, and a pending approval older than APPROVAL_TTL_MINUTES expires. Both keep the
row (it is the audit trail) with a status and a note, and neither is ever executed.

Real PostgreSQL, because the thing under test is a conditional UPDATE keyed on JSON fields and
the database clock; a mocked session would accept any statement.
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.routes.trade_desk as td
from app.core.config import settings
from app.models.execution_event import ExecutionEvent

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DB_URL, reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL"),
]


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine(TEST_DB_URL, pool_size=5, max_overflow=5)
    async with engine.begin() as conn:
        await conn.run_sync(ExecutionEvent.__table__.drop, checkfirst=True)
        await conn.run_sync(ExecutionEvent.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    await engine.dispose()


def _sig(ticker="AMZN", action="BUY", asset="equity", strategy=None, sid=None):
    s = {"id": sid or f"sig-{uuid.uuid4().hex[:8]}", "ticker": ticker, "action": action, "asset_type": asset}
    if strategy:
        s["strategy"] = strategy
    return s


async def _rows(factory, kind="pending_approval"):
    async with factory() as s:
        return (await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.kind == kind).order_by(ExecutionEvent.created_at)
        )).scalars().all()


async def _age(factory, signal_id: str, minutes: int):
    """Back-date a row on the DATABASE clock, the one the expiry compares against."""
    async with factory() as s, s.begin():
        await s.execute(text(
            "UPDATE execution_events SET created_at = now() - make_interval(mins => :m) WHERE signal_id = :i"
        ), {"m": minutes, "i": signal_id})


async def _insert(factory, kind, status, signal_id, ticker="AMZN", age_minutes=0):
    async with factory() as s, s.begin():
        s.add(ExecutionEvent(kind=kind, signal_id=signal_id, ticker=ticker, asset_type="equity",
                             status=status, payload={"id": signal_id, "ticker": ticker, "action": "BUY"}))
    if age_minutes:
        await _age(factory, signal_id, age_minutes)


# ── a newer scan replaces the older one ─────────────────────────────────────

async def test_a_newer_scan_of_the_same_decision_supersedes_the_older_one(sessions):
    first, second = _sig(sid="s1"), _sig(sid="s2")
    await td._queue_pending_approval(first)
    await td._queue_pending_approval(second)

    by_id = {r.signal_id: r for r in await _rows(sessions)}
    assert by_id["s1"].status == "superseded" and by_id["s2"].status == "pending"
    note = by_id["s1"].payload["approval"]
    assert note["resolution"] == "superseded" and note["superseded_by"] == "s2"   # the audit trail says by what
    assert by_id["s1"].payload["ticker"] == "AMZN"                                  # original payload kept


async def test_fifty_rescans_leave_exactly_one_live_approval(sessions):
    for i in range(50):
        await td._queue_pending_approval(_sig(sid=f"s{i}"))
    rows = await _rows(sessions)
    assert len(rows) == 50
    assert [r.signal_id for r in rows if r.status == "pending"] == ["s49"]
    assert len(await td._get_pending_approvals()) == 1


@pytest.mark.parametrize("a,b", [
    (_sig(action="BUY"), _sig(action="SELL")),                                   # opposite directions
    (_sig(ticker="AMZN"), _sig(ticker="AAPL")),
    (_sig(asset="equity"), _sig(asset="options")),
    (_sig(asset="options", strategy="bull_put_spread"), _sig(asset="options", strategy="bear_call_spread")),
])
async def test_different_decisions_do_not_replace_each_other(sessions, a, b):
    await td._queue_pending_approval(a)
    await td._queue_pending_approval(b)
    assert [r.status for r in await _rows(sessions)] == ["pending", "pending"]


async def test_a_missing_strategy_matches_a_missing_strategy(sessions):
    await td._queue_pending_approval(_sig(sid="s1"))
    await td._queue_pending_approval(_sig(sid="s2"))
    assert {r.signal_id: r.status for r in await _rows(sessions)} == {"s1": "superseded", "s2": "pending"}


async def test_resolved_approvals_are_never_touched_by_a_newer_scan(sessions):
    for sid, status in (("a1", "approved"), ("r1", "rejected")):
        await _insert(sessions, "pending_approval", status, sid)
    await td._queue_pending_approval(_sig(sid="new"))
    assert {r.signal_id: r.status for r in await _rows(sessions)} == {"a1": "approved", "r1": "rejected", "new": "pending"}


async def test_a_superseded_approval_cannot_be_approved(sessions):
    await td._queue_pending_approval(_sig(sid="s1"))
    await td._queue_pending_approval(_sig(sid="s2"))
    assert await td._resolve_pending_approval("s1", "approved") is None       # the route turns this into a 404
    assert await td._resolve_pending_approval("s2", "approved") is not None


async def test_a_signal_without_a_ticker_is_queued_and_supersedes_nothing(sessions):
    await td._queue_pending_approval(_sig(sid="s1"))
    await td._queue_pending_approval({"id": "s2", "asset_type": "equity", "action": "BUY"})
    assert [r.status for r in await _rows(sessions)] == ["pending", "pending"]


# ── expiry ──────────────────────────────────────────────────────────────────

async def test_pending_approvals_older_than_the_ttl_expire_and_fresh_ones_do_not(sessions):
    await _insert(sessions, "pending_approval", "pending", "old", age_minutes=settings.approval_ttl_minutes + 5)
    await _insert(sessions, "pending_approval", "pending", "fresh", ticker="AAPL", age_minutes=5)

    assert await td._expire_stale_approvals() == 1
    by_id = {r.signal_id: r for r in await _rows(sessions)}
    assert by_id["old"].status == "expired" and by_id["fresh"].status == "pending"
    assert by_id["old"].payload["approval"]["resolution"] == "expired"
    assert str(settings.approval_ttl_minutes) in by_id["old"].payload["approval"]["reason"]


async def test_the_ttl_is_configurable(sessions):
    await _insert(sessions, "pending_approval", "pending", "x", age_minutes=30)
    with patch.object(settings, "approval_ttl_minutes", 60):
        assert await td._expire_stale_approvals() == 0
    with patch.object(settings, "approval_ttl_minutes", 10):
        assert await td._expire_stale_approvals() == 1


async def test_expiry_touches_only_pending_approvals(sessions):
    old = settings.approval_ttl_minutes + 60
    await _insert(sessions, "pending_approval", "approved", "ap", age_minutes=old)
    await _insert(sessions, "pending_approval", "rejected", "rj", age_minutes=old)
    await _insert(sessions, "execution", "submitted", "ex", age_minutes=old)       # the audit log
    await _insert(sessions, "rotation_review", "pending", "rr", age_minutes=old)    # a different queue
    assert await td._expire_stale_approvals() == 0
    statuses = {r.signal_id: r.status for k in ("pending_approval", "execution", "rotation_review")
                for r in await _rows(sessions, k)}
    assert statuses == {"ap": "approved", "rj": "rejected", "ex": "submitted", "rr": "pending"}


async def test_the_whole_backlog_expires_at_once(sessions):
    async with sessions() as s, s.begin():
        for i in range(500):
            s.add(ExecutionEvent(kind="pending_approval", signal_id=f"b{i}", ticker=f"T{i % 60}",
                                 asset_type="equity", status="pending",
                                 payload={"id": f"b{i}", "ticker": f"T{i % 60}", "action": "BUY"}))
    async with sessions() as s, s.begin():
        await s.execute(text("UPDATE execution_events SET created_at = now() - interval '3 days'"))
    assert await td._expire_stale_approvals() == 500
    assert await td._get_pending_approvals() == []
    assert all(r.status == "expired" for r in await _rows(sessions))                 # kept, not deleted


# ── what the operator is shown ──────────────────────────────────────────────

async def test_a_stale_approval_is_never_offered_even_if_expiry_did_not_run(sessions):
    await _insert(sessions, "pending_approval", "pending", "old", age_minutes=settings.approval_ttl_minutes + 5)
    await _insert(sessions, "pending_approval", "pending", "fresh", ticker="AAPL")
    assert [p["id"] for p in await td._get_pending_approvals()] == ["fresh"]


async def test_the_pending_route_expires_the_backlog_and_counts_only_live_ones(sessions):
    await _insert(sessions, "pending_approval", "pending", "old", age_minutes=settings.approval_ttl_minutes + 5)
    await _insert(sessions, "pending_approval", "pending", "fresh", ticker="AAPL")
    out = await td.get_pending()
    assert out["count"] == 1 and [p["id"] for p in out["pending"]] == ["fresh"]
    assert {r.signal_id: r.status for r in await _rows(sessions)} == {"old": "expired", "fresh": "pending"}


async def test_the_pending_route_still_answers_if_expiry_fails(sessions):
    await _insert(sessions, "pending_approval", "pending", "old", age_minutes=settings.approval_ttl_minutes + 5)
    await _insert(sessions, "pending_approval", "pending", "fresh", ticker="AAPL")
    with patch.object(td, "_expire_stale_approvals", new=AsyncMock(side_effect=RuntimeError("db hiccup"))):
        out = await td.get_pending()
    assert [p["id"] for p in out["pending"]] == ["fresh"]                            # the stale one stays hidden


async def test_a_failed_tidy_never_stops_a_signal_being_queued(sessions):
    with patch.object(td, "_supersede_pending_approvals", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await td._queue_pending_approval(_sig(sid="s1"))
    assert [(r.signal_id, r.status) for r in await _rows(sessions)] == [("s1", "pending")]


async def test_reject_all_still_clears_whatever_is_pending(sessions):
    await td._queue_pending_approval(_sig(ticker="AMZN", sid="s1"))
    await td._queue_pending_approval(_sig(ticker="AAPL", sid="s2"))
    with patch.object(td, "_log_execution", new=AsyncMock()):
        out = await td.reject_all_pending()
    assert out["rejected"] == 2
