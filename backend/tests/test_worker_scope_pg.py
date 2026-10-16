"""Background workers process one owner's book at a time, against that owner's broker.

Batch E2 steps 4 and 6. Real PostgreSQL, for the reason the other isolation
suites give: the thing under test is a predicate applied to rows that exist,
and against a mocked session a per-owner loop and the unscoped loop it
replaces return exactly the same thing.

These are the highest-consequence tests in Batch E2, because the workers here
decide what the system believes it holds:

* the **fills poller** decides whether a position is still open. Unscoped, a
  position absent from tenant A's broker — absent because it was never there —
  closes tenant B's trade, and the close writes a P&L.
* the **reconciler** decides whether the broker and the database agree.
  Unscoped, tenant B's real positions come back as `phantom_in_db` and tenant
  A's as `untracked_at_broker`, and either can drive an adopt.
* the **claim sweep** releases a claim when the broker says the order is
  ABSENT. A broker never asked about a claim says ABSENT about it, so an
  unscoped sweep hands an in-flight position back to the duplicate guard as
  free.

Each of those is asserted below with two owners' rows present at once.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.position_claim import PositionClaim
from app.models.trade import Trade

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]

ORG_A = uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000c1")
ORG_B = uuid.UUID("bbbbbbbb-0000-4000-8000-0000000000c2")

_SESSION_BINDINGS = (
    "app.core.database.AsyncSessionLocal",
    "app.services.position_reconciler.AsyncSessionLocal",
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS broker_connections CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
        await conn.execute(text(
            "CREATE TABLE organizations (id uuid PRIMARY KEY, "
            "personal_for_user_id uuid NULL)"
        ))
        await conn.run_sync(Trade.__table__.create)
        await conn.run_sync(PositionClaim.__table__.create)
        # broker_for_scope asks this table whether an organization has a
        # connection of its own. Created empty on purpose: "no connection"
        # is the case that must NOT fall back to the process broker.
        #
        # Raw DDL, not the model: `organizations` above is created with raw
        # SQL so it is not in SQLAlchemy's metadata, and the model's foreign
        # key cannot be compiled against a table it cannot see.
        await conn.execute(text("""
            CREATE TABLE broker_connections (
                id uuid PRIMARY KEY,
                organization_id uuid NOT NULL,
                created_by_user_id uuid NULL,
                broker varchar(20) NOT NULL,
                environment varchar(10) NOT NULL,
                label varchar(80) NULL,
                api_key_enc bytea NULL,
                secret_key_enc bytea NULL,
                key_last4 varchar(8) NULL,
                status varchar(12) NOT NULL,
                version integer NOT NULL DEFAULT 1,
                created_at timestamptz NOT NULL DEFAULT now(),
                last_verified_at timestamptz NULL,
                revoked_at timestamptz NULL
            )
        """))
        for org in (ORG_A, ORG_B):
            await conn.execute(
                text("INSERT INTO organizations (id, personal_for_user_id) "
                     "VALUES (:i, :u)"), {"i": org, "u": uuid.uuid4()},
            )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    # Module-level state in main.py; a leftover entry would make an unrelated
    # test book a close.
    from app import main as _m
    _m._close_pending.clear()
    with ExitStack() as stack:
        for target in _SESSION_BINDINGS:
            stack.enter_context(patch(target, factory))
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS broker_connections CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
    await engine.dispose()


async def _seed(factory, org_id, ticker, *, status="open", qty=10):
    tid = uuid.uuid4()
    async with factory() as s, s.begin():
        s.add(Trade(
            id=tid, organization_id=org_id, strategy="bull_put_spread",
            underlying=ticker, spread_type="equity_long",
            short_strike=450, long_strike=445,
            expiration=dt.date(2026, 12, 19),
            entry_date=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2),
            status=status, quantity=qty,
        ))
    return tid


def _position(symbol, qty=10, avg_cost=100.0):
    return MagicMock(underlying=symbol, symbol=symbol, quantity=qty, avg_cost=avg_cost)


def _broker(positions=()):
    b = MagicMock()
    b.get_positions = AsyncMock(return_value=list(positions))
    b.get_equity_positions = AsyncMock(return_value=list(positions))
    return b


def _scoped(org_id, broker, source="test"):
    from app.services.worker_scope import ScopedWork

    return ScopedWork(organization_id=org_id, broker=broker, broker_source=source)


# ══════════════════════════════════════════════════════════════════════════
# 1. Which scopes a sweep visits
# ══════════════════════════════════════════════════════════════════════════

async def test_only_owners_with_open_trades_are_visited(db):
    """An owner with no position costs a broker round trip to compare two
    empty sets, so the iteration is driven by the trades."""
    from app.services.worker_scope import scopes_with_open_trades

    await _seed(db, ORG_A, "AAA")
    async with db() as s:
        scopes = await scopes_with_open_trades(s)

    assert scopes == [ORG_A], f"visited {scopes}"


async def test_the_single_operator_scope_is_visited_when_unattributed_rows_exist(db):
    from app.services.worker_scope import scopes_with_open_trades

    await _seed(db, None, "ORPHAN")
    await _seed(db, ORG_A, "AAA")
    async with db() as s:
        scopes = await scopes_with_open_trades(s)

    assert None in scopes, "the unattributed rows would never be swept"
    assert set(scopes) == {None, ORG_A}


async def test_a_closed_only_owner_is_not_visited(db):
    from app.services.worker_scope import scopes_with_open_trades

    await _seed(db, ORG_B, "BBB", status="closed")
    async with db() as s:
        scopes = await scopes_with_open_trades(s)

    assert scopes == [], f"visited {scopes} with nothing open"


# ══════════════════════════════════════════════════════════════════════════
# 2. Which broker a scope gets — and when it gets none
# ══════════════════════════════════════════════════════════════════════════

async def test_the_single_operator_scope_uses_the_process_broker(db):
    from app.services.worker_scope import broker_for_scope

    sentinel = _broker()
    with patch("app.broker.broker_factory.get_broker", return_value=sentinel):
        async with db() as s:
            work = await broker_for_scope(s, None)

    assert work.broker is sentinel
    assert work.broker_source == "process"


async def test_an_organization_without_a_connection_is_unresolved_not_defaulted(db):
    """THE bug step 6 exists to prevent.

    Falling back to the process singleton here would compare tenant A's
    broker against tenant B's trades. `BrokerUnresolved` makes that
    impossible to reach by accident.
    """
    from app.services.worker_scope import BrokerUnresolved, broker_for_scope

    process = _broker([_position("SOMEONE_ELSES")])
    with patch("app.broker.broker_factory.get_broker", return_value=process):
        async with db() as s:
            with pytest.raises(BrokerUnresolved):
                await broker_for_scope(s, ORG_A)


async def test_an_organization_with_a_connection_gets_its_own_client(db):
    from app.services.worker_scope import broker_for_scope

    their_client = _broker()
    scoped_result = MagicMock(client=their_client, connection_id="conn-123")
    process = _broker()
    with patch("app.broker.broker_factory.get_broker", return_value=process), \
         patch("app.broker.connection_scoped.scoped_alpaca_for",
               new=AsyncMock(return_value=scoped_result)):
        async with db() as s:
            work = await broker_for_scope(s, ORG_A)

    assert work.broker is their_client, "got the operator's broker, not theirs"
    assert work.broker is not process
    assert work.broker_source == "conn-123"


# ══════════════════════════════════════════════════════════════════════════
# 3. One owner's failure does not end the sweep
# ══════════════════════════════════════════════════════════════════════════

async def test_an_unresolvable_owner_is_skipped_and_the_others_still_run(db):
    from app.services.worker_scope import BrokerUnresolved, for_each_scope

    await _seed(db, None, "ORPHAN")
    await _seed(db, ORG_A, "AAA")
    seen = []

    async def _work(scoped):
        seen.append(scoped.organization_id)

    async def _resolve(session, org_id, **kw):
        if org_id is None:
            return _scoped(None, _broker())
        raise BrokerUnresolved("no connection")

    with patch("app.services.worker_scope.broker_for_scope", new=_resolve):
        summary = await for_each_scope(db, _work, job="t")

    assert seen == [None], f"ran for {seen}"
    assert summary["skipped"] == 1 and summary["ok"] == 1
    assert summary["failed"] == 0, "a missing connection is not a failure"


async def test_one_owners_exception_does_not_stop_the_next(db):
    from app.services.worker_scope import for_each_scope

    await _seed(db, None, "ORPHAN")
    await _seed(db, ORG_A, "AAA")
    ran = []

    async def _work(scoped):
        ran.append(scoped.organization_id)
        if scoped.organization_id is None:
            raise RuntimeError("broker exploded")

    async def _resolve(session, org_id, **kw):
        return _scoped(org_id, _broker())

    with patch("app.services.worker_scope.broker_for_scope", new=_resolve):
        summary = await for_each_scope(db, _work, job="t")

    assert ran == [None, ORG_A], "the sweep stopped at the first failure"
    assert summary["failed"] == 1 and summary["ok"] == 1
    assert summary["errors"] and "broker exploded" in summary["errors"][0]


# ══════════════════════════════════════════════════════════════════════════
# 4. The reconciler compares within one scope only (step 6)
# ══════════════════════════════════════════════════════════════════════════

async def test_another_owners_trades_are_not_reported_as_phantom(db):
    """Unscoped, tenant B's real position is 'open in the DB, absent at the
    broker' — which is what drives a phantom warning, and can drive a halt."""
    from app.services.position_reconciler import PositionReconciler

    await _seed(db, ORG_A, "AAA")
    await _seed(db, ORG_B, "BBB")

    res = await PositionReconciler(
        _broker([_position("AAA")]), organization_id=ORG_A,
    ).check()

    assert res.phantom_in_db == [], f"phantom_in_db={res.phantom_in_db}"
    assert res.untracked_at_broker == []
    assert res.clean, f"warnings={res.warnings}"
    assert res.db_open_trade_count == 1, "counted another owner's book"


async def test_another_owners_broker_position_is_not_reported_as_untracked(db):
    """The mirror: A's broker holds AAA, and B — who does not — must not see
    it as an untracked position at their own broker."""
    from app.services.position_reconciler import PositionReconciler

    await _seed(db, ORG_B, "BBB")

    res = await PositionReconciler(
        _broker([_position("BBB")]), organization_id=ORG_B,
    ).check()

    assert res.untracked_at_broker == []
    assert res.clean


async def test_an_unscoped_reconciler_cannot_be_constructed():
    """The type signature, not a convention: there is no default scope."""
    from app.services.position_reconciler import PositionReconciler

    with pytest.raises(TypeError):
        PositionReconciler(_broker())        # no organization_id


# ══════════════════════════════════════════════════════════════════════════
# 5. The adopt path stamps the owner whose broker reported the position
# ══════════════════════════════════════════════════════════════════════════

async def test_an_adopted_position_is_stamped_with_that_brokers_owner(db):
    """The second trade-writing site. It wrote NULL until the pairing existed."""
    from app.main import _adopt_untracked_positions

    with patch("app.services.observability.observability"):
        await _adopt_untracked_positions(
            ["NEWPOS"], scoped=_scoped(ORG_A, _broker([_position("NEWPOS", 5, 20.0)])),
        )

    async with db() as s:
        rows = (await s.execute(
            select(Trade).where(Trade.underlying == "NEWPOS")
        )).scalars().all()

    assert len(rows) == 1, f"{len(rows)} rows written"
    assert rows[0].organization_id == ORG_A, (
        f"adopted position stamped {rows[0].organization_id}, not the owner "
        f"of the broker that reported it"
    )
    assert rows[0].approved_by == "reconciler_adopt"


async def test_an_adopt_is_not_skipped_because_another_owner_holds_the_ticker(db):
    """The pre-insert race guard is scoped too: another owner's open trade on
    the same ticker is not a reason to leave this owner's position untracked."""
    from app.main import _adopt_untracked_positions

    await _seed(db, ORG_B, "SHARED")

    with patch("app.services.observability.observability"):
        await _adopt_untracked_positions(
            ["SHARED"], scoped=_scoped(ORG_A, _broker([_position("SHARED", 5, 20.0)])),
        )

    async with db() as s:
        rows = (await s.execute(
            select(Trade).where(Trade.underlying == "SHARED")
        )).scalars().all()

    owners = {r.organization_id for r in rows}
    assert owners == {ORG_A, ORG_B}, (
        f"owners={owners} — ORG_A's untracked position was skipped because "
        f"ORG_B held the same ticker"
    )


# ══════════════════════════════════════════════════════════════════════════
# 6. The fills poller closes only the book its broker is authoritative for
# ══════════════════════════════════════════════════════════════════════════

async def test_a_position_absent_from_one_brokers_list_does_not_close_another_owners_trade(db):
    """The worst unscoped-worker failure: a fabricated exit on someone else's
    trade, with a P&L written for it.

    The grace window has to be pre-expired for this to test anything. A trade
    missing from the broker is not closed on the first sighting — it goes into
    `_close_pending` and is only booked after CLOSE_GRACE_SECONDS. An earlier
    version of this test polled once and asserted no close, which passed with
    the scoping removed: the close had not come due yet, so it proved nothing.
    Seeding `_close_pending` past the window makes the unscoped read actually
    book the close, which is what must not happen.
    """
    from app import main as m
    from app.main import _poll_fills_for_scope
    from app.services.trade_recorder import trade_recorder

    await _seed(db, ORG_A, "AAA")
    bbb_id = await _seed(db, ORG_B, "BBB")

    m._close_pending[str(bbb_id)] = (
        dt.datetime.now(dt.timezone.utc)
        - dt.timedelta(seconds=m.CLOSE_GRACE_SECONDS + 60)
    )

    # ORG_A's broker holds AAA and knows nothing of BBB.
    with patch.object(trade_recorder, "confirm_fill", new=AsyncMock()), \
         patch.object(trade_recorder, "cancel_pending", new=AsyncMock()), \
         patch.object(trade_recorder, "update_excursion", new=AsyncMock()), \
         patch.object(trade_recorder, "record_exit", new=AsyncMock()) as exit_, \
         patch.object(trade_recorder, "record_close_unknown",
                      new=AsyncMock()) as unknown:
        await _poll_fills_for_scope(_scoped(ORG_A, _broker([_position("AAA")])))

    # Either path would be a fabricated exit on a trade this broker was never
    # authoritative for — record_exit writes a P&L, record_close_unknown
    # closes it with none. Neither is ORG_A's sweep's to do.
    assert exit_.await_count == 0 and unknown.await_count == 0, (
        f"ORG_A's sweep closed another owner's trade: record_exit="
        f"{exit_.await_count} record_close_unknown={unknown.await_count}"
    )

    async with db() as s2:
        bbb = (await s2.execute(
            select(Trade).where(Trade.underlying == "BBB")
        )).scalar_one()
    assert bbb.status == "open", "ORG_B's trade was closed by ORG_A's sweep"


# ══════════════════════════════════════════════════════════════════════════
# 7. The claim sweep (step 6, the #102 interaction)
# ══════════════════════════════════════════════════════════════════════════

async def test_one_owners_broker_does_not_release_another_owners_claim(db):
    """ABSENT past the settle window releases a claim, and a broker that was
    never asked about a claim says ABSENT. Scoping is what stops the release."""
    from app.models.position_claim import PositionClaim as PC
    from app.services.position_claim import BrokerVerdict, reconcile_unresolved

    async with db() as s, s.begin():
        s.add(PC(
            claim_token=str(uuid.uuid4()), scope=str(ORG_B),
            underlying="BBB", asset_class="options", state="submitted",
            idempotency_key=f"idem-{uuid.uuid4().hex[:8]}",
            lease_expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            claimed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
            submitted_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
        ))

    async def _absent(_claim):
        return BrokerVerdict.ABSENT

    # ORG_A's sweep, with a broker that answers ABSENT for everything.
    counts = await reconcile_unresolved(_absent, scope=str(ORG_A))

    async with db() as s:
        remaining = (await s.execute(select(PC))).scalars().all()

    assert counts["released"] == 0, "released a claim from another owner's scope"
    assert len(remaining) == 1, "ORG_B's in-flight claim was deleted by ORG_A's sweep"


async def test_a_sweep_does_release_its_own_settled_absent_claim(db):
    """The control: scoping must not have broken the release it exists for."""
    from app.models.position_claim import PositionClaim as PC
    from app.services.position_claim import BrokerVerdict, reconcile_unresolved

    async with db() as s, s.begin():
        s.add(PC(
            claim_token=str(uuid.uuid4()), scope=str(ORG_A),
            underlying="AAA", asset_class="options", state="submitted",
            idempotency_key=f"idem-{uuid.uuid4().hex[:8]}",
            lease_expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            claimed_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
            submitted_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
        ))

    async def _absent(_claim):
        return BrokerVerdict.ABSENT

    counts = await reconcile_unresolved(_absent, scope=str(ORG_A))

    assert counts["released"] == 1, (
        f"counts={counts} — the scoped sweep no longer releases its own "
        f"settled, absent claim, which is the whole point of the sweep"
    )


# ══════════════════════════════════════════════════════════════════════════
# 8. The workers that are deliberately NOT scoped
# ══════════════════════════════════════════════════════════════════════════

def test_global_workers_are_not_scoped():
    """Market data belongs to no tenant, and that is a decision, not an omission.

    `sector_cache` derives sector membership and `rotation_correlation_cache`
    derives return correlations. Both are properties of the market. Scoping
    them would mean N identical computations and N× the upstream fetches for
    identical results.

    Asserted rather than left to the reader, per CLAUDE.md's guidance on guard
    tests — and asserted by reading the source, because these take no scope
    argument to inspect at runtime.
    """
    import inspect

    from app.services import rotation_correlation_cache, sector_cache

    for mod in (sector_cache, rotation_correlation_cache):
        src = inspect.getsource(mod)
        assert "owned_by" not in src, (
            f"{mod.__name__} now filters by owner. If that is deliberate, it "
            f"is no longer a global worker and this test should move it to "
            f"the scoped list."
        )
        assert "worker_scope" not in src, (
            f"{mod.__name__} now resolves a per-owner scope; see above."
        )


def test_the_order_submitting_workers_still_use_the_operators_own_broker():
    """A boundary, asserted so it cannot drift silently.

    `stop_backfill` PLACES stop orders and `orphan_order_sweep` CANCELS
    orders. Pointing either at a customer's connection would mean a timer
    submitting and cancelling orders in that customer's account, which the
    open operating-model ADR does not permit. Reconciliation is read-only and
    is per-owner; these two are not, and stay on the operator's own broker
    until that decision is made explicitly.

    If this test is deliberately inverted, the ADR is what has to change with
    it — not just this file.
    """
    import inspect

    from app import main

    for name in ("_backfill_equity_stops", "_sweep_orphaned_orders"):
        src = inspect.getsource(getattr(main, name))
        assert "for_each_scope" not in src, (
            f"{name} now runs per owner. It submits or cancels orders, so "
            f"read this test's docstring before changing it."
        )
        assert "get_broker()" in src, (
            f"{name} no longer uses the operator's own broker explicitly"
        )
