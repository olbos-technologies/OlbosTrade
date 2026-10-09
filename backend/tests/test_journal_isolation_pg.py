"""One customer's trading journal must not be readable, listable or writable
by another.

journal_entries had NO ownership column — not organization_id, not even
user_id — and every route was a shared query. Listing returned everyone's
entries; fetching or updating by id worked on any row regardless of who asked.

These run against a real PostgreSQL because the thing under test is a
query predicate applied to rows that actually exist. A mocked session returns
whatever the test told it to, so a scoped query and an unscoped one look
identical through it — which is how an isolation bug survives a passing suite.
`idx` the migration adds, the FK, and the NULL-is-not-a-wildcard behaviour are
all properties of the database, not of Python.

Set OLBOS_TEST_DATABASE_URL to run these; CI provides one.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models.organization  # noqa: F401  (resolves the organizations FK target)
import app.models.trade         # noqa: F401  (resolves the trades FK target)
from app.models.journal_entry import JournalEntry

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]

ORG_A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
ORG_B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")


@pytest_asyncio.fixture
async def sessions():
    # NullPool: pooled asyncpg connections outlive a test's event loop and
    # the next test then finds a connection bound to a dead one.
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        # A minimal organizations table so the model's foreign key resolves.
        # The full organization/user graph is covered by
        # test_organization_service; what is under test here is the SCOPING.
        await conn.execute(text("DROP TABLE IF EXISTS journal_entries"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.execute(text(
            "CREATE TABLE organizations (id uuid PRIMARY KEY, "
            "personal_for_user_id uuid NULL)"
        ))
        # journal_entries.trade_id references it; SQLAlchemy resolves the whole
        # table's foreign keys when compiling even a count().
        await conn.execute(text("CREATE TABLE trades (id uuid PRIMARY KEY)"))
        for org in (ORG_A, ORG_B):
            await conn.execute(
                text("INSERT INTO organizations (id) VALUES (:i)"), {"i": org}
            )
        await conn.execute(text("""
            CREATE TABLE journal_entries (
                id uuid PRIMARY KEY,
                organization_id uuid NULL REFERENCES organizations(id) ON DELETE CASCADE,
                trade_id uuid NULL REFERENCES trades(id) ON DELETE SET NULL,
                pre_trade_thesis text,
                confidence_level int,
                market_context text,
                post_trade_notes text,
                followed_rules boolean,
                exit_felt_right boolean,
                tags jsonb,
                loss_category varchar(40),
                mistake_tags jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    # journal.py does `from app.core.database import AsyncSessionLocal` at
    # import time, so its binding is captured once. Patching only the source
    # module works or not depending on whether journal.py happened to be
    # imported first — which made these tests pass alone and fail in a full
    # run. Patch the binding the route actually uses.
    with patch("app.api.routes.journal.AsyncSessionLocal", factory), \
         patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS journal_entries"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
    await engine.dispose()


def _conn(user_id=None):
    """HTTPConnection stand-in; state.user is what current_user reads.

    These tests call the route functions directly, so FastAPI never resolves
    defaults — Query(...) params must be passed explicitly or they arrive as
    Query objects.
    """
    return NS(state=NS(user={"id": str(user_id)} if user_id else {}))


async def _seed(factory, org_id, thesis) -> uuid.UUID:
    eid = uuid.uuid4()
    async with factory() as s, s.begin():
        s.add(JournalEntry(id=eid, organization_id=org_id,
                           pre_trade_thesis=thesis, confidence_level=3))
    return eid


def _as_org(org_id):
    """Pin owner_scope to one organization, bypassing user→org resolution.

    The mapping from user to personal organization is covered by
    test_organization_service; what matters here is that whatever scope comes
    back is actually APPLIED to the query.
    """
    async def _scope(session, conn):
        return org_id
    return patch("app.api.routes.journal.owner_scope", _scope)


# ── list ───────────────────────────────────────────────────────────────────

async def test_listing_returns_only_your_own_entries(sessions):
    from app.api.routes.journal import list_entries
    await _seed(sessions, ORG_A, "mine")
    await _seed(sessions, ORG_B, "theirs")

    with _as_org(ORG_A):
        out = await list_entries(_conn(ORG_A), limit=50, offset=0)

    assert out.get("error") is None, f"list_entries swallowed an error: {out.get('error')}"
    theses = [e["pre_trade_thesis"] for e in out["entries"]]
    assert theses == ["mine"]
    assert out["total"] == 1, "the count leaked the other organization's rows"


async def test_an_organization_with_no_entries_sees_none(sessions):
    from app.api.routes.journal import list_entries
    await _seed(sessions, ORG_B, "theirs")

    with _as_org(ORG_A):
        out = await list_entries(_conn(ORG_A), limit=50, offset=0)

    assert out["entries"] == [] and out["total"] == 0


# ── detail: cross-organization ids are indistinguishable from missing ones ──

async def test_fetching_another_organizations_entry_is_404_not_403(sessions):
    from fastapi import HTTPException
    from app.api.routes.journal import get_entry
    theirs = await _seed(sessions, ORG_B, "theirs")

    with _as_org(ORG_A):
        with pytest.raises(HTTPException) as caught:
            await get_entry(str(theirs), _conn(ORG_A))

    assert caught.value.status_code == 404, (
        "403 confirms the row exists — the id must answer exactly as a "
        "nonexistent one does"
    )


async def test_a_missing_id_and_a_foreign_id_answer_identically(sessions):
    from fastapi import HTTPException
    from app.api.routes.journal import get_entry
    theirs = await _seed(sessions, ORG_B, "theirs")

    with _as_org(ORG_A):
        with pytest.raises(HTTPException) as foreign:
            await get_entry(str(theirs), _conn(ORG_A))
        with pytest.raises(HTTPException) as absent:
            await get_entry(str(uuid.uuid4()), _conn(ORG_A))

    assert foreign.value.status_code == absent.value.status_code
    assert foreign.value.detail == absent.value.detail


async def test_you_can_fetch_your_own_entry(sessions):
    from app.api.routes.journal import get_entry
    mine = await _seed(sessions, ORG_A, "mine")

    with _as_org(ORG_A):
        out = await get_entry(str(mine), _conn(ORG_A))

    assert out["entry"]["pre_trade_thesis"] == "mine"


# ── write ──────────────────────────────────────────────────────────────────

async def test_updating_another_organizations_entry_is_refused_and_changes_nothing(sessions):
    from fastapi import HTTPException
    from app.api.routes.journal import update_entry, JournalEntryPatch
    theirs = await _seed(sessions, ORG_B, "theirs")

    with _as_org(ORG_A):
        with pytest.raises(HTTPException) as caught:
            await update_entry(str(theirs), JournalEntryPatch(post_trade_notes="pwned"),
                               _conn(ORG_A))
    assert caught.value.status_code == 404

    async with sessions() as s:
        row = (await s.execute(
            select(JournalEntry).where(JournalEntry.id == theirs)
        )).scalar_one()
    assert row.post_trade_notes is None, "a refused write still mutated the row"


async def test_a_created_entry_is_stamped_with_the_callers_organization(sessions):
    from app.api.routes.journal import create_entry, JournalEntryIn
    with _as_org(ORG_A):
        out = await create_entry(
            JournalEntryIn(pre_trade_thesis="t", confidence_level=3, market_context="c"),
            _conn(ORG_A),
        )

    async with sessions() as s:
        row = (await s.execute(
            select(JournalEntry).where(JournalEntry.id == uuid.UUID(out["id"]))
        )).scalar_one()
    assert row.organization_id == ORG_A


# ── analytics must not aggregate across tenants ────────────────────────────

async def test_analytics_do_not_aggregate_across_organizations(sessions):
    from app.api.routes.journal import get_mistake_frequency
    async with sessions() as s, s.begin():
        s.add(JournalEntry(id=uuid.uuid4(), organization_id=ORG_B,
                           pre_trade_thesis="theirs", confidence_level=3,
                           mistake_tags=["revenge_trade"]))

    with _as_org(ORG_A):
        out = await get_mistake_frequency(_conn(ORG_A))

    assert not out.get("mistakes"), (
        "another organization's mistakes appeared in this one's analytics"
    )


# ── NULL ownership is a scope, not a wildcard ──────────────────────────────

async def test_unattributed_rows_are_invisible_to_an_organization(sessions):
    """Migration 0039 leaves pre-ownership rows NULL rather than guessing an
    owner. They must fail closed, not match everyone."""
    from app.api.routes.journal import list_entries
    await _seed(sessions, None, "operator era")

    with _as_org(ORG_A):
        out = await list_entries(_conn(ORG_A), limit=50, offset=0)

    assert out["entries"] == [] and out["total"] == 0


async def test_single_operator_mode_sees_exactly_the_unattributed_rows(sessions):
    """auth disabled → scope None → the operator's own history, and nothing
    belonging to an organization."""
    from app.api.routes.journal import list_entries
    await _seed(sessions, None, "operator era")
    await _seed(sessions, ORG_A, "a customer's")

    with _as_org(None):
        out = await list_entries(_conn(), limit=50, offset=0)

    assert [e["pre_trade_thesis"] for e in out["entries"]] == ["operator era"]


async def test_a_missing_identity_with_auth_on_is_401_not_a_default_scope(sessions):
    """Fail closed. There is no safe scope to fall back to when auth is on and
    the request carries no identity."""
    from fastapi import HTTPException
    from app.api.routes.journal import list_entries

    async def _boom(session, conn):
        raise PermissionError("no authenticated user")

    with patch("app.api.routes.journal.owner_scope", _boom):
        with pytest.raises(HTTPException) as caught:
            await list_entries(_conn(), limit=50, offset=0)
    assert caught.value.status_code == 401
