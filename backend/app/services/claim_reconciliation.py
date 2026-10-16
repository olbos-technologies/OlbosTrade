"""Running the unresolved-claim sweep: at startup, and periodically.

A claim left `submitted` or `unknown` blocks new entries on that position
until something establishes what became of the order. Until this module
existed, `reconcile_unresolved` had no caller: the mechanism was present and
nothing ever ran it, so in production a crashed submit blocked its position
until a human noticed. That is the gap this closes.

What it does NOT do is widen what counts as evidence. Every decision still
comes from `position_claim.reconcile_unresolved`, which releases only on an
authoritative absence past the settle window. This module decides *when* to
ask and *how often* to retry; it never decides the answer.

Retries are bounded, and bounded on purpose. The sweep is retried only while
the broker is failing to answer — an `unreachable` or `indeterminate` outcome
with nothing released — because retrying a definite answer gains nothing. A
claim that survives the bound is not retried into oblivion: it is logged at
CRITICAL with the operator's way out, and left for the next tick.
"""

from __future__ import annotations

import asyncio

from app.services.position_claim import reconcile_unresolved, unresolved
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: How many times one sweep will re-ask when the broker could not answer.
#: Small: the periodic worker comes back in minutes anyway, so a long retry
#: chain here only delays the rest of the scheduler.
MAX_ATTEMPTS = 3
#: Seconds before the second attempt; doubled for each one after.
RETRY_BASE_SECONDS = 2.0


async def reconcile_all_scopes(*, settle_seconds: int | None = None) -> dict:
    """One sweep per owner, each against that owner's own broker.

    Batch E2 step 6. The claim sweep has the same shape as position
    reconciliation and the same failure: a release happens when the broker
    says ABSENT past the settle window, and a broker that was never asked
    about a claim says ABSENT about it. So sweeping tenant B's claims against
    tenant A's broker would release B's in-flight positions back to the
    duplicate guard as free — the guard would then admit a second entry on a
    position that is already being opened, which is the exact race #102 added
    claims to close.

    An owner whose broker cannot be resolved is skipped, leaving the claims
    blocked. Blocked is the safe direction: it refuses new entries on those
    positions rather than permitting duplicates.
    """
    from app.core.database import AsyncSessionLocal
    from app.services.trade_scope import claim_scope
    from app.services.worker_scope import for_each_scope

    results: dict = {}

    async def _sweep(scoped) -> None:
        results[scoped.organization_id] = await reconcile_once(
            broker=scoped.broker,
            scope=claim_scope(scoped.organization_id),
            settle_seconds=settle_seconds,
        )

    summary = await for_each_scope(AsyncSessionLocal, _sweep, job="position_claims")
    summary["per_scope"] = {
        ("single-operator" if k is None else str(k)): v for k, v in results.items()
    }
    return summary


async def reconcile_once(*, broker=None, scope: str | None = None,
                         settle_seconds: int | None = None) -> dict:
    """Run one sweep, retrying a bounded number of times if nothing answered.

    Returns the last sweep's counts, plus `attempts`. Never raises: a
    reconciliation failure must not take down startup or stop the scheduler,
    and a claim left blocked is the safe direction.

    `scope` restricts the sweep to one owner's claims and must be paired with
    that owner's broker — see `reconcile_all_scopes`, which is what the
    scheduler calls. Left None, this sweeps every scope, which is why the
    no-broker path below is safe: `lookup_for(None)` answers INDETERMINATE for
    everything and INDETERMINATE releases nothing.
    """
    from app.services.claim_lookup import lookup_for

    if broker is None:
        try:
            from app.broker.broker_factory import get_broker
            broker = get_broker()
        except Exception as exc:
            # No broker is not an error here — it means nothing can be
            # established, which lookup_for(None) reports as indeterminate.
            logger.warning("Claim reconciliation has no broker: %s", exc)
            broker = None

    lookup = lookup_for(broker)
    kwargs = {} if settle_seconds is None else {"settle_seconds": settle_seconds}
    if scope is not None:
        kwargs["scope"] = scope

    counts: dict = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            counts = await reconcile_unresolved(lookup, **kwargs)
        except Exception as exc:
            logger.error(
                "Claim reconciliation sweep %d/%d failed: %s",
                attempt, MAX_ATTEMPTS, exc,
            )
            counts = {"error": str(exc)}
        else:
            if not any(counts.values()):
                logger.debug("Claim reconciliation: nothing unresolved")
                counts["attempts"] = attempt
                return counts

            logger.info(
                "Claim reconciliation sweep %d/%d: released=%d still_unresolved=%d "
                "indeterminate=%d unreachable=%d too_fresh=%d",
                attempt, MAX_ATTEMPTS,
                counts.get("released", 0), counts.get("still_unresolved", 0),
                counts.get("indeterminate", 0), counts.get("unreachable", 0),
                counts.get("too_fresh", 0),
            )

            # Only re-ask while the broker is the thing that did not answer.
            # PRESENT, ABSENT and too_fresh are all answers; asking again
            # would just repeat them.
            unanswered = counts.get("unreachable", 0) + counts.get("indeterminate", 0)
            if unanswered == 0 or counts.get("released", 0):
                counts["attempts"] = attempt
                return counts

        if attempt < MAX_ATTEMPTS:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info(
                "Claim reconciliation: broker did not answer; retrying in %.0fs "
                "(attempt %d of %d)", delay, attempt + 1, MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)

    counts["attempts"] = MAX_ATTEMPTS
    await _report_persistently_blocked()
    return counts


async def _report_persistently_blocked() -> None:
    """Say exactly what is still blocked, and what the way out is.

    A sweep that exhausts its retries has established nothing, so the claims
    are still blocking entries. Logging the count alone would leave an
    operator to work out which positions are affected; logging the positions
    and the override makes the next step obvious.
    """
    try:
        rows = await unresolved(limit=20)
    except Exception as exc:
        logger.error("Could not list unresolved claims after retries: %s", exc)
        return

    if not rows:
        return

    logger.critical(
        "Claim reconciliation exhausted %d attempts with %d claim(s) still "
        "unresolved — new entries on these positions are BLOCKED. Resolve via "
        "GET /api/admin/position-claims and POST "
        "/api/admin/position-claims/{claim_token}/release (audited).",
        MAX_ATTEMPTS, len(rows),
    )
    for row in rows:
        logger.critical(
            "  BLOCKED %s %s state=%s key=%s reason=%s",
            row.underlying, row.asset_class, row.state,
            row.idempotency_key, row.unresolved_reason,
        )


async def reconcile_at_startup() -> None:
    """One sweep as the process comes up.

    This is the case the claim lifecycle exists for: the previous process may
    have died between recording intent and hearing back from the broker, and
    whatever it left behind is blocking entries right now. Waiting for the
    first periodic tick would mean carrying that block for no reason.
    """
    logger.info("Reconciling unresolved position claims at startup")
    counts = await reconcile_all_scopes()
    logger.info("Startup claim reconciliation complete: %s", counts)
