"""
OutboxEvent and InboxReceipt (MASTER_ARCHITECTURE §7.3, §8.1, §12).

The transactional outbox pattern, which exists to close one specific gap: a
process that commits a domain change and then publishes an event can die
between the two, and the event is lost forever. §8.1 requires that "order
creation and outbox creation occur in the same transaction" — so the event is
committed WITH the order, by the same COMMIT, and a publisher moves it
afterwards. A crash can then only mean "not published yet", never "published
nothing and lost the fact".

The inbox is the mirror: "consumers record inbox receipts before or with side
effects". At-least-once delivery means a consumer WILL see the same event
twice, and for an order dispatch that is a duplicate order. The receipt is what
makes the second delivery a no-op.

INERT ON ITS OWN. Nothing writes these yet; the durable dispatch worker that
consumes them is §21 Phase 2. The tables and their uniqueness constraints land
with the records they protect so the guarantee is reviewable as one thing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

STATUS_PENDING = "pending"
STATUS_PUBLISHED = "published"
#: Exhausted its attempts. Parked for an operator rather than retried forever
#: — §18's dead-letter operations.
STATUS_DEAD_LETTER = "dead_letter"
OUTBOX_STATUSES = (STATUS_PENDING, STATUS_PUBLISHED, STATUS_DEAD_LETTER)


class OutboxEvent(Base):
    """An event committed with the state change that produced it."""

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Nullable: some events are organization-scoped and some are not. No FK,
    #: deliberately — an outbox row must survive the deletion of whatever it
    #: describes, or the log loses exactly the events worth keeping.
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    #: What this event is about, as type + id rather than a foreign key, for
    #: the same reason.
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    #: §12.2 requires a versioned envelope: a consumer must be able to tell
    #: which shape it is reading before it reads it.
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # The publisher's only query: oldest unpublished first. Partial, so the
        # index stays small as published rows accumulate — the table is
        # append-mostly and the pending set is tiny by comparison.
        Index("idx_outbox_pending", "created_at",
              postgresql_where=(status == STATUS_PENDING)),
        Index("idx_outbox_aggregate", "aggregate_type", "aggregate_id"),
    )


class InboxReceipt(Base):
    """Proof a consumer already processed an event.

    The primary key is (consumer, event_id): the same event delivered twice to
    one consumer collides on insert, and that collision IS the deduplication.
    It is recorded in the same transaction as the side effect, so "processed"
    and "recorded as processed" cannot come apart.
    """

    __tablename__ = "inbox_receipts"

    consumer: Mapped[str] = mapped_column(String(60), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_inbox_receipts_processed", "processed_at"),)
