"""
Session authentication dependency, and the default-deny allowlist.

Default-deny is the whole point of Phase 2. There are 144 route decorators in
this app and 15 of them carried the operator-key dependency; adding a
per-route dependency to the rest guarantees that one gets missed, and the
missed one is found by an incident rather than by a reviewer. So the check is
applied globally and routes opt OUT by path, with a test that enumerates every
registered route and fails CI if a new one is neither protected nor
deliberately allowlisted.

Everything here is inert while settings.auth_enabled is False, which is the
default: an existing single-operator install keeps working on nginx Basic Auth
plus the X-Api-Key operator key exactly as before.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, WebSocketException, status
from starlette.requests import HTTPConnection

from app.core.config import settings
from app.services.auth_service import SESSION_COOKIE_NAME, hash_token, is_session_valid
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Paths reachable without a session. Kept as exact matches and explicit
# prefixes rather than regexes — a permissive pattern here is a hole, and this
# list is the security boundary, so it should be boring to read.
PUBLIC_EXACT = {
    "/api/auth/login",
    "/api/auth/logout",
    # Tells a client whether auth is switched on at all, and whether it holds a
    # session. Must be public: with auth disabled a 401 from /me is ambiguous
    # between "logged out" and "there is nothing to log into", and a frontend
    # that cannot tell them apart shows a login page where login 404s.
    "/api/auth/status",
    # Asking for an account, and redeeming an approved one. Both are public by
    # necessity: the caller has no account yet, which is the entire point.
    #
    # Note what is NOT here. The operator's review queue lives at
    # /api/admin/access-requests, on a different prefix, precisely because this
    # allowlist matches on PATH and knows nothing about METHOD — entries here
    # open every verb on that path. Had the queue been a GET on
    # /api/access-requests, allowlisting the form would have published it.
    "/api/access-requests",
    "/api/access-requests/claim",
    "/api/health",
    "/health",
    "/",
}
# Each entry matches itself or a path segment beneath it — "/static" covers
# "/static" and "/static/app.css", NOT "/staticfiles/secrets". See
# is_public_path: a bare startswith made every entry here an open-ended
# wildcard, which is the opposite of what an allowlist is for.
PUBLIC_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/static",
    "/assets",
)


def is_public_path(path: str) -> bool:
    """
    Exact match, or a path SEGMENT beneath an allowlisted prefix.

    The obvious implementation is `path.startswith(p)`, and it was wrong: it
    made "/staticfiles/secrets", "/docs-internal" and "/openapi.json.bak"
    public, because none of those are beneath the prefix — they merely begin
    with the same characters. An allowlist whose entries silently extend to
    arbitrary sibling paths is not a boundary. Found in review.
    """
    if path in PUBLIC_EXACT:
        return True
    return any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES)


# How stale last_seen_at may get before it is worth a write.
LAST_SEEN_REFRESH_S = 60.0


def _should_touch(last_seen, now: datetime) -> bool:
    if last_seen is None:
        return True
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return (now - last_seen).total_seconds() >= LAST_SEEN_REFRESH_S


class SessionLookupError(Exception):
    """The session store could not be read. Distinct from "no valid session"."""


async def load_session_user(conn: HTTPConnection) -> Optional[dict]:
    """
    Resolve the caller from the session cookie, or None.

    Swallows a lookup failure and returns None, because the caller that matters
    — require_session, on every protected route — must fail CLOSED: an
    unreadable session is not an authenticated one.

    A caller that needs to tell "no session" from "could not check" (the
    /api/auth/status boot probe, where the difference decides between showing a
    login form and showing an outage notice) must use resolve_session_user
    instead and handle SessionLookupError.
    """
    try:
        return await resolve_session_user(conn)
    except SessionLookupError as exc:
        logger.warning("Session lookup failed (treating as unauthenticated): %s", exc)
        return None


async def resolve_session_user(conn: HTTPConnection) -> Optional[dict]:
    """
    Same resolution, but raises SessionLookupError instead of hiding an
    unreadable session store behind a None that reads as "logged out".

    Takes an HTTPConnection — the shared base of Request and WebSocket — because
    this runs on both, and asking for a Request would make it uncallable on a
    WebSocket route. See require_session.

    Returns a plain dict rather than the ORM object so callers cannot
    accidentally lazy-load or mutate a detached instance.
    """
    token = conn.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.user import User, UserSession

    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(UserSession.token_hash == hash_token(token))
                .limit(1)
            )).first()
            if row is None:
                return None
            session, user = row
            if not is_session_valid(session):
                return None
            if not user.is_active:
                # Disabling an account must end its access immediately, not at
                # the next session expiry.
                return None

            resolved = {
                "id": str(user.id),
                "email": user.email,
                "tier": user.tier,
                "session_id": str(session.id),
            }

            # Authentication is decided above. last_seen_at is bookkeeping, and
            # it used to share the outer try — so a failed UPDATE fell into the
            # fail-closed handler and denied a request that had already proved
            # it held a valid session. A write problem must not deny a read.
            #
            # It is also throttled: this ran an UPDATE plus a commit on every
            # authenticated request, polling included. A "last seen" accurate to
            # the minute is worth no more than that.
            now = datetime.now(timezone.utc)
            if _should_touch(session.last_seen_at, now):
                try:
                    session.last_seen_at = now
                    await db.commit()
                except Exception as exc:      # noqa: BLE001 - never blocks auth
                    logger.warning("Could not update last_seen_at: %s", exc)

            return resolved
    except Exception as exc:
        # Raised, not swallowed. load_session_user turns this back into None so
        # protected routes still fail closed; only callers that can act on the
        # difference see it.
        raise SessionLookupError(str(exc)) from exc


async def require_session(conn: HTTPConnection) -> dict:
    """
    Global dependency. Rejects anything without a valid session once
    auth_enabled is on; a no-op otherwise.

    The parameter is an HTTPConnection, NOT a Request, and that is load-bearing.
    As an app-level dependency this is attached to every route including the
    /api/ibkr/live WebSocket, and FastAPI supplies a WebSocket rather than a
    Request in a WebSocket scope. Annotating it Request made the dependency
    uncallable there — a TypeError on every connection attempt, regardless of
    whether auth was enabled, which broke live market data for the frontend
    rather than rejecting anyone. HTTPConnection is the common base of both and
    carries everything used here: cookies, url, state, client.
    """
    if not settings.auth_enabled:
        return {}

    if is_public_path(conn.url.path):
        return {}

    user = await load_session_user(conn)
    if user is None:
        _reject(conn)

    # Downstream handlers read this instead of re-querying.
    conn.state.user = user
    return user


def _reject(conn: HTTPConnection) -> None:
    """
    Refuse the connection in whichever protocol it arrived on.

    An HTTPException raised during a WebSocket handshake is not translated into
    a close frame by Starlette; it surfaces as a server error. The rejection has
    to speak the right protocol or "denied" reads as "broken".
    """
    if conn.scope.get("type") == "websocket":
        # 1008 = policy violation, the conventional close code for auth failure.
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION,
                                 reason="Authentication required")
    raise HTTPException(status_code=401, detail="Authentication required")


def current_user(conn: HTTPConnection) -> dict:
    """Read the user resolved by require_session. {} when auth is disabled."""
    return getattr(conn.state, "user", {}) or {}
