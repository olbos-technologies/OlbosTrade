"""
Authentication: password hashing and opaque session lifecycle.

Pure-ish service layer — the FastAPI wiring lives in app/api/routes/auth.py and
app/api/deps.py. Kept separate so the security-relevant logic is testable
without HTTP.

Token design: 32 bytes from secrets.token_urlsafe, stored as its SHA-256. The
plaintext token exists only in the cookie; the database holds a digest, so a
dump does not hand over live sessions. SHA-256 is correct here precisely
because the token is high-entropy CSPRNG output rather than a guessable
secret — there is nothing to brute-force, and this runs on every request.
Passwords are the opposite case and use Argon2id.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.utils.logger import get_logger

logger = get_logger(__name__)

_hasher = PasswordHasher()

SESSION_COOKIE_NAME = "olbos_session"

#: Password bounds, defined ONCE and imported everywhere.
#:
#: These used to live as a literal in LoginRequest and a copy in
#: scripts/create_user.py, tied together only by a comment saying they must
#: match. The failure that comment describes is real: provision an account
#: with a password longer than the login route accepts and it is valid in the
#: database and impossible to log in with, with no error explaining why. A
#: comment cannot prevent that; an import can.
MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 1024
TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    False on mismatch, and also on a malformed stored hash — a corrupt or
    truncated hash column must fail closed rather than raise into the login
    route, where the traceback would distinguish "bad hash" from "wrong
    password" to whoever is probing.
    """
    try:
        _hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except (InvalidHashError, Exception) as exc:  # noqa: BLE001 - fail closed
        logger.warning("Password verification failed on a malformed hash: %s", exc)
        return False


def new_session_token() -> tuple[str, str]:
    """Return (plaintext_token, token_hash). Only the hash is ever stored."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def is_session_valid(session, now: Optional[datetime] = None) -> bool:
    """
    A session is valid only if it exists, was not revoked, and has not expired.

    Written as an explicit allow rather than a chain of early returns because
    every one of these three has to hold; a missing check here is a session
    that outlives its logout.
    """
    if session is None:
        return False
    now = now or datetime.now(timezone.utc)
    if session.revoked_at is not None:
        return False
    expires = session.expires_at
    if expires is None:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > now


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()
