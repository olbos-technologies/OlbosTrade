"""
Connecting a user's own broker account.

WHAT THIS CHANGES AND WHAT IT DOES NOT. It gives each user a place to store
their own Alpaca key pair. It does NOT yet route order execution through those
keys — get_broker() is still a process-wide singleton reading the operator's
settings, called from 51 sites across 18 files, and rerouting it is a change
of a different size and a different risk profile. Splitting them is
deliberate: this PR can be reviewed for "are credentials stored safely",
which is a question with a clear answer, without also having to answer "does
every order still go to the right account".

So a connection made here is inert until that second change lands. The
response says so rather than implying an order will use it.

ELITE ONLY, via require_broker_access — the same gate the rest of the
broker-connected surface uses, so the plan boundary is defined in one place
(tier_deps) rather than restated here.

DEPENDENCY ORDER IS LOAD-BEARING. require_auth_enabled comes first in the
router's list. FastAPI resolves router dependencies before the handler body,
and #71 shipped this the other way round: a tier check answering 403 and a
rate limiter answering 429 on an install where the routes were supposed not
to exist at all. First in the list, or the 404 loses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth_deps import current_user
from app.api.rate_limit import rate_limit
from app.api.routes.access_requests import require_auth_enabled
from app.api.tier_deps import require_broker_access
from app.services import broker_connection_service as svc
from app.services import credential_cipher
from app.services.broker_connection_service import (
    BrokerConnectionError, VerificationRejected, VerificationUnavailable,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/brokers",
    tags=["Broker Connections"],
    dependencies=[Depends(require_auth_enabled), Depends(require_broker_access)],
)


class ConnectIn(BaseModel):
    broker: str = Field(default="alpaca", max_length=20)
    environment: str = Field(default="paper", max_length=10)
    # The bounds match the service's. Pydantic rejects the absurd before any
    # of it reaches encryption; the service re-checks because it is also
    # callable from outside a request.
    api_key: str = Field(..., max_length=svc.MAX_CREDENTIAL_LEN)
    secret_key: str = Field(..., max_length=svc.MAX_CREDENTIAL_LEN)
    label: str = Field(default="", max_length=svc.MAX_LABEL_LEN)


def _require_cipher() -> None:
    """503, not 500, when the deployment cannot encrypt.

    A missing BROKER_ENCRYPTION_KEY is an operator problem the user cannot
    act on, and the message says so without naming the variable to an
    anonymous-ish caller — the log carries the detail.
    """
    if credential_cipher.is_configured():
        return
    logger.error("BROKER_ENCRYPTION_KEY is not set — refusing to store credentials")
    raise HTTPException(
        status_code=503,
        detail="Broker connections are not available on this deployment yet.",
    )


@router.get("/connections")
async def list_connections(request: Request) -> dict:
    """Every connection this user has made. Never decrypts anything."""
    from app.core.database import AsyncSessionLocal

    user = current_user(request)
    async with AsyncSessionLocal() as db:
        connections = await svc.list_for_user(db, user["id"])
    return {
        "connections": connections,
        # Told plainly rather than discovered later. A user who connects a
        # broker and then watches orders go somewhere else has been misled by
        # this screen.
        "execution_routing_enabled": False,
        "note": (
            "Stored securely. Order execution still uses the platform account "
            "until per-user routing is enabled."
        ),
    }


@router.post("/connections")
async def create_connection(
    body: ConnectIn,
    request: Request,
    _rl: None = Depends(rate_limit),
) -> dict:
    """Verify a key pair with the broker, then store it encrypted.

    VERIFY FIRST. A credential that Alpaca rejects never reaches the database:
    storing it would give the user a connection that looks connected and fails
    at the first order, which is the worst moment to find out.

    A broker that cannot be REACHED is not a rejection — see the service
    docstring. The credential is stored unverified and the response says which
    of the two happened.
    """
    from app.core.database import AsyncSessionLocal

    _require_cipher()
    user = current_user(request)

    try:
        broker = svc.normalize_broker(body.broker)
        environment = svc.normalize_environment(body.environment)
    except BrokerConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    api_key = (body.api_key or "").strip()
    secret_key = (body.secret_key or "").strip()
    if not api_key or not secret_key:
        raise HTTPException(
            status_code=400, detail="Both the API key and the secret are required."
        )

    verified_at = None
    verification: dict | None = None
    unverified_reason = None
    try:
        verification = await svc.verify_alpaca(api_key, secret_key, environment)
        from datetime import datetime, timezone
        verified_at = datetime.now(timezone.utc)
    except VerificationRejected as exc:
        # The one case that blocks the write. 400 because the user can fix it.
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except VerificationUnavailable as exc:
        # Stored anyway, flagged unverified. Logged with the failure class
        # only — never the response body, which can echo request headers.
        logger.warning("Could not verify broker credentials (%s) — storing unverified", exc)
        unverified_reason = "Could not reach the broker to verify these keys."

    try:
        async with AsyncSessionLocal() as db:
            conn = await svc.connect(
                db, user["id"],
                broker=broker, environment=environment,
                api_key=api_key, secret_key=secret_key,
                label=body.label, verified_at=verified_at,
            )
            payload = svc.serialize(conn)
    except BrokerConnectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except credential_cipher.CredentialCipherUnavailable:
        # Raced with the check above, or the key is present but unusable.
        logger.error("Encryption unavailable while storing a broker connection")
        raise HTTPException(
            status_code=503,
            detail="Broker connections are not available on this deployment yet.",
        ) from None

    return {
        "ok": True,
        "connection": payload,
        "verification": verification,
        "unverified_reason": unverified_reason,
        "execution_routing_enabled": False,
    }


@router.post("/connections/{connection_id}/verify")
async def verify_connection(
    connection_id: str,
    request: Request,
    _rl: None = Depends(rate_limit),
) -> dict:
    """Re-check a stored credential against the broker.

    Decrypts, which is why it is rate-limited and why it answers 404 for a
    connection belonging to anyone else — scoped through credentials_for,
    which filters on user_id, rather than by looking the id up directly.
    """
    from app.core.database import AsyncSessionLocal

    _require_cipher()
    user = current_user(request)

    async with AsyncSessionLocal() as db:
        connections = await svc.list_for_user(db, user["id"])
        match = next(
            (c for c in connections
             if c["id"] == connection_id and c["status"] == "active"),
            None,
        )
        if match is None:
            # Same answer for "does not exist" and "is not yours".
            raise HTTPException(status_code=404, detail="No such connection")

        found = await svc.credentials_for(
            db, user["id"],
            broker=match["broker"], environment=match["environment"],
        )
        if found is None:
            raise HTTPException(status_code=404, detail="No such connection")
        api_key, secret_key, _base, row = found

        try:
            verification = await svc.verify_alpaca(
                api_key, secret_key, row.environment
            )
        except VerificationRejected as exc:
            return {"ok": False, "verified": False, "detail": str(exc)}
        except VerificationUnavailable as exc:
            logger.warning("Broker verification unavailable (%s)", exc)
            raise HTTPException(
                status_code=503,
                detail="Could not reach the broker. Try again shortly.",
            ) from None

        await svc.touch_verified(db, row.id)

    return {"ok": True, "verified": True, "verification": verification}


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str, request: Request) -> dict:
    """Disconnect. Revokes the row and destroys the stored secrets."""
    from app.core.database import AsyncSessionLocal

    user = current_user(request)
    async with AsyncSessionLocal() as db:
        removed = await svc.revoke(db, user["id"], connection_id)
    if not removed:
        raise HTTPException(status_code=404, detail="No such connection")
    return {"ok": True, "detail": "Disconnected. The stored keys were deleted."}
