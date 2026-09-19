"""
Login, logout, and "who am I".

There is deliberately no registration route — accounts are provisioned with
scripts/create_user.py. Self-service signup on a platform that connects to
brokers pulls in email verification, bot defence and abuse response, none of
which is worth building before there is a reason to.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.auth_deps import current_user
from app.api.rate_limit import client_ip, login_rate_limit
from app.core.config import settings
from app.services.auth_service import (
    MAX_PASSWORD_LEN, MIN_PASSWORD_LEN, SESSION_COOKIE_NAME, hash_password,
    hash_token, new_session_token, normalize_email, session_expiry,
    verify_password,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=320)
    # The shared bound, not a literal: a cap here that is lower than the one
    # create_user.py enforces makes a provisioned account impossible to log
    # into, with nothing saying why.
    password: str = Field(..., max_length=MAX_PASSWORD_LEN)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., max_length=MAX_PASSWORD_LEN)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LEN,
                              max_length=MAX_PASSWORD_LEN)


class SessionOut(BaseModel):
    id: str
    created_at: str
    last_seen_at: str | None
    user_agent: str | None
    ip: str | None
    current: bool


class UserOut(BaseModel):
    id: str
    email: str
    tier: str


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    _rl: None = Depends(login_rate_limit),
) -> dict:
    """
    Exchange credentials for a session cookie.

    Every failure path returns the same 401 and the same message. Saying
    "no such user" versus "wrong password" hands an attacker a free account
    enumeration oracle, and this app's users are, by construction, people with
    brokerage accounts.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.user import User, UserSession

    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Authentication is not enabled")

    email = normalize_email(body.email)
    generic = HTTPException(status_code=401, detail="Invalid email or password")

    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.email == email).limit(1)
        )).scalar_one_or_none()

        # Verify even when the user is missing, against a throwaway hash, so a
        # nonexistent account does not answer measurably faster than a real one
        # with a wrong password.
        stored = user.password_hash if user else _DUMMY_HASH
        ok = verify_password(body.password, stored)

        if user is None or not ok or not user.is_active:
            logger.warning("Failed login for %s", email or "(blank)")
            raise generic

        token, token_hash = new_session_token()
        db.add(UserSession(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=session_expiry(settings.auth_session_hours),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
            # Same trust rule as the rate limiter — a session audit row saying
            # every login came from the proxy is worse than useless.
            ip=client_ip(request),
        ))
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
        out = UserOut(id=str(user.id), email=user.email, tier=user.tier)

    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.auth_session_hours * 3600,
        httponly=True,                     # not readable by script: XSS cannot lift it
        secure=settings.auth_cookie_secure,
        samesite="lax",                    # survives top-level navigation, blocks cross-site POST
        path="/",
    )
    logger.info("Login: %s", out.email)
    return {"user": out.model_dump()}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    """
    Revoke the session server-side, then clear the cookie.

    Server-side first: clearing only the cookie would leave a token that still
    authenticates anyone who copied it.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.user import UserSession

    token = request.cookies.get(SESSION_COOKIE_NAME)
    revoked = True
    if token:
        try:
            async with AsyncSessionLocal() as db:
                session = (await db.execute(
                    select(UserSession).where(UserSession.token_hash == hash_token(token)).limit(1)
                )).scalar_one_or_none()
                if session is not None and session.revoked_at is None:
                    session.revoked_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception as exc:
            # The cookie still gets cleared below — the person asked to log out
            # and that part needs no database. But the row is still live, so a
            # token someone copied starts working again the moment the database
            # does, and saying {"ok": true} here would be a lie the caller has
            # no way to detect. Report it instead; the session still dies at
            # expires_at, so the exposure is bounded by AUTH_SESSION_HOURS.
            logger.error("Logout could not revoke server-side: %s", exc)
            revoked = False

    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    if not revoked:
        response.status_code = 503
        return {"ok": False, "detail": "Signed out here, but the session could not "
                                       "be revoked server-side. Retry to be sure."}
    return {"ok": True}


@router.get("/status")
async def status(request: Request) -> dict:
    """
    What the frontend needs at boot, in one public call.

    Public on purpose, and it has to be: with auth DISABLED every route is
    open, so a client asking /me gets a 401 that means "no session" — which is
    indistinguishable from "auth is on and you are logged out". A frontend that
    cannot tell those apart shows a login page on an instance where login
    returns 404. This endpoint answers the actual question.

    It leaks only whether auth is switched on. The user block is filled in from
    the caller's own session, so an unauthenticated request learns nothing
    about who else exists.
    """
    if not settings.auth_enabled:
        return {"auth_enabled": False, "authenticated": False, "user": None}

    # Resolved here rather than read off request.state: this path is in the
    # public allowlist, so require_session returned early without loading it.
    #
    # resolve_session_user, NOT load_session_user: the latter turns an
    # unreadable session store into None, which is right for a protected route
    # (fail closed) and wrong here. A database outage would answer
    # "authenticated: false" with a 200, the client would show the login form,
    # and the operator would retype credentials into a login that cannot
    # succeed either. This is a status probe, so it reports the outage.
    from app.api.auth_deps import SessionLookupError, resolve_session_user

    try:
        user = await resolve_session_user(request)
    except SessionLookupError as exc:
        logger.warning("Auth status could not read the session store: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Cannot determine session state right now",
        ) from exc

    return {
        "auth_enabled": True,
        "authenticated": user is not None,
        "user": {k: user[k] for k in ("id", "email", "tier") if k in user} if user else None,
    }


@router.get("/me")
async def me(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"user": {k: user[k] for k in ("id", "email", "tier") if k in user}}


# A valid Argon2 hash of a value nobody holds. Used so a login attempt for a
# nonexistent account still pays the hashing cost — otherwise response timing
# tells an attacker which emails exist.
from app.services.auth_service import hash_password as _hash_password  # noqa: E402

_DUMMY_HASH = _hash_password("not-a-real-password-timing-equaliser")


@router.post("/password")
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    _rl: None = Depends(login_rate_limit),
) -> dict:
    """
    Change the signed-in user's password and cut every other session loose.

    THE CURRENT PASSWORD IS REQUIRED even though the caller already holds a
    valid session. A session cookie proves "this browser logged in at some
    point"; it does not prove the person typing now is the owner. Without this
    check, anyone with a borrowed laptop or a lifted cookie could set a new
    password and own the account outright — the one action that turns temporary
    access into permanent access.

    EVERY OTHER SESSION IS REVOKED, and that is the point rather than a side
    effect. People change a password precisely when they think someone else has
    access; leaving that someone else logged in makes the whole exercise
    theatre. The CURRENT session survives, because logging you out of the tab
    you just used is a worse experience for no security gain — you have already
    proved the password twice in this request.

    Rate-limited on the login limiter deliberately: this route verifies a
    password, so it is an oracle for guessing one, and it should cost the same
    as guessing at the front door.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.user import User, UserSession

    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Authentication is not enabled")

    user_id = (current_user(request) or {}).get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = request.cookies.get(SESSION_COOKIE_NAME)
    current_hash = hash_token(token) if token else None

    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(User).where(User.id == user_id).limit(1)
        )).scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Authentication required")

        if not verify_password(body.current_password, user.password_hash):
            logger.warning("Password change refused for %s — wrong current password",
                           user.email)
            raise HTTPException(status_code=401, detail="Current password is incorrect")

        # Rejected AFTER the current-password check, so this cannot be used to
        # probe whether a password is correct without knowing it.
        if body.new_password == body.current_password:
            raise HTTPException(status_code=400,
                                detail="New password must differ from the current one")

        user.password_hash = hash_password(body.new_password)

        now = datetime.now(timezone.utc)
        others = (await db.execute(
            select(UserSession).where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
            )
        )).scalars().all()
        revoked = 0
        for s in others:
            if current_hash is not None and s.token_hash == current_hash:
                continue
            s.revoked_at = now
            revoked += 1

        await db.commit()
        email = user.email

    logger.info("Password changed for %s — %d other session(s) revoked", email, revoked)
    return {"ok": True, "other_sessions_revoked": revoked}


@router.get("/sessions")
async def list_sessions(request: Request) -> dict:
    """
    Every live session for the signed-in user, newest first.

    Exists so "am I logged in somewhere I don't recognise?" is answerable. A
    session list nobody can see is an audit trail for after the fact only.

    Returns no token or token hash. The hash is enough to look up and revoke a
    session, so shipping it to the browser would turn an informational endpoint
    into a way to cut someone else off.
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.user import UserSession

    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Authentication is not enabled")

    user_id = (current_user(request) or {}).get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = request.cookies.get(SESSION_COOKIE_NAME)
    current_hash = hash_token(token) if token else None
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.created_at.desc())
        )).scalars().all()

        out = [
            SessionOut(
                id=str(s.id),
                created_at=s.created_at.isoformat(),
                last_seen_at=s.last_seen_at.isoformat() if s.last_seen_at else None,
                user_agent=s.user_agent,
                ip=s.ip,
                current=(current_hash is not None and s.token_hash == current_hash),
            ).model_dump()
            for s in rows
        ]

    return {"sessions": out}


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(session_id: str, request: Request) -> dict:
    """
    Revoke one of the signed-in user's own sessions.

    Scoped to the caller's own rows by the WHERE clause, not by checking
    ownership after loading: a query that can only ever return your own
    sessions cannot be talked into revoking someone else's with a guessed id.
    An id belonging to another user returns 404, the same as one that does not
    exist — which is also the right answer, since confirming "that id is real
    but not yours" is an enumeration oracle.

    Revoking the CURRENT session is allowed and behaves as a logout, except the
    cookie is left in place — the next request fails the session check and the
    browser is bounced to login. Refusing it would be surprising: "log out my
    other devices" and "log out this one" belong on the same list.
    """
    import uuid as _uuid

    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.user import UserSession

    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="Authentication is not enabled")

    user_id = (current_user(request) or {}).get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        target = _uuid.UUID(session_id)
    except (ValueError, AttributeError, TypeError):
        # A malformed id is not a server error and must not reach the database.
        raise HTTPException(status_code=404, detail="No such session")

    async with AsyncSessionLocal() as db:
        session = (await db.execute(
            select(UserSession).where(
                UserSession.id == target,
                UserSession.user_id == user_id,
            ).limit(1)
        )).scalar_one_or_none()

        if session is None:
            raise HTTPException(status_code=404, detail="No such session")
        if session.revoked_at is not None:
            return {"ok": True, "already_revoked": True}

        session.revoked_at = datetime.now(timezone.utc)
        await db.commit()

    logger.info("Session %s revoked by its owner", session_id)
    return {"ok": True}
