"""
Connection-scoped Alpaca clients (MASTER_ARCHITECTURE §21 Phase 2, action 1).

Two properties matter here and neither is about happy-path construction:

  * an organization's order must use THAT organization's credentials and
    THAT connection's host -- not the operator's settings;
  * a credential must not be able to reach a log, a repr, or an exception.

§24 lists "credentials never appear in browser, logs, or events" as a Phase 2
exit criterion, so it is asserted rather than assumed.
"""

from __future__ import annotations

import uuid

import pytest

from app.broker import connection_scoped as cs
from app.broker.alpaca_client import AlpacaClient
from app.broker.connection_scoped import ConnectionNotExecutable
from app.core.config import settings

pytestmark = pytest.mark.asyncio

ORG = uuid.UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")
KEY = "PKTENANTKEY0000000A"
SECRET = "tenant-secret-value-never-logged-0001"
PAPER = "https://paper-api.alpaca.markets"
LIVE = "https://api.alpaca.markets"


class _Row:
    def __init__(self, *, environment="paper", api_key_enc="cipher", version=None):
        self.id = uuid.uuid4()
        self.environment = environment
        self.api_key_enc = api_key_enc
        if version is not None:
            self.version = version


def _patch_credentials(monkeypatch, result):
    async def _fake(db, organization_id, *, broker="alpaca", environment=None):
        _patch_credentials.seen = {"org": organization_id, "broker": broker,
                                   "environment": environment}
        return result
    monkeypatch.setattr(cs.connections, "credentials_for", _fake)


# ── Routing the right credentials ────────────────────────────────────────────

async def test_builds_a_client_from_the_organizations_connection(monkeypatch):
    row = _Row(environment="paper")
    _patch_credentials(monkeypatch, (KEY, SECRET, PAPER, row))

    scoped = await cs.scoped_alpaca_for(object(), ORG)

    assert isinstance(scoped.client, AlpacaClient)
    assert scoped.client._headers["APCA-API-KEY-ID"] == KEY
    assert scoped.client._headers["APCA-API-SECRET-KEY"] == SECRET
    assert scoped.organization_id == str(ORG)
    assert scoped.connection_id == str(row.id)


async def test_the_host_comes_from_the_connection_not_the_operator_settings(monkeypatch):
    """The mistake this layer exists to prevent.

    If the base URL came from settings.alpaca_base_url, an operator running
    live would send a paper-connected organization's order to the live
    endpoint -- real money, against an account that never agreed to it.
    """
    monkeypatch.setattr(settings, "alpaca_base_url", LIVE, raising=False)
    _patch_credentials(monkeypatch, (KEY, SECRET, PAPER, _Row(environment="paper")))

    scoped = await cs.scoped_alpaca_for(object(), ORG)

    assert scoped.client._trading_base == PAPER, (
        "a paper connection was pointed at the operator's live host"
    )
    assert scoped.environment == "paper"


async def test_a_live_connection_keeps_its_live_host(monkeypatch):
    """The same check in the other direction, so the test above cannot pass by
    the base URL being hardcoded to paper."""
    monkeypatch.setattr(settings, "alpaca_base_url", PAPER, raising=False)
    _patch_credentials(monkeypatch, (KEY, SECRET, LIVE, _Row(environment="live")))

    scoped = await cs.scoped_alpaca_for(object(), ORG)

    assert scoped.client._trading_base == LIVE
    assert scoped.environment == "live"


async def test_the_environment_argument_is_passed_through(monkeypatch):
    _patch_credentials(monkeypatch, (KEY, SECRET, PAPER, _Row()))
    await cs.scoped_alpaca_for(object(), ORG, environment="paper")
    assert _patch_credentials.seen["environment"] == "paper"
    assert _patch_credentials.seen["broker"] == "alpaca"


# ── Refusing to execute ──────────────────────────────────────────────────────

async def test_no_connection_raises_rather_than_returning_none(monkeypatch):
    """Returning None would let a caller fall through to the process-wide
    singleton and route one organization's order through the operator's own
    account. An exception cannot be mistaken for 'no connection, carry on'."""
    _patch_credentials(monkeypatch, None)

    with pytest.raises(ConnectionNotExecutable) as exc:
        await cs.scoped_alpaca_for(object(), ORG)
    assert str(ORG) in str(exc.value)


async def test_a_revoked_connection_cannot_become_a_client(monkeypatch):
    """Defence in depth: credentials_for already filters on status, but this
    is the last point before a credential becomes something that can trade."""
    _patch_credentials(monkeypatch, (KEY, SECRET, PAPER, _Row(api_key_enc=None)))

    with pytest.raises(ConnectionNotExecutable) as exc:
        await cs.scoped_alpaca_for(object(), ORG)
    assert "revoked" in str(exc.value)


async def test_ibkr_is_refused_with_the_reason(monkeypatch):
    err = cs.scoped_broker_unsupported("ibkr")
    assert isinstance(err, ConnectionNotExecutable)
    assert "single logged-in session" in str(err)


async def test_an_unknown_broker_is_refused_too():
    """The fallback matters: a broker added to the connections table but not
    here must fail closed, not fall through to whatever the process holds."""
    err = cs.scoped_broker_unsupported("schwab")
    assert isinstance(err, ConnectionNotExecutable)
    assert "schwab" in str(err)


# ── Credentials must not leak (§24 exit criterion) ───────────────────────────

async def test_the_scoped_broker_repr_carries_no_credential(monkeypatch):
    _patch_credentials(monkeypatch, (KEY, SECRET, PAPER, _Row()))
    scoped = await cs.scoped_alpaca_for(object(), ORG)

    for rendered in (repr(scoped), str(scoped), f"{scoped}"):
        assert KEY not in rendered
        assert SECRET not in rendered


async def test_the_client_repr_carries_no_credential():
    client = AlpacaClient(api_key=KEY, secret_key=SECRET, base_url=PAPER)
    for rendered in (repr(client), str(client), f"{client}"):
        assert KEY not in rendered, "the API key reached a repr"
        assert SECRET not in rendered, "the API secret reached a repr"
    # and it still says something useful
    assert "paper" in repr(client)


async def test_that_leak_assertion_would_catch_a_real_leak():
    """The mutation: a default repr WOULD expose the headers, so the test
    above is not passing because reprs happen to be short."""
    class _Leaky(AlpacaClient):
        def __repr__(self):            # what Python gives you for free
            return f"AlpacaClient({self.__dict__})"

    leaked = repr(_Leaky(api_key=KEY, secret_key=SECRET, base_url=PAPER))
    assert KEY in leaked and SECRET in leaked, (
        "the mutation did not leak, so the assertion above proves nothing"
    )


async def test_an_exception_from_a_missing_connection_names_no_credential(monkeypatch):
    _patch_credentials(monkeypatch, None)
    try:
        await cs.scoped_alpaca_for(object(), ORG)
    except ConnectionNotExecutable as exc:
        assert KEY not in str(exc) and SECRET not in str(exc)


# ── The default client is unchanged ──────────────────────────────────────────

async def test_an_unscoped_client_still_reads_the_operator_settings(monkeypatch):
    """The compatibility guarantee. 52 call sites build AlpacaClient() through
    the factory with no arguments, and this change must not alter any of them."""
    monkeypatch.setattr(settings, "alpaca_base_url", LIVE, raising=False)
    monkeypatch.setattr(settings, "alpaca_api_key", "OPERATORKEY", raising=False)
    monkeypatch.setattr(settings, "alpaca_secret_key", "operatorsecret", raising=False)

    client = AlpacaClient()

    assert client._trading_base == LIVE
    assert client._headers["APCA-API-KEY-ID"] == "OPERATORKEY"
    assert client._headers["APCA-API-SECRET-KEY"] == "operatorsecret"
    assert client.connection_id is None, "an unscoped client belongs to no tenant"


async def test_the_connection_version_travels_with_the_client(monkeypatch):
    """§8 revalidates it before submitting. A client that does not know which
    version it was built from cannot be checked."""
    _patch_credentials(monkeypatch, (KEY, SECRET, PAPER, _Row(version=7)))
    scoped = await cs.scoped_alpaca_for(object(), ORG)
    assert scoped.connection_version == 7
    assert scoped.client.connection_version == 7


async def test_a_connection_without_a_version_column_reads_as_one(monkeypatch):
    """The column does not exist yet. Documented default rather than a crash,
    and called out so nobody reads the worker's revalidation as meaningful
    until the column lands."""
    _patch_credentials(monkeypatch, (KEY, SECRET, PAPER, _Row()))
    scoped = await cs.scoped_alpaca_for(object(), ORG)
    assert scoped.connection_version == 1
