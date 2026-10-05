"""
The outbox drain (MASTER_ARCHITECTURE §8.1, §18).

The properties worth asserting are all about failure, not success: redelivery
after a crash, two workers racing, a handler that always throws, and an event
type nobody consumes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.messaging import (
    STATUS_DEAD_LETTER, STATUS_PENDING, STATUS_PUBLISHED,
    InboxReceipt, OutboxEvent,
)
from app.services import outbox_publisher as ob
from app.services.outbox_publisher import Handler

pytestmark = pytest.mark.asyncio

CONSUMER = "dispatch-worker"


def _event(event_type="order.dispatch", *, attempts=0, created_at=None):
    return OutboxEvent(
        id=uuid.uuid4(), aggregate_type="order", aggregate_id=uuid.uuid4(),
        event_type=event_type, event_version=1, payload={"ok": True},
        status=STATUS_PENDING, attempts=attempts,
        created_at=created_at or datetime.now(timezone.utc),
    )


class _DB:
    """Enough SQLAlchemy to exercise the drain, including its locking query."""

    def __init__(self, events=None, receipts=None, *, receipt_conflict=False):
        self.events = list(events or [])
        self.receipts = list(receipts or [])
        self.staged: list = []
        self.commits = 0
        self.rollbacks = 0
        self.receipt_conflict = receipt_conflict
        self.sql_seen: list[str] = []
        self.statements: list = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql_seen.append(sql)
        self.statements.append(stmt)
        params = stmt.compile().params

        if "inbox_receipts" in sql:
            hit = [r for r in self.receipts
                   if r.consumer == params.get("consumer_1")
                   and r.event_id == params.get("event_id_1")]
            return _Result(hit)

        rows = list(self.events)
        if "id_1" in params and "status_1" not in params:
            rows = [e for e in rows if e.id == params["id_1"]]
        elif params.get("status_1") == STATUS_PENDING:
            rows = sorted([e for e in rows if e.status == STATUS_PENDING],
                          key=lambda e: e.created_at)
            limit = params.get("param_1")
            if limit:
                rows = rows[:limit]
        return _Result(rows)

    def add(self, obj):
        if self.receipt_conflict and isinstance(obj, InboxReceipt):
            raise IntegrityError("insert", {}, Exception("duplicate receipt"))
        self.staged.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        for obj in self.staged:
            if isinstance(obj, InboxReceipt):
                self.receipts.append(obj)
        self.staged.clear()
        self.commits += 1

    async def rollback(self):
        self.staged.clear()
        self.rollbacks += 1


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


# ── Claiming ─────────────────────────────────────────────────────────────────

async def test_the_claim_locks_and_skips_locked_rows():
    """Read off the compiled statement, not the source.

    Without SKIP LOCKED a second worker either serialises behind the first or,
    with a plain read, dispatches the same order twice. A `.with_for_update()`
    inside a comment is what made an equivalent assertion vacuous on #70.

    Compiled against the POSTGRESQL dialect specifically. str(stmt) uses the
    default dialect, which renders FOR UPDATE and silently drops SKIP LOCKED —
    so a test that read the default string would assert the half that is not
    the point and pass with the clause missing.
    """
    db = _DB([_event()])
    await ob.claim_pending(db)
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    assert "SKIP LOCKED" in sql, (
        "two workers would take the same event, and a dispatch handler would "
        "send the same order twice"
    )


async def test_the_claim_takes_the_oldest_first():
    now = datetime.now(timezone.utc)
    old = _event(created_at=now - timedelta(hours=2))
    new = _event(created_at=now)
    db = _DB([new, old])
    claimed = await ob.claim_pending(db, limit=1)
    assert claimed == [old], "a newer event jumped the queue"


async def test_only_pending_events_are_claimed():
    pending, published = _event(), _event()
    published.status = STATUS_PUBLISHED
    db = _DB([published, pending])
    assert await ob.claim_pending(db) == [pending]


# ── The happy path ───────────────────────────────────────────────────────────

async def test_a_handled_event_is_published_with_a_receipt():
    seen = []
    db = _DB([_event()])
    handlers = {"order.dispatch": Handler(fn=lambda e: _record(seen, e))}

    result = await ob.drain_once(db, consumer=CONSUMER, handlers=handlers)

    assert result.published == 1 and result.failed == 0
    assert db.events[0].status == STATUS_PUBLISHED
    assert db.events[0].published_at is not None
    assert len(seen) == 1
    assert [(r.consumer, r.event_id) for r in db.receipts] == [(CONSUMER, db.events[0].id)]


async def _record(seen, event):
    seen.append(event.id)


# ── Redelivery and races ─────────────────────────────────────────────────────

async def test_a_redelivered_event_does_not_run_the_handler_again():
    """The crash this exists for: the side effect landed, the commit did not.

    On redelivery the receipt is already there, so the handler must NOT run —
    for a dispatch handler, running it again is a second order.
    """
    event = _event()
    calls = []
    db = _DB([event], receipts=[InboxReceipt(consumer=CONSUMER, event_id=event.id)])
    handlers = {"order.dispatch": Handler(fn=lambda e: _record(calls, e))}

    result = await ob.drain_once(db, consumer=CONSUMER, handlers=handlers)

    assert calls == [], "a redelivered event ran its handler a second time"
    assert result.skipped_duplicate == 1
    assert event.status == STATUS_PUBLISHED, "the bookkeeping was not completed"


async def test_a_receipt_for_a_different_consumer_does_not_suppress_delivery():
    """Receipts are per consumer. One consumer having seen an event says
    nothing about another, and treating it as global would silently drop
    deliveries as soon as a second consumer existed."""
    event = _event()
    calls = []
    db = _DB([event], receipts=[InboxReceipt(consumer="other", event_id=event.id)])
    handlers = {"order.dispatch": Handler(fn=lambda e: _record(calls, e))}

    result = await ob.drain_once(db, consumer=CONSUMER, handlers=handlers)
    assert result.published == 1 and len(calls) == 1


async def test_losing_the_receipt_race_is_a_hit_not_a_failure():
    """Two workers claimed it somehow; the insert collides. The work is done."""
    async def _noop(event):
        return None

    db = _DB([_event()], receipt_conflict=True)
    handlers = {"order.dispatch": Handler(fn=_noop)}

    result = await ob.drain_once(db, consumer=CONSUMER, handlers=handlers)

    assert result.skipped_duplicate == 1
    assert result.failed == 0, "a duplicate receipt was counted as a failure"
    assert db.rollbacks == 1


# ── External handlers ────────────────────────────────────────────────────────

async def test_an_external_handler_gets_its_receipt_before_the_side_effect():
    """§8.1: "record inbox receipts BEFORE or with side effects".

    A broker call cannot be rolled back. A receipt written afterwards is lost
    by the same crash that leaves the order at the broker, and the retry sends
    it twice. Before is the only safe order.
    """
    order = []
    db = _DB([_event()])

    async def _external(event):
        order.append("side-effect")

    original_add = db.add

    def _tracking_add(obj):
        if isinstance(obj, InboxReceipt):
            order.append("receipt")
        original_add(obj)

    db.add = _tracking_add
    handlers = {"order.dispatch": Handler(fn=_external, external=True)}

    await ob.drain_once(db, consumer=CONSUMER, handlers=handlers)

    assert order == ["receipt", "side-effect"], (
        "the receipt must be written before an effect that cannot be undone"
    )


async def test_an_internal_handler_gets_its_receipt_with_the_side_effect():
    """The inverse, so the ordering above is not accidental."""
    order = []
    db = _DB([_event()])

    async def _internal(event):
        order.append("side-effect")

    original_add = db.add

    def _tracking_add(obj):
        if isinstance(obj, InboxReceipt):
            order.append("receipt")
        original_add(obj)

    db.add = _tracking_add
    handlers = {"order.dispatch": Handler(fn=_internal, external=False)}

    await ob.drain_once(db, consumer=CONSUMER, handlers=handlers)
    assert order == ["side-effect", "receipt"]


# ── Failure, retry and dead-letter ───────────────────────────────────────────

async def test_a_failing_handler_increments_attempts_and_stays_pending():
    event = _event()
    db = _DB([event])

    async def _boom(e):
        raise RuntimeError("broker unreachable")

    result = await ob.drain_once(db, consumer=CONSUMER,
                                handlers={"order.dispatch": Handler(fn=_boom)})

    assert result.failed == 1 and result.dead_lettered == 0
    assert event.attempts == 1
    assert event.status == STATUS_PENDING, "a retryable failure was not retried"
    assert "broker unreachable" in event.last_error


async def test_the_counter_survives_the_rollback():
    """The bug this guards: writing the counter in the transaction that just
    failed rolls the counter back too, and the event retries forever with
    attempts stuck at zero."""
    event = _event(attempts=2)
    db = _DB([event])

    async def _boom(e):
        raise RuntimeError("still down")

    await ob.drain_once(db, consumer=CONSUMER,
                        handlers={"order.dispatch": Handler(fn=_boom)})
    assert event.attempts == 3


async def test_enough_failures_park_the_event():
    """§18: retrying forever turns one bad event into a loop that starves
    every good one behind it."""
    event = _event(attempts=4)
    db = _DB([event])

    async def _boom(e):
        raise RuntimeError("permanently broken")

    result = await ob.drain_once(db, consumer=CONSUMER,
                                 handlers={"order.dispatch": Handler(fn=_boom)},
                                 max_attempts=5)

    assert result.dead_lettered == 1
    assert event.status == STATUS_DEAD_LETTER
    assert event.attempts == 5


async def test_a_dead_lettered_event_is_not_claimed_again():
    event = _event()
    event.status = STATUS_DEAD_LETTER
    assert await ob.claim_pending(_DB([event])) == []


async def test_the_error_column_is_truncated():
    """An error is diagnostic. An unbounded one is a payload copied into a
    column that was never meant to hold it."""
    event = _event()
    db = _DB([event])

    async def _boom(e):
        raise RuntimeError("x" * 5000)

    await ob.drain_once(db, consumer=CONSUMER,
                        handlers={"order.dispatch": Handler(fn=_boom)})
    assert len(event.last_error) <= 500


async def test_one_bad_event_does_not_stop_the_batch():
    """A loop that dies on one event stops delivering every good one behind it."""
    bad, good = _event(), _event("order.ack")
    db = _DB([bad, good])
    done = []

    async def _boom(e):
        raise RuntimeError("nope")

    result = await ob.drain_once(db, consumer=CONSUMER, handlers={
        "order.dispatch": Handler(fn=_boom),
        "order.ack": Handler(fn=lambda e: _record(done, e)),
    })

    assert result.failed == 1 and result.published == 1
    assert done == [good.id]


async def test_a_vanished_event_does_not_crash_the_failure_path():
    """The row is re-read in a fresh transaction after a failure, and it can
    be gone by then — pruned, or requeued and handled by another worker. The
    drain must survive losing the row it was about to record against."""
    event = _event()
    db = _DB([event])

    async def _boom(e):
        db.events.clear()                 # disappears mid-failure
        raise RuntimeError("gone")

    result = await ob.drain_once(db, consumer=CONSUMER,
                                 handlers={"order.dispatch": Handler(fn=_boom)})

    assert result.failed == 1 and result.dead_lettered == 0


# ── Unknown event types ──────────────────────────────────────────────────────

async def test_an_unhandled_event_type_is_left_pending_not_failed():
    """Normal during a rollout: one version publishes what another has not
    learned to read. Failing it would dead-letter events that are fine."""
    event = _event("order.something.new")
    db = _DB([event])

    result = await ob.drain_once(db, consumer=CONSUMER, handlers={})

    assert result.unhandled == 1
    assert result.failed == 0 and result.dead_lettered == 0
    assert event.status == STATUS_PENDING
    assert event.attempts == 0, "an unconsumed event burned a retry"


# ── Dead-letter requeue ──────────────────────────────────────────────────────

async def test_requeue_clears_the_attempt_count():
    event = _event(attempts=5)
    event.status = STATUS_DEAD_LETTER
    db = _DB([event])

    assert await ob.requeue_dead_letter(db, event.id) is True
    assert event.status == STATUS_PENDING
    assert event.attempts == 0 and event.last_error is None


async def test_requeue_refuses_an_event_that_is_not_parked():
    """Requeueing a pending or published event would redeliver a side effect
    nobody asked to repeat."""
    event = _event()
    assert await ob.requeue_dead_letter(_DB([event]), event.id) is False
    event.status = STATUS_PUBLISHED
    assert await ob.requeue_dead_letter(_DB([event]), event.id) is False


async def test_requeue_of_an_unknown_id_is_false_not_an_error():
    assert await ob.requeue_dead_letter(_DB([]), uuid.uuid4()) is False


async def test_drain_result_reports_whether_it_did_work():
    assert ob.DrainResult(claimed=0).did_work is False
    assert ob.DrainResult(claimed=1).did_work is True
