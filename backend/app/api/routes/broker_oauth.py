"""Authenticated broker-hosted sign-in, currently paper/read-only only."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from app.api.auth_deps import current_user
from app.api.rate_limit import rate_limit
from app.api.routes.access_requests import require_auth_enabled
from app.api.tier_deps import require_broker_access
from app.core.database import get_async_session
from app.services import broker_oauth_service as svc, credential_cipher
from app.services.organization_service import personal_org_for

router = APIRouter(prefix="/api/brokers/oauth", tags=["Broker Sign-in"],
    dependencies=[Depends(require_auth_enabled), Depends(require_broker_access)])
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
RETURN_PATH = "/terminal/account/brokers"


async def identity(request, db):
    user = current_user(request)
    session_id = user.get("session_id")
    if not session_id:
        raise HTTPException(401, "Sign in again to connect a broker.")
    org = await personal_org_for(db, user["id"])
    return org.id, user["id"], session_id


@router.get("/providers")
async def providers():
    return {"providers": svc.providers(), "execution_enabled": False}


@router.get("/connections")
async def connections(request: Request, db=Depends(get_async_session)):
    org, _, _ = await identity(request, db)
    return {"connections": await svc.list_grants(db, org), "execution_enabled": False}


@router.post("/{provider}/start", dependencies=[Depends(rate_limit)])
async def start(provider: str, request: Request, db=Depends(get_async_session)):
    if provider != "alpaca":
        raise HTTPException(400, "This broker is not available for sign-in yet.")
    org, user, session = await identity(request, db)
    try:
        url = await svc.begin(db, org, user, session)
    except svc.OAuthUnavailable:
        raise HTTPException(503, "Broker sign-in is not configured on this deployment.") from None
    return {"authorization_url": url, "environment": "paper", "permission": "read_only"}


@router.get("/alpaca/callback")
async def callback(request: Request, db=Depends(get_async_session)):
    # Read raw query manually: validation errors must never echo authorization codes.
    org, user, session = await identity(request, db)
    query = request.scope.get("broker_oauth_query", request.query_params)
    try:
        if any(len(query.getlist(key)) > 1 for key in ("state", "code", "error")):
            raise svc.OAuthRejected()
        await svc.consume(db, query.get("state"), org, user, session)
        if query.get("error") is not None:
            raise svc.OAuthRejected()
        token = await svc.exchange(query.get("code"))
        account = await svc.account_identity(token)
        await svc.save(db, org, token, account)
    except (svc.OAuthRejected, svc.OAuthUnavailable,
            credential_cipher.CredentialCipherUnavailable):
        return RedirectResponse(RETURN_PATH + "?broker_oauth=failed", status_code=303, headers=HEADERS)
    return RedirectResponse(RETURN_PATH + "?broker_oauth=connected", status_code=303, headers=HEADERS)


@router.delete("/connections/{grant_id}")
async def disconnect(grant_id: str, request: Request, db=Depends(get_async_session)):
    org, _, _ = await identity(request, db)
    if not await svc.disconnect(db, org, grant_id):
        raise HTTPException(404, "No such connection")
    return {"ok": True, "detail": "Disconnected from OlbosTrade. You can also revoke access in Alpaca's app settings."}


@router.post("/connections/{grant_id}/verify", dependencies=[Depends(rate_limit)])
async def verify_connection(grant_id: str, request: Request, db=Depends(get_async_session)):
    from app.services import broker_connection_health as health
    org, _, _ = await identity(request, db)
    try:
        result = await health.verify(db, org, grant_id)
    except health.ConnectionMissing:
        raise HTTPException(404, "Connection unavailable. Reconnect this account.") from None
    except health.ConnectionChanged:
        raise HTTPException(409, "Connection changed during verification. Refresh and try again.") from None
    from fastapi.responses import JSONResponse
    return JSONResponse(result, headers=HEADERS)


@router.post("/connections/{grant_id}/snapshot", dependencies=[Depends(rate_limit)])
async def account_snapshot(grant_id: str, request: Request, db=Depends(get_async_session)):
    from app.services import broker_paper_snapshot as snapshots, broker_connection_health as health
    from fastapi.responses import JSONResponse
    org, _, _ = await identity(request, db)
    try:
        result = await snapshots.snapshot(db, org, grant_id)
    except health.ConnectionMissing:
        raise HTTPException(404, "Connection unavailable. Reconnect this account.") from None
    except health.ConnectionChanged:
        raise HTTPException(409, "Connection changed during retrieval. Refresh and try again.") from None
    return JSONResponse(result, headers=HEADERS)
