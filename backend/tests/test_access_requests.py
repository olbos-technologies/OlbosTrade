"""
Access requests: asking, approving, and redeeming — exercised over HTTP.

Same limitation as test_account_management.py and worth restating: there is no
aiosqlite in this stack and no Postgres in CI, so the database is an in-memory
stand-in answering the queries these routes actually issue. That proves route
logic, not the SQL. The partial unique index that makes "one live request per
address" true is asserted separately, in test_access_request_schema.py.

What this file is for is the behaviour that is expensive to get wrong:

  * the public form answers IDENTICALLY whether or not the address already has
    an account — otherwise a platform whose users are, by construction, people
    with brokerage accounts ships a way to ask "does this person trade here?";
  * and identically when the write fails, because a 500 on some addresses and
    a 200 on others is that same oracle by another route;
  * a setup token appears exactly once, in the approval response, and never in
    the queue, the claim response, or a log line;
  * every redemption failure is the same 400, so the token space cannot be
    probed with feedback;
  * a token is good for one account, once — a replay creates nothing;
  * approving twice is refused, because the second token would not replace the
    first, it would join it.
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
from app.api.routes.access_requests import admin_router, router
from app.core.config import settings
from app.models.access_request import (
    STATUS_APPROVED, STATUS_CLAIMED, STATUS_DENIED, STATUS_PENDING,
    AccessRequest,
)
from app.models.user import TIER_FREE, User, UserSession
from app.services.access_request_service import new_setup_token, setup_token_expiry
from app.services.auth_service import (
    SESSION_COOKIE_NAME, hash_password, hash_token, verify_password,
)

OPERATOR_KEY = "operator-secret-key"
CHOSEN_PASSWORD = "a sufficiently long chosen passphrase"


# ── in-memory stand-in ───────────────────────────────────────────────────────

def _require_uuid(column: str, value) -> None:
    """Refuse a string where the column is UUID(as_uuid=True).

    Carried over from test_account_management.py, where a permissive version
    of this stand-in certified `str(user.id)` as working against a UUID column
    — asyncpg rejects that before the query runs. Being strict about types is
    the only way a fake catches that class at all.
    """
    if not isinstance(value, uuid.UUID):
        raise AssertionError(
            f"{column} is UUID(as_uuid=True) but the query bound "
            f"{type(value).__name__} {value!r}. asyncpg would reject this "
            f"before the query ran. Parse it with uuid.UUID() at the call site."
        )


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
        self.requests: list[AccessRequest] = []
        self.sessions: list[UserSession] = []
        self.commits = 0
        #: Set to make the next commit raise, standing in for the partial
        #: unique index tripping on a duplicate that raced past the read.
        self.fail_on_commit = False
        #: Whether the row-returning lookups in claim/approve/deny asked for
        #: FOR UPDATE. Recorded from the compiled statement rather than by
        #: scanning the source, because a `.with_for_update()` in a COMMENT is
        #: what made the equivalent assertion vacuous on #70.
        self.locked: list[str] = []
        self.unlocked: list[str] = []


class _FakeSession:
    """Dispatches on the entities a select names plus the params it binds, so a
    query the code stops issuing surfaces as an empty result rather than a
    silent pass."""

    def __init__(self, store: _Store):
        self.store = store
        self._staged: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        entities = [d["entity"].__name__ for d in stmt.column_descriptions]
        params = stmt.compile().params
        sql = str(stmt)

        if entities == ["User"]:
            if "email_1" in params:
                return _Result([u for u in self.store.users
                                if u.email == params["email_1"]])
            raise AssertionError(f"unexpected User query params: {sorted(params)}")

        if entities == ["AccessRequest"]:
            rows = list(self.store.requests)
            if "id_1" in params:
                _require_uuid("AccessRequest.id", params["id_1"])
                rows = [r for r in rows if r.id == params["id_1"]]
                self._note_lock("by_id", sql)
            if "email_1" in params:
                rows = [r for r in rows if r.email == params["email_1"]]
            if "status_1" in params:
                rows = [r for r in rows if r.status == params["status_1"]]
            if "setup_token_hash_1" in params:
                rows = [r for r in rows
                        if r.setup_token_hash == params["setup_token_hash_1"]]
                self._note_lock("by_token", sql)
            # ORDER BY is honoured rather than ignored: a route that promises
            # "newest first" and a stand-in returning insertion order agree by
            # accident, and the test then proves nothing about the ordering.
            if "ORDER BY" in sql and "created_at DESC" in sql:
                rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
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

    def _note_lock(self, label: str, sql: str) -> None:
        (self.store.locked if "FOR UPDATE" in sql
         else self.store.unlocked).append(label)

    def add(self, obj):
        """STAGED, not stored. Rows land in the store on commit.

        The first version of this appended straight to the store, which made
        every "and nothing was written" assertion vacuous: a route whose
        commit raised still left the row visible, so the test could not tell a
        rollback from a successful write. Defaults that a real flush applies
        are filled in here, since that is where they would be applied.
        """
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        # created_at carries server_default=func.now() and is NOT NULL, so a
        # real flush always populates it. Leaving it None would make the route
        # look broken for a state the database cannot produce.
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        if isinstance(obj, AccessRequest):
            if obj.status is None:
                obj.status = STATUS_PENDING       # column default
        elif not isinstance(obj, UserSession):
            if getattr(obj, "tier", None) is None:
                obj.tier = TIER_FREE              # column default
            if getattr(obj, "is_active", None) is None:
                obj.is_active = True              # column default
        self._staged.append(obj)

    async def commit(self):
        if self.store.fail_on_commit:
            self._staged.clear()                  # as a rollback would
            raise RuntimeError("duplicate key value violates unique constraint")
        for obj in self._staged:
            if isinstance(obj, AccessRequest):
                self.store.requests.append(obj)
            elif isinstance(obj, UserSession):
                self.store.sessions.append(obj)
            else:
                self.store.users.append(obj)
        self._staged.clear()
        self.store.commits += 1


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _FakeSession(s))
    rate_limit_mod._signup_log.clear()
    rate_limit_mod._login_log.clear()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "secret_key", OPERATOR_KEY)
    return s


@pytest.fixture
def client():
    """Mirrors the real app: the same app-level session dependency, so the
    allowlist in auth_deps is what decides whether the public routes are
    reachable. A test app without it would pass no matter what the allowlist
    said."""
    app = FastAPI(dependencies=[Depends(require_session)])
    app.include_router(router)
    app.include_router(admin_router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def operator(store):
    """An operator with a live session, since the admin routes sit behind BOTH
    the session dependency and the API key."""
    user = User(id=uuid.uuid4(), email="ops@example.com",
                password_hash=hash_password("x" * 20), tier="elite",
                is_active=True)
    store.users.append(user)
    session = UserSession(
        id=uuid.uuid4(), user_id=user.id, token_hash=hash_token("op-token"),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
        created_at=datetime.now(timezone.utc),
    )
    store.sessions.append(session)
    return {"cookies": {SESSION_COOKIE_NAME: "op-token"},
            "headers": {"X-Api-Key": OPERATOR_KEY}}


def _pending(store, email="trader@example.com", reason="I trade options"):
    req = AccessRequest(id=uuid.uuid4(), email=email, reason=reason,
                        status=STATUS_PENDING,
                        created_at=datetime.now(timezone.utc))
    store.requests.append(req)
    return req


def _approved(store, email="trader@example.com", *, expires_at=None):
    token, token_hash = new_setup_token()
    req = AccessRequest(
        id=uuid.uuid4(), email=email, reason=None, status=STATUS_APPROVED,
        setup_token_hash=token_hash,
        setup_token_expires_at=expires_at or setup_token_expiry(),
        created_at=datetime.now(timezone.utc),
        reviewed_at=datetime.now(timezone.utc),
    )
    store.requests.append(req)
    return token, req


async def _submit(client, email, reason="please"):
    return await client.post("/api/access-requests",
                             json={"email": email, "reason": reason})


# ── the enumeration rule ─────────────────────────────────────────────────────

async def test_the_form_answers_identically_whoever_is_asking(client, store):
    """The single most important assertion in this file.

    A new address, one that already has an account, one already queued, and
    one previously denied must be indistinguishable from the outside. Anything
    else turns a public form into a way to test whether a named person holds a
    brokerage-connected account here.
    """
    store.users.append(User(id=uuid.uuid4(), email="known@example.com",
                            password_hash=hash_password("x" * 20),
                            tier=TIER_FREE, is_active=True))
    _pending(store, "queued@example.com")
    denied = _pending(store, "denied@example.com")
    denied.status = STATUS_DENIED

    seen = set()
    for email in ("brand-new@example.com", "known@example.com",
                  "queued@example.com", "denied@example.com"):
        r = await _submit(client, email)
        seen.add((r.status_code, r.text))

    assert len(seen) == 1, (
        f"the form gave {len(seen)} distinguishable answers: {seen}. Every "
        "one of these must look the same to the submitter."
    )
    assert seen.pop()[0] == 200


async def test_a_failed_write_is_not_a_signal_either(client, store):
    """A duplicate that races past the read trips the partial unique index.

    If that surfaced as a 500 while a fresh address got a 200, the enumeration
    oracle would be back — through the error path rather than the happy one.
    """
    store.fail_on_commit = True

    r_failed = await _submit(client, "new@example.com")
    assert r_failed.status_code == 200
    assert not store.requests, "the failed commit must not have persisted a row"

    # The same submission with a working database, for comparison.
    store.fail_on_commit = False
    rate_limit_mod._signup_log.clear()
    r_ok = await _submit(client, "other@example.com")

    assert (r_failed.status_code, r_failed.text) == (r_ok.status_code, r_ok.text)
    assert [r.email for r in store.requests] == ["other@example.com"]


async def test_a_known_address_is_not_queued_twice(client, store):
    """Identical responses must not be achieved by writing a row anyway.

    The queue is the operator's view, and it is the one place the difference
    is supposed to show. A duplicate row here would make the queue useless
    exactly when someone is hammering the form.
    """
    _pending(store, "queued@example.com")
    store.users.append(User(id=uuid.uuid4(), email="known@example.com",
                            password_hash=hash_password("x" * 20),
                            tier=TIER_FREE, is_active=True))

    await _submit(client, "queued@example.com")
    await _submit(client, "known@example.com")

    assert len(store.requests) == 1
    assert [r.email for r in store.requests] == ["queued@example.com"]


async def test_case_and_whitespace_cannot_dodge_the_dedupe(client, store):
    """"  Known@Example.COM " is the same person as "known@example.com".

    Without normalisation the address would land in the queue as a new
    request, which both clutters the queue and — because the response would
    then be the one written for a new address — is a difference the submitter
    could measure if the two paths ever diverged.
    """
    store.users.append(User(id=uuid.uuid4(), email="known@example.com",
                            password_hash=hash_password("x" * 20),
                            tier=TIER_FREE, is_active=True))

    r = await _submit(client, "  Known@Example.COM ")

    assert r.status_code == 200
    assert not store.requests


async def test_a_new_address_is_recorded_with_its_reason(client, store):
    await _submit(client, "New@Example.com", reason="  I run a covered-call book  ")

    assert len(store.requests) == 1
    req = store.requests[0]
    assert req.email == "new@example.com"        # stored lower-cased
    assert req.reason == "I run a covered-call book"
    assert req.status == STATUS_PENDING
    assert req.setup_token_hash is None          # nothing is granted by asking


async def test_an_empty_reason_is_stored_as_null_not_blank(client, store):
    """So the queue shows "no reason given" rather than an empty string that
    reads as a reason the operator failed to render."""
    await _submit(client, "new@example.com", reason="   ")
    assert store.requests[0].reason is None


async def test_something_that_is_not_an_email_is_refused(client, store):
    for bad in ("not-an-email", "@example.com", "trader@"):
        r = await _submit(client, bad)
        assert r.status_code == 422, bad
    assert not store.requests


async def test_the_form_is_rate_limited(client, store):
    """Five an hour per address. Without this the queue fills with whatever a
    script feels like sending, and is useless precisely when it matters."""
    for i in range(rate_limit_mod.SIGNUP_MAX_REQUESTS):
        r = await _submit(client, f"person{i}@example.com")
        assert r.status_code == 200, i

    r = await _submit(client, "one-too-many@example.com")
    assert r.status_code == 429
    assert len(store.requests) == rate_limit_mod.SIGNUP_MAX_REQUESTS


# ── approval ─────────────────────────────────────────────────────────────────

async def test_approval_returns_the_token_once_and_stores_only_its_hash(
    client, store, operator
):
    """The token is the entire grant, so the database must not hold a usable
    copy — the same reason sessions store a digest."""
    req = _pending(store)

    r = await client.post("/api/admin/access-requests/approve",
                          json={"request_id": str(req.id)}, **operator)

    assert r.status_code == 200
    token = r.json()["setup_token"]
    assert token
    assert req.status == STATUS_APPROVED
    assert req.setup_token_hash == hash_token(token)
    assert token not in str(req.__dict__), "the plaintext token was persisted"


async def test_the_queue_never_shows_a_token_or_its_hash(client, store, operator):
    """Knowing the hash is not knowing the token — but it is enough to look a
    grant up, and the queue has no use for it. Only "is there a live grant"
    is operationally interesting."""
    token, req = _approved(store)

    r = await client.get("/api/admin/access-requests", **operator)

    assert r.status_code == 200
    body = r.text
    assert token not in body
    assert req.setup_token_hash not in body
    assert r.json()["requests"][0]["has_live_token"] is True


async def test_approving_twice_is_refused(client, store, operator):
    """A second approval would MINT a second token, not replace the first.

    The operator's mental model after re-approving is "the old one is dead";
    the reality would be two live grants. Refusing keeps those in agreement.
    """
    req = _pending(store)
    first = await client.post("/api/admin/access-requests/approve",
                              json={"request_id": str(req.id)}, **operator)
    first_hash = req.setup_token_hash

    second = await client.post("/api/admin/access-requests/approve",
                               json={"request_id": str(req.id)}, **operator)

    assert first.status_code == 200
    assert second.status_code == 409
    assert req.setup_token_hash == first_hash


async def test_denial_kills_any_grant_and_blocks_approval(client, store, operator):
    req = _pending(store)

    denied = await client.post("/api/admin/access-requests/deny",
                               json={"request_id": str(req.id)}, **operator)
    after = await client.post("/api/admin/access-requests/approve",
                              json={"request_id": str(req.id)}, **operator)

    assert denied.status_code == 200
    assert req.status == STATUS_DENIED
    assert req.setup_token_hash is None
    assert req.setup_token_expires_at is None
    assert after.status_code == 409


async def test_a_malformed_request_id_is_a_404_not_a_500(client, store, operator):
    """AccessRequest.id is UUID(as_uuid=True): binding "not-a-uuid" fails in
    asyncpg before the query runs. Parsed at the boundary so the caller gets
    the answer they asked for."""
    for body in ({"request_id": "not-a-uuid"}, {"request_id": ""}):
        r = await client.post("/api/admin/access-requests/approve",
                              json=body, **operator)
        assert r.status_code == 404, body


async def test_review_lookups_lock_the_row(client, store, operator):
    """Two approvals arriving together would both read status=pending and both
    mint a token. Asserted from the COMPILED statement — the equivalent check
    on #70 matched `.with_for_update()` inside a comment and proved nothing."""
    req = _pending(store)
    await client.post("/api/admin/access-requests/approve",
                      json={"request_id": str(req.id)}, **operator)

    assert "by_id" in store.locked
    assert "by_id" not in store.unlocked


# ── the operator gate ────────────────────────────────────────────────────────

async def test_the_queue_is_not_reachable_without_the_operator_key(client, store,
                                                                   operator):
    """Both gates, independently: a session alone is not enough, and the key
    alone is not enough."""
    key_only = await client.get("/api/admin/access-requests",
                                headers=operator["headers"])
    session_only = await client.get("/api/admin/access-requests",
                                    cookies=operator["cookies"])

    assert key_only.status_code == 401      # no session
    assert session_only.status_code == 403  # no key


async def test_the_admin_prefix_is_not_public(client):
    """The allowlist matches on PATH, not method. If the queue had been a GET
    on /api/access-requests, allowlisting the public form would have published
    every pending address."""
    from app.api.auth_deps import is_public_path

    assert is_public_path("/api/access-requests")
    assert is_public_path("/api/access-requests/claim")
    for path in ("/api/admin/access-requests",
                 "/api/admin/access-requests/approve",
                 "/api/admin/access-requests/deny"):
        assert not is_public_path(path), path


async def test_approval_is_refused_when_no_operator_key_is_configured(
    client, store, operator, monkeypatch
):
    """require_api_key no-ops on an empty SECRET_KEY, which is fine for a
    read-only dev box and not fine for minting account grants. These routes
    use the strict variant, which 503s instead of waving the caller through.
    """
    req = _pending(store)
    monkeypatch.setattr(settings, "secret_key", "")

    r = await client.post("/api/admin/access-requests/approve",
                          json={"request_id": str(req.id)}, **operator)

    assert r.status_code == 503
    assert req.status == STATUS_PENDING


# ── redemption ───────────────────────────────────────────────────────────────

async def test_claiming_creates_the_account_with_the_chosen_password(client, store):
    """The operator never learns this password. That is the point of handing
    over a token rather than a credential."""
    token, req = _approved(store, "trader@example.com")

    r = await client.post("/api/access-requests/claim",
                          json={"token": token, "password": CHOSEN_PASSWORD})

    assert r.status_code == 200
    assert len(store.users) == 1
    user = store.users[0]
    assert user.email == "trader@example.com"
    assert verify_password(CHOSEN_PASSWORD, user.password_hash)
    assert not verify_password(token, user.password_hash)
    assert req.status == STATUS_CLAIMED
    assert req.claimed_at is not None


async def test_a_new_account_starts_on_the_free_tier_and_active(client, store):
    """Redeeming a grant must not be a way to arrive on a paid tier."""
    token, _ = _approved(store)
    await client.post("/api/access-requests/claim",
                      json={"token": token, "password": CHOSEN_PASSWORD})

    assert store.users[0].tier == TIER_FREE
    assert store.users[0].is_active is True


async def test_a_token_works_once(client, store):
    token, req = _approved(store)

    first = await client.post("/api/access-requests/claim",
                              json={"token": token, "password": CHOSEN_PASSWORD})
    second = await client.post("/api/access-requests/claim",
                               json={"token": token, "password": CHOSEN_PASSWORD})

    assert first.status_code == 200
    assert second.status_code == 400
    assert len(store.users) == 1


@pytest.mark.parametrize("scenario", ["wrong", "expired", "denied", "pending"])
async def test_every_redemption_failure_looks_the_same(client, store, scenario):
    """Distinguishing them would let someone probe the token space with
    feedback — "that one exists but is expired" is a hit."""
    if scenario == "wrong":
        token = new_setup_token()[0]
    elif scenario == "expired":
        token, _ = _approved(store, expires_at=datetime.now(timezone.utc)
                             - timedelta(minutes=1))
    elif scenario == "denied":
        token, req = _approved(store)
        req.status = STATUS_DENIED
    else:
        token, req = _approved(store)
        req.status = STATUS_PENDING

    r = await client.post("/api/access-requests/claim",
                          json={"token": token, "password": CHOSEN_PASSWORD})

    assert r.status_code == 400
    assert r.json() == {"detail": "That setup link is not valid"}
    assert not store.users


async def test_an_approval_with_no_expiry_is_not_a_grant_that_never_dies(
    client, store
):
    """A null expiry is missing data, not permission. Treating it as unlimited
    is how a row written by some future code path becomes a permanent key."""
    token, req = _approved(store)
    req.setup_token_expires_at = None

    r = await client.post("/api/access-requests/claim",
                          json={"token": token, "password": CHOSEN_PASSWORD})

    assert r.status_code == 400
    assert not store.users


async def test_a_claim_for_an_address_that_already_has_an_account_is_refused(
    client, store
):
    """Someone provisioned by hand between approval and redemption. Creating a
    second row would violate the unique index on users.email and 500; the same
    generic 400 keeps the failure uninformative."""
    token, _ = _approved(store, "trader@example.com")
    store.users.append(User(id=uuid.uuid4(), email="trader@example.com",
                            password_hash=hash_password("x" * 20),
                            tier=TIER_FREE, is_active=True))

    r = await client.post("/api/access-requests/claim",
                          json={"token": token, "password": CHOSEN_PASSWORD})

    assert r.status_code == 400
    assert len(store.users) == 1


async def test_a_short_password_is_refused_and_creates_nothing(client, store):
    """The bound is shared with the login route. A cap here below the one
    create_user.py enforces would provision an account nobody can log into."""
    token, req = _approved(store)

    r = await client.post("/api/access-requests/claim",
                          json={"token": token, "password": "short"})

    assert r.status_code == 422
    assert not store.users
    assert req.status == STATUS_APPROVED, "the grant must survive a rejected try"


async def test_the_claim_response_leaks_nothing(client, store):
    token, req = _approved(store)

    r = await client.post("/api/access-requests/claim",
                          json={"token": token, "password": CHOSEN_PASSWORD})

    body = r.text
    assert token not in body
    assert CHOSEN_PASSWORD not in body
    assert store.users[0].password_hash not in body


async def test_the_claim_lookup_locks_the_row(client, store):
    """Two redemptions of one token arriving together would both read
    status=approved and both try to create the account."""
    token, _ = _approved(store)
    await client.post("/api/access-requests/claim",
                      json={"token": token, "password": CHOSEN_PASSWORD})

    assert "by_token" in store.locked
    assert "by_token" not in store.unlocked


# ── the switch ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/access-requests", {"email": "a@b.co", "reason": "x"}),
    ("post", "/api/access-requests/claim",
     {"token": "t", "password": CHOSEN_PASSWORD}),
])
async def test_the_public_routes_do_not_exist_while_auth_is_off(
    client, store, monkeypatch, method, path, body
):
    """An install on nginx Basic Auth alone has no accounts to grant, and a
    claim route that creates users no login route will accept is worse than
    no claim route."""
    monkeypatch.setattr(settings, "auth_enabled", False)

    r = await getattr(client, method)(path, json=body)

    assert r.status_code == 404
    assert not store.requests
    assert not store.users
