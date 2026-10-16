"""Which trades a caller — or an autonomous entry — may touch.

`trades` is read by 22 modules. Every one of them was a shared query before
Batch E2, so this module exists to make the scoped form the easy one to write
and the unscoped form hard to write by accident.

Two rules carried over from Batch E (journal entries, migration 0039), for the
same reasons:

**`None` is a scope, not an absence of one.** It is the single-operator scope
used when auth is disabled, and it matches exactly the rows migration 0041
declined to guess an owner for. Writing a filter as "no filter when None"
reinstates the shared query this removes — it is the single most likely way to
get E2 wrong, so `owned_by` is the only place that decision is made.

**Scope comes from the authenticated server context, never from the request.**
An `organization_id` parameter a client could send would hand the isolation
decision to the caller.

One rule Batch E did not need, because a journal entry nobody owns is inert
while an open trade nobody owns is a live position: an entry initiated by the
background scanner has no caller to take a scope from, and must therefore
either resolve one unambiguously or refuse. See `autonomous_scope`.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select

from app.models.trade import Trade
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AmbiguousOwner(Exception):
    """A background-initiated entry could not establish whose position it is.

    Raised rather than defaulting, because every available default is wrong:
    the single-operator scope would file one tenant's position under another's,
    and "no scope" is the shared query. The caller turns this into a refusal.
    """


def owned_by(org_id: Optional[uuid.UUID]):
    """The filter restricting a query to one owner's trades.

    `org_id is None` matches the unattributed rows — the single-operator scope
    — and NOT every row. This function is the only correct way to express the
    filter; `test_trade_scope_is_the_only_filter` asserts that route modules
    do not hand-roll it.
    """
    if org_id is None:
        return Trade.organization_id.is_(None)
    return Trade.organization_id == org_id


async def request_scope(db, conn) -> Optional[uuid.UUID]:
    """The scope of a request-initiated read or write.

    Delegates to `organization_service.owner_scope`, so trades and journal
    entries cannot drift apart on what a caller's scope is. Propagates
    PermissionError for a request with auth on and no identity: there is no
    safe default to fall back to, and answering with the single-operator scope
    would show one tenant's positions to an unauthenticated caller.
    """
    from app.services.organization_service import owner_scope

    return await owner_scope(db, conn)


async def autonomous_scope(db) -> Optional[uuid.UUID]:
    """The scope for an entry the background scanner initiated.

    `trade_desk.handle_signal` is called by the scanner in `main.py`, and in
    AUTOPILOT mode it calls `_execute_signal` with no request and no caller.
    So the entry path has to answer "whose position is this?" without one.

    Three cases, and only one of them guesses — so that one refuses:

    * auth disabled    -> the single-operator scope (None). There is one
                          operator, one set of positions, and nothing to
                          isolate from.
    * exactly one personal organization -> that organization. The same
                          "unambiguous or nothing" test migration 0041 applies
                          to its backfill: one organization means one person
                          could own this.
    * zero or several   -> AmbiguousOwner. Autopilot refuses to enter rather
                          than filing the position under a tenant that may not
                          own it.

    Refusing is the safe direction: a missed automated entry costs an
    opportunity, while an entry attributed to the wrong tenant puts one
    customer's capital behind another's signal and shows them a position that
    is not theirs. It is also loud — the caller surfaces it as a blocked
    entry with a reason — rather than silently picking a tenant.

    NOTE: this makes the entry path's scope explicit. It does NOT scope the
    background workers that process trades after entry (fills polling,
    excursion tracking, stop backfill, reconciliation). Those are Batch E2
    steps 4 and 6, held back deliberately, and they still process every row.
    """
    from app.core.config import settings
    from app.models.organization import Organization

    if not settings.auth_enabled:
        return None

    rows = (await db.execute(
        select(Organization.id).where(Organization.personal_for_user_id.isnot(None))
    )).scalars().all()

    if len(rows) == 1:
        return rows[0]

    raise AmbiguousOwner(
        f"{len(rows)} personal organizations exist, so an autonomous entry "
        f"cannot establish an owner without guessing"
    )


async def count_unattributed_open_trades(db) -> int:
    """Open positions migration 0041 left unowned.

    Worth surfacing because they are not inert: they still participate in the
    background paths that are not yet scoped. An operator should know the
    number is zero before adding a second tenant.
    """
    return (await db.execute(
        select(func.count()).select_from(Trade).where(
            Trade.organization_id.is_(None),
            Trade.status.in_(["open", "pending"]),
        )
    )).scalar() or 0


def claim_scope(org_id: Optional[uuid.UUID]) -> str:
    """The position-claim `scope` value for an owner.

    `position_claims` keys on (scope, underlying, asset_class) and the column
    already existed for this — PR #102 added it carrying the literal
    `"global"` and said why: `trades` had no owner, so scoping the claim while
    the duplicate read beside it stayed global would let the claim admit an
    entry the duplicate check still treated as a conflict. The two have to
    agree, which is why this lands in the same change as the scoped read.

    The single-operator scope keeps the existing `"global"` value rather than
    inventing a new token, so claims written before this change remain valid
    and keep blocking the positions they were taken for. A real organization
    uses its UUID.
    """
    from app.models.position_claim import GLOBAL_SCOPE

    return GLOBAL_SCOPE if org_id is None else str(org_id)
