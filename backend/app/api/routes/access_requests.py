"""
Ask for an account; approve one; redeem an approval.

TWO PREFIXES, NOT ONE, and the reason is load-bearing. The allowlist in
auth_deps matches on PATH ALONE — it has no notion of method, because
`is_public_path("/api/access-requests")` is all require_session gets to see.
So if the public submission form and the operator's review queue shared a
path, allowlisting the form would publish the queue: every pending request,
every email address, every reason, behind nothing but the operator key. The
public routes therefore live under /api/access-requests and the operator's
under /api/admin/access-requests, where allowlisting one cannot reach the
other.

THE ENUMERATION RULE, because it shapes every response here. This app's users
are, by construction, people with brokerage accounts. A form that answers
"that address already has an account" differently from "it does not" hands
anyone a way to test whether a given person trades here. So the public
submission route returns the SAME body and the SAME status for:

  * a genuinely new address,
  * an address that already has an account,
  * an address with a request already pending,
  * an address that was previously denied.

The operator sees the difference in the queue. The submitter never does.

The same rule governs redemption: a wrong token, an expired one, one already
used, and one for a request that was denied all return an identical 400.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_api_key_configured
from app.api.rate_limit import client_ip, signup_rate_limit
from app.core.config import settings
from app.models.access_request import (
    STATUS_APPROVED, STATUS_CLAIMED, STATUS_DENIED, STATUS_PENDING,
)
from app.services.access_request_service import (
    MAX_REASON_LEN, is_claimable, is_reviewable, new_setup_token,
    setup_token_expiry,
)
from app.services.auth_service import (
    MAX_PASSWORD_LEN, MIN_PASSWORD_LEN, hash_password, hash_token,
    normalize_email,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: Public. Both paths here are named in auth_deps.PUBLIC_EXACT.
router = APIRouter(prefix="/api/access-requests", tags=["Access"])

#: Operator-only, and deliberately NOT under the public prefix. Guarded by
#: require_api_key_configured rather than require_api_key: the permissive
#: variant no-ops when SECRET_KEY is empty, and "mint a live account grant"
#: is not a thing to leave open on a misconfigured install. With auth enabled
#: these also sit behind the app-level session dependency.
admin_router = APIRouter(
    prefix="/api/admin/access-requests",
    tags=["Access"],
    dependencies=[Depends(require_api_key_configured)],
)

#: One body for every outcome of a submission. Deliberately says "recorded"
#: rather than "created": it is true whether or not a row was written, which
#: is what keeps the duplicate and already-a-user cases indistinguishable.
_SUBMITTED = {
    "ok": True,
    "detail": "Request recorded. You will be contacted if it is approved.",
}


class AccessRequestIn(BaseModel):
    email: str = Field(..., max_length=320)
    reason: str | None = Field(default=None, max_length=MAX_REASON_LEN)


class ClaimIn(BaseModel):
    token: str = Field(..., max_length=512)
    # The shared bounds, not literals. A cap here below the one create_user.py
    # enforces would provision an account that cannot be logged into.
    password: str = Field(..., min_length=MIN_PASSWORD_LEN,
                          max_length=MAX_PASSWORD_LEN)


class ReviewIn(BaseModel):
    request_id: str = Field(..., max_length=64)


def _enabled_or_404() -> None:
    """These routes do not exist while auth is off.

    An install running on nginx Basic Auth alone has no accounts to grant, so
    a queue nobody can act on is worse than no queue — and a claim route that
    creates users no login route will accept is worse still.
    """
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Authentication is not enabled")


def _request_uuid(raw: str) -> uuid.UUID:
    """Parse an id, or 404.

    Parsed to uuid.UUID at the boundary because AccessRequest.id is
    UUID(as_uuid=True): binding a str there fails in asyncpg before the query
    runs, which surfaces as a 500 rather than "no such request". Exactly the
    defect review found on the session-revoke route.
    """
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="No such request")


@router.post("")
async def submit_request(
    body: AccessRequestIn,
    request: Request,
    _rl: None = Depends(signup_rate_limit),
) -> dict:
    """Record a request for an account. Public, rate-limited, uninformative."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.access_request import AccessRequest
    from app.models.user import User

    _enabled_or_404()

    email = normalize_email(body.email)
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        # Shape only. This is not an existence check and says nothing about
        # who is registered.
        raise HTTPException(status_code=422, detail="That is not an email address")

    try:
        async with AsyncSessionLocal() as db:
            existing_user = (await db.execute(
                select(User).where(User.email == email).limit(1)
            )).scalar_one_or_none()
            pending = (await db.execute(
                select(AccessRequest).where(
                    AccessRequest.email == email,
                    AccessRequest.status == STATUS_PENDING,
                ).limit(1)
            )).scalar_one_or_none()

            if existing_user is None and pending is None:
                db.add(AccessRequest(
                    email=email,
                    reason=(body.reason or "").strip() or None,
                    status=STATUS_PENDING,
                    ip=client_ip(request),
                ))
                await db.commit()
                logger.info("Access request recorded for %s", email)
            else:
                # Nothing written, same answer returned. Logged so the operator
                # can see repeat traffic the queue will not show.
                logger.info("Access request ignored for %s (already known)", email)
    except HTTPException:
        raise
    except Exception as exc:      # noqa: BLE001 - see below
        # A failure must not become a signal either. If a duplicate raced past
        # the check above and tripped the partial unique index, a 500 on some
        # addresses and a 200 on others is the same oracle by another route.
        logger.error("Access request could not be recorded: %s", exc)

    return dict(_SUBMITTED)


@router.post("/claim")
async def claim_request(
    body: ClaimIn,
    _rl: None = Depends(signup_rate_limit),
) -> dict:
    """Redeem a setup token and create the account.

    Public, because the person has no account yet — that is the whole point —
    and rate-limited for the same reason the submission route is.

    EVERY FAILURE IS THE SAME 400. A wrong token, an expired one, one already
    used, and one whose request was denied are indistinguishable. Saying which
    would turn this into a way to probe the token space with feedback.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.access_request import AccessRequest
    from app.models.user import User

    _enabled_or_404()

    generic = HTTPException(status_code=400,
                            detail="That setup link is not valid")
    token_hash = hash_token(body.token)

    async with AsyncSessionLocal() as db:
        req = (await db.execute(
            select(AccessRequest)
            .where(AccessRequest.setup_token_hash == token_hash)
            .limit(1)
            # Locked for the same reason the password-change route locks the
            # user row: two redemptions of one token arriving together would
            # both read status=approved and both try to create the account.
            .with_for_update()
        )).scalar_one_or_none()

        if not is_claimable(req):
            logger.warning("Rejected setup-token claim")
            raise generic

        # Between approval and redemption someone may have been provisioned by
        # hand. Creating a second row would violate the unique index on
        # users.email and 500; the same generic error keeps it uninformative.
        existing = (await db.execute(
            select(User).where(User.email == req.email).limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            logger.warning("Setup token claimed for an address that already "
                           "has an account: %s", req.email)
            raise generic

        db.add(User(email=req.email, password_hash=hash_password(body.password)))
        req.status = STATUS_CLAIMED
        req.claimed_at = datetime.now(timezone.utc)
        # The hash is KEPT rather than cleared, so a replay finds this row and
        # is refused by is_claimable() instead of finding nothing — which would
        # be indistinguishable from a typo and much harder to diagnose.
        await db.commit()
        email = req.email

    logger.info("Account created from access request: %s", email)
    return {"ok": True, "detail": "Account created. You can sign in now."}


@admin_router.get("")
async def list_requests(status: str | None = None) -> dict:
    """The operator's queue."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.access_request import AccessRequest

    _enabled_or_404()

    async with AsyncSessionLocal() as db:
        stmt = select(AccessRequest).order_by(AccessRequest.created_at.desc())
        if status:
            stmt = stmt.where(AccessRequest.status == status)
        rows = (await db.execute(stmt)).scalars().all()

        out = [{
            "id": str(r.id),
            "email": r.email,
            "reason": r.reason,
            "status": r.status,
            "ip": r.ip,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "claimed_at": r.claimed_at.isoformat() if r.claimed_at else None,
            # Never the hash, and never the token. Whether a grant is still
            # live is what the operator needs; the digest is not.
            "has_live_token": bool(r.setup_token_hash) and r.claimed_at is None,
        } for r in rows]

    return {"requests": out}


@admin_router.post("/approve")
async def approve_request(body: ReviewIn) -> dict:
    """Approve a pending request and mint its one-time setup token.

    THE TOKEN IS RETURNED EXACTLY ONCE, here, and only its hash is stored.
    There is no outbound email anywhere in this codebase, so nothing can send
    it — the operator passes it on by whatever channel they already trust.

    That constraint turns out to be a feature: no credential is ever
    transmitted by this system, and the operator never learns the password the
    person eventually sets.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.access_request import AccessRequest

    _enabled_or_404()
    target = _request_uuid(body.request_id)
    token, token_hash = new_setup_token()

    async with AsyncSessionLocal() as db:
        req = (await db.execute(
            select(AccessRequest).where(AccessRequest.id == target).limit(1)
            .with_for_update()
        )).scalar_one_or_none()

        if req is None:
            raise HTTPException(status_code=404, detail="No such request")
        if not is_reviewable(req):
            # Re-approving would mint a SECOND live token for the same grant,
            # leaving the first working after the operator believes they have
            # replaced it.
            raise HTTPException(
                status_code=409,
                detail=f"Request is {req.status}, not pending",
            )

        req.status = STATUS_APPROVED
        req.setup_token_hash = token_hash
        req.setup_token_expires_at = setup_token_expiry()
        req.reviewed_at = datetime.now(timezone.utc)
        await db.commit()
        email = req.email
        expires = req.setup_token_expires_at

    logger.info("Access request approved for %s", email)
    return {
        "ok": True,
        "email": email,
        "setup_token": token,
        "expires_at": expires.isoformat(),
        "detail": ("Give this token to the person — it is shown once and is "
                   "not recoverable. They set their own password with it."),
    }


@admin_router.post("/deny")
async def deny_request(body: ReviewIn) -> dict:
    """Deny a pending request.

    Not final: the partial unique index covers only pending rows, so a denied
    address can apply again. A plain unique index on email would have made
    every denial permanent, which is a harsher policy than anyone chose.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.access_request import AccessRequest

    _enabled_or_404()
    target = _request_uuid(body.request_id)

    async with AsyncSessionLocal() as db:
        req = (await db.execute(
            select(AccessRequest).where(AccessRequest.id == target).limit(1)
            .with_for_update()
        )).scalar_one_or_none()
        if req is None:
            raise HTTPException(status_code=404, detail="No such request")
        if not is_reviewable(req):
            raise HTTPException(status_code=409,
                                detail=f"Request is {req.status}, not pending")

        req.status = STATUS_DENIED
        req.reviewed_at = datetime.now(timezone.utc)
        # Any token is cleared. A pending request should not have one, but if
        # some future path leaves one behind, denial must kill the grant.
        req.setup_token_hash = None
        req.setup_token_expires_at = None
        await db.commit()
        email = req.email

    logger.info("Access request denied for %s", email)
    return {"ok": True}
