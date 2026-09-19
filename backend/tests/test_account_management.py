"""
Password change and session management, exercised over HTTP.

Same limitation as test_auth_routes.py and worth restating: there is no
aiosqlite in this stack and no Postgres in CI, so the database is an in-memory
stand-in answering the queries these routes actually issue. That proves route
logic and the session lifecycle, not the SQL.

What it is for is the behaviour that is expensive to get wrong:

  * a valid session alone cannot change a password — otherwise a lifted cookie
    becomes permanent ownership of the account;
  * changing a password cuts every OTHER session loose, because people change
    passwords precisely when they think someone else is logged in;
  * the current session survives, because logging you out of the tab you just
    authenticated in is friction with no security gain;
  * no endpoint ever emits a session token or its hash — the hash is enough to
    revoke a session, so leaking it turns a read endpoint into a weapon;
  * one user cannot revoke another's session, and asking about a session that
    is not theirs is indistinguishable from asking about one that does not
    exist.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import Depends, FastAPI

import app.api.rate_limit as rate_limit_mod
import app.core.database as db_mod
from app.api.auth_deps import require_session
from app.api.routes.auth import router as auth_router
from app.core.config import settings
from app.models.user import User, UserSession
from app.services.auth_service import (
    MIN_PASSWORD_LEN, SESSION_COOKIE_NAME, hash_password, hash_token,
)

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different sufficiently long passphrase"


# ── in-memory stand-in ───────────────────────────────────────────────────────

class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _Scalars(self._rows)


class _Store:
    def __init__(self):
        self.users: list[User] = []
        self.sessions: list[UserSession] = []
        self.commits = 0


class _FakeSession:
    """Dispatches on the entities a select names plus the params it binds, so a
    query the code stops issuing surfaces as an empty result rather than a
    silent pass."""

    def __init__(self, store: _Store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        entities = [d["entity"].__name__ for d in stmt.column_descriptions]
        params = stmt.compile().params

        if entities == ["User"]:
            if "email_1" in params:
                return _Result([u for u in self.store.users
                                if u.email == params["email_1"]])
            if "id_1" in params:
                return _Result([u for u in self.store.users
                                if str(u.id) == str(params["id_1"])])
            raise AssertionError(f"unexpected User query params: {sorted(params)}")

        if entities == ["UserSession"]:
            rows = list(self.store.sessions)
            if "token_hash_1" in params:
                rows = [s for s in rows if s.token_hash == params["token_hash_1"]]
            if "user_id_1" in params:
                rows = [s for s in rows if str(s.user_id) == str(params["user_id_1"])]
            if "id_1" in params:
                rows = [s for s in rows if str(s.id) == str(params["id_1"])]
            # revoked_at IS NULL and expires_at > now appear as SQL, not params;
            # applying them here keeps the stand-in honest about what the real
            # query would return.
            sql = str(stmt)
            if "revoked_at IS NULL" in sql:
                rows = [s for s in rows if s.revoked_at is None]
            if "expires_at >" in sql:
                now = datetime.now(timezone.utc)
                rows = [s for s in rows if s.expires_at > now]
            return _Result(rows)

        if entities == ["UserSession", "User"]:
            th = params.get("token_hash_1")
            rows = []
            for s in self.store.sessions:
                if s.token_hash != th:
                    continue
                user = next((u for u in self.store.users if u.id == s.user_id), None)
                if user is not None:
                    rows.append((s, user))
            return _Result(rows)

        raise AssertionError(f"unexpected query over {entities}")

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()          # normally assigned at flush
        # created_at carries server_default=func.now() and is NOT NULL, so a
        # real flush always populates it. The stand-in has to do the same:
        # leaving it None here would make the route look broken for a state
        # the database cannot produce, and "fixing" the route to tolerate None
        # would hide that the column is non-nullable.
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        (self.store.sessions if isinstance(obj, UserSession) else self.store.users).append(obj)

    async def commit(self):
        self.store.commits += 1


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _FakeSession(s))
    rate_limit_mod._login_log.clear()
    rate_limit_mod._request_log.clear()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
    return s


@pytest.fixture
def user(store):
    u = User(id=uuid.uuid4(), email="trader@example.com",
             password_hash=hash_password(PASSWORD), tier="pro", is_active=True)
    store.users.append(u)
    return u


@pytest.fixture
def client():
    app = FastAPI(dependencies=[Depends(require_session)])
    app.include_router(auth_router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _login(client, password=PASSWORD):
    return await client.post("/api/auth/login",
                             json={"email": "trader@example.com", "password": password})


def _other_session(store, user, *, ua="Firefox", ip="10.0.0.9"):
    """A second live session for the same user, as if from another device."""
    s = UserSession(
        id=uuid.uuid4(), user_id=user.id, token_hash="f" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
        created_at=datetime.now(timezone.utc), user_agent=ua, ip=ip,
    )
    store.sessions.append(s)
    return s


# ── password change ──────────────────────────────────────────────────────────

async def test_a_session_alone_cannot_change_the_password(client, user, store):
    """The whole point of asking for the current password.

    Without it, anyone holding a cookie — a borrowed laptop, a lifted token —
    turns temporary access into permanent ownership of the account.
    """
    async with client:
        await _login(client)
        r = await client.post("/api/auth/password", json={
            "current_password": "not the password",
            "new_password": NEW_PASSWORD,
        })
    assert r.status_code == 401
    # The hash is salted, so compare by verifying rather than by equality.
    from app.services.auth_service import verify_password
    assert verify_password(PASSWORD, user.password_hash)
    assert not verify_password(NEW_PASSWORD, user.password_hash)


async def test_the_correct_current_password_changes_it(client, user):
    from app.services.auth_service import verify_password
    async with client:
        await _login(client)
        r = await client.post("/api/auth/password", json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
        })
    assert r.status_code == 200
    assert verify_password(NEW_PASSWORD, user.password_hash)
    assert not verify_password(PASSWORD, user.password_hash)


async def test_changing_the_password_revokes_other_sessions(client, user, store):
    """People change a password when they think someone else has access.
    Leaving that someone else logged in makes the exercise theatre."""
    other = _other_session(store, user)
    async with client:
        await _login(client)
        r = await client.post("/api/auth/password", json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
        })
    assert r.status_code == 200
    assert other.revoked_at is not None, "the other device is still logged in"
    assert r.json()["other_sessions_revoked"] == 1


async def test_the_current_session_survives_the_change(client, user, store):
    """Logging you out of the tab you just authenticated in is friction with
    no security gain — the password was proved twice in that request."""
    async with client:
        await _login(client)
        await client.post("/api/auth/password", json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
        })
        # Still authenticated: a protected route answers rather than 401ing.
        r = await client.get("/api/auth/sessions")
    assert r.status_code == 200


async def test_the_new_password_must_differ(client, user):
    async with client:
        await _login(client)
        r = await client.post("/api/auth/password", json={
            "current_password": PASSWORD,
            "new_password": PASSWORD,
        })
    assert r.status_code == 400


async def test_a_short_password_is_rejected_by_the_schema(client, user):
    async with client:
        await _login(client)
        r = await client.post("/api/auth/password", json={
            "current_password": PASSWORD,
            "new_password": "x" * (MIN_PASSWORD_LEN - 1),
        })
    assert r.status_code == 422


async def test_password_change_requires_authentication(client, user):
    async with client:
        r = await client.post("/api/auth/password", json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
        })
    assert r.status_code == 401


# ── session listing ──────────────────────────────────────────────────────────

async def test_sessions_lists_the_current_one_and_marks_it(client, user):
    async with client:
        await _login(client)
        r = await client.get("/api/auth/sessions")
    body = r.json()["sessions"]
    assert len(body) == 1
    assert body[0]["current"] is True


async def test_sessions_never_leak_a_token_or_its_hash(client, user, store):
    """The hash is enough to look up and revoke a session. Shipping it to the
    browser turns an informational endpoint into a way to cut someone off."""
    other = _other_session(store, user)
    async with client:
        await _login(client)
        r = await client.get("/api/auth/sessions")
    raw = r.text
    assert other.token_hash not in raw
    for s in r.json()["sessions"]:
        assert "token" not in " ".join(s.keys()).lower()


async def test_revoked_and_expired_sessions_are_not_listed(client, user, store):
    revoked = _other_session(store, user, ua="Revoked")
    revoked.revoked_at = datetime.now(timezone.utc)
    expired = _other_session(store, user, ua="Expired")
    expired.token_hash = "e" * 64
    expired.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

    async with client:
        await _login(client)
        r = await client.get("/api/auth/sessions")
    agents = [s["user_agent"] for s in r.json()["sessions"]]
    assert "Revoked" not in agents and "Expired" not in agents


# ── revocation ───────────────────────────────────────────────────────────────

async def test_a_user_can_revoke_their_own_other_session(client, user, store):
    other = _other_session(store, user)
    async with client:
        await _login(client)
        r = await client.post(f"/api/auth/sessions/{other.id}/revoke")
    assert r.status_code == 200
    assert other.revoked_at is not None


async def test_a_user_cannot_revoke_someone_elses_session(client, user, store):
    """Scoped by the WHERE clause, not by an ownership check after loading —
    a query that can only return your own rows cannot be talked into
    revoking another user's with a guessed id."""
    stranger = User(id=uuid.uuid4(), email="someone@else.com",
                    password_hash=hash_password(PASSWORD), tier="free", is_active=True)
    store.users.append(stranger)
    theirs = UserSession(
        id=uuid.uuid4(), user_id=stranger.id, token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
        created_at=datetime.now(timezone.utc),
    )
    store.sessions.append(theirs)

    async with client:
        await _login(client)
        r = await client.post(f"/api/auth/sessions/{theirs.id}/revoke")
    assert r.status_code == 404, "one user revoked another user's session"
    assert theirs.revoked_at is None


async def test_an_unknown_session_is_404_not_500(client, user):
    async with client:
        await _login(client)
        r = await client.post(f"/api/auth/sessions/{uuid.uuid4()}/revoke")
    assert r.status_code == 404


async def test_a_malformed_session_id_is_404_not_500(client, user):
    """A bad id must not reach the database as a cast error."""
    async with client:
        await _login(client)
        r = await client.post("/api/auth/sessions/not-a-uuid/revoke")
    assert r.status_code == 404


# ── the routes are not public ────────────────────────────────────────────────

def test_the_new_auth_routes_are_not_allowlisted():
    """The allowlist is default-deny, and login/logout/status are on it. These
    three must not be: a public /api/auth/password would let anyone set any
    password they could guess the current value for, with no session at all.
    """
    from app.api.auth_deps import is_public_path
    for path in ("/api/auth/password",
                 "/api/auth/sessions",
                 "/api/auth/sessions/abc/revoke"):
        assert not is_public_path(path), f"{path} is publicly reachable"


# ── the password bounds are shared, not copied ───────────────────────────────

def test_the_password_bounds_are_imported_everywhere_not_redefined():
    r"""create_user.py used to carry its own MIN/MAX tied to LoginRequest's
    max_length by a comment saying they must match.

    The failure that comment described is real and silent: provision an account
    with a password longer than the login route accepts and it is valid in the
    database and impossible to log in with, with no error explaining why. A
    comment cannot prevent that. This asserts the mechanism that can — that
    nobody has reintroduced a literal.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    service = repo / "backend" / "app" / "services" / "auth_service.py"
    consumers = [
        repo / "backend" / "app" / "api" / "routes" / "auth.py",
        repo / "backend" / "scripts" / "create_user.py",
    ]

    # The definition lives in exactly one place.
    assert re.search(r"^MAX_PASSWORD_LEN\s*=\s*\d+", service.read_text(), re.M), (
        "auth_service.py no longer defines MAX_PASSWORD_LEN — if it moved, "
        "point this test at the new home rather than deleting it."
    )

    for path in consumers:
        text = path.read_text()
        assert "MAX_PASSWORD_LEN" in text, f"{path.name} does not use the shared bound"
        redefined = re.search(r"^(MIN|MAX)_PASSWORD_LEN\s*=\s*\d+", text, re.M)
        assert not redefined, (
            f"{path.name} redefines {redefined.group(0) if redefined else ''} "
            f"instead of importing it. Two copies drift; the drift is invisible "
            f"until an account exists that cannot log in."
        )


def test_the_login_route_does_not_hardcode_a_password_length():
    """A literal here that is lower than create_user.py's bound makes a
    provisioned account impossible to log into."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2]
            / "backend" / "app" / "api" / "routes" / "auth.py").read_text()
    assert "max_length=1024" not in text, (
        "auth.py hardcodes a password max_length again — use MAX_PASSWORD_LEN"
    )
