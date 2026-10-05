"""
Storing, verifying and retrieving a user's own broker credentials.

The routes above this are thin on purpose: everything that decides whether a
credential may be written, read back or handed to a broker client lives here,
so there is one place to audit rather than one per endpoint.

WHAT NEVER LEAVES THIS MODULE. `serialize()` is the only shape the API layer
is given, and it contains no ciphertext and no plaintext — only `key_last4`,
which is four characters and useless on its own. `credentials_for()` is the
single function that decrypts, it is not called by any listing route, and it
returns a tuple rather than a dict so it cannot be mistaken for something
serialisable and returned by accident.

VERIFICATION IS NOT ALL-OR-NOTHING, and the distinction is deliberate. Alpaca
answering 401 means the key is wrong, and storing it would leave the user with
a connection that looks fine and fails at the first order. Alpaca being
unreachable means nothing about the key: refusing then would make connecting
impossible during an Alpaca outage, which is the moment a user is most likely
to be re-checking their setup. So: a definite rejection blocks the write, a
network failure stores the credential with last_verified_at left NULL, and the
UI shows it as unverified until a later check succeeds.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.broker_connection import (
    BROKER_ALPACA, BROKERS, ENV_LIVE, ENV_PAPER, ENVIRONMENTS,
    STATUS_ACTIVE, STATUS_REVOKED, BrokerConnection,
)
from app.services import credential_cipher
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: Alpaca's two trading hosts. Hardcoded rather than read from
#: settings.alpaca_base_url because that setting is the OPERATOR's account,
#: and a per-user connection must not inherit it: an operator pointed at live
#: would otherwise silently send every user's paper credentials to the live
#: host, where they do not authenticate — or worse, would not notice if they
#: did.
ALPACA_HOSTS = {
    ENV_PAPER: "https://paper-api.alpaca.markets",
    ENV_LIVE: "https://api.alpaca.markets",
}

VERIFY_TIMEOUT_SECONDS = 10.0

#: Alpaca keys are ~20 and ~40 characters. The bounds here are loose on
#: purpose — a hard length check would break the day Alpaca changes format,
#: and the real validation is asking Alpaca itself.
MAX_CREDENTIAL_LEN = 256
MAX_LABEL_LEN = 60


class BrokerConnectionError(Exception):
    """A connection attempt that the user can fix by changing their input."""


class VerificationRejected(BrokerConnectionError):
    """The broker positively rejected these credentials."""


class VerificationUnavailable(Exception):
    """The broker could not be reached. Says nothing about the credentials."""


def normalize_broker(value: str) -> str:
    broker = (value or "").strip().lower()
    if broker not in BROKERS:
        raise BrokerConnectionError(
            f"Unknown broker. Supported: {', '.join(BROKERS)}."
        )
    if broker != BROKER_ALPACA:
        # Honest refusal rather than a row that can never be used. See the
        # model docstring: IBKR needs a gateway container per account, which
        # no amount of application code here provides.
        raise BrokerConnectionError(
            "Only Alpaca can be connected with API keys today. IBKR requires a "
            "dedicated gateway session per account and is not yet available "
            "for self-service connection."
        )
    return broker


def normalize_environment(value: str) -> str:
    env = (value or "").strip().lower()
    if env not in ENVIRONMENTS:
        raise BrokerConnectionError(
            f"Environment must be one of: {', '.join(ENVIRONMENTS)}."
        )
    return env


def serialize(conn: BrokerConnection) -> dict:
    """The only representation the API layer ever sees.

    Note what is absent: api_key_enc and secret_key_enc. Not redacted —
    ABSENT. A redaction is a line of code someone can delete; a field that was
    never read cannot be leaked by editing a format string.
    """
    return {
        "id": str(conn.id),
        "broker": conn.broker,
        "environment": conn.environment,
        "label": conn.label or "",
        "key_last4": conn.key_last4 or "",
        "status": conn.status,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
        "last_verified_at": (
            conn.last_verified_at.isoformat() if conn.last_verified_at else None
        ),
        "verified": conn.last_verified_at is not None,
    }


async def verify_alpaca(api_key: str, secret_key: str, environment: str) -> dict:
    """Ask Alpaca whether this key pair works, against the right host.

    Returns the account summary on success. Raises VerificationRejected for a
    definite no and VerificationUnavailable for anything else — the caller
    treats those very differently and must not have to guess from a status
    code.
    """
    base = ALPACA_HOSTS[environment]
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    try:
        async with httpx.AsyncClient(timeout=VERIFY_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{base}/v2/account", headers=headers)
    except Exception as exc:  # noqa: BLE001 - any transport problem
        # type(exc).__name__ only. The exception text of an httpx failure can
        # contain the request URL, and these requests carry credentials in
        # headers that some proxies echo back in error bodies.
        raise VerificationUnavailable(type(exc).__name__) from None

    if resp.status_code in (401, 403):
        raise VerificationRejected(
            "Alpaca rejected these credentials. Check the key and secret, and "
            "that they belong to the environment you selected."
        )
    if resp.status_code >= 400:
        raise VerificationUnavailable(f"HTTP {resp.status_code}")

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        raise VerificationUnavailable("unreadable response") from None

    # Only the fields we will show. Alpaca's account payload includes balances
    # and an account number; copying it wholesale into our response would put
    # that in places it was never meant to go.
    return {
        "account_status": data.get("status"),
        "currency": data.get("currency"),
        "pattern_day_trader": data.get("pattern_day_trader"),
    }


async def list_for_org(db, organization_id) -> list[dict]:
    """Every connection an organization owns, active and revoked, newest first.

    Revoked rows are included because "I disconnected this yesterday" is a
    question the screen should answer. They carry no secrets — revoke clears
    the ciphertext.
    """
    rows = (await db.execute(
        select(BrokerConnection)
        .where(BrokerConnection.organization_id == _as_uuid(organization_id))
        .order_by(BrokerConnection.created_at.desc())
    )).scalars().all()
    return [serialize(r) for r in rows]


async def connect(db, organization_id, *, broker: str, environment: str,
                  api_key: str, secret_key: str, label: str = "",
                  created_by_user_id=None,
                  verified_at: Optional[datetime] = None) -> BrokerConnection:
    """Store one credential pair, replacing any active one for that slot.

    REPLACING, not rejecting. Rotating an Alpaca key is routine, and a user
    whose key changed should be able to paste the new one rather than having
    to find a disconnect button first. The old row is revoked in the same
    transaction, which is also what keeps the partial unique index satisfied:
    it permits exactly one active row per (org, broker, environment), so the
    revoke has to land before the insert or the insert violates it.

    `verified_at` is passed in rather than computed here because verification
    happens BEFORE this call — a rejected credential must never reach the
    database, not even briefly.
    """
    if not credential_cipher.is_configured():
        # Not a user error, so not a BrokerConnectionError. The route turns
        # this into a 503: the deployment is missing a key, and no amount of
        # retrying or re-typing on the user's part will help.
        raise credential_cipher.CredentialCipherUnavailable(
            "BROKER_ENCRYPTION_KEY is not configured on this deployment"
        )

    broker = normalize_broker(broker)
    environment = normalize_environment(environment)
    api_key = (api_key or "").strip()
    secret_key = (secret_key or "").strip()
    if not api_key or not secret_key:
        raise BrokerConnectionError("Both the API key and the secret are required.")
    if len(api_key) > MAX_CREDENTIAL_LEN or len(secret_key) > MAX_CREDENTIAL_LEN:
        raise BrokerConnectionError("That does not look like an Alpaca key pair.")

    oid = _as_uuid(organization_id)
    actor = _as_uuid(created_by_user_id) if created_by_user_id else None

    # Two attempts, because the lock below cannot cover the FIRST connection
    # for a slot. SELECT ... FOR UPDATE locks the rows it finds, and on a
    # first connect it finds none — so two concurrent requests both see "no
    # active row", both insert, and the partial unique index turns one of them
    # into an unhandled IntegrityError. A 500, where the documented behaviour
    # is a replacement. Raised in review on #78; the comment on the lock used
    # to claim it covered this case and it never did.
    #
    # The retry is enough rather than a stopgap: whichever request loses the
    # race re-reads AFTER the winner has committed, so the second pass finds a
    # real row, locks it, revokes it and inserts — exactly the path a
    # sequential reconnect takes. A second conflict would mean a third
    # concurrent writer for one organization's single slot, which is not a case
    # worth spinning on.
    for attempt in (1, 2):
        try:
            return await _connect_once(
                db, oid, broker=broker, environment=environment,
                api_key=api_key, secret_key=secret_key, label=label,
                created_by_user_id=actor, verified_at=verified_at,
            )
        except IntegrityError:
            await db.rollback()
            if attempt == 2:
                raise
            logger.info(
                "Broker connect raced another request for org=%s %s/%s — retrying",
                oid, broker, environment,
            )


async def _connect_once(db, oid, *, broker: str, environment: str,
                        api_key: str, secret_key: str, label: str,
                        created_by_user_id, verified_at: Optional[datetime]) -> BrokerConnection:
    """One attempt at the revoke-then-insert. See connect() for the retry."""
    now = datetime.now(timezone.utc)

    existing = (await db.execute(
        select(BrokerConnection)
        .where(
            BrokerConnection.organization_id == oid,
            BrokerConnection.broker == broker,
            BrokerConnection.environment == environment,
            BrokerConnection.status == STATUS_ACTIVE,
        )
        # Locks an EXISTING active row so two reconnects cannot both revoke
        # it and both insert. It does not — cannot — cover the first connect
        # for a slot, where there is no row to lock; connect()'s retry handles
        # that case.
        .with_for_update()
    )).scalars().all()
    for row in existing:
        _revoke_in_place(row, now)

    # flush, not commit. The revokes must be visible to the index check that
    # the insert triggers, while both stay in one transaction — a commit here
    # would leave a user with no active connection if the insert then failed.
    await db.flush()

    conn = BrokerConnection(
        organization_id=oid,
        created_by_user_id=created_by_user_id,
        broker=broker,
        environment=environment,
        label=(label or "").strip()[:MAX_LABEL_LEN],
        api_key_enc=credential_cipher.encrypt(api_key),
        secret_key_enc=credential_cipher.encrypt(secret_key),
        key_last4=credential_cipher.last4(api_key),
        status=STATUS_ACTIVE,
        last_verified_at=verified_at,
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    # The organization id, the broker and the environment. Never the label
    # (user text) and never any part of the key beyond what is already stored
    # in clear.
    logger.info("Broker connection stored: org=%s broker=%s env=%s",
                oid, broker, environment)
    return conn


def _revoke_in_place(row: BrokerConnection, now: datetime) -> None:
    """Mark one row revoked and destroy its secrets.

    Clearing the ciphertext is the point. A revoked row that kept its
    encrypted key would mean "disconnect" left the credential in the database
    forever — a user who disconnects because a key leaked would be no better
    off than before.
    """
    row.status = STATUS_REVOKED
    row.revoked_at = now
    row.api_key_enc = None
    row.secret_key_enc = None


async def revoke(db, organization_id, connection_id) -> bool:
    """Disconnect one connection. True if something was revoked.

    Scoped by organization_id as well as id, so a guessed or leaked id from
    another account revokes nothing. Returns False rather than raising for a
    missing row: the two cases — never existed, and belongs to someone else —
    must be indistinguishable to the caller.
    """
    try:
        cid = _as_uuid(connection_id)
    except (ValueError, AttributeError, TypeError):
        return False

    row = (await db.execute(
        select(BrokerConnection)
        .where(
            BrokerConnection.id == cid,
            BrokerConnection.organization_id == _as_uuid(organization_id),
            BrokerConnection.status == STATUS_ACTIVE,
        )
        .with_for_update()
    )).scalar_one_or_none()
    if row is None:
        return False

    _revoke_in_place(row, datetime.now(timezone.utc))
    await db.commit()
    logger.info("Broker connection revoked: org=%s id=%s", row.organization_id, cid)
    return True


async def touch_verified(db, connection_id) -> None:
    """Record that a stored credential was just confirmed to work."""
    row = (await db.execute(
        select(BrokerConnection).where(BrokerConnection.id == _as_uuid(connection_id))
    )).scalar_one_or_none()
    if row is None:
        return
    row.last_verified_at = datetime.now(timezone.utc)
    await db.commit()


async def credentials_for(db, organization_id, *, broker: str = BROKER_ALPACA,
                          environment: Optional[str] = None
                          ) -> Optional[tuple[str, str, str, BrokerConnection]]:
    """The decrypted key pair and host for a user's active connection.

    THE ONLY FUNCTION HERE THAT DECRYPTS. Everything a screen needs comes from
    serialize(); this exists for the execution path and nothing else.

    Returns None when the user has not connected that broker, so callers fall
    back to the operator's own credentials rather than failing — which is what
    keeps single-operator installs working exactly as they did before this
    table existed.

    With `environment` unset, prefers LIVE over paper. A user who has
    connected both has gone out of their way to do so, and the live account is
    the one they mean when they place an order; paper is the sandbox they opt
    into per request.
    """
    oid = _as_uuid(organization_id)
    rows = (await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.organization_id == oid,
            BrokerConnection.broker == broker,
            BrokerConnection.status == STATUS_ACTIVE,
        )
    )).scalars().all()
    if not rows:
        return None

    if environment is not None:
        environment = normalize_environment(environment)
        rows = [r for r in rows if r.environment == environment]
        if not rows:
            return None
    else:
        rows.sort(key=lambda r: 0 if r.environment == ENV_LIVE else 1)

    row = rows[0]
    if not row.api_key_enc or not row.secret_key_enc:
        # An active row with no ciphertext should not exist — revoke clears
        # both and sets the status together. Treated as "not connected"
        # rather than crashing an order path over a broken row.
        logger.error("Active broker connection %s has no stored credentials", row.id)
        return None

    api_key = credential_cipher.decrypt(row.api_key_enc)
    secret_key = credential_cipher.decrypt(row.secret_key_enc)
    return api_key, secret_key, ALPACA_HOSTS[row.environment], row


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
