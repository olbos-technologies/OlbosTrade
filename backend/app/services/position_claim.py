"""
Taking, advancing and reconciling position claims.

See app/models/position_claim.py for the lifecycle and why it is a lifecycle
rather than a lease. This module is the only thing that writes these rows.

Every lease comparison uses `func.now()` — DATABASE time on both sides. A
worker whose clock has drifted must not be able to reclaim a live claim early
or keep a dead one past its lease, and the only clock all workers share is the
one in the database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from datetime import timedelta
from typing import Awaitable, Callable, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.position_claim import (
    GLOBAL_SCOPE, STATE_PENDING, STATE_SUBMITTED, STATE_UNKNOWN,
    UNRESOLVED_STATES, PositionClaim,
)
from app.services.trade_identity import position_identity_key
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Governs only the `pending` part of the window — the stretch in which nothing
# has been sent. Once a claim is `submitted` this stops applying, so it does
# not need to cover a slow broker: it needs to cover the gap between claiming
# and deciding to submit.
DEFAULT_LEASE_SECONDS = 120


@dataclass(frozen=True)
class Claim:
    """A held claim. `token` authorises every later change to it."""
    token: uuid.UUID
    idempotency_key: str
    underlying: str
    asset_class: str
    scope: str = GLOBAL_SCOPE


async def try_claim(
    underlying: str,
    asset_class: str,
    *,
    scope: str = GLOBAL_SCOPE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    dispatch_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Optional[Claim]:
    """Claim (scope, underlying, asset class) for an entry about to be submitted.

    Returns a Claim when this caller holds it and may proceed, or None when it
    must not submit — either another entry is in flight, or a previous one's
    outcome is still unknown.

    Reaping is deliberately narrow: only `pending` claims past their lease are
    removed. A `submitted` or `unknown` claim is never reclaimed by time,
    because a lease running out is not evidence that no order exists. Those
    need reconcile_unresolved() or an operator.

    The decision is made by the database: reap and insert happen in one
    transaction and the insert is ON CONFLICT DO NOTHING, so two concurrent
    callers cannot both be handed a Claim.
    """
    from app.core.database import AsyncSessionLocal

    key_underlying, key_class = position_identity_key(underlying, asset_class)
    token = uuid.uuid4()
    idem = idempotency_key or f"oc-{token.hex[:24]}"

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Reap first, in the same transaction, so a claim left behind by a
            # worker that died BEFORE sending anything cannot wedge the symbol.
            # `state == pending` is the whole safety property of this line.
            await session.execute(
                delete(PositionClaim).where(
                    PositionClaim.scope == scope,
                    PositionClaim.underlying == key_underlying,
                    PositionClaim.asset_class == key_class,
                    PositionClaim.state == STATE_PENDING,
                    PositionClaim.lease_expires_at <= func.now(),
                )
            )
            result = await session.execute(
                pg_insert(PositionClaim)
                .values(
                    scope=scope,
                    underlying=key_underlying,
                    asset_class=key_class,
                    claim_token=token,
                    state=STATE_PENDING,
                    idempotency_key=idem,
                    dispatch_id=dispatch_id,
                    lease_expires_at=func.now() + timedelta(seconds=lease_seconds),
                )
                .on_conflict_do_nothing(
                    index_elements=["scope", "underlying", "asset_class"]
                )
            )
            won = result.rowcount == 1

    if won:
        return Claim(token=token, idempotency_key=idem,
                     underlying=key_underlying, asset_class=key_class, scope=scope)

    blocker = await describe(key_underlying, key_class, scope=scope)
    logger.info(
        "Position claim for %s %s refused — %s",
        key_underlying, key_class,
        f"state={blocker.state}" if blocker else "held by another entry",
    )
    return None


async def mark_submitted(claim: Claim) -> bool:
    """Record, durably, that an order is about to go to the broker.

    CALLED BEFORE THE BROKER CALL, NOT AFTER. Recording it afterwards leaves
    the gap this exists to close: a process killed mid-call would have sent an
    order and persisted nothing, and the claim's lease would then hand the
    position to someone else. The cost of recording first is that an order
    rejected before it ever reached the venue still blocks until resolved,
    which is the direction that does not double-fill an account.

    Returns False when the claim is no longer this caller's — the token did
    not match a row — which means something else now owns the position and
    this caller must not submit.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                update(PositionClaim)
                .where(PositionClaim.claim_token == claim.token)
                .values(state=STATE_SUBMITTED, submitted_at=func.now())
            )
    if result.rowcount != 1:
        logger.error(
            "Claim %s for %s %s is no longer held — refusing to submit",
            claim.token, claim.underlying, claim.asset_class,
        )
        return False
    return True


async def resolve(claim: Claim) -> None:
    """The outcome is known. Release the position for future entries.

    Keyed on the token, never on the symbol: a worker whose pending claim was
    reaped must not be able to delete the claim of whoever holds the position
    now.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                delete(PositionClaim).where(PositionClaim.claim_token == claim.token)
            )
    if result.rowcount == 0:
        logger.warning(
            "Claim %s for %s %s was already gone when resolved",
            claim.token, claim.underlying, claim.asset_class,
        )


async def mark_unknown(claim: Claim, reason: str) -> None:
    """The submission returned no usable answer.

    The claim stays and stops being reclaimable by time. Blocking a legitimate
    re-entry until someone establishes what happened is the point: the
    alternative is sending a second order into an account that may already
    hold the first.
    """
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                update(PositionClaim)
                .where(PositionClaim.claim_token == claim.token)
                .values(state=STATE_UNKNOWN, unresolved_reason=reason[:200])
            )
    logger.critical(
        "Claim %s for %s %s is UNRESOLVED (%s) — idempotency key %s. No further "
        "entry for this position until reconciled.",
        claim.token, claim.underlying, claim.asset_class, reason,
        claim.idempotency_key,
    )


async def describe(
    underlying: str, asset_class: str, *, scope: str = GLOBAL_SCOPE
) -> Optional[PositionClaim]:
    """The claim currently blocking this position, if any. Read-only."""
    from app.core.database import AsyncSessionLocal

    key_underlying, key_class = position_identity_key(underlying, asset_class)
    async with AsyncSessionLocal() as session:
        return (await session.execute(
            select(PositionClaim).where(
                PositionClaim.scope == scope,
                PositionClaim.underlying == key_underlying,
                PositionClaim.asset_class == key_class,
            )
        )).scalar_one_or_none()


async def unresolved(limit: int = 100) -> list[PositionClaim]:
    """Claims whose outcome nobody has established. Each one is a position
    that cannot be re-entered until someone does."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        return list((await session.execute(
            select(PositionClaim)
            .where(PositionClaim.state.in_(UNRESOLVED_STATES))
            .order_by(PositionClaim.claimed_at)
            .limit(limit)
        )).scalars().all())


class BrokerVerdict(str, Enum):
    """What the broker was able to tell us about one client_order_id.

    `Optional[order]` was not enough. It collapsed two different answers into
    None: "I asked, and this broker authoritatively has no such order" and "I
    asked, and I cannot see one *yet*". Order-by-client-id lookups are not
    immediately consistent — an order accepted moments ago can read as missing
    — and releasing a claim on that reading re-opens the position to a second
    entry on a live order, which is the exact duplicate this module exists to
    prevent. Only ABSENT may release a claim.
    """

    #: The broker states positively that no such order exists.
    ABSENT = "absent"
    #: The broker has an order for this key, in any state.
    PRESENT = "present"
    #: Neither could be established — not visible yet, a partial or degraded
    #: response, a key the broker does not recognise as either. Resolves nothing.
    INDETERMINATE = "indeterminate"


#: What the broker says about one unresolved claim. Takes the CLAIM, not just
#: its key: whether a lookup can establish absence at all depends on the asset
#: class (only the options path transmits the key), so a key-only contract
#: cannot express "this is not answerable". See services/claim_lookup.py.
#:
#: Raising means the lookup failed, which is NOT the same as "no such order"
#: and must not resolve a claim — same contract as
#: ambiguous_order_resolver.BrokerLookup.
BrokerLookup = Callable[["PositionClaim"], Awaitable[BrokerVerdict]]

# Even an authoritative ABSENT is not trusted on a claim this young. A broker
# that has accepted an order can still answer "no such order" for a short
# while, and the cost of believing it is a duplicate position. Waiting costs a
# blocked entry on one underlying; this is the asymmetry the whole module is
# built around.
DEFAULT_SETTLE_SECONDS = 120


async def reconcile_unresolved(
    lookup: BrokerLookup,
    *,
    limit: int = 50,
    settle_seconds: int = DEFAULT_SETTLE_SECONDS,
) -> dict:
    """Ask the broker what became of each unresolved claim.

    A claim is released ONLY when the broker positively states the order does
    not exist (BrokerVerdict.ABSENT) AND the claim is older than
    `settle_seconds`, so a not-yet-visible order cannot be mistaken for an
    absent one. PRESENT, INDETERMINATE, a lookup that raises, and anything
    still inside the settle window all leave the claim exactly as it is.

    "I could not ask", "it is not there yet" and "there is nothing there" are
    three different answers and only the last one releases anything.

    This is deliberately the conservative half of reconciliation. It closes
    claims that provably correspond to no order; it does not try to reconstruct
    positions, which is position_reconciler's and ambiguous_order_resolver's
    work and reachable through the same client_order_id.

    Every count it returns is a distinct outcome, so an operator can tell a
    quiet broker from a clean sweep — `released: 0, unreachable: 7` and
    `released: 7` must never look alike.
    """
    from app.core.database import AsyncSessionLocal

    counts = {
        "released": 0,
        "still_unresolved": 0,
        "indeterminate": 0,
        "unreachable": 0,
        "too_fresh": 0,
    }

    # Settled-ness is decided by the DATABASE clock, like every other time
    # comparison here; a worker with a fast clock must not be able to declare a
    # claim old enough to abandon.
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(
                PositionClaim,
                (
                    func.extract(
                        "epoch",
                        func.now() - func.coalesce(
                            PositionClaim.submitted_at, PositionClaim.claimed_at
                        ),
                    ) >= settle_seconds
                ).label("settled"),
            )
            .where(PositionClaim.state.in_(UNRESOLVED_STATES))
            .order_by(PositionClaim.claimed_at)
            .limit(limit)
        )).all()

    for claim, settled in rows:
        try:
            verdict = await lookup(claim)
        except Exception as exc:
            counts["unreachable"] += 1
            logger.error(
                "Could not ask the broker about claim %s (%s %s): %s",
                claim.claim_token, claim.underlying, claim.asset_class, exc,
            )
            continue

        if verdict is BrokerVerdict.PRESENT:
            counts["still_unresolved"] += 1
            logger.warning(
                "Claim %s for %s %s corresponds to a real broker order (%s) — "
                "leaving it blocked for position reconciliation",
                claim.claim_token, claim.underlying, claim.asset_class,
                claim.idempotency_key,
            )
            continue

        if verdict is not BrokerVerdict.ABSENT:
            # INDETERMINATE, or anything a lookup returns that is not one of
            # the three verdicts. Unrecognised answers are treated as the
            # cautious one rather than assumed absent.
            counts["indeterminate"] += 1
            logger.warning(
                "Broker could not say whether claim %s (%s) exists — leaving "
                "it blocked",
                claim.claim_token, claim.idempotency_key,
            )
            continue

        if not settled:
            counts["too_fresh"] += 1
            logger.info(
                "Broker reports no order for claim %s (%s), but it is younger "
                "than the %ss settle window — not releasing yet",
                claim.claim_token, claim.idempotency_key, settle_seconds,
            )
            continue

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    delete(PositionClaim).where(
                        PositionClaim.claim_token == claim.claim_token
                    )
                )
        counts["released"] += 1
        logger.info(
            "Claim %s released — the broker authoritatively has no order for %s",
            claim.claim_token, claim.idempotency_key,
        )

    return counts


async def force_release(
    claim_token: uuid.UUID, *, operator: str, reason: str
) -> bool:
    """Operator override: drop a claim the automated path will not.

    A claim left `submitted` or `unknown` blocks its position until the broker
    gives an authoritative answer. When the broker never will — a venue that no
    longer knows the key, an outage resolved by hand — somebody has to be able
    to clear it, or the only way out is editing the table directly, which
    leaves no trace.

    So this exists, and it is audited rather than silent: the delete and the
    `execution_events` row are written in ONE transaction, so a release can
    never land without the record of who did it and why. Returns False when the
    token names no claim, and writes nothing in that case — an override that
    overrode nothing must not leave an audit row saying it did.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent

    async with AsyncSessionLocal() as session:
        async with session.begin():
            claim = (await session.execute(
                select(PositionClaim).where(
                    PositionClaim.claim_token == claim_token
                )
            )).scalar_one_or_none()
            if claim is None:
                return False

            session.add(ExecutionEvent(
                kind="claim_override",
                ticker=claim.underlying,
                asset_type=claim.asset_class,
                status="force_released",
                payload={
                    "claim_token": str(claim.claim_token),
                    "scope": claim.scope,
                    "underlying": claim.underlying,
                    "asset_class": claim.asset_class,
                    "state": claim.state,
                    "idempotency_key": claim.idempotency_key,
                    "dispatch_id": claim.dispatch_id,
                    "unresolved_reason": claim.unresolved_reason,
                    "operator": operator,
                    "reason": reason,
                },
            ))
            await session.execute(
                delete(PositionClaim).where(
                    PositionClaim.claim_token == claim_token
                )
            )

    logger.warning(
        "Claim %s (%s %s, state=%s) force-released by %s: %s",
        claim_token, claim.underlying, claim.asset_class, claim.state,
        operator, reason,
    )
    return True
