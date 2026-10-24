"""R2-Q internal atomic permit consumption, NOT an entry authorization API.

This helper can transition only a pre-existing ISSUED database row. There is
intentionally no issuance endpoint or wiring into order_submission: no live
entry is possible through this module. Caller must separately prove broker
history completeness, account identity, risk and current kill-switch state.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, update

from app.models.entry_authorization_record import EntryAuthorizationRecord


async def consume_stored_permit(
    *,
    token: str,
    organization_id: uuid.UUID,
    principal_id: uuid.UUID,
    broker_connection_id: uuid.UUID,
    broker_account_id: str,
    broker: str,
    asset_class: str,
    position_identity: str,
    environment: str,
    intent_digest: str,
    claim_token: uuid.UUID,
    risk_revision: str,
    kill_switch_revision: str,
) -> bool:
    """Atomically consume one matching, non-expired row using database time.

    Does NOT permit a broker call: the entry boundary still unconditionally
    rejects, and no code issues a permit. External attestation and fresh
    guard checks remain mandatory before any future integration.
    """
    if (
        not isinstance(token, str) or len(token) < 32
        or not isinstance(organization_id, uuid.UUID)
        or not isinstance(principal_id, uuid.UUID)
        or not isinstance(broker_connection_id, uuid.UUID)
        or not isinstance(claim_token, uuid.UUID)
        or not isinstance(broker_account_id, str) or not broker_account_id.strip()
        or not isinstance(broker, str) or not broker.strip()
        or asset_class not in ("equity", "options")
        or not isinstance(position_identity, str) or not position_identity.strip()
        or environment not in ("paper", "live")
        or not isinstance(intent_digest, str) or len(intent_digest) != 64
        or not isinstance(risk_revision, str) or not risk_revision
        or not isinstance(kill_switch_revision, str) or not kill_switch_revision
    ):
        return False
    # Hash bearer material before comparing; do not log or persist plaintext.
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    from app.core.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                changed = await session.execute(
                    update(EntryAuthorizationRecord)
                    .where(
                        EntryAuthorizationRecord.token_hash == token_hash,
                        EntryAuthorizationRecord.state == "issued",
                        EntryAuthorizationRecord.expires_at > func.now(),
                        EntryAuthorizationRecord.organization_id == organization_id,
                        EntryAuthorizationRecord.principal_id == principal_id,
                        EntryAuthorizationRecord.broker_connection_id == broker_connection_id,
                        EntryAuthorizationRecord.broker_account_id == broker_account_id,
                        EntryAuthorizationRecord.broker == broker,
                        EntryAuthorizationRecord.asset_class == asset_class,
                        EntryAuthorizationRecord.position_identity == position_identity,
                        EntryAuthorizationRecord.environment == environment,
                        EntryAuthorizationRecord.intent_digest == intent_digest,
                        EntryAuthorizationRecord.position_claim_token == claim_token,
                        EntryAuthorizationRecord.risk_revision == risk_revision,
                        EntryAuthorizationRecord.kill_switch_revision == kill_switch_revision,
                    )
                    .values(state="consumed", consumed_at=func.now())
                )
                accepted = changed.rowcount == 1
        return accepted
    except Exception:
        # Database failures, serialization conflicts and unknown outcomes
        # cannot be elevated into authorization.
        return False
