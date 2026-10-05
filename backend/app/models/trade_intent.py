"""
TradeIntent, IntentEvaluation, ApprovalDecision (MASTER_ARCHITECTURE §7.3-7.4).

The requested economics of a trade, the risk authority's answer about it, and a
human's or policy's approval of that answer. One intent per request, whatever
asked for it — manual, Copilot, Autopilot, liquidation or administrative — so
that "every paper order source creates the same intent contract" (§21 Phase 1
exit criterion) is checkable rather than asserted.

INERT ON ITS OWN, exactly as migration 0034 was. Nothing writes these tables
yet: routing every order source through them is §21 Phase 3, and putting a
schema change and a rewrite of the live order path in one reviewable unit is
how both get reviewed badly. The records and their constraints land first so
the constraints are arguable before anything depends on them.

IMMUTABLE. An intent is the snapshot a decision was made against; §7.4 requires
that "any material edit creates a new intent version and invalidates prior
evaluations and approvals". That is why `supersedes_id` and `version` exist and
why there is no update path — an edited intent is a new row, and the old
evaluation points at the old row, which is what makes the audit trail mean
something.
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

# ── Vocabulary ───────────────────────────────────────────────────────────────

SOURCE_MANUAL = "manual"
SOURCE_COPILOT = "copilot"
SOURCE_AUTOPILOT = "autopilot"
SOURCE_LIQUIDATION = "liquidation"
SOURCE_ADMINISTRATIVE = "administrative"
#: §4's "one execution path": every one of these enters the same pipeline.
SOURCES = (SOURCE_MANUAL, SOURCE_COPILOT, SOURCE_AUTOPILOT,
           SOURCE_LIQUIDATION, SOURCE_ADMINISTRATIVE)

SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDES = (SIDE_BUY, SIDE_SELL)

ASSET_EQUITY = "equity"
ASSET_OPTION = "option"
#: Crypto is signals-only and has no order path (§2.3). Listed so the column
#: has a documented vocabulary, NOT as permission to route one.
ASSET_CRYPTO = "crypto"
ASSET_CLASSES = (ASSET_EQUITY, ASSET_OPTION, ASSET_CRYPTO)

EVAL_APPROVED = "approved"
EVAL_BLOCKED = "blocked"
EVAL_STALE = "stale"
EVAL_RESULTS = (EVAL_APPROVED, EVAL_BLOCKED, EVAL_STALE)

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_EXPIRED = "expired"
DECISIONS = (DECISION_APPROVED, DECISION_REJECTED, DECISION_EXPIRED)


class TradeIntent(Base):
    """What was asked for, by whom, against which connection, at what time."""

    __tablename__ = "trade_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Nullable because §7.4 permits "requested_by_user_id OR
    #: service_identity_id" — a liquidation raised by the kill switch has no
    #: user. SET NULL so deleting a person does not delete the record that a
    #: trade was intended.
    requested_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    service_identity: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Which connection, and WHICH VERSION of it. §7.2 increments a
    #: connection's version on any routing- or security-relevant change, and
    #: §8's worker revalidates it before submitting: an intent evaluated
    #: against one set of credentials must not execute against another.
    broker_connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("broker_connections.id", ondelete="RESTRICT"),
        nullable=True,
    )
    broker_connection_version: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    environment: Mapped[str] = mapped_column(String(10), nullable=False)
    account_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Null until a canonical instrument registry exists (§6.4). Carried now so
    #: adding one is a backfill rather than a schema change on the order path.
    canonical_instrument_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    symbol_display: Mapped[str] = mapped_column(String(32), nullable=False)

    side: Mapped[str] = mapped_column(String(8), nullable=False)
    #: NUMERIC, never float: a fractional share or a contract count that
    #: rounds is a position that does not reconcile.
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    #: §7.4: "The client may request a size. The risk authority determines the
    #: approved maximum size." Both are recorded; the evaluation holds the
    #: number that is allowed to execute.
    sizing_request: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    limit_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(12), nullable=False, default="day")
    option_legs: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    strategy_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    strategy_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    signal_snapshot_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    entry_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invalidation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    maximum_risk_request: Mapped[Optional[float]] = mapped_column(
        Numeric(20, 8), nullable=True
    )

    #: §6.4: "Every decision snapshot MUST identify the data used and its
    #: freshness. Missing or stale required data fails closed." Nullable in
    #: the schema, required by the risk authority — a NULL here is what a
    #: fail-closed check looks for, so making the column NOT NULL would hide
    #: the condition it is meant to catch.
    data_as_of: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Versioning, per §7.4's "material edit creates a new intent version".
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: §8.1: "The API requires an idempotency key for every mutating trading
    #: command", scoped by organization and command type. The partial unique
    #: index below is that scope.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    command_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="create_order"
    )
    #: Ties an intent to everything downstream of it across tables and logs.
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: An intent that was never acted on must not execute later against prices
    #: it was never evaluated against.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # THE idempotency constraint: one intent per organization, command
        # type and key. Retrying a submit with the same key cannot create a
        # second intent, which is what stops a double-tapped button or a
        # client retry from becoming two orders.
        Index("idx_trade_intents_idempotency",
              "organization_id", "command_type", "idempotency_key", unique=True),
        Index("idx_trade_intents_org_created", "organization_id", "created_at"),
        Index("idx_trade_intents_correlation", "correlation_id"),
    )


class IntentEvaluation(Base):
    """The risk authority's answer, with the snapshots it was based on.

    Stored rather than recomputed: §9 requires one risk gateway, and an
    approval that cannot be re-read is not auditable. Keeping the policy and
    data snapshots means a later "why was this allowed" has an answer that
    does not depend on today's configuration.
    """

    __tablename__ = "intent_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="CASCADE"),
        nullable=False,
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Every reason, not the first. A caller told one blocker at a time fixes
    #: it, resubmits, and is told the next one.
    block_reasons: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    #: What the risk authority will actually permit, which may be less than
    #: the intent requested and is the number execution must use.
    approved_quantity: Mapped[Optional[float]] = mapped_column(
        Numeric(20, 8), nullable=True
    )
    policy_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    policy_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    data_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    data_as_of: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_intent_evaluations_intent", "intent_id"),)


class ApprovalDecision(Base):
    """Who approved it, with what evidence, and until when.

    `expires_at` is not decoration: §12's Copilot approval is a decision about
    a market that moves. An approval with no expiry silently becomes a
    standing authorisation to trade on stale information.
    """

    __tablename__ = "approval_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("intent_evaluations.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    #: NULL for a policy approval (Autopilot within its envelope). The column
    #: being empty is how "no human approved this" stays visible.
    decided_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_policy: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: §6.1's step-up authentication: what proved the approver was present.
    step_up_evidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("idx_approval_decisions_intent", "intent_id"),)
