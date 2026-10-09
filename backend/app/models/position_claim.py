"""
Position claims — the duplicate guard's missing half, with a lifecycle.

`_execute_signal` Stage 3 asks the database whether an open or pending trade
already exists for (underlying, asset class) and skips if one does. That read
is correct and it is not sufficient: the row it looks for is written only
AFTER the broker accepts, so two signals arriving inside that round trip both
read zero rows, both pass, and both submit. A claim taken BEFORE submission
gives the second one something to collide with.

WHY A STATE AND NOT JUST A LEASE. A claim that merely expired would, at the
120-second mark, hand the position to a second caller while the first order's
fate was still unknown — a lease timeout is not evidence that no order exists.
So the lease only governs the part of the window where nothing has been sent:

    pending    the claim is held, nothing has gone to the broker. A worker
               that dies here left no order, so the LEASE may reclaim it.

    submitted  intent recorded, the order is at or on its way to the broker.
               The lease stops applying. Nothing but a definite outcome frees
               this, because "the lease ran out" says nothing about whether an
               order exists.

    unknown    submission returned no usable answer — timeout, dropped
               connection, crash after sending. Same as submitted for
               blocking purposes, and labelled so reconciliation can find it.

`submitted` is written BEFORE the broker call, not after. Recording it
afterwards would leave the gap this is here to close: a process killed
mid-call would have sent an order and recorded nothing. The cost is that an
order rejected before it ever reached the venue still blocks until resolved,
which is the safe direction.

IDEMPOTENCY IS NOT UNIFORM ACROSS BROKERS. `idempotency_key` is handed to the
broker as `client_order_id` on the options path (SpreadOrder carries it, and
Alpaca honours it), which makes a retry safe there. `place_equity_order` takes
no such parameter, so on the equity path the key is only a local correlation
handle for reconciliation — the broker will happily accept the same order
twice. That asymmetry is exactly why a submitted claim must never be freed by
a timer.

SCOPE. Claims are global today: `scope` is always 'global'. Positions
themselves are not yet owned — `trades` has no organization column — so
scoping claims per tenant while the duplicate read beside them stays global
would make the two disagree. The column exists so that the batch which gives
trades an owner can populate it in one migration instead of reshaping a
primary key. See the PR description for the consequence while it is global.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

#: Nothing has been sent; the lease may reclaim this.
STATE_PENDING = "pending"
#: Intent recorded, order at the broker. Only an outcome frees it.
STATE_SUBMITTED = "submitted"
#: Outcome unknown. Blocks like `submitted`, and findable by reconciliation.
STATE_UNKNOWN = "unknown"

#: States that a lease expiry must NOT reclaim.
UNRESOLVED_STATES = (STATE_SUBMITTED, STATE_UNKNOWN)

#: The only scope in use. See the module docstring.
GLOBAL_SCOPE = "global"


class PositionClaim(Base):
    __tablename__ = "position_claims"

    # Composite primary key IS the uniqueness guarantee. Keyed exactly as
    # trade_identity.position_identity_key() keys it, so SPY shares and a SPY
    # option spread claim separately and neither false-blocks the other.
    scope: Mapped[str] = mapped_column(
        String(64), primary_key=True, server_default=GLOBAL_SCOPE
    )
    underlying: Mapped[str] = mapped_column(String(16), primary_key=True)
    asset_class: Mapped[str] = mapped_column(String(10), primary_key=True)

    # Ownership. Every state change and release is keyed on this rather than
    # on the symbol, so a worker that wakes after its lease was reclaimed
    # cannot delete the claim of whoever holds the position now.
    claim_token: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )

    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=STATE_PENDING
    )

    # Handed to the broker as client_order_id where the broker supports one.
    # Always recorded locally so reconciliation has something to look up.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dispatch_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Server-side defaults throughout: lease decisions compare database time
    # with database time, so a worker whose clock has drifted cannot reclaim a
    # live claim early or hold a dead one late.
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # When the claim entered `submitted` — the moment after which a lease
    # expiry stops meaning anything.
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why the outcome is unknown, for whoever has to reconcile it.
    unresolved_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
