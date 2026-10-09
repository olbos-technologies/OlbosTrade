"""
Taking and reaping position claims.

See app/models/position_claim.py for why claims exist. This module is the only
thing that writes them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.position_claim import PositionClaim
from app.services.trade_identity import position_identity_key
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Long enough to cover a broker round trip and the bookkeeping after it, short
# enough that a failed attempt does not lock a symbol out for a meaningful part
# of a session. A claim outliving its usefulness only delays a retry; one
# expiring too early reopens the window it exists to close, so this errs long.
DEFAULT_CLAIM_TTL_SECONDS = 120


async def try_claim(
    underlying: str,
    asset_class: str,
    *,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    dispatch_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Claim (underlying, asset class) for an entry about to be submitted.

    True means this caller holds the claim and may proceed. False means
    another entry for the same position is already in flight and this one must
    not submit.

    The decision is made by the database, not by this function: expired claims
    are reaped and the new one inserted with ON CONFLICT DO NOTHING inside one
    transaction, so two concurrent callers cannot both be told True. Checking
    for an existing row and then inserting would have exactly the race it is
    here to prevent.
    """
    from app.core.database import AsyncSessionLocal

    key_underlying, key_class = position_identity_key(underlying, asset_class)
    at = now or datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Reap first, in the same transaction, so a claim left behind by a
            # killed process cannot wedge the symbol until someone notices.
            await session.execute(
                delete(PositionClaim).where(PositionClaim.expires_at <= at)
            )
            result = await session.execute(
                pg_insert(PositionClaim)
                .values(
                    underlying=key_underlying,
                    asset_class=key_class,
                    claimed_at=at,
                    expires_at=at + timedelta(seconds=ttl_seconds),
                    dispatch_id=dispatch_id,
                )
                .on_conflict_do_nothing(
                    index_elements=["underlying", "asset_class"]
                )
            )
            won = result.rowcount == 1

    if not won:
        logger.info(
            "Position claim for %s %s is already held — an entry is in flight",
            key_underlying, key_class,
        )
    return won


async def release(underlying: str, asset_class: str) -> None:
    """Hand a claim back early.

    Not called on the main entry path — see the model docstring on why claims
    expire rather than being released there. This exists for callers that know
    an entry definitively will not be attempted and want the symbol free
    immediately, and for tests.
    """
    from app.core.database import AsyncSessionLocal

    key_underlying, key_class = position_identity_key(underlying, asset_class)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                delete(PositionClaim).where(
                    PositionClaim.underlying == key_underlying,
                    PositionClaim.asset_class == key_class,
                )
            )
