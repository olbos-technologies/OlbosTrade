"""
Resolving AMBIGUOUS orders (MASTER_ARCHITECTURE §8, §8.1).

Every test here is about the same asymmetry: concluding "the broker never got
it" when it did means a re-submission and two real positions; concluding "needs
a human" when the answer was obvious costs a delay. The tests that matter are
the ones asserting the resolver errs towards the human.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.oms_order import Fill, Order, OrderAttempt, OrderEvent
from app.services import ambiguous_order_resolver as r
from app.services import order_state_machine as sm
from app.services.ambiguous_order_resolver import BrokerFill, BrokerOrderView

pytestmark = pytest.mark.asyncio

NOW = datetime.now(timezone.utc)


def _order(state=sm.AMBIGUOUS, *, quantity=10, filled=0):
    return Order(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), intent_id=uuid.uuid4(),
        environment="paper", state=state, version=1,
        symbol_display="SPY", asset_class="equity", side="buy",
        order_type="market", quantity=quantity, filled_quantity=filled,
        correlation_id=uuid.uuid4(), created_at=NOW, updated_at=NOW,
    )


def _attempt(order, *, number=1, broker_order_id=None):
    return OrderAttempt(
        id=uuid.uuid4(), order_id=order.id, attempt_number=number,
        client_order_id=f"olbos-{order.id}", broker_order_id=broker_order_id,
        outcome="timeout", submitted_at=NOW,
    )


class _DB:
    def __init__(self, orders=None, attempts=None, fills=None):
        self.orders = list(orders or [])
        self.attempts = list(attempts or [])
        self.fills = list(fills or [])
        self.events: list[OrderEvent] = []
        self.staged: list = []
        self.commits = 0

    async def execute(self, stmt):
        sql = str(stmt)
        params = stmt.compile().params
        if "oms_order_attempts" in sql:
            rows = sorted([a for a in self.attempts
                           if a.order_id == params.get("order_id_1")],
                          key=lambda a: a.attempt_number, reverse=True)
            return _Result(rows[:1])
        if "oms_order_events" in sql:
            return _Result([e.sequence for e in self.events
                            if e.order_id == params.get("order_id_1")])
        if "oms_fills" in sql:
            return _Result([f.broker_execution_id for f in self.fills
                            if f.order_id == params.get("order_id_1")])
        rows = [o for o in self.orders if o.state == params.get("state_1")]
        return _Result(sorted(rows, key=lambda o: o.created_at))

    def add(self, obj):
        self.staged.append(obj)

    async def flush(self):
        """Pending rows become visible to subsequent queries, as they do in a
        real session. The two-step partial-fill path depends on exactly this."""
        for obj in self.staged:
            if isinstance(obj, OrderEvent) and obj not in self.events:
                self.events.append(obj)
            elif isinstance(obj, Fill) and obj not in self.fills:
                self.fills.append(obj)

    async def commit(self):
        await self.flush()
        self.staged.clear()
        self.commits += 1


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


def _lookup(status, **kw):
    async def _fn(client_order_id):
        return BrokerOrderView(status=status, **kw)
    return _fn


# ── Unambiguous broker evidence resolves the order ───────────────────────────

async def test_a_filled_order_is_resolved_to_filled():
    order = _order()
    db = _DB([order], [_attempt(order)])

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_FILLED))

    assert res.to_state == sm.FILLED and order.state == sm.FILLED
    assert db.events[-1].from_state == sm.AMBIGUOUS
    assert db.events[-1].actor == "reconciliation"


async def test_a_working_order_becomes_acknowledged():
    order = _order()
    db = _DB([order], [_attempt(order)])
    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_WORKING))
    assert res.to_state == sm.ACKNOWLEDGED


async def test_a_cancelled_order_is_resolved_to_cancelled():
    order = _order()
    db = _DB([order], [_attempt(order)])
    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_CANCELED))
    assert res.to_state == sm.CANCELED


async def test_a_partial_fill_goes_through_acknowledged():
    """§7.5 has no AMBIGUOUS -> PARTIALLY_FILLED edge, so it takes two moves.
    Doing it in one would write a state the table forbids."""
    order = _order()
    db = _DB([order], [_attempt(order)])

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_PARTIALLY_FILLED))

    assert res.to_state == sm.PARTIALLY_FILLED
    assert [(e.from_state, e.to_state) for e in db.events] == [
        (sm.AMBIGUOUS, sm.ACKNOWLEDGED),
        (sm.ACKNOWLEDGED, sm.PARTIALLY_FILLED),
    ], "the order jumped a state the machine does not allow"


# ── The dangerous cases resolve towards a human ──────────────────────────────

async def test_not_found_does_NOT_auto_cancel():
    """The decision this module turns on.

    §8.1 permits a retry once a lookup proves no order exists. A not-found can
    also mean the wrong account was queried, or that the broker's search is not
    yet consistent with a submission made seconds ago. Acting on it
    automatically turns either into a duplicate position.
    """
    order = _order()
    db = _DB([order], [_attempt(order)])

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_NOT_FOUND))

    assert order.state == sm.MANUAL_REVIEW, (
        "a not-found was treated as proof and the order was resolved "
        "automatically"
    )
    assert order.state != sm.CANCELED
    assert res.outcome == "not_found_needs_review"


async def test_a_failed_lookup_leaves_the_order_ambiguous():
    """An unreachable broker says NOTHING about the order. Treating silence as
    'no order' is exactly how a live order gets re-submitted."""
    async def _boom(client_order_id):
        raise TimeoutError("broker unreachable")

    order = _order()
    db = _DB([order], [_attempt(order)])

    res = await r.resolve_one(db, order, _boom)

    assert order.state == sm.AMBIGUOUS, "a failed lookup changed the order"
    assert res.resolved is False
    assert res.outcome == "lookup_failed"
    assert db.commits == 0, "a failed lookup wrote state"


async def test_a_rejected_order_goes_to_manual_review():
    """§7.5 has no AMBIGUOUS -> REJECTED edge. Rather than quietly widening the
    state machine, the resolver routes to a human and says why."""
    order = _order()
    db = _DB([order], [_attempt(order)])

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_REJECTED))

    assert order.state == sm.MANUAL_REVIEW
    assert "no AMBIGUOUS -> REJECTED edge" in res.detail


async def test_an_unrecognised_broker_status_goes_to_manual_review():
    """The resolver consumes an external system's vocabulary, which is where an
    unexpected string is most likely to arrive."""
    order = _order()
    db = _DB([order], [_attempt(order)])

    res = await r.resolve_one(db, order, _lookup("something_new"))

    assert order.state == sm.MANUAL_REVIEW
    assert res.outcome == "unrecognised"


async def test_an_ambiguous_order_with_no_attempt_goes_to_manual_review():
    """There is no id to ask about. An order cannot reach AMBIGUOUS without an
    attempt, so this is a bug rather than a market condition."""
    order = _order()
    db = _DB([order], attempts=[])

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_FILLED))

    assert order.state == sm.MANUAL_REVIEW
    assert res.outcome == "no_attempt"


# ── Fills ────────────────────────────────────────────────────────────────────

async def test_fills_are_recorded_from_the_lookup():
    order = _order(quantity=10)
    db = _DB([order], [_attempt(order)])
    fills = (BrokerFill("exec-1", Decimal("6"), Decimal("500.10"), NOW),
             BrokerFill("exec-2", Decimal("4"), Decimal("500.20"), NOW))

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_FILLED, fills=fills))

    assert res.fills_recorded == 2
    assert {f.broker_execution_id for f in db.fills} == {"exec-1", "exec-2"}
    assert order.filled_quantity == Decimal("10")


async def test_a_repeated_sweep_does_not_double_count_a_fill():
    """A sweep runs over the same orders repeatedly by design. The unique index
    is the guarantee; this keeps the sweep from spending a transaction on a
    conflict it can see coming."""
    order = _order()
    existing = Fill(order_id=order.id, broker_execution_id="exec-1",
                    broker="alpaca", account_ref="", quantity=Decimal("6"),
                    price=Decimal("500.10"), executed_at=NOW)
    db = _DB([order], [_attempt(order)], fills=[existing])
    fills = (BrokerFill("exec-1", Decimal("6"), Decimal("500.10"), NOW),)

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_FILLED, fills=fills))

    assert res.fills_recorded == 0
    assert len(db.fills) == 1, "the same execution was recorded twice"


async def test_the_broker_order_id_is_backfilled_onto_the_attempt():
    """After a timeout the attempt has no broker id. The lookup is where it
    becomes known, and without it the order cannot be found again."""
    order = _order()
    attempt = _attempt(order, broker_order_id=None)
    db = _DB([order], [attempt])

    await r.resolve_one(db, order, _lookup(r.LOOKUP_WORKING, broker_order_id="brk-99"))

    assert attempt.broker_order_id == "brk-99"


# ── Guards ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", [sm.FILLED, sm.ACKNOWLEDGED, sm.CREATED,
                                   sm.CANCELED, sm.MANUAL_REVIEW])
async def test_a_non_ambiguous_order_is_left_alone(state):
    order = _order(state)
    db = _DB([order], [_attempt(order)])

    res = await r.resolve_one(db, order, _lookup(r.LOOKUP_FILLED))

    assert res.resolved is False and order.state == state
    assert db.commits == 0


async def test_every_transition_bumps_the_optimistic_version():
    order = _order()
    db = _DB([order], [_attempt(order)])
    await r.resolve_one(db, order, _lookup(r.LOOKUP_FILLED))
    assert order.version == 2


async def test_event_sequence_increments():
    order = _order()
    db = _DB([order], [_attempt(order)])
    await r.resolve_one(db, order, _lookup(r.LOOKUP_PARTIALLY_FILLED))
    assert [e.sequence for e in db.events] == [1, 2]


# ── Sweep ────────────────────────────────────────────────────────────────────

async def test_the_sweep_takes_ambiguous_orders_oldest_first():
    old = _order(); old.created_at = NOW - timedelta(hours=2)
    new = _order()
    settled = _order(sm.FILLED)
    db = _DB([new, settled, old], [_attempt(old), _attempt(new)])

    results = await r.sweep(db, _lookup(r.LOOKUP_WORKING))

    assert [res.order_id for res in results] == [str(old.id), str(new.id)]


async def test_one_bad_order_does_not_stop_the_sweep():
    bad, good = _order(), _order()
    db = _DB([bad, good], [_attempt(bad), _attempt(good)])

    async def _selective(client_order_id):
        if client_order_id == f"olbos-{bad.id}":
            raise RuntimeError("exploded")
        return BrokerOrderView(status=r.LOOKUP_WORKING)

    results = await r.sweep(db, _selective)

    assert len(results) == 2
    assert good.state == sm.ACKNOWLEDGED, "a later order was skipped"


async def test_the_sweep_survives_the_resolver_itself_raising(monkeypatch):
    """The outer safety net, which the test above does NOT reach.

    A lookup that raises is caught inside resolve_one, so that path never
    exercises the sweep's own handler. This one makes resolve_one itself blow
    up -- a database error, an illegal transition -- and asserts the batch
    carries on. Without it, one unresolvable order stops every other order
    being reconciled, indefinitely.
    """
    first, second = _order(), _order()
    second.created_at = NOW + timedelta(minutes=1)
    db = _DB([first, second], [_attempt(first), _attempt(second)])

    calls = []
    real = r.resolve_one

    async def _flaky(db_, order, lookup):
        calls.append(order.id)
        if order.id == first.id:
            raise RuntimeError("database went away")
        return await real(db_, order, lookup)

    monkeypatch.setattr(r, "resolve_one", _flaky)

    results = await r.sweep(db, _lookup(r.LOOKUP_WORKING))

    assert len(results) == 2
    assert results[0].outcome == "resolver_error"
    assert results[0].resolved is False
    assert "database went away" in results[0].detail
    assert second.state == sm.ACKNOWLEDGED, "the second order was never reached"


async def test_the_sweep_ignores_orders_that_are_not_ambiguous():
    db = _DB([_order(sm.FILLED), _order(sm.ACKNOWLEDGED)])
    assert await r.sweep(db, _lookup(r.LOOKUP_FILLED)) == []
