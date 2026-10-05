"""
Draining the transactional outbox (MASTER_ARCHITECTURE §8.1, §12, §18).

§8.1 requires that order creation and outbox creation happen in one
transaction, so an event is committed WITH the state change that produced it
and a crash can only ever mean "not published yet". This module is the other
half: the loop that moves committed events out, exactly once in effect.

WHAT "DURABLE" MEANS HERE, PRECISELY. The durability is in Postgres, not in
this process. Every claim, outcome and receipt is a committed row, so a worker
that dies mid-batch loses nothing: the rows it had not finished are still
pending and the next pass picks them up. The LOOP is still in-process asyncio
-- extracting it into its own service is §21 Phase 3, and calling this a
durable job system before that would be overclaiming. What it is: a restartable
drain over durable state.

DELIVERY IS AT-LEAST-ONCE. A handler WILL see the same event twice: a crash
after the side effect and before the commit is indistinguishable from a crash
before both. The InboxReceipt is what makes the second delivery a no-op, and
its primary key (consumer, event_id) is what makes that a database guarantee
rather than a convention.

HANDLERS MUST BE IDEMPOTENT, and for an external side effect that is not
optional. A handler that calls a broker cannot roll back; §8.1's rule applies
to it directly -- a timeout is AMBIGUOUS, not a retry. Such a handler records
its own attempt before acting and resolves it afterwards. This module does not
and cannot do that for it, which is why `external` is a declared property of a
handler rather than something inferred.

NOTHING IS REGISTERED YET. The dispatch handler that submits orders is Phase
2's second action, and it is not here: this is the mechanism, reviewable before
anything rides on it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.messaging import (
    STATUS_DEAD_LETTER, STATUS_PENDING, STATUS_PUBLISHED,
    InboxReceipt, OutboxEvent,
)

logger = logging.getLogger(__name__)

#: How many events one pass claims. Small on purpose: a batch is one
#: transaction's worth of lock, and a long one holds rows away from every
#: other worker.
DEFAULT_BATCH_SIZE = 20

#: After this many failures an event stops being retried and is parked for an
#: operator (§18's dead-letter operations). Retrying forever turns one bad
#: event into a loop that starves every good one behind it.
DEFAULT_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Handler:
    """What to do with one event type.

    `external` marks a handler whose side effect leaves this process and
    therefore cannot be rolled back. It is declared rather than detected
    because only the author of the handler knows, and getting it wrong in the
    optimistic direction means a retried broker call.
    """

    fn: Callable[[OutboxEvent], Awaitable[None]]
    external: bool = False


@dataclass
class DrainResult:
    claimed: int = 0
    published: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    dead_lettered: int = 0
    unhandled: int = 0

    @property
    def did_work(self) -> bool:
        return self.claimed > 0


async def claim_pending(db, *, limit: int = DEFAULT_BATCH_SIZE) -> list[OutboxEvent]:
    """Lock a batch of pending events for this worker alone.

    SKIP LOCKED is the whole point: two workers draining concurrently must
    take DIFFERENT rows, not block on each other and not take the same one.
    Without it a second worker either serialises behind the first (no
    throughput gained) or, with a plain read, dispatches the same order twice.

    Oldest first, so a persistently failing event cannot starve newer ones
    indefinitely -- it dead-letters out of the way after DEFAULT_MAX_ATTEMPTS.
    """
    result = await db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.status == STATUS_PENDING)
        .order_by(OutboxEvent.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(result.scalars().all())


async def _already_processed(db, consumer: str, event_id) -> bool:
    found = await db.execute(
        select(InboxReceipt.event_id).where(
            InboxReceipt.consumer == consumer,
            InboxReceipt.event_id == event_id,
        )
    )
    return found.scalar_one_or_none() is not None


async def drain_once(
    db,
    *,
    consumer: str,
    handlers: Mapping[str, Handler],
    limit: int = DEFAULT_BATCH_SIZE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> DrainResult:
    """One pass: claim a batch, run each handler, record what happened.

    Returns counts rather than raising, because a loop that dies on one bad
    event stops delivering every good one behind it. Failures are recorded on
    the row and surfaced in the result; the caller decides whether to care.
    """
    result = DrainResult()
    events = await claim_pending(db, limit=limit)
    result.claimed = len(events)

    for event in events:
        handler = handlers.get(event.event_type)

        if handler is None:
            # Not an error, and deliberately not a failure: an event type this
            # deployment does not consume is normal during a rollout where one
            # version publishes what another has not learned to read yet.
            # Counted so it is visible, left pending so the consumer that does
            # understand it still gets it.
            result.unhandled += 1
            logger.debug("No handler for %s (event %s)", event.event_type, event.id)
            continue

        if await _already_processed(db, consumer, event.id):
            # Redelivery after a crash between the side effect and the commit.
            # The effect already happened; only the bookkeeping is missing.
            _mark_published(event)
            result.skipped_duplicate += 1
            continue

        try:
            if handler.external:
                # §8.1: "consumers record inbox receipts BEFORE or with side
                # effects". For an effect that cannot be rolled back, before is
                # the only safe order -- a receipt written afterwards is lost
                # by the same crash that leaves the effect in place, and the
                # retry repeats it. At-most-once attempt, and the handler owns
                # resolving an outcome it did not see.
                db.add(InboxReceipt(consumer=consumer, event_id=event.id))
                await db.flush()
                await handler.fn(event)
            else:
                await handler.fn(event)
                db.add(InboxReceipt(consumer=consumer, event_id=event.id))

            _mark_published(event)
            await db.commit()
            result.published += 1

        except IntegrityError:
            # Another worker inserted the same receipt between the check above
            # and this insert. Losing that race means the work is done, which
            # is a hit rather than a failure.
            await db.rollback()
            result.skipped_duplicate += 1
            logger.debug("Receipt race on event %s; another worker had it", event.id)

        except Exception as exc:                      # noqa: BLE001
            await db.rollback()
            dead = await _record_failure(db, event.id, exc, max_attempts)
            if dead:
                result.dead_lettered += 1
            else:
                result.failed += 1

    return result


def _mark_published(event: OutboxEvent) -> None:
    event.status = STATUS_PUBLISHED
    event.published_at = datetime.now(timezone.utc)


async def _record_failure(db, event_id, exc: Exception, max_attempts: int) -> bool:
    """Count the failure and park the event if it has had enough. True if parked.

    Re-read in a fresh transaction because the rollback above discarded the
    row's identity map entry; writing the counter in the transaction that just
    failed would roll the counter back too, and the event would retry forever
    with attempts stuck at zero.
    """
    row = (await db.execute(
        select(OutboxEvent).where(OutboxEvent.id == event_id)
    )).scalar_one_or_none()
    if row is None:
        return False

    row.attempts = (row.attempts or 0) + 1
    # str(exc), never the exception object and never a payload: an event
    # carrying a credential must not have it copied into an error column.
    row.last_error = str(exc)[:500]

    parked = row.attempts >= max_attempts
    if parked:
        row.status = STATUS_DEAD_LETTER
        logger.error(
            "Outbox event %s dead-lettered after %s attempts: %s",
            event_id, row.attempts, row.last_error,
        )
    else:
        logger.warning(
            "Outbox event %s failed (attempt %s/%s): %s",
            event_id, row.attempts, max_attempts, row.last_error,
        )
    await db.commit()
    return parked


async def requeue_dead_letter(db, event_id) -> bool:
    """Put a parked event back, with its attempt count cleared.

    The operator action §18 asks for. Deliberately manual: an event reached
    dead-letter because something was wrong, and automatic requeue is how a
    permanent failure becomes a permanent loop.
    """
    row = (await db.execute(
        select(OutboxEvent).where(OutboxEvent.id == event_id)
    )).scalar_one_or_none()
    if row is None or row.status != STATUS_DEAD_LETTER:
        return False
    row.status = STATUS_PENDING
    row.attempts = 0
    row.last_error = None
    await db.commit()
    logger.info("Outbox event %s requeued from dead-letter", event_id)
    return True
