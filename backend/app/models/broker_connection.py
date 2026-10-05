"""
Broker credentials, owned by an organization.

WHY ALPACA ONLY, FOR NOW. Alpaca authenticates every request with an API key
pair and holds no session, so one process can act for many users by sending
different headers. IBKR cannot work that way here: the ibkr-gateway container
is a single logged-in session, and IBKR_CLIENT_ID multiplexes connections to
THE SAME account rather than selecting between accounts. Supporting N users'
IBKR accounts needs N gateway containers — infrastructure, not application
code, and the separate per-tenant stack config.py already describes.

The `broker` column exists anyway and is not hardcoded to alpaca, so adding
IBKR later is a row, not a migration of everything that references this table.

CREDENTIALS ARE ENCRYPTED, NOT HASHED. A password is verified and never
recovered, so Argon2 is right for it. An API key is SENT to Alpaca on every
call, so it has to come back out — see services/credential_cipher.py, which is
a separate module precisely so the two are never confused.

The last four characters of the key are stored in the clear on purpose. The
list view needs to show WHICH credential is connected, and doing that from
`key_last4` means a read-only screen never decrypts anything — so a bug in a
listing endpoint cannot hand back a live trading credential.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

BROKER_ALPACA = "alpaca"
BROKER_IBKR = "ibkr"
BROKERS = (BROKER_ALPACA, BROKER_IBKR)

#: Alpaca runs paper and live as different hosts with different credentials,
#: so the environment is part of what identifies a connection, not a flag on
#: one. A user may legitimately hold both.
ENV_PAPER = "paper"
ENV_LIVE = "live"
ENVIRONMENTS = (ENV_PAPER, ENV_LIVE)

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"

#: Disconnecting REVOKES rather than deletes, and clears the ciphertext.
#: Keeping the row keeps "when did this account stop being connected"
#: answerable; clearing the ciphertext means a revoked row is not a credential
#: store. Both matter, and neither is achieved by a DELETE.


class BrokerConnection(Base):
    __tablename__ = "broker_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: The OWNER, and the execution-routing key. See ADR-0001: an organization
    #: owns a connection, and every user has a personal organization, so today
    #: this is one user's own org and behaves as `user_id` used to.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Provenance, not ownership: WHO connected it. SET NULL rather than
    #: CASCADE on purpose — deleting the person who pasted the key must not
    #: delete the organization's record that a connection existed, which is
    #: the same reason disconnecting revokes instead of deleting.
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    broker: Mapped[str] = mapped_column(String(20), nullable=False)
    environment: Mapped[str] = mapped_column(String(10), nullable=False)
    #: What the user calls it. Theirs to choose; never used for routing.
    label: Mapped[str] = mapped_column(String(60), nullable=False, default="")

    #: Fernet tokens. Nullable because a revoked row keeps its history and
    #: loses its secrets.
    api_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    secret_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_last4: Mapped[str] = mapped_column(String(8), nullable=False, default="")

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_ACTIVE
    )
    #: §7.2: increments on a security- or routing-relevant change, so §8's
    #: worker can revalidate with one integer comparison immediately before
    #: submitting. Revocation bumps it; recording a successful verification
    #: does NOT -- see _revoke_in_place and touch_verified. A credential
    #: ROTATION does not bump it either, because connect() revokes this row
    #: and inserts a new one: an in-flight order holding the old id finds it
    #: revoked, which stops the order by a different route.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Last time these credentials were confirmed to work against the broker.
    #: A key can be revoked at Alpaca without anything here changing, so this
    #: records when the claim was last true rather than asserting it still is.
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # PARTIAL unique: one ACTIVE connection per organization, broker and
        # environment. Partial so that revoking and reconnecting works — a
        # plain unique index would make the old revoked row block the new one
        # forever, and the only fix would be deleting the history the revoke
        # was designed to keep.
        #
        # This is also what MASTER_ARCHITECTURE §7.2 means by "only one
        # connection may be the active execution target for a given
        # organization and execution scope", enforced by the database rather
        # than by the service remembering to check.
        Index(
            "idx_broker_connections_active",
            "organization_id", "broker", "environment",
            unique=True,
            postgresql_where=(status == STATUS_ACTIVE),
        ),
        Index("idx_broker_connections_org", "organization_id"),
    )
