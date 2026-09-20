"""
Requests for an account, and the one-time token that turns one into a user.

WHY A REQUEST AND NOT A SIGNUP. scripts/create_user.py states the reason this
platform has no public registration: self-service signup on something that
connects to brokers pulls in email verification, bot defence and abuse
response. That reasoning still holds. What it left missing was any way for a
person to ASK, so the landing page's "Start Free" pointed at an unauthenticated
terminal. This is the ask, not the signup.

WHY A TOKEN AND NOT A MAILED PASSWORD. There is no outbound email anywhere in
this codebase — no SMTP, no provider SDK, nothing. Approval therefore cannot
send anything, and a design that assumed it could would be broken on arrival.

So approval mints a one-time token, shown to the operator EXACTLY once, which
they pass to the person by whatever channel they already trust. The person
redeems it and chooses their own password. Two consequences worth stating,
both good: no credential is ever transmitted by this system, and the operator
never learns the password they are provisioning.

Only the SHA-256 of the token is stored, for the same reason sessions store
only a hash — a database dump must not hand over live grants.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_CLAIMED = "claimed"
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_DENIED, STATUS_CLAIMED)

#: How long an approval stays redeemable. Long enough to reach someone who is
#: not at their desk, short enough that a token found in a chat log months
#: later is already dead.
SETUP_TOKEN_TTL_HOURS = 168  # 7 days


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Stored lower-cased and matched lower-cased, same rule as User.email:
    # case-sensitive emails let the same person queue twice.
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    # Free text from a public form. Length-capped at the schema too; the column
    # is Text so a long, genuine answer is not silently truncated into
    # something that reads as evasive.
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_PENDING
    )

    # SHA-256 of the setup token, never the token. Nullable because it exists
    # only between approval and redemption.
    setup_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    setup_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Kept for triage rather than for enforcement. A burst of requests from one
    # address is the signal that the public form is being abused, and without
    # this the only evidence is the rate limiter's 429s, which are not stored.
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # PARTIAL unique index: one live request per address, while still
        # letting someone who was denied apply again later. A plain unique
        # index on email would make a denial permanent, which is a harsher
        # policy than anyone chose and an awkward one to reverse by hand.
        Index(
            "idx_access_requests_email_pending",
            "email",
            unique=True,
            postgresql_where=(status == STATUS_PENDING),
        ),
        Index("idx_access_requests_token", "setup_token_hash"),
        Index("idx_access_requests_status", "status"),
    )
