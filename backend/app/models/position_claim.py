"""
Position claims — the duplicate guard's missing half.

`_execute_signal` Stage 3 asks the database whether an open or pending trade
already exists for (underlying, asset class) and skips if one does. That read
is correct and it is not sufficient: the row it looks for is only written
AFTER the broker accepts the order, so between the check and the row there is
a full broker round trip. Two signals for the same name that arrive inside
that window both read zero rows, both pass, and both submit — one position,
two economic orders.

A claim closes the window from the other end. It is taken BEFORE submission,
so the second attempt finds something to collide with even though no trade
row exists yet. The primary key does the deciding: INSERT ... ON CONFLICT DO
NOTHING lets exactly one of two concurrent inserters report success, in the
database, under contention — the same reason the Copilot approval claim is a
conditional UPDATE rather than a read followed by a write.

EXPIRY RATHER THAN RELEASE. A claim is not handed back when an entry fails.
Releasing it would mean threading a release through every exit of a ~400-line
function in the live order path, and a missed path would wedge a symbol
permanently. Instead each claim carries its own expiry and the next attempt
reaps it. The cost is a short cooldown on that name after a failed attempt,
which this codebase already treats as desirable behaviour (Stage 3b imposes
one after a close). The benefit is that a crashed or killed process cannot
leave a symbol locked out.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PositionClaim(Base):
    __tablename__ = "position_claims"

    # Composite primary key IS the uniqueness guarantee. Keyed exactly as
    # trade_identity.position_identity_key() keys it, so SPY shares and a SPY
    # option spread claim separately and neither false-blocks the other.
    underlying: Mapped[str] = mapped_column(String(16), primary_key=True)
    asset_class: Mapped[str] = mapped_column(String(10), primary_key=True)

    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # When this claim stops blocking a new entry. Absolute rather than a TTL
    # so the reaper is a plain comparison and does not depend on when it runs.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Which dispatch holds it — for reading the log back, not for logic.
    dispatch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
