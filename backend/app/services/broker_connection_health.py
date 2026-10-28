"""Point-in-time paper account verification. Never grants order authority.

A result is written only if the exact credential, identity and previous health
observation still match. Revocation, reauthorization or a faster verification
wins over an in-flight response. No database lock is held during broker I/O.
"""
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update

from app.models.broker_oauth import BrokerOAuthGrant
from app.services import credential_cipher
from app.services.broker_oauth_service import PAPER_ACCOUNT_URL


class ConnectionMissing(Exception):
    pass


class ConnectionChanged(Exception):
    pass


async def probe(token, expected_account_id):
    """Return only a fixed health label; never return raw broker data/errors."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(PAPER_ACCOUNT_URL,
                headers={"Authorization": "Bearer " + token})
            if response.status_code in (401, 403):
                return "reconnect_required"
            response.raise_for_status()
            data = response.json()
        account_id = str(uuid.UUID(data["id"]))
        if account_id != expected_account_id:
            return "identity_mismatch"
        # A successful account read is not proof it is unrestricted.
        flags = [data[k] for k in ("account_blocked", "trading_blocked", "trade_suspended_by_user")]
        if any(type(flag) is not bool for flag in flags):
            return "unavailable"
        if data.get("status") != "ACTIVE" or any(flags):
            return "restricted"
        return "verified"
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return "unavailable"


async def verify(db, organization_id, grant_id):
    try:
        gid = uuid.UUID(str(grant_id))
    except ValueError:
        raise ConnectionMissing() from None
    oid = uuid.UUID(str(organization_id))
    row = (await db.execute(select(BrokerOAuthGrant).where(
        BrokerOAuthGrant.id == gid, BrokerOAuthGrant.organization_id == oid,
        BrokerOAuthGrant.status == "active", BrokerOAuthGrant.broker == "alpaca",
        BrokerOAuthGrant.environment == "paper"))).scalar_one_or_none()
    if row is None or not row.access_token_enc:
        raise ConnectionMissing()

    # Capture primitives, not a mutable ORM row that another operation can refresh.
    ciphertext, account_id, previous_check = row.access_token_enc, row.account_id, row.last_checked_at
    try:
        token = credential_cipher.decrypt(ciphertext)
    except (credential_cipher.CredentialCipherUnavailable, credential_cipher.CredentialDecryptionError):
        health = "unavailable"
    else:
        health = await probe(token, account_id)
    checked_at = datetime.now(timezone.utc)
    changes = {"health": health, "last_checked_at": checked_at}
    if health in ("reconnect_required", "identity_mismatch"):
        changes.update(status="reconnect_required", access_token_enc=None)

    result = await db.execute(update(BrokerOAuthGrant).where(
        BrokerOAuthGrant.id == gid, BrokerOAuthGrant.organization_id == oid,
        BrokerOAuthGrant.status == "active", BrokerOAuthGrant.broker == "alpaca",
        BrokerOAuthGrant.environment == "paper", BrokerOAuthGrant.account_id == account_id,
        BrokerOAuthGrant.access_token_enc == ciphertext,
        BrokerOAuthGrant.last_checked_at == previous_check,
    ).values(**changes).returning(BrokerOAuthGrant.id))
    written = result.scalar_one_or_none()
    await db.commit()
    if written is None:
        raise ConnectionChanged()
    return {"id": str(gid), "health": health, "last_checked_at": checked_at.isoformat(),
            "status": changes.get("status", "active"), "execution_enabled": False,
            "positions_reconciled": False}
