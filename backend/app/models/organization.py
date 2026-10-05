"""
Organizations — the entity that OWNS a broker connection.

Decided in docs/adr/0001-broker-connection-ownership.md (Accepted 2026-10-05).
Every user gets a PERSONAL organization at first use, so today this behaves
exactly like the per-user model it replaces: one user, one organization, one
connection. The point is not what it does now, it is that re-keying the
execution path happens once, while there is no live trading and no money
depending on the answer.

WHAT IS DELIBERATELY NOT HERE. Roles are not enforced, invitations do not
exist, and no organization has a second member. `role` is stored because
membership without it is a row you have to migrate later, but nothing reads it
to make a decision yet — do not take its presence as an authorization system.

Per-organization execution enablement is also absent on purpose. MASTER_
ARCHITECTURE §6.1 asks for it, but a safety flag that exists and is not
enforced is worse than one that does not exist: it reads as a control during a
review and stops nothing. `EXECUTION_ENABLED` in config.py remains the only
execution kill at this layer, and it answers a different question anyway —
"may this INSTANCE trade" rather than "may this TENANT trade".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

#: One user acting for themselves. The only kind that exists today.
KIND_PERSONAL = "personal"
#: Reserved. Nothing creates one yet; ADR-0001 defers multi-member orgs.
KIND_TEAM = "team"
KINDS = (KIND_PERSONAL, KIND_TEAM)

ROLE_OWNER = "owner"
ROLES = (ROLE_OWNER,)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=KIND_PERSONAL)

    #: Set for a personal organization, NULL for a team one, and UNIQUE — which
    #: is what makes "one personal organization per user" a database guarantee
    #: rather than something the service layer has to remember. Postgres does
    #: not collide NULLs in a unique index, so team organizations are unaffected.
    #:
    #: The get-or-create in services/organization_service.py races like any
    #: SELECT-then-INSERT; this constraint is what turns the loser of that race
    #: into a retry instead of a duplicate.
    personal_for_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_organizations_personal_user", "personal_for_user_id", unique=True),
    )


class OrganizationMember(Base):
    """Which users may act for an organization.

    A join table rather than an owner column on `organizations`, even though
    every organization has exactly one member today. "Which organizations may
    this user act for" is THE authorization question on the execution path, and
    answering it through a join means the query written now keeps its shape
    when a second member finally exists. ADR-0001 permitted deferring this
    table; it is here because deferring it would mean rewriting that lookup on
    the money path twice, which is the cost the ADR exists to avoid paying.
    """

    __tablename__ = "organization_members"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=ROLE_OWNER)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_organization_members_user", "user_id"),)
