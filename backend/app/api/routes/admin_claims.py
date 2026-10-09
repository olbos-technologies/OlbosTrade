"""Operator view of unresolved position claims, and the audited override.

A claim in `submitted` or `unknown` blocks new entries on its position until
something establishes what became of the order. Reconciliation clears the ones
the broker can speak to; these routes exist for the ones it cannot — an
unsupported lookup, a key the broker never received, a venue that has
forgotten the id. Without them the only way out is editing the table by hand,
which leaves no trace of who did it.

Two rules shape the release route:

**Identity comes from authentication, never from the request body.** An
operator field a caller could fill in is not an audit trail, it is a
suggestion. `current_user` is the only source, and a caller it cannot identify
is refused rather than recorded as "unknown" — an audit row naming nobody
looks like evidence and is not.

**A reason is required and must say something.** A blank or whitespace reason
is rejected. The whole value of the record is that the next person can tell
why a guard against duplicate positions was overridden.

This lives under /api/admin/ like the access-request queue, because the
session allowlist in auth_deps matches on PATH ALONE and knows nothing about
method — a prefix shared with anything public would publish these.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.api.auth_deps import current_user
from app.services import position_claim
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/position-claims", tags=["admin"])


class ReleaseRequest(BaseModel):
    #: strip_whitespace before min_length, so "   " is rejected rather than
    #: counted as three characters of justification. The audit row's whole
    #: value is that the next person can tell why the duplicate guard was
    #: overridden, and a blank reason is not an answer.
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)
    ] = Field(description="Why this claim is being released. Recorded in the audit row.")


def _operator(request: Request) -> str:
    """The authenticated caller, or a 403.

    Deliberately not falling back to a placeholder. With auth disabled there
    is no identity to record, and an override attributed to "operator" is
    indistinguishable from one attributed to a real person — so the override
    is refused instead. The claim stays blocked, which is the safe direction,
    and the operator is told to enable auth rather than quietly getting an
    unattributable audit row.
    """
    user = current_user(request) or {}
    identity = user.get("id") or user.get("email")
    if not identity:
        raise HTTPException(
            403,
            "Releasing a position claim is audited and requires an "
            "authenticated operator; this request has no identity to record.",
        )
    return str(identity)


@router.get("")
async def list_unresolved_claims(request: Request) -> dict:
    """What is currently blocking new entries, and why.

    Read-only and safe to poll. `state` is the thing to read: `submitted`
    means intent was recorded and the outcome was never confirmed; `unknown`
    means a submission failed in a way that left the outcome undetermined.
    """
    _ = _operator(request)   # listing what is blocked is operator-only too
    rows = await position_claim.unresolved(limit=200)
    return {
        "count": len(rows),
        "claims": [
            {
                "claim_token": str(row.claim_token),
                "scope": row.scope,
                "underlying": row.underlying,
                "asset_class": row.asset_class,
                "state": row.state,
                "idempotency_key": row.idempotency_key,
                "dispatch_id": row.dispatch_id,
                "unresolved_reason": row.unresolved_reason,
                "claimed_at": row.claimed_at.isoformat() if row.claimed_at else None,
                "submitted_at": (
                    row.submitted_at.isoformat() if row.submitted_at else None
                ),
                # Said plainly, because it is the decision the operator is
                # about to make: releasing this re-opens the position to a new
                # entry, so it must only be done once the order's fate is known.
                "releasing_allows_a_new_entry": True,
            }
            for row in rows
        ],
    }


@router.post("/{claim_token}/release")
async def release_claim(
    claim_token: str, body: ReleaseRequest, request: Request
) -> dict:
    """Force-release one claim, recording who did it and why.

    404 for a token that names no claim, and for a malformed one — the same
    answer either way, so this cannot be used to probe which tokens exist.
    """
    operator = _operator(request)

    try:
        token = uuid.UUID(claim_token)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, "No such claim.")

    released = await position_claim.force_release(
        token, operator=operator, reason=body.reason,
    )
    if not released:
        raise HTTPException(404, "No such claim.")

    logger.warning(
        "Operator %s released position claim %s: %s",
        operator, token, body.reason,
    )
    return {"released": True, "claim_token": str(token), "operator": operator}
