"""
Reversible encryption for stored broker credentials.

WHY THIS IS NOT auth_service. Passwords there are Argon2id — one-way, by
design, because nothing ever needs the original back. A broker API key is the
opposite: it has to be decryptable, because it is sent to Alpaca on every
request. Different problem, different primitive, deliberately a different
module so nobody reaches for the wrong one.

WHAT IS STORED. Fernet (AES-128-CBC with an HMAC-SHA256 authentication tag,
versioned and timestamped). Authenticated encryption matters here: an attacker
with write access to the database could otherwise flip bits in a ciphertext
and steer a decrypted key somewhere useful. Fernet rejects a tampered token
instead of returning plausible garbage.

THE KEY IS ITS OWN SETTING, NOT DERIVED FROM SECRET_KEY, and that is the most
consequential decision in this file. SECRET_KEY is the operator API key. It
gets rotated — routinely, and urgently after any suspected exposure. If broker
credentials were encrypted under a key derived from it, that rotation would
silently make every stored credential undecryptable, and the first symptom
would be orders failing during market hours. Incident response must never
destroy data as a side effect.

NOTHING HERE LOGS. Not the key, not the plaintext, not a prefix of either, and
not on the error paths — an exception message carrying "failed to decrypt
<token>" is the same leak wearing a different hat.
"""

from __future__ import annotations

from app.core.config import settings


class CredentialCipherUnavailable(RuntimeError):
    """No usable encryption key is configured.

    Raised rather than falling back to storing plaintext. A deployment that
    cannot encrypt broker credentials must refuse to hold them.
    """


class CredentialDecryptionError(RuntimeError):
    """A stored credential could not be decrypted.

    Means the key changed, or the ciphertext was altered. Deliberately carries
    no detail about which: the difference is not actionable to a caller and
    the detail is exactly what an attacker probing the store wants.
    """


def is_configured() -> bool:
    """Whether credentials can be stored at all on this deployment."""
    return bool(getattr(settings, "broker_encryption_key", ""))


def _fernet():
    from cryptography.fernet import Fernet

    key = getattr(settings, "broker_encryption_key", "")
    if not key:
        raise CredentialCipherUnavailable(
            "BROKER_ENCRYPTION_KEY is not set — broker credentials cannot be "
            "stored. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception as exc:  # noqa: BLE001 - never echo the key
        raise CredentialCipherUnavailable(
            "BROKER_ENCRYPTION_KEY is not a valid Fernet key (it must be 32 "
            f"url-safe base64-encoded bytes): {type(exc).__name__}"
        ) from None


def encrypt(plaintext: str) -> str:
    """Encrypt one credential. Returns a Fernet token, safe to store."""
    if not plaintext:
        raise ValueError("refusing to encrypt an empty credential")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt one stored credential.

    `from None` on the raise, so the traceback cannot carry the token or any
    library detail about why it failed.
    """
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise CredentialDecryptionError(
            "a stored broker credential could not be decrypted — the "
            "encryption key has changed, or the stored value was altered"
        ) from None
    except CredentialCipherUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CredentialDecryptionError(
            f"a stored broker credential could not be read ({type(exc).__name__})"
        ) from None


def last4(plaintext: str) -> str:
    """The tail of a key, for showing which credential is stored.

    Four characters is enough to tell two keys apart in a list and useless for
    reconstructing either. Stored alongside the ciphertext so the list view
    never needs to decrypt anything — a read-only screen should not be able to
    recover a live trading credential.
    """
    return (plaintext or "")[-4:]
