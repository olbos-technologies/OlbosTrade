"""
Per-user broker credentials: encrypting them, storing them, giving them back.

The thing this file is really guarding is that a credential which can be sent
to a broker cannot leak out of any other door. So the assertions cluster
around the boundaries rather than the happy path:

  * a ciphertext never appears in anything the API returns;
  * a revoked connection keeps its history and loses its secrets;
  * one user's connection id is worthless to another user;
  * a credential the broker REJECTS is never written at all, while a broker
    that is merely unreachable does not block the user;
  * and the routes 404 on an auth-disabled install BEFORE the tier check or
    the rate limiter get to answer — the ordering bug #71 shipped.

Same database limitation as test_access_requests.py: no aiosqlite here and no
Postgres in CI, so the session is an in-memory stand-in answering the queries
these functions actually issue. It proves logic, not SQL. The partial unique
index that makes "one active connection per user, broker and environment" true
is asserted against the model metadata in test_broker_connection_schema.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

import app.api.rate_limit as rate_limit_mod
import app.core.database as db_mod
from app.core.config import settings
from app.models.broker_connection import (
    ENV_LIVE, ENV_PAPER, STATUS_ACTIVE, STATUS_REVOKED, BrokerConnection,
)
from app.models.organization import KIND_PERSONAL, Organization
from app.services import broker_connection_service as svc
from app.services import credential_cipher

# A valid Fernet key, generated once and pinned here. Test-only and never
# deployed; generating a fresh one per run would make a failure that depends
# on key contents impossible to reproduce.
TEST_KEY = "ZmFrZS1rZXktZm9yLXRlc3RzLW9ubHktMzJieXRlcyE="

ALPACA_KEY = "PKTEST0000EXAMPLEKEY"
ALPACA_SECRET = "s3cr3t-example-secret-value-not-real-0000"


# ── in-memory stand-in ───────────────────────────────────────────────────────

def _require_uuid(column: str, value) -> None:
    """Refuse a string where the column is UUID(as_uuid=True).

    Carried over from test_access_requests.py for the same reason: asyncpg
    rejects a str bound to a UUID column before the query runs, and a
    permissive fake would certify that bug as working.
    """
    if not isinstance(value, uuid.UUID):
        raise AssertionError(
            f"{column} is UUID(as_uuid=True) but the query bound "
            f"{type(value).__name__} {value!r}. asyncpg would reject this."
        )


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        if len(self._rows) > 1:
            raise AssertionError("scalar_one_or_none() got more than one row")
        return self._rows[0] if self._rows else None


class _Store:
    def __init__(self):
        self.connections: list[BrokerConnection] = []
        #: Personal organizations, as migration 0036 backfills them: one per
        #: user. Pre-populated so a route can resolve its caller without the
        #: get-or-create path running in every test.
        self.organizations: list[Organization] = [
            Organization(id=ORG_A, name="a", kind=KIND_PERSONAL,
                         personal_for_user_id=USER_A,
                         created_at=datetime.now(timezone.utc)),
            Organization(id=ORG_B, name="b", kind=KIND_PERSONAL,
                         personal_for_user_id=USER_B,
                         created_at=datetime.now(timezone.utc)),
        ]
        self.commits = 0
        #: Which lookups asked for FOR UPDATE, read off the compiled statement
        #: rather than by grepping the source — a `.with_for_update()` inside a
        #: COMMENT is what made an equivalent assertion vacuous on #70.
        self.locked: list[str] = []
        self.unlocked: list[str] = []


class _FakeSession:
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

        # Routes resolve the caller's organization before touching a
        # connection, so the fake has to answer that lookup too. Returning
        # the pre-seeded personal org is what migration 0036 leaves behind.
        if entities == ["Organization"]:
            orgs = list(self.store.organizations)
            if "personal_for_user_id_1" in params:
                _require_uuid("Organization.personal_for_user_id",
                              params["personal_for_user_id_1"])
                orgs = [o for o in orgs
                        if o.personal_for_user_id == params["personal_for_user_id_1"]]
            return _Result(orgs)

        if entities != ["BrokerConnection"]:
            raise AssertionError(f"unexpected query over {entities}")

        rows = list(self.store.connections)
        if "id_1" in params:
            _require_uuid("BrokerConnection.id", params["id_1"])
            rows = [r for r in rows if r.id == params["id_1"]]
        if "organization_id_1" in params:
            _require_uuid("BrokerConnection.organization_id", params["organization_id_1"])
            rows = [r for r in rows
                    if r.organization_id == params["organization_id_1"]]
        if "broker_1" in params:
            rows = [r for r in rows if r.broker == params["broker_1"]]
        if "environment_1" in params:
            rows = [r for r in rows if r.environment == params["environment_1"]]
        if "status_1" in params:
            rows = [r for r in rows if r.status == params["status_1"]]

        (self.store.locked if "FOR UPDATE" in sql
         else self.store.unlocked).append(sorted(params))

        # ORDER BY honoured rather than ignored: a route promising "newest
        # first" and a fake returning insertion order agree by accident.
        if "ORDER BY" in sql and "created_at DESC" in sql:
            rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
        return _Result(rows)

    def add(self, obj):
        """Staged, not stored — rows land on commit, so "nothing was written"
        is a real assertion rather than a vacuous one."""
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)   # server_default
        if getattr(obj, "status", None) is None:
            obj.status = STATUS_ACTIVE                    # column default
        self._staged.append(obj)

    async def flush(self):
        """Mutations are already visible (same objects); staged inserts are not.

        That asymmetry is the real one: connect() relies on the revoke landing
        before the insert is checked against the partial unique index.
        """

    async def rollback(self):
        self._staged.clear()

    async def commit(self):
        for obj in self._staged:
            self.store.connections.append(obj)
        self._staged.clear()
        self.store.commits += 1

    async def refresh(self, obj):
        return obj


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _FakeSession(s))
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "broker_encryption_key", TEST_KEY, raising=False)
    rate_limit_mod._request_log.clear()
    return s


@pytest.fixture
def cipher_key(monkeypatch):
    monkeypatch.setattr(settings, "broker_encryption_key", TEST_KEY, raising=False)


USER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
#: Each user's personal organization — the OWNER of a connection since
#: ADR-0001. One per user, so these stand in one-for-one for the user ids
#: these tests used to key on.
ORG_A = uuid.UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")
ORG_B = uuid.UUID("bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb")


def _row(store, organization_id=ORG_A, *, broker="alpaca", environment=ENV_PAPER,
         status=STATUS_ACTIVE, key=ALPACA_KEY, created_at=None):
    conn = BrokerConnection(
        id=uuid.uuid4(), organization_id=organization_id, broker=broker,
        environment=environment, label="", status=status,
        api_key_enc=credential_cipher.encrypt(key) if status == STATUS_ACTIVE else None,
        secret_key_enc=(credential_cipher.encrypt(ALPACA_SECRET)
                        if status == STATUS_ACTIVE else None),
        key_last4=credential_cipher.last4(key),
        created_at=created_at or datetime.now(timezone.utc),
    )
    store.connections.append(conn)
    return conn


# ── the cipher ───────────────────────────────────────────────────────────────

def test_an_unconfigured_deployment_refuses_rather_than_storing_plaintext(monkeypatch):
    """The failure mode this exists to prevent is a fallback that "works"."""
    monkeypatch.setattr(settings, "broker_encryption_key", "", raising=False)
    assert credential_cipher.is_configured() is False
    with pytest.raises(credential_cipher.CredentialCipherUnavailable):
        credential_cipher.encrypt("anything")


def test_a_credential_survives_a_round_trip(cipher_key):
    token = credential_cipher.encrypt(ALPACA_SECRET)
    assert token != ALPACA_SECRET
    assert ALPACA_SECRET not in token
    assert credential_cipher.decrypt(token) == ALPACA_SECRET


def test_two_encryptions_of_one_secret_differ(cipher_key):
    """Fernet carries a random IV. Identical ciphertexts would mean an
    observer with table access could tell which users share a key."""
    assert credential_cipher.encrypt(ALPACA_SECRET) != credential_cipher.encrypt(ALPACA_SECRET)


def test_a_tampered_ciphertext_is_refused_not_silently_decoded(cipher_key):
    """The reason for authenticated encryption, stated as a test.

    Someone with write access to the database must not be able to flip bits
    and steer a decrypted key somewhere useful.
    """
    token = credential_cipher.encrypt(ALPACA_SECRET)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(credential_cipher.CredentialDecryptionError):
        credential_cipher.decrypt(tampered)


def test_a_credential_encrypted_under_another_key_does_not_decrypt(monkeypatch, cipher_key):
    """Rotating BROKER_ENCRYPTION_KEY must fail loudly, never return garbage.

    This is also the test that justifies the setting being separate from
    SECRET_KEY: if they were shared, every routine rotation of the operator
    key would land here, in production, mid-session.
    """
    token = credential_cipher.encrypt(ALPACA_SECRET)
    other = "b3RoZXIta2V5LWZvci10ZXN0cy0zMmJ5dGVzLWFiY2Q="
    monkeypatch.setattr(settings, "broker_encryption_key", other, raising=False)
    with pytest.raises(credential_cipher.CredentialDecryptionError):
        credential_cipher.decrypt(token)


def test_no_error_message_carries_the_secret_or_the_key(monkeypatch, cipher_key):
    """An exception string is a leak wearing a different hat — it reaches logs,
    error trackers and sometimes the response body."""
    token = credential_cipher.encrypt(ALPACA_SECRET)
    monkeypatch.setattr(settings, "broker_encryption_key", "not-a-valid-key",
                        raising=False)
    with pytest.raises(credential_cipher.CredentialCipherUnavailable) as exc:
        credential_cipher.decrypt(token)
    message = str(exc.value)
    assert "not-a-valid-key" not in message
    assert ALPACA_SECRET not in message
    assert token not in message


def test_last4_is_four_characters_and_not_the_key(cipher_key):
    assert credential_cipher.last4(ALPACA_KEY) == ALPACA_KEY[-4:]
    assert len(credential_cipher.last4(ALPACA_KEY)) == 4
    assert credential_cipher.last4("") == ""


# ── what the API layer is allowed to see ─────────────────────────────────────

def test_serialize_carries_no_ciphertext_and_no_plaintext(cipher_key):
    conn = BrokerConnection(
        id=uuid.uuid4(), organization_id=ORG_A, broker="alpaca", environment=ENV_PAPER,
        label="my paper account", status=STATUS_ACTIVE,
        api_key_enc=credential_cipher.encrypt(ALPACA_KEY),
        secret_key_enc=credential_cipher.encrypt(ALPACA_SECRET),
        key_last4=credential_cipher.last4(ALPACA_KEY),
        created_at=datetime.now(timezone.utc),
    )
    payload = svc.serialize(conn)
    blob = repr(payload)
    assert ALPACA_KEY not in blob and ALPACA_SECRET not in blob
    assert conn.api_key_enc not in blob and conn.secret_key_enc not in blob
    assert "api_key_enc" not in payload and "secret_key_enc" not in payload
    # What it DOES carry, so the screen can name the connection.
    assert payload["key_last4"] == ALPACA_KEY[-4:]
    assert payload["verified"] is False


def test_that_assertion_would_actually_catch_a_leak(cipher_key):
    """A guard that cannot fail is decoration. This is the mutation."""
    conn = BrokerConnection(
        id=uuid.uuid4(), organization_id=ORG_A, broker="alpaca", environment=ENV_PAPER,
        label="", status=STATUS_ACTIVE,
        api_key_enc=credential_cipher.encrypt(ALPACA_KEY),
        secret_key_enc=credential_cipher.encrypt(ALPACA_SECRET),
        key_last4=credential_cipher.last4(ALPACA_KEY),
        created_at=datetime.now(timezone.utc),
    )
    leaky = {**svc.serialize(conn), "api_key_enc": conn.api_key_enc}
    assert conn.api_key_enc in repr(leaky), (
        "the leak detector above cannot see a ciphertext that IS present"
    )


# ── connecting ───────────────────────────────────────────────────────────────

async def test_connect_stores_a_credential_that_comes_back_out(store):
    db = _FakeSession(store)
    conn = await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                             api_key=ALPACA_KEY, secret_key=ALPACA_SECRET,
                             label="paper")
    assert conn.status == STATUS_ACTIVE
    assert conn.api_key_enc and conn.api_key_enc != ALPACA_KEY
    assert credential_cipher.decrypt(conn.api_key_enc) == ALPACA_KEY
    assert credential_cipher.decrypt(conn.secret_key_enc) == ALPACA_SECRET
    assert conn.key_last4 == ALPACA_KEY[-4:]


async def test_reconnecting_replaces_the_active_row_and_destroys_its_secrets(store, cipher_key):
    """Key rotation is routine. Pasting the new key must work without hunting
    for a disconnect button first — and must not leave the old key behind."""
    old = _row(store, ORG_A, environment=ENV_PAPER, key="PKOLDOLDOLDOLDOLD")
    db = _FakeSession(store)
    await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                      api_key=ALPACA_KEY, secret_key=ALPACA_SECRET)

    assert old.status == STATUS_REVOKED
    assert old.revoked_at is not None
    assert old.api_key_enc is None and old.secret_key_enc is None, (
        "the replaced credential is still stored — a user rotating a LEAKED "
        "key would be no better off than before"
    )
    active = [c for c in store.connections if c.status == STATUS_ACTIVE]
    assert len(active) == 1, "the partial unique index would reject this"
    assert credential_cipher.decrypt(active[0].api_key_enc) == ALPACA_KEY


async def test_connect_locks_the_row_it_is_about_to_replace(store, cipher_key):
    """FOR UPDATE, read off the compiled SQL rather than the source.

    Without it two simultaneous connects both read "no active row", both
    insert, and the unique index turns a legitimate replace into a 500.
    """
    _row(store, ORG_A)
    db = _FakeSession(store)
    await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                      api_key=ALPACA_KEY, secret_key=ALPACA_SECRET)
    assert store.locked, "the lookup before the replace did not lock"


async def test_paper_and_live_are_separate_connections(store, cipher_key):
    """A user may legitimately hold both; connecting live must not revoke paper."""
    paper = _row(store, ORG_A, environment=ENV_PAPER)
    db = _FakeSession(store)
    await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_LIVE,
                      api_key=ALPACA_KEY, secret_key=ALPACA_SECRET)
    assert paper.status == STATUS_ACTIVE, "connecting live revoked the paper account"
    assert len([c for c in store.connections if c.status == STATUS_ACTIVE]) == 2


async def test_two_organizations_connect_independently(store, cipher_key):
    """The whole point of the feature, stated once: one tenant's Alpaca setup
    is not the other's.

    Keyed on organization since ADR-0001. With one personal organization per
    user this is still "two people", which is why the assertion reads the same
    as it did — what changed is that the isolation is now enforced on the
    column execution will route by."""
    db = _FakeSession(store)
    await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                      api_key="PKAAAAAAAAAAAAAAAAAA", secret_key=ALPACA_SECRET,
                      created_by_user_id=USER_A)
    await svc.connect(db, ORG_B, broker="alpaca", environment=ENV_PAPER,
                      api_key="PKBBBBBBBBBBBBBBBBBB", secret_key=ALPACA_SECRET,
                      created_by_user_id=USER_B)
    active = [c for c in store.connections if c.status == STATUS_ACTIVE]
    assert len(active) == 2, "one organization's connect revoked the other's"
    assert {c.organization_id for c in active} == {ORG_A, ORG_B}
    # Provenance survives the re-key: who pasted the key is still recorded.
    assert {c.created_by_user_id for c in active} == {USER_A, USER_B}


async def test_connect_refuses_when_the_deployment_cannot_encrypt(store, monkeypatch):
    monkeypatch.setattr(settings, "broker_encryption_key", "", raising=False)
    db = _FakeSession(store)
    with pytest.raises(credential_cipher.CredentialCipherUnavailable):
        await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                          api_key=ALPACA_KEY, secret_key=ALPACA_SECRET)
    assert store.connections == [], "a credential was stored without encryption"


async def test_ibkr_is_refused_with_a_reason_rather_than_stored(store, cipher_key):
    """An unusable row would be worse than a clear no: the user would believe
    their IBKR account was connected and find out at the first order."""
    db = _FakeSession(store)
    with pytest.raises(svc.BrokerConnectionError) as exc:
        await svc.connect(db, ORG_A, broker="ibkr", environment=ENV_PAPER,
                          api_key=ALPACA_KEY, secret_key=ALPACA_SECRET)
    assert "gateway" in str(exc.value).lower()
    assert store.connections == []


async def test_an_unknown_broker_and_environment_are_refused(store, cipher_key):
    db = _FakeSession(store)
    for kwargs in ({"broker": "robinhood", "environment": ENV_PAPER},
                   {"broker": "alpaca", "environment": "production"}):
        with pytest.raises(svc.BrokerConnectionError):
            await svc.connect(db, ORG_A, api_key=ALPACA_KEY,
                              secret_key=ALPACA_SECRET, **kwargs)
    assert store.connections == []


async def test_a_blank_credential_is_refused(store, cipher_key):
    db = _FakeSession(store)
    with pytest.raises(svc.BrokerConnectionError):
        await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                          api_key="   ", secret_key=ALPACA_SECRET)


# ── revoking ─────────────────────────────────────────────────────────────────

async def test_revoke_keeps_the_row_and_destroys_the_secrets(store, cipher_key):
    row = _row(store, ORG_A)
    db = _FakeSession(store)
    assert await svc.revoke(db, ORG_A, row.id) is True
    assert row.status == STATUS_REVOKED
    assert row.api_key_enc is None and row.secret_key_enc is None
    assert row in store.connections, (
        "the row was deleted — 'when did this stop being connected' is now "
        "unanswerable"
    )
    assert row.key_last4, "the label that identifies the row was also cleared"


async def test_one_users_connection_id_is_worthless_to_another(store, cipher_key):
    """Scoped by user_id as well as id. False, not an error: 'never existed'
    and 'belongs to someone else' must be indistinguishable."""
    row = _row(store, ORG_A)
    db = _FakeSession(store)
    assert await svc.revoke(db, ORG_B, row.id) is False
    assert row.status == STATUS_ACTIVE
    assert row.api_key_enc is not None


async def test_revoking_an_unknown_or_malformed_id_is_false_not_a_crash(store, cipher_key):
    db = _FakeSession(store)
    assert await svc.revoke(db, ORG_A, uuid.uuid4()) is False
    assert await svc.revoke(db, ORG_A, "not-a-uuid") is False


async def test_revoking_twice_is_false_the_second_time(store, cipher_key):
    row = _row(store, ORG_A)
    db = _FakeSession(store)
    assert await svc.revoke(db, ORG_A, row.id) is True
    assert await svc.revoke(db, ORG_A, row.id) is False


# ── reading credentials back ─────────────────────────────────────────────────

async def test_credentials_for_returns_the_decrypted_pair_and_the_right_host(store, cipher_key):
    _row(store, ORG_A, environment=ENV_PAPER)
    db = _FakeSession(store)
    found = await svc.credentials_for(db, ORG_A, environment=ENV_PAPER)
    assert found is not None
    api_key, secret_key, base, _row_obj = found
    assert api_key == ALPACA_KEY and secret_key == ALPACA_SECRET
    assert base == "https://paper-api.alpaca.markets"


async def test_the_host_comes_from_the_environment_not_the_operators_setting(store, monkeypatch, cipher_key):
    """settings.alpaca_base_url is the OPERATOR's account. A user's paper
    connection must reach the paper host even on an install pointed at live —
    otherwise one setting silently redirects everyone's credentials."""
    monkeypatch.setattr(settings, "alpaca_base_url",
                        "https://api.alpaca.markets", raising=False)
    _row(store, ORG_A, environment=ENV_PAPER)
    db = _FakeSession(store)
    _k, _s, base, _r = await svc.credentials_for(db, ORG_A, environment=ENV_PAPER)
    assert base == "https://paper-api.alpaca.markets"


async def test_live_wins_when_the_user_has_both_and_asks_for_neither(store, cipher_key):
    _row(store, ORG_A, environment=ENV_PAPER)
    _row(store, ORG_A, environment=ENV_LIVE)
    db = _FakeSession(store)
    _k, _s, base, row = await svc.credentials_for(db, ORG_A)
    assert row.environment == ENV_LIVE
    assert base == "https://api.alpaca.markets"


async def test_no_connection_means_none_not_an_error(store, cipher_key):
    """None, so callers fall back to the operator's own credentials — which is
    what keeps a single-operator install working exactly as it did before this
    table existed."""
    db = _FakeSession(store)
    assert await svc.credentials_for(db, ORG_A) is None


async def test_a_revoked_connection_is_not_returned(store, cipher_key):
    _row(store, ORG_A, status=STATUS_REVOKED)
    db = _FakeSession(store)
    assert await svc.credentials_for(db, ORG_A) is None


async def test_an_active_row_with_no_ciphertext_reads_as_not_connected(store, cipher_key):
    """Should be impossible — revoke clears both fields and the status
    together. Treated as "not connected" rather than crashing an order path
    over a row that should not exist."""
    row = _row(store, ORG_A)
    row.api_key_enc = None
    db = _FakeSession(store)
    assert await svc.credentials_for(db, ORG_A) is None


async def test_one_users_credentials_are_never_returned_for_another(store, cipher_key):
    _row(store, ORG_A)
    db = _FakeSession(store)
    assert await svc.credentials_for(db, ORG_B) is None


# ── verification against the broker ──────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _patch_http(monkeypatch, handler):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, **kwargs):
            return handler(url, headers or {})

    monkeypatch.setattr(svc.httpx, "AsyncClient", _FakeClient)


async def test_verification_sends_the_key_to_the_matching_host(monkeypatch):
    seen = {}

    def handler(url, headers):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse(200, {"status": "ACTIVE", "currency": "USD"})

    _patch_http(monkeypatch, handler)
    await svc.verify_alpaca(ALPACA_KEY, ALPACA_SECRET, ENV_LIVE)
    assert seen["url"] == "https://api.alpaca.markets/v2/account"
    assert seen["headers"]["APCA-API-KEY-ID"] == ALPACA_KEY
    assert seen["headers"]["APCA-API-SECRET-KEY"] == ALPACA_SECRET


async def test_verification_returns_only_the_fields_we_intend_to_show(monkeypatch):
    """Alpaca's account payload carries balances and an account number.
    Copying it wholesale would put those in a response that was never
    designed to hold them."""
    _patch_http(monkeypatch, lambda url, headers: _FakeResponse(200, {
        "status": "ACTIVE", "currency": "USD", "pattern_day_trader": False,
        "account_number": "PA3ABCDEF", "cash": "100000",
        "buying_power": "200000",
    }))
    result = await svc.verify_alpaca(ALPACA_KEY, ALPACA_SECRET, ENV_PAPER)
    assert set(result) == {"account_status", "currency", "pattern_day_trader"}
    assert "PA3ABCDEF" not in repr(result)
    assert "100000" not in repr(result)


@pytest.mark.parametrize("status_code", [401, 403])
async def test_a_rejected_key_is_a_rejection(monkeypatch, status_code):
    _patch_http(monkeypatch, lambda url, headers: _FakeResponse(status_code))
    with pytest.raises(svc.VerificationRejected):
        await svc.verify_alpaca(ALPACA_KEY, ALPACA_SECRET, ENV_PAPER)


@pytest.mark.parametrize("status_code", [500, 502, 429])
async def test_a_broker_side_failure_is_not_a_rejection(monkeypatch, status_code):
    """The distinction the whole design rests on. A 500 from Alpaca says
    nothing about the key, and treating it as "wrong key" would tell users to
    re-type a credential that was fine."""
    _patch_http(monkeypatch, lambda url, headers: _FakeResponse(status_code))
    with pytest.raises(svc.VerificationUnavailable):
        await svc.verify_alpaca(ALPACA_KEY, ALPACA_SECRET, ENV_PAPER)


async def test_a_network_failure_is_not_a_rejection_and_does_not_echo_the_url(monkeypatch):
    def handler(url, headers):
        raise RuntimeError(f"connection refused to {url} key={ALPACA_KEY}")

    _patch_http(monkeypatch, handler)
    with pytest.raises(svc.VerificationUnavailable) as exc:
        await svc.verify_alpaca(ALPACA_KEY, ALPACA_SECRET, ENV_PAPER)
    assert ALPACA_KEY not in str(exc.value), (
        "the transport error text reached the exception — httpx errors quote "
        "the request, and some proxies echo headers into error bodies"
    )


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """A test app shaped like the real one: the router with its own
    dependencies, and a stand-in for require_session that puts an Elite user
    on request.state where auth_deps.current_user reads it."""
    from app.api.routes.broker_connections import router

    def _fake_session(request: Request) -> None:
        request.state.user = {"id": str(USER_A), "email": "elite@example.com",
                              "tier": "elite"}

    app = FastAPI(dependencies=[Depends(_fake_session)])
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def free_client():
    from app.api.routes.broker_connections import router

    def _fake_session(request: Request) -> None:
        request.state.user = {"id": str(USER_A), "email": "free@example.com",
                              "tier": "free"}

    app = FastAPI(dependencies=[Depends(_fake_session)])
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _connect_body(**over):
    body = {"broker": "alpaca", "environment": "paper",
            "api_key": ALPACA_KEY, "secret_key": ALPACA_SECRET, "label": "paper"}
    body.update(over)
    return body


async def test_free_cannot_reach_these_routes(free_client, store):
    async with free_client as c:
        r = await c.get("/api/brokers/connections")
    assert r.status_code == 403
    assert "Elite" in r.json()["detail"]


async def test_the_routes_do_not_exist_when_auth_is_disabled(client, store, monkeypatch):
    """404 BEFORE the tier check and BEFORE the rate limiter.

    This is the ordering bug #71 shipped: the checks in front answered first,
    so a route that was supposed not to exist still consumed a caller's
    rate-limit quota and reported on the install's own configuration. Six
    calls, not one — a single call passes even when the limiter is ahead of
    the 404, which is exactly how it hid the first time.
    """
    monkeypatch.setattr(settings, "auth_enabled", False)
    async with client as c:
        codes = []
        for _ in range(6):
            r = await c.post("/api/brokers/connections", json=_connect_body())
            codes.append(r.status_code)
    assert codes == [404] * 6, f"got {codes} — something answered before the 404"


async def test_connecting_without_an_encryption_key_is_503_not_500(client, store, monkeypatch):
    monkeypatch.setattr(settings, "broker_encryption_key", "", raising=False)
    async with client as c:
        r = await c.post("/api/brokers/connections", json=_connect_body())
    assert r.status_code == 503
    assert store.connections == []
    # The variable name is for the operator's log, not the caller's screen.
    assert "BROKER_ENCRYPTION_KEY" not in r.text


async def test_a_credential_the_broker_rejects_is_never_stored(client, store, monkeypatch):
    """Verify first. A stored-but-invalid credential looks connected and fails
    at the first order, which is the worst moment to find out."""
    _patch_http(monkeypatch, lambda url, headers: _FakeResponse(401))
    async with client as c:
        r = await c.post("/api/brokers/connections", json=_connect_body())
    assert r.status_code == 400
    assert store.connections == [], "a rejected credential reached the database"


async def test_an_unreachable_broker_stores_the_credential_unverified(client, store, monkeypatch):
    """The other half of that decision. Refusing here would make connecting
    impossible during an Alpaca outage — the moment a user is most likely to
    be re-checking their setup."""
    def handler(url, headers):
        raise RuntimeError("connection refused")

    _patch_http(monkeypatch, handler)
    async with client as c:
        r = await c.post("/api/brokers/connections", json=_connect_body())
    assert r.status_code == 200
    body = r.json()
    assert body["connection"]["verified"] is False
    assert body["unverified_reason"], "stored unverified without saying so"
    assert len(store.connections) == 1


async def test_a_successful_connect_returns_nothing_secret(client, store, monkeypatch):
    _patch_http(monkeypatch, lambda url, headers: _FakeResponse(200, {
        "status": "ACTIVE", "currency": "USD", "account_number": "PA3ABCDEF"}))
    async with client as c:
        r = await c.post("/api/brokers/connections", json=_connect_body())
    assert r.status_code == 200
    assert ALPACA_KEY not in r.text and ALPACA_SECRET not in r.text
    assert "PA3ABCDEF" not in r.text
    stored = store.connections[0]
    assert stored.api_key_enc not in r.text
    body = r.json()
    assert body["connection"]["verified"] is True
    assert body["connection"]["key_last4"] == ALPACA_KEY[-4:]


async def test_the_response_says_execution_is_not_routed_yet(client, store, monkeypatch):
    """A user who connects a broker and then watches orders go somewhere else
    has been misled by this screen. Per-user routing is a separate change;
    until it lands, the API says so rather than implying otherwise."""
    _patch_http(monkeypatch, lambda url, headers: _FakeResponse(200, {"status": "ACTIVE"}))
    async with client as c:
        created = await c.post("/api/brokers/connections", json=_connect_body())
        listed = await c.get("/api/brokers/connections")
    assert created.json()["execution_routing_enabled"] is False
    assert listed.json()["execution_routing_enabled"] is False


async def test_listing_never_decrypts_and_never_leaks(client, store, cipher_key):
    _row(store, ORG_A, environment=ENV_PAPER)
    _row(store, ORG_A, environment=ENV_LIVE)
    _row(store, ORG_B, environment=ENV_PAPER)
    async with client as c:
        r = await c.get("/api/brokers/connections")
    assert r.status_code == 200
    connections = r.json()["connections"]
    assert len(connections) == 2, "the listing returned another user's connection"
    assert ALPACA_KEY not in r.text and ALPACA_SECRET not in r.text
    for stored in store.connections:
        if stored.api_key_enc:
            assert stored.api_key_enc not in r.text


async def test_listing_is_newest_first(client, store, cipher_key):
    now = datetime.now(timezone.utc)
    _row(store, ORG_A, environment=ENV_PAPER, created_at=now - timedelta(days=3))
    _row(store, ORG_A, environment=ENV_LIVE, created_at=now)
    async with client as c:
        r = await c.get("/api/brokers/connections")
    assert [c_["environment"] for c_ in r.json()["connections"]] == [ENV_LIVE, ENV_PAPER]


async def test_deleting_another_users_connection_is_404(client, store, cipher_key):
    row = _row(store, ORG_B)
    async with client as c:
        r = await c.delete(f"/api/brokers/connections/{row.id}")
    assert r.status_code == 404
    assert row.status == STATUS_ACTIVE
    assert row.api_key_enc is not None


async def test_deleting_your_own_connection_disconnects_it(client, store, cipher_key):
    row = _row(store, ORG_A)
    async with client as c:
        r = await c.delete(f"/api/brokers/connections/{row.id}")
    assert r.status_code == 200
    assert row.status == STATUS_REVOKED
    assert row.api_key_enc is None


async def test_verifying_another_users_connection_is_404(client, store, monkeypatch, cipher_key):
    called = []
    _patch_http(monkeypatch, lambda url, headers: called.append(url) or _FakeResponse(200, {}))
    row = _row(store, ORG_B)
    async with client as c:
        r = await c.post(f"/api/brokers/connections/{row.id}/verify")
    assert r.status_code == 404
    assert called == [], "someone else's credentials were sent to the broker"


async def test_verifying_your_own_connection_records_the_check(client, store, monkeypatch, cipher_key):
    _patch_http(monkeypatch, lambda url, headers: _FakeResponse(200, {"status": "ACTIVE"}))
    row = _row(store, ORG_A)
    assert row.last_verified_at is None
    async with client as c:
        r = await c.post(f"/api/brokers/connections/{row.id}/verify")
    assert r.status_code == 200
    assert r.json()["verified"] is True
    assert row.last_verified_at is not None
    assert ALPACA_KEY not in r.text

# ── what a validation failure is allowed to say ─────────────────────────────

async def test_an_oversized_credential_is_never_echoed_back(client, store):
    """The leak this PR shipped with, and the reason ConnectIn has no
    max_length on its credential fields.

    A Pydantic constraint is enforced before the handler and raises a 422 whose
    detail carries `input` — the offending value, which here is the whole
    secret. The frontend JSON.stringifies a non-string detail into the message
    and renders it in an alert, so an over-long key landed in the DOM.
    Reproduced before fixing: a 300-character secret came back in the response
    body verbatim.
    """
    huge = "SUPERSECRET-" + "Z" * 400
    async with client as c:
        r = await c.post("/api/brokers/connections",
                         json=_connect_body(secret_key=huge))

    assert r.status_code == 400, (
        f"expected our own 400, got {r.status_code} — a 422 here means "
        f"Pydantic rejected it first and echoed the value"
    )
    assert huge not in r.text, "the response echoed the credential back"
    assert "Z" * 40 not in r.text, "part of the credential survived in the body"
    assert store.connections == []


async def test_the_same_holds_for_an_oversized_api_key(client, store):
    huge = "PK" + "Q" * 400
    async with client as c:
        r = await c.post("/api/brokers/connections", json=_connect_body(api_key=huge))
    assert r.status_code == 400
    assert huge not in r.text
    assert "Q" * 40 not in r.text


async def test_an_absurd_credential_never_reaches_the_broker(client, store, monkeypatch):
    """Why the length check sits in the HANDLER and not only in the service.

    The service checks too, and on its own that already yields a safe 400 —
    so removing the handler check changes no status code and no message. What
    it changes is ORDER: the service check runs after verify_alpaca(), so
    without the handler check a 400-character value is first packed into an
    outbound request header and sent to Alpaca.

    Found by mutation: deleting the handler check failed nothing until this
    test existed.
    """
    called = []

    def handler(url, headers):
        called.append(url)
        return _FakeResponse(200, {"status": "ACTIVE"})

    _patch_http(monkeypatch, handler)
    async with client as c:
        r = await c.post("/api/brokers/connections",
                         json=_connect_body(secret_key="Z" * 400))

    assert r.status_code == 400
    assert called == [], (
        "an over-long credential was sent to the broker before being "
        "rejected — the length check must run before verification"
    )


async def test_a_long_label_is_still_rejected_by_the_schema(client, store):
    """label KEEPS its max_length, deliberately: it is the user's own words,
    not a secret, so echoing it in a 422 is harmless — and a schema
    constraint is the better guard when there is nothing to hide."""
    async with client as c:
        r = await c.post("/api/brokers/connections",
                         json=_connect_body(label="L" * 200))
    assert r.status_code == 422
    assert store.connections == []


# ── the feature switch reaches every route ──────────────────────────────────

async def test_listing_refuses_when_the_deployment_cannot_encrypt(client, store, monkeypatch):
    """MyBrokers decides whether to render the credential form from THIS call.

    Without the guard an unconfigured deployment answered 200 with an empty
    list, the form appeared, and a user typed a live brokerage secret into a
    screen that could not store it — finding out only when the POST came back
    503. Raised in review on #78.
    """
    monkeypatch.setattr(settings, "broker_encryption_key", "", raising=False)
    async with client as c:
        r = await c.get("/api/brokers/connections")
    assert r.status_code == 503, (
        "the list route answered without an encryption key, so the UI will "
        "show a form that cannot store what is typed into it"
    )


# ── the first connection for a slot is a race ───────────────────────────────

async def test_two_simultaneous_first_connections_do_not_500(store, cipher_key):
    """FOR UPDATE locks the rows it FINDS, and on a first connect it finds
    none — so both requests insert and the partial unique index turns one into
    an IntegrityError. The retry re-reads after the winner committed and takes
    the ordinary replace path.

    The race is simulated by making the first commit raise IntegrityError, the
    way Postgres would report the index violation.
    """
    from sqlalchemy.exc import IntegrityError

    db = _FakeSession(store)
    calls = {"n": 0}
    real_commit = db.commit

    async def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            # As the loser of the race sees it.
            raise IntegrityError("INSERT INTO broker_connections", {}, Exception(
                'duplicate key value violates unique constraint '
                '"idx_broker_connections_active"'))
        await real_commit()

    db.commit = flaky_commit
    conn = await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                             api_key=ALPACA_KEY, secret_key=ALPACA_SECRET)

    assert calls["n"] == 2, "the conflict was not retried"
    assert conn.status == STATUS_ACTIVE
    assert credential_cipher.decrypt(conn.api_key_enc) == ALPACA_KEY


async def test_a_persistent_conflict_still_raises_rather_than_spinning(store, cipher_key):
    """Two attempts, not a loop. A second conflict means a third concurrent
    writer for one user's single slot, which is not worth spinning on — and an
    unbounded retry would turn a stuck index into a hung request."""
    from sqlalchemy.exc import IntegrityError

    db = _FakeSession(store)
    calls = {"n": 0}

    async def always_conflict():
        calls["n"] += 1
        raise IntegrityError("INSERT INTO broker_connections", {}, Exception("dup"))

    db.commit = always_conflict
    with pytest.raises(IntegrityError):
        await svc.connect(db, ORG_A, broker="alpaca", environment=ENV_PAPER,
                          api_key=ALPACA_KEY, secret_key=ALPACA_SECRET)
    assert calls["n"] == 2, f"expected exactly 2 attempts, got {calls['n']}"

