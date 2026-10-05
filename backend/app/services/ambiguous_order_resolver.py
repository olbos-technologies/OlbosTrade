"""
Resolving AMBIGUOUS orders against broker truth (MASTER_ARCHITECTURE §8, §8.1).

The other half of the state the OMS state machine is built around. AMBIGUOUS
means a submission left the process and no answer came back; the order may be
working, filled, cancelled, or may never have reached the broker. Until now
only a human could clear it. This asks the broker.

NOT the position reconciler. services/position_reconciler.py compares broker
POSITIONS against the legacy Trade model and halts trading on a mismatch. This
resolves individual OMS ORDERS by the client order id recorded on their
dispatch attempt. Different question, different records, no overlap.

THE LOOKUP IS INJECTED, like crypto_scan's bars_fetcher, so the dangerous paths
are testable without a broker and without the network.

THE ASYMMETRY THAT DECIDES EVERY RULE BELOW. Concluding "the broker never got
it" when the broker did get it leads to a re-submission and two real positions.
Concluding "needs a human" when the answer was obvious costs a delay. So every
uncertain case resolves towards a human, and only unambiguous broker evidence
moves an order on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Optional, Sequence

from sqlalchemy import select

from app.models.oms_order import Fill, Order, OrderAttempt, OrderEvent
from app.services import order_state_machine as sm

logger = logging.getLogger(__name__)

# ── What a broker lookup can say ─────────────────────────────────────────────

LOOKUP_WORKING = "working"
LOOKUP_PARTIALLY_FILLED = "partially_filled"
LOOKUP_FILLED = "filled"
LOOKUP_CANCELED = "canceled"
LOOKUP_REJECTED = "rejected"
LOOKUP_NOT_FOUND = "not_found"
LOOKUPS = (LOOKUP_WORKING, LOOKUP_PARTIALLY_FILLED, LOOKUP_FILLED,
           LOOKUP_CANCELED, LOOKUP_REJECTED, LOOKUP_NOT_FOUND)


@dataclass(frozen=True)
class BrokerFill:
    execution_id: str
    quantity: Decimal
    price: Decimal
    executed_at: datetime
    fee: Optional[Decimal] = None


@dataclass(frozen=True)
class BrokerOrderView:
    """What the broker says about one client order id."""

    status: str
    broker_order_id: Optional[str] = None
    fills: Sequence[BrokerFill] = field(default_factory=tuple)


#: (client_order_id) -> what the broker says. Raising is a failed lookup, which
#: is NOT the same as LOOKUP_NOT_FOUND and must never be treated as one.
BrokerLookup = Callable[[str], Awaitable[BrokerOrderView]]


@dataclass
class Resolution:
    order_id: str
    outcome: str
    from_state: str
    to_state: Optional[str]
    fills_recorded: int = 0
    detail: str = ""

    @property
    def resolved(self) -> bool:
        return self.to_state is not None


async def resolve_one(db, order: Order, lookup: BrokerLookup) -> Resolution:
    """Ask the broker about one AMBIGUOUS order and record what it says."""
    if order.state != sm.AMBIGUOUS:
        return Resolution(str(order.id), "not_ambiguous", order.state, None,
                          detail=f"order is {order.state}")

    attempt = await _latest_attempt(db, order.id)
    if attempt is None:
        # Nothing was ever dispatched, so there is no id to ask about. An
        # order cannot reach AMBIGUOUS without an attempt, so this is a bug
        # rather than a market condition -- and a human should see it.
        return await _transition(
            db, order, sm.MANUAL_REVIEW, "resolver",
            "ambiguous with no dispatch attempt to look up", outcome="no_attempt")

    try:
        view = await lookup(attempt.client_order_id)
    except Exception as exc:                                   # noqa: BLE001
        # A failed lookup says NOTHING about the order. Leaving it AMBIGUOUS is
        # the only safe answer: treating an unreachable broker as "no order"
        # is precisely how a live order gets re-submitted.
        logger.warning("Lookup failed for order %s: %s", order.id, exc)
        return Resolution(str(order.id), "lookup_failed", order.state, None,
                          detail=str(exc)[:300])

    if view.status not in LOOKUPS:
        return await _transition(
            db, order, sm.MANUAL_REVIEW, "resolver",
            f"broker returned unrecognised status {view.status!r}",
            outcome="unrecognised")

    if attempt.broker_order_id is None and view.broker_order_id:
        attempt.broker_order_id = view.broker_order_id

    recorded = await _record_fills(db, order, view.fills)

    if view.status == LOOKUP_FILLED:
        return await _transition(db, order, sm.FILLED, "reconciliation",
                                 "broker reports filled", fills=recorded)

    if view.status == LOOKUP_CANCELED:
        return await _transition(db, order, sm.CANCELED, "reconciliation",
                                 "broker reports cancelled", fills=recorded)

    if view.status == LOOKUP_WORKING:
        return await _transition(db, order, sm.ACKNOWLEDGED, "reconciliation",
                                 "broker reports working", fills=recorded)

    if view.status == LOOKUP_PARTIALLY_FILLED:
        # Two transitions, because §7.5 has no AMBIGUOUS -> PARTIALLY_FILLED
        # edge: an order becomes acknowledged first and partial after. Doing it
        # in one step would mean writing a state the table forbids.
        await _transition(db, order, sm.ACKNOWLEDGED, "reconciliation",
                          "broker reports partially filled", commit=False)
        return await _transition(db, order, sm.PARTIALLY_FILLED, "reconciliation",
                                 "broker reports partially filled", fills=recorded)

    if view.status == LOOKUP_REJECTED:
        # §7.5 has no AMBIGUOUS -> REJECTED edge. A rejection discovered during
        # recovery therefore goes to a human rather than to the state that
        # describes it. That looks like a gap in the spec's table rather than a
        # deliberate omission, and it is left as one rather than quietly
        # widened here -- extending the state machine is a spec change.
        return await _transition(
            db, order, sm.MANUAL_REVIEW, "reconciliation",
            "broker reports rejected; §7.5 has no AMBIGUOUS -> REJECTED edge",
            outcome="rejected_needs_review")

    # LOOKUP_NOT_FOUND.
    #
    # §8.1 permits a retry "after broker lookup proves that no economic order
    # exists", and this is that lookup -- but it is deliberately NOT treated as
    # proof here. A not-found can also mean the wrong account was queried, or
    # that the broker's order search is not yet consistent with a submission
    # made seconds ago. Acting on it automatically turns either of those into a
    # duplicate position, and this client has never been exercised against a
    # live Alpaca account. A human decides, and re-raising the trade is a new
    # intent rather than a retry of this order.
    #
    # Tightening this to an automatic resolution is a later decision that
    # should be backed by evidence from paper trading, not assumed now.
    return await _transition(
        db, order, sm.MANUAL_REVIEW, "reconciliation",
        "broker has no record of this order; a human decides whether it was "
        "never received or the lookup was wrong",
        outcome="not_found_needs_review")


async def sweep(db, lookup: BrokerLookup, *, limit: int = 50) -> list[Resolution]:
    """Resolve every AMBIGUOUS order, oldest first.

    §21 Phase 2's "scheduled reconciliation". One bad order does not stop the
    sweep: an exception inside resolve_one is already caught per order, and
    anything it re-raises is recorded and skipped rather than abandoning the
    rest of the batch.
    """
    rows = (await db.execute(
        select(Order)
        .where(Order.state == sm.AMBIGUOUS)
        .order_by(Order.created_at)
        .limit(limit)
    )).scalars().all()

    out: list[Resolution] = []
    for order in rows:
        try:
            out.append(await resolve_one(db, order, lookup))
        except Exception as exc:                               # noqa: BLE001
            logger.exception("Resolver raised on order %s", order.id)
            out.append(Resolution(str(order.id), "resolver_error", order.state,
                                  None, detail=str(exc)[:300]))
    return out


# ── Internals ────────────────────────────────────────────────────────────────

async def _latest_attempt(db, order_id) -> Optional[OrderAttempt]:
    return (await db.execute(
        select(OrderAttempt)
        .where(OrderAttempt.order_id == order_id)
        .order_by(OrderAttempt.attempt_number.desc())
        .limit(1)
    )).scalar_one_or_none()


async def _record_fills(db, order: Order, fills: Sequence[BrokerFill]) -> int:
    """Insert fills the ledger does not already hold.

    Checked here as well as by the unique index on
    (broker, account_ref, broker_execution_id): the index is the guarantee, and
    this keeps a repeated sweep from spending a transaction on a conflict it
    can see coming. A sweep runs repeatedly over the same orders by design.
    """
    if not fills:
        return 0

    existing = set((await db.execute(
        select(Fill.broker_execution_id).where(Fill.order_id == order.id)
    )).scalars().all())

    recorded = 0
    for f in fills:
        if f.execution_id in existing:
            continue
        db.add(Fill(
            order_id=order.id,
            broker_execution_id=f.execution_id,
            broker=getattr(order, "broker", "alpaca"),
            account_ref="",
            quantity=f.quantity,
            price=f.price,
            fee=f.fee,
            executed_at=f.executed_at,
        ))
        recorded += 1

    if recorded:
        order.filled_quantity = (order.filled_quantity or 0) + sum(
            f.quantity for f in fills if f.execution_id not in existing
        )
    return recorded


async def _next_sequence(db, order_id) -> int:
    rows = (await db.execute(
        select(OrderEvent.sequence).where(OrderEvent.order_id == order_id)
    )).scalars().all()
    return (max(rows) + 1) if rows else 1


async def _transition(db, order: Order, to_state: str, actor: str, reason: str,
                      *, fills: int = 0, commit: bool = True,
                      outcome: Optional[str] = None) -> Resolution:
    """Move the order, append the event, bump the optimistic version.

    Every move goes through assert_transition. The resolver is exactly the kind
    of caller §7.5 means by "no route or adapter may update arbitrary state
    strings" -- it is reacting to an external system's vocabulary, which is
    where an unexpected string is most likely to arrive.
    """
    from_state = order.state
    sm.assert_transition(from_state, to_state)

    order.state = to_state
    order.version = (order.version or 1) + 1
    order.updated_at = datetime.now(timezone.utc)

    db.add(OrderEvent(
        order_id=order.id,
        sequence=await _next_sequence(db, order.id),
        from_state=from_state,
        to_state=to_state,
        actor=actor,
        reason=reason,
    ))
    # Flushed, not just added. The partial-fill path transitions twice in one
    # transaction, and the second call computes its sequence by querying the
    # events table: without the flush the first event is still pending, both
    # get sequence 1, and the UNIQUE (order_id, sequence) index rejects the
    # insert. Caught by test_event_sequence_increments rather than in
    # production, which is the only reason it is a comment and not an incident.
    await db.flush()

    if commit:
        await db.commit()

    logger.info("Order %s resolved %s -> %s (%s)", order.id, from_state,
                to_state, reason)
    return Resolution(str(order.id), outcome or to_state.lower(), from_state,
                      to_state, fills_recorded=fills, detail=reason)
