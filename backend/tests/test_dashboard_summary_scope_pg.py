"""`/api/dashboard/summary` reports the caller's P&L, not everyone's.

The two sums in `dashboard_summary` read the `trades` table directly from
`app/main.py` and had no ownership filter at all — they totalled every
organization's closed trades and returned the figure to an endpoint that
also required no authentication.

The symptom was visible on the production dashboard: the headline "Olbos
total P&L" read a non-zero loss while the closed-trade chart directly
beneath it — which reads the *scoped* `/api/paper-trade/history` — said "No
closed trades in this range" with ALL selected. Two queries answering
different questions, stacked and presented as one story.

Nothing caught it because `test_trade_scope_guard` scans `app/api/routes`
and `app/services`; this query lives in `app/main.py`, outside both roots.
That blind spot is closed separately in that guard. This file pins the
behaviour.

── Why real PostgreSQL ───────────────────────────────────────────────────

Same reason `test_trades_route_isolation_pg` gives: what is under test is a
predicate applied to rows that exist. Against a mocked session a scoped
query and an unscoped one are indistinguishable — both return whatever the
mock was told to return, so this suite would pass with the filter deleted,
which is precisely the bug it exists to catch.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.trade import Trade

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]

ORG_A = uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000a1")
ORG_B = uuid.UUID("bbbbbbbb-0000-4000-8000-0000000000b1")

#: `dashboard_summary` imports AsyncSessionLocal inside the function body,
#: so the one core binding is genuinely enough here — unlike the route
#: modules, which bind it at import time and need patching individually.
#: Verified: dropping this patch makes these tests read the real database.
_SESSION_BINDINGS = (
    "app.core.database.AsyncSessionLocal",
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await c.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
        await c.execute(text(
            "CREATE TABLE organizations (id uuid PRIMARY KEY, "
            "personal_for_user_id uuid NULL)"
        ))
        await c.run_sync(Trade.__table__.create)
        for org in (ORG_A, ORG_B):
            await c.execute(
                text("INSERT INTO organizations (id, personal_for_user_id) "
                     "VALUES (:i, :u)"),
                {"i": org, "u": uuid.uuid4()},
            )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with ExitStack() as stack:
        for target in _SESSION_BINDINGS:
            stack.enter_context(patch(target, factory))
        yield factory
    async with engine.begin() as c:
        await c.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await c.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
    await engine.dispose()


def _conn(user_id=None):
    return NS(state=NS(user={"id": str(user_id)} if user_id else {}))


def _as_org(org_id):
    """Pin the request scope; the user -> org mapping is tested elsewhere.

    What matters here is that whatever scope comes back is APPLIED.
    """
    async def _scope(session, conn):
        return org_id
    return patch("app.services.trade_scope.request_scope", _scope)


async def _seed(factory, org_id, pnl, *, days_ago=3, ticker="AAA"):
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s, s.begin():
        s.add(Trade(
            id=uuid.uuid4(), organization_id=org_id,
            strategy="equity_momentum", underlying=ticker,
            spread_type="equity_long",
            entry_date=now - dt.timedelta(days=days_ago + 1),
            exit_date=now - dt.timedelta(days=days_ago),
            short_strike=450, long_strike=445,
            expiration=dt.date(2026, 12, 19),
            status="closed", pnl=pnl, quantity=10,
        ))


async def _summary(org_id):
    """Call the endpoint with the broker stubbed out.

    The broker half is irrelevant here and would otherwise try to reach a
    gateway; `total_equity` is deliberately NOT scoped, so it is not what
    this file asserts on.
    """
    from app.main import dashboard_summary
    with _as_org(org_id), patch("app.broker.broker_factory.get_broker",
                                side_effect=RuntimeError("no broker in tests")):
        return await dashboard_summary(_conn(uuid.uuid4()))


async def test_total_pnl_counts_only_the_callers_trades(db):
    await _seed(db, ORG_A, -500.0)
    await _seed(db, ORG_B, +9000.0)

    a = await _summary(ORG_A)
    assert a["total_pnl"] == -500.0, a

    b = await _summary(ORG_B)
    assert b["total_pnl"] == 9000.0, b


async def test_day_pnl_counts_only_the_callers_trades(db):
    await _seed(db, ORG_A, -120.0, days_ago=0)
    await _seed(db, ORG_B, +4000.0, days_ago=0)
    # An older row of the caller's own, to prove the day filter still applies
    # on top of the ownership filter rather than replacing it.
    await _seed(db, ORG_A, -999.0, days_ago=30)

    a = await _summary(ORG_A)
    assert a["day_pnl"] == -120.0, a
    assert a["total_pnl"] == -1119.0, a


async def test_unattributed_rows_are_not_everybodys(db):
    """`owned_by(None)` is the single-operator scope, not a wildcard.

    Rows migration 0041 declined to guess an owner for belong to the
    single-operator scope alone. An organization must not inherit them, and
    the single-operator caller must not inherit the organizations'.
    """
    await _seed(db, None, -77.0)
    await _seed(db, ORG_A, -11.0)

    assert (await _summary(None))["total_pnl"] == -77.0
    assert (await _summary(ORG_A))["total_pnl"] == -11.0


async def test_an_empty_scope_reports_zero_rather_than_the_desk_total(db):
    """The regression this file exists for.

    A caller with no trades of their own must see 0.00, not the sum of
    everyone else's. Before the fix this returned -8500.0 — the figure that
    appeared on the dashboard above an empty chart.
    """
    await _seed(db, ORG_B, -8500.0)

    empty = await _summary(ORG_A)
    assert empty["total_pnl"] == 0.0, empty
    assert empty["day_pnl"] == 0.0, empty
