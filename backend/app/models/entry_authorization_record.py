"""R2-Q durable entry authorization ledger (schema only).

No API currently issues or consumes these rows. A row is not broker permission
until complete evidence, atomic consumption, and risk checks are implemented.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EntryAuthorizationRecord(Base):
    __tablename__ = "entry_authorizations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('issued', 'consumed', 'expired', 'revoked')",
            name="ck_entry_authorizations_state",
        ),
        CheckConstraint("expires_at > issued_at", name="ck_entry_authorizations_expiry"),
        CheckConstraint(
            "(state = 'consumed' AND consumed_at IS NOT NULL) OR "
            "(state <> 'consumed' AND consumed_at IS NULL)",
            name="ck_entry_authorizations_consumed_at",
        ),
        UniqueConstraint("token_hash", name="uq_entry_authorizations_token_hash"),
        Index("ix_entry_authorizations_scope", "organization_id", "broker_account_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    broker_connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False)
    position_identity: Mapped[str] = mapped_column(String(160), nullable=False)
    intent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    risk_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    kill_switch_revision: Mapped[str] = mapped_column(String(160), nullable=False)
    position_claim_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
