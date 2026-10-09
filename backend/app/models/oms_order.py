"""
The canonical OMS order and its ledgers (MASTER_ARCHITECTURE §7.3, §7.5, §8.1).

Order is the state authority. OrderLeg carries multi-leg economics. OrderAttempt
records one dispatch to a broker. OrderEvent is the append-only transition log.
Fill is the execution ledger.

WHY SEPARATE TABLES. `executionStatus.ts` currently derives a status by matching
on result strings, with a comment saying there is "no broker ack/partial/cancel
entity yet". That is the gap: a status inferred from a string cannot answer how
much filled, at what price, on which attempt, or whether the broker ever
acknowledged it. These tables exist so the answer is recorded rather than
reconstructed.

INERT ON ITS OWN. Nothing writes them yet — routing the live order path through
them is §21 Phase 3. The constraints land first so they can be argued about
before anything depends on them.

ONE ATTEMPT IS NOT ONE ORDER. An order may be dispatched more than once, but
only after §8.1's rule is satisfied: a retry is permitted "only after broker
lookup proves that no economic order exists". Attempts are therefore recorded
individually, each with a stable client order id, so "did we send this twice"
is answerable from the database rather than from logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.order_state_machine import INITIAL

ATTEMPT_PENDING = "pending"
ATTEMPT_ACKNOWLEDGED = "acknowledged"
ATTEMPT_REJECTED = "rejected"
#: The transport never returned an answer. Distinct from rejected, which IS an
#: answer — conflating them is what turns an unknown into a resubmission.
ATTEMPT_TIMEOUT = "timeout"
ATTEMPT_OUTCOMES = (ATTEMPT_PENDING, ATTEMPT_ACKNOWLEDGED,
                    ATTEMPT_REJECTED, ATTEMPT_TIMEOUT)


class Order(Base):
    """Canonical order. The only authority on what state a trade is in."""

    __tablename__ = "oms_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: RESTRICT, not CASCADE: an order is a financial record, and deleting the
    #: intent that produced it must fail rather than erase the trail.
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    broker_connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_connections.id", ondelete="RESTRICT"),
        nullable=True,
    )
    #: Revalidated by the worker before submission (§8). If the connection has
    #: moved on since evaluation, the order does not go.
    broker_connection_version: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    environment: Mapped[str] = mapped_column(String(10), nullable=False)

    state: Mapped[str] = mapped_column(String(24), nullable=False, default=INITIAL)
    #: §7.5: "State transitions MUST use optimistic version checks." A writer
    #: updates WHERE id = ? AND version = ?, so two concurrent transitions
    #: cannot both win and silently lose one.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    symbol_display: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    time_in_force: Mapped[str] = mapped_column(String(12), nullable=False, default="day")
    limit_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)

    quantity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    #: Derived from the Fill ledger, stored for querying. The ledger is the
    #: truth; this is a projection of it and must never be edited on its own.
    filled_quantity: Mapped[float] = mapped_column(
        Numeric(20, 8), nullable=False, default=0
    )
    average_fill_price: Mapped[Optional[float]] = mapped_column(
        Numeric(20, 8), nullable=True
    )

    #: Bracket and multi-leg relationships (§8.2: "Multi-leg and bracket
    #: relationships are preserved").
    parent_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oms_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # One canonical order per intent. The intent already carries the
        # idempotency key, so this is what carries its guarantee forward: a
        # retried dispatch of the same intent cannot create a second order.
        Index("idx_oms_orders_intent", "intent_id", unique=True),
        Index("idx_oms_orders_org_state", "organization_id", "state"),
        Index("idx_oms_orders_correlation", "correlation_id"),
    )


class OrderLeg(Base):
    """One leg of an order. A single-leg equity order has exactly one."""

    __tablename__ = "oms_order_legs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oms_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol_display: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    #: Option identity. Null for equity.
    expiry: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    strike: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    right: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)

    __table_args__ = (
        Index("idx_oms_order_legs_order_index", "order_id", "leg_index", unique=True),
    )


class OrderAttempt(Base):
    """One dispatch to a broker, with the client order id that was sent.

    §8.1: "Provider-supported client order IDs use the stable OMS order ID or a
    deterministic derivative." Storing the exact id sent is what makes a
    post-timeout broker lookup possible: without it, an AMBIGUOUS order cannot
    be searched for at the broker and can only be resolved by a human.
    """

    __tablename__ = "oms_order_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oms_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ATTEMPT_PENDING
    )
    #: Why it failed, when it did. Never a credential — see the logging rule
    #: in broker_connection_service.
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_oms_attempts_order_number", "order_id", "attempt_number",
              unique=True),
        # A client order id must identify exactly one attempt, or a broker
        # lookup after a timeout cannot tell which dispatch it found.
        Index("idx_oms_attempts_client_order_id", "client_order_id", unique=True),
    )


class OrderEvent(Base):
    """Append-only state transition log. Never updated, never deleted."""

    __tablename__ = "oms_order_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oms_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Monotonic per order, so the log can be replayed in order even when two
    #: rows share a timestamp.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Null only for the creation event, which has no prior state.
    from_state: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Who or what caused it: a user id, a worker name, "reconciliation".
    actor: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_oms_events_order_sequence", "order_id", "sequence", unique=True),
    )


class Fill(Base):
    """One execution reported by the broker.

    §8.1: "Partial fills are first-class events." A fill is a row, not a status
    — four partials and a final are five rows, and the order's filled_quantity
    is their sum rather than an independently-maintained number that can drift.
    """

    __tablename__ = "oms_fills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oms_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    leg_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("oms_order_legs.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The broker's own execution identifier.
    broker_execution_id: Mapped[str] = mapped_column(String(128), nullable=False)
    broker: Mapped[str] = mapped_column(String(20), nullable=False)
    account_ref: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    quantity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    fee: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    #: The broker's timestamp, not ours. Reconciliation compares against the
    #: broker's clock, and substituting local time hides latency and reordering.
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # §7.3: "unique per provider/account". Reconciliation and websocket
        # updates both report the same execution, so without this a replayed
        # message double-counts a fill and the position silently doubles.
        Index("idx_oms_fills_unique_execution",
              "broker", "account_ref", "broker_execution_id", unique=True),
        Index("idx_oms_fills_order", "order_id"),
    )
