"""
Rules for turning an access request into an account.

Kept out of the route so each rule is testable on its own and so the route
reads as a sequence of decisions rather than a wall of conditionals. Every
predicate here answers one question and fails toward refusing.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.access_request import (
    SETUP_TOKEN_TTL_HOURS, STATUS_APPROVED, STATUS_PENDING,
)
from app.services.auth_service import hash_token

#: 32 bytes of CSPRNG, URL-safe. The same size the session token uses, for the
#: same reason: this value is the entire proof of an approval, so it has to be
#: unguessable rather than merely unique.
SETUP_TOKEN_BYTES = 32

#: A reason longer than this is almost certainly not a reason. Capped at the
#: schema so the column never sees it; the column is Text so a long GENUINE
#: answer is not truncated into something that reads as evasive.
MAX_REASON_LEN = 2000


def new_setup_token() -> tuple[str, str]:
    """Return (plaintext_token, token_hash). Only the hash is ever stored."""
    token = secrets.token_urlsafe(SETUP_TOKEN_BYTES)
    return token, hash_token(token)


def setup_token_expiry(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)) + timedelta(hours=SETUP_TOKEN_TTL_HOURS)


def is_claimable(request, now: Optional[datetime] = None) -> bool:
    """Can this approved request still be redeemed?

    Written as an explicit allow rather than a chain of early returns, the same
    shape as is_session_valid, because every one of these has to hold and a
    missing check here is an account created from a dead grant.

    Deliberately does NOT check the token itself — the caller has already
    matched on the hash to find this row. Mixing the lookup into the predicate
    would make it impossible to test the rules without a database.
    """
    if request is None:
        return False
    if request.status != STATUS_APPROVED:
        # Covers pending (never approved), denied, and claimed (already used).
        # A claimed request keeps its token hash rather than clearing it, so
        # that a replay finds THIS row and is refused here, instead of finding
        # nothing and being indistinguishable from a typo.
        return False
    if request.claimed_at is not None:
        return False
    if not request.setup_token_hash:
        return False
    expires = request.setup_token_expires_at
    if expires is None:
        # An approval with no expiry is a grant that never dies. Refuse rather
        # than treat a missing bound as unlimited.
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > (now or datetime.now(timezone.utc))


def is_reviewable(request) -> bool:
    """Only a pending request can be approved or denied.

    Re-approving an already-approved request would mint a SECOND live token
    for the same grant, so the first one keeps working after the operator
    believes they have replaced it.
    """
    return request is not None and request.status == STATUS_PENDING
