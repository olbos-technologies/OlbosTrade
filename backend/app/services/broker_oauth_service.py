"""Alpaca paper/read-only OAuth. No trading scopes or execution credential access.

State is persisted as a hash, bound to a user, organization and login session,
expires after ten minutes and is atomically consumed BEFORE exchanging a code.
Only the server exchanges and encrypts tokens. Broker responses never escape.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.models.broker_oauth import BrokerOAuthAttempt, BrokerOAuthGrant
from app.services import credential_cipher

AUTHORIZE_URL = "https://app.alpaca.markets/oauth/authorize"
TOKEN_URL = "https://api.alpaca.markets/oauth/token"
PAPER_ACCOUNT_URL = "https://paper-api.alpaca.markets/v2/account"
CALLBACK_PATH = "/api/brokers/oauth/alpaca/callback"


class OAuthUnavailable(Exception):
    pass


class OAuthRejected(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configured():
    try:
        uri = urlsplit(settings.alpaca_oauth_redirect_uri)
    except ValueError:
        return False
    return bool(settings.alpaca_oauth_client_id and settings.alpaca_oauth_client_secret
                and credential_cipher.is_configured() and uri.scheme == "https"
                and uri.hostname and not uri.username and not uri.password
                and uri.path == CALLBACK_PATH and not uri.query and not uri.fragment)


def providers():
    return [
        {"id": "alpaca", "name": "Alpaca", "available": configured(),
         "environment": "paper", "permission": "read_only",
         "note": "Sign in at Alpaca. Paper account access only; automated trading is not enabled."},
        {"id": "ibkr", "name": "Interactive Brokers", "available": False,
         "note": "Coming soon — third-party broker integration and approval required."},
        {"id": "schwab", "name": "Charles Schwab (thinkorswim)", "available": False,
         "note": "Coming soon — commercial broker integration and approval required."},
    ]


def require_configured():
    if not configured():
        raise OAuthUnavailable("Broker sign-in is not configured on this deployment.")


async def begin(db, organization_id, user_id, session_id):
    require_configured()
    now = datetime.now(timezone.utc)
    # Bounded lifetime; clean expired attempts as new attempts are created.
    await db.execute(delete(BrokerOAuthAttempt).where(BrokerOAuthAttempt.expires_at <= now))
    state = secrets.token_urlsafe(32)
    db.add(BrokerOAuthAttempt(state_hash=digest(state),
        organization_id=uuid.UUID(str(organization_id)), user_id=uuid.UUID(str(user_id)),
        session_hash=digest(session_id), expires_at=now + timedelta(minutes=10)))
    await db.commit()
    return AUTHORIZE_URL + "?" + urlencode({"response_type": "code",
        "client_id": settings.alpaca_oauth_client_id,
        "redirect_uri": settings.alpaca_oauth_redirect_uri, "state": state,
        "env": "paper"})  # Omit scope: Alpaca defaults to read-only.


async def consume(db, state, organization_id, user_id, session_id):
    if not isinstance(state, str) or not 32 <= len(state) <= 128:
        raise OAuthRejected("The broker sign-in expired or could not be verified. Start again.")
    result = await db.execute(delete(BrokerOAuthAttempt).where(
        BrokerOAuthAttempt.state_hash == digest(state),
        BrokerOAuthAttempt.organization_id == uuid.UUID(str(organization_id)),
        BrokerOAuthAttempt.user_id == uuid.UUID(str(user_id)),
        BrokerOAuthAttempt.session_hash == digest(session_id),
        BrokerOAuthAttempt.expires_at > datetime.now(timezone.utc),
    ).returning(BrokerOAuthAttempt.state_hash))
    consumed = result.scalar_one_or_none()
    await db.commit()  # Consume even if the subsequent broker request fails.
    if consumed is None:
        raise OAuthRejected("The broker sign-in expired or could not be verified. Start again.")


async def exchange(code):
    require_configured()
    if not isinstance(code, str) or not 1 <= len(code) <= 2048:
        raise OAuthRejected("The broker sign-in could not be completed. Start again.")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(TOKEN_URL, data={
                "grant_type": "authorization_code", "code": code,
                "client_id": settings.alpaca_oauth_client_id,
                "client_secret": settings.alpaca_oauth_client_secret,
                "redirect_uri": settings.alpaca_oauth_redirect_uri})
            response.raise_for_status()
            data = response.json()
        token = data.get("access_token")
        # A wider grant is rejected, never silently accepted as read-only.
        if (data.get("token_type", "").lower() != "bearer"
                or data.get("scope", "") != ""
                or not isinstance(token, str) or not 1 <= len(token) <= 8192
                or any(c.isspace() or ord(c) < 32 for c in token)):
            raise ValueError()
        return token
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise OAuthRejected("Alpaca did not provide a valid read-only connection. Start again.") from None


async def account_identity(token):
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(PAPER_ACCOUNT_URL,
                headers={"Authorization": "Bearer " + token})
            response.raise_for_status()
            data = response.json()
        account_id = str(uuid.UUID(data["id"]))
        if data.get("status") != "ACTIVE":
            raise ValueError()
        return account_id
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise OAuthRejected("Could not verify an active Alpaca paper account. Start again.") from None


async def save(db, organization_id, token, account_id):
    # Upsert also serializes concurrent reauthorizations of the same account.
    stmt = insert(BrokerOAuthGrant).values(id=uuid.uuid4(),
        organization_id=uuid.UUID(str(organization_id)), broker="alpaca", environment="paper",
        account_id=account_id, access_token_enc=credential_cipher.encrypt(token), status="active")
    await db.execute(stmt.on_conflict_do_update(constraint="uq_broker_oauth_account",
        set_={"access_token_enc": stmt.excluded.access_token_enc, "status": "active",
              "connected_at": datetime.now(timezone.utc)}))
    await db.commit()


async def list_grants(db, organization_id):
    rows = (await db.execute(select(BrokerOAuthGrant).where(
        BrokerOAuthGrant.organization_id == uuid.UUID(str(organization_id)),
        BrokerOAuthGrant.status == "active").order_by(BrokerOAuthGrant.connected_at.desc()))).scalars().all()
    return [{"id": str(r.id), "broker": r.broker, "environment": r.environment,
             "account_last4": r.account_id[-4:], "permission": "read_only",
             "connected_at": r.connected_at.isoformat(), "execution_enabled": False}
            for r in rows]


async def disconnect(db, organization_id, grant_id):
    try:
        gid = uuid.UUID(grant_id)
    except ValueError:
        return False
    row = (await db.execute(select(BrokerOAuthGrant).where(
        BrokerOAuthGrant.id == gid,
        BrokerOAuthGrant.organization_id == uuid.UUID(str(organization_id)),
        BrokerOAuthGrant.status == "active").with_for_update())).scalar_one_or_none()
    if row is None:
        return False
    row.access_token_enc = None
    row.status = "revoked"
    await db.commit()
    return True
