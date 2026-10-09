"""
SQLAlchemy model for journal_entries.
Tracks pre/post trade psychology, tags, and loss analysis.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Owner (ADR-0001). Nullable on purpose and staying that way: rows written
    # before ownership existed carry no recoverable author, and migration 0039
    # leaves those unattributed rather than guessing one. NULL is not a
    # wildcard — it matches only the unowned scope used when auth is disabled,
    # so an unattributed row is invisible to every organization-scoped query.
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )

    trade_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )

    # Pre-trade psychology
    pre_trade_thesis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_level: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="1-5 scale"
    )
    market_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Post-trade review
    post_trade_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    followed_rules: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    exit_felt_right: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Tagging
    tags: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True,
        comment="high-iv | low-iv | trending | range-bound | earnings-nearby"
    )

    # Loss analysis
    loss_category: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True,
        comment="bad-signal | good-signal-luck | rule-breach | black-swan"
    )
    mistake_tags: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True,
        comment="overrode-exit | ignored-kill-switch | sized-too-big | chased-entry"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )
