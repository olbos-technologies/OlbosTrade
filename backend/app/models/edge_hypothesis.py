"""SQLAlchemy model for edge_hypotheses: the Edge Lab ledger (see services/edge_ledger.py).

The registration columns are LOCKED: written once, when the idea is registered, and never
changed. That is the whole point of the ledger, so it is enforced here, in the ORM, and not left
to whichever caller is added later. (Raw SQL can still bypass an ORM guard; the guard is there so
no application code path can edit a pre-registration by accident or convenience.)

The results are append-only history; `status` and `verdict_reasons` are derived from them by
the pure evaluator and are the only fields that move.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, String, Text, event, func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

LOCKED_FIELDS = ("title", "statement", "family", "rule", "universe", "horizon", "criteria",
                 "data_plan", "retroactive", "registered_by", "locked_at")


class EdgeHypothesis(Base):
    __tablename__ = "edge_hypotheses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── locked at registration ──
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)       # the claim, in plain words
    family: Mapped[str] = mapped_column(String(80), nullable=False)    # what the multiple-testing count is over
    rule: Mapped[str] = mapped_column(Text, nullable=False)            # the exact entry/exit rule
    universe: Mapped[list] = mapped_column(JSONB, nullable=False)
    horizon: Mapped[str] = mapped_column(String(60), nullable=False)
    criteria: Mapped[dict] = mapped_column(JSONB, nullable=False)      # min_n, min_effect, cost, alpha, baseline...
    data_plan: Mapped[str] = mapped_column(Text, nullable=False)
    retroactive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    registered_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ── what moves ──
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="registered")
    results: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # append-only
    verdict_reasons: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    retired_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_edge_hypotheses_family", "family"),
        Index("idx_edge_hypotheses_status", "status"),
    )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id), "title": self.title, "statement": self.statement, "family": self.family,
            "rule": self.rule, "universe": self.universe, "horizon": self.horizon, "criteria": self.criteria,
            "data_plan": self.data_plan, "retroactive": self.retroactive, "registered_by": self.registered_by,
            "locked_at": self.locked_at.isoformat() if self.locked_at else None,
            "status": self.status, "results": self.results or [], "verdict_reasons": self.verdict_reasons or [],
            "retired_reason": self.retired_reason,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@event.listens_for(EdgeHypothesis, "before_update")
def _registration_is_locked(mapper, connection, target):
    state = sa_inspect(target)
    changed = [f for f in LOCKED_FIELDS if state.attrs[f].history.has_changes()]
    if changed:
        raise ValueError(
            "a registered hypothesis cannot be edited (" + ", ".join(changed) + "): its rule and "
            "criteria were locked before testing. Register a new hypothesis instead."
        )
