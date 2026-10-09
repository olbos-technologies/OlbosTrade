"""
Resolving the organization a caller acts for.

Per ADR-0001 every user has exactly one personal organization. This creates it
LAZILY, on first use, rather than at registration — deliberately. There are two
user-creation paths today (routes/auth.py and routes/access_requests.py) and
nothing stops a third appearing; a hook on registration is a thing to forget,
while a get-or-create on the read path cannot be bypassed by a caller that does
not exist yet. It also means the users backfilled by migration 0036 and any
user created afterwards reach the same state by the same code.

IDEMPOTENT UNDER CONCURRENCY. SELECT-then-INSERT is not atomic: two requests
for the same new user can both miss and both insert. The unique index on
organizations.personal_for_user_id turns that into an IntegrityError for the
loser, which is a dedup HIT rather than a failure — roll back, re-select, and
return the winner's row. Both callers get the same organization, which is the
only thing that matters.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.organization import (
    KIND_PERSONAL, ROLE_OWNER, Organization, OrganizationMember,
)

logger = logging.getLogger(__name__)


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _select_personal(db, uid: uuid.UUID) -> Organization | None:
    result = await db.execute(
        select(Organization).where(Organization.personal_for_user_id == uid)
    )
    return result.scalar_one_or_none()


async def personal_org_for(db, user_id) -> Organization:
    """The user's personal organization, creating it if absent."""
    uid = _as_uuid(user_id)

    existing = await _select_personal(db, uid)
    if existing is not None:
        return existing

    org = Organization(name="", kind=KIND_PERSONAL, personal_for_user_id=uid)
    db.add(org)
    try:
        await db.flush()
        db.add(OrganizationMember(
            organization_id=org.id, user_id=uid, role=ROLE_OWNER,
        ))
        await db.commit()
    except IntegrityError:
        # Lost the race. The winner's row is the answer.
        await db.rollback()
        winner = await _select_personal(db, uid)
        if winner is None:
            # The unique index rejected the insert but no row is visible, so
            # the constraint that fired was not the one assumed. Surfacing it
            # beats returning an organization that does not exist.
            raise
        logger.debug("personal org race resolved for user=%s -> %s", uid, winner.id)
        return winner

    return org


async def owner_scope(db, conn) -> uuid.UUID | None:
    """The organization whose private records this request may touch.

    Returns the caller's personal organization, or None when auth is disabled —
    the single-operator deployment, where there is no user to resolve and no
    second tenant to isolate from. None is a SCOPE, not an absence of one:
    callers must filter on `organization_id IS NULL` for it, which matches
    exactly the unattributed rows migration 0039 declined to guess an owner
    for. Treating None as "no filter" would reinstate the shared query this
    exists to remove.

    Resolved from the authenticated server context only. Nothing a client
    sends takes part in deciding which records it can see.
    """
    from app.api.auth_deps import current_user
    from app.core.config import settings

    if not settings.auth_enabled:
        return None
    user = current_user(conn)
    uid = user.get("id")
    if not uid:
        # Auth is on but this request carries no identity. Fail closed: there
        # is no safe default scope to fall back to.
        raise PermissionError("no authenticated user for an owner-scoped request")
    org = await personal_org_for(db, uid)
    return org.id


async def org_ids_for_user(db, user_id) -> list[uuid.UUID]:
    """Every organization this user may act for.

    One entry today. It is a list because that is the shape the authorization
    question has once a second member exists, and a caller written against a
    list does not change then.
    """
    uid = _as_uuid(user_id)
    result = await db.execute(
        select(OrganizationMember.organization_id).where(
            OrganizationMember.user_id == uid
        )
    )
    return [row[0] for row in result.all()]


async def user_may_act_for(db, user_id, organization_id) -> bool:
    """Server-side membership check.

    The only authorization question this module answers. It is not a role
    check: ADR-0001 defers roles, and `role` exists in the schema without
    anything reading it. Do not let this grow into one without an ADR — a
    half-enforced permission model is how a viewer ends up placing an order.
    """
    uid = _as_uuid(user_id)
    oid = _as_uuid(organization_id)
    result = await db.execute(
        select(OrganizationMember.organization_id).where(
            OrganizationMember.user_id == uid,
            OrganizationMember.organization_id == oid,
        )
    )
    return result.scalar_one_or_none() is not None
