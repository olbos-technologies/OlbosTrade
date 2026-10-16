"""Entry is scoped to an owner, and an entry with no establishable owner is refused.

Batch E2 steps 1, 2 and 5. Real PostgreSQL throughout: what is under test is a
predicate applied to rows that exist, and a mocked session makes a scoped query
and an unscoped one look identical — the same reason the journal isolation
tests use a real database.

Three properties, in rising order of how badly getting them wrong would hurt:

1. **One tenant's position does not block another's entry.** Before this, the
   duplicate guard and the position claim were both global, so tenant A
   holding SPY options refused tenant B's SPY entry with `entry_in_flight`.
   Over-blocking rather than a safety hole — and a cross-tenant information
   channel.

2. **A trade is stamped with the caller's organization.** The write path is
   two sites; this covers the one that runs on every entry.

3. **An entry whose owner cannot be established is refused, not guessed.**
   `handle_signal` calls `_execute_signal` from the background scanner in
   AUTOPILOT mode with no request, so there is nobody to ask. With auth on and
   two organizations, there is no non-guessing answer, and a guess would put
   one customer's capital behind another's signal.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.api.routes.trade_desk as td
from app.api.routes.trade_desk import _execute_signal
from app.models.position_claim import PositionClaim
from app.models.trade import Trade
from app.services import trade_scope

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]

ORG_A = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
ORG_B = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")


@pytest.fixture(autouse=True)
def _market_open():
    """Pin the market open; otherwise these tests tell the time."""
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS journal_entries"))
        await conn.execute(text("DROP TABLE IF EXISTS strategy_snapshots CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS strategy_presets CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
        await conn.execute(text(
            "CREATE TABLE organizations (id uuid PRIMARY KEY, "
            "personal_for_user_id uuid NULL)"
        ))
        await conn.run_sync(Trade.__table__.create)
        await conn.run_sync(PositionClaim.__table__.create)
        # record_fill also seeds a journal entry and reads the preset table.
        # Raw DDL, not the model: organizations is created with raw SQL above,
        # so it is not in SQLAlchemy's metadata and the model's foreign key
        # cannot be compiled against it.
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
        from app.models.strategy_preset import StrategyPreset
        from app.models.strategy_snapshot import StrategySnapshot
        await conn.run_sync(StrategyPreset.__table__.create)
        await conn.run_sync(StrategySnapshot.__table__.create)
        for org in (ORG_A, ORG_B):
            await conn.execute(
                text("INSERT INTO organizations (id, personal_for_user_id) "
                     "VALUES (:i, :u)"),
                {"i": org, "u": uuid.uuid4()},
            )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS journal_entries"))
        await conn.execute(text("DROP TABLE IF EXISTS strategy_snapshots CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS strategy_presets CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
    await engine.dispose()


def _as_org(org_id):
    """Pin the request scope. The user→organization mapping is covered by
    test_organization_service; what matters here is that the scope is APPLIED."""
    async def _scope(session, conn):
        return org_id
    return patch("app.services.trade_scope.request_scope", _scope)


def _signal(ticker="SPY"):
    return {
        "id": f"sig-{uuid.uuid4().hex[:8]}",
        "ticker": ticker,
        "asset_type": "options",
        "strategy": "bull_put_spread",
        "action": "SELL",
        "quantity": 1,
        "short_strike": 450.0,
        "long_strike": 445.0,
        "expiration": "2030-01-18",
        "option_type": "put",
        "credit": 1.0,
        # The frequency controller (stage 1c) reads `confidence` and blocks a
        # non-manual entry below the mode's floor — before the ownership stage
        # this file is about.
        "confidence": 90,
        "score": 0.9,
        "dispatch_id": f"d-{uuid.uuid4().hex[:8]}",
    }


def _clean_portfolio():
    from app.services.guardrails import PortfolioState
    return PortfolioState(current_value=100_000.0, starting_capital=100_000.0,
                          daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
                          consecutive_losses=0, trades_today=0)


async def _run_submitted(_priority, fn, **_kw):
    """Coordinator stub that actually invokes the work.

    ibkr_coordinator.submit also carries the account fetch, so counting its
    awaits does not count orders — the broker method must really be called.
    """
    return await fn() if callable(fn) else fn


def _broker():
    b = MagicMock()
    b.place_order = AsyncMock(return_value=MagicMock(
        order_id="ORD-1", status="submitted", fill_price=None,
        filled_quantity=None, remaining_quantity=None, message=None))
    b.place_equity_order = AsyncMock()
    # The account-mode guard (stage 4) awaits this; a bare MagicMock attribute
    # is not awaitable and the entry blocks with account_unverified.
    b.get_account_summary = AsyncMock(return_value=MagicMock(
        buying_power=100000.0, net_liquidation=100000.0))
    return b


def _patches(broker):
    """Everything up to the ownership stage, neutralised.

    The frequency controller (stage 1c) and strategy health (1d) sit before
    the ownership stage and block a non-manual entry on confidence and EV.
    They have their own tests; leaving them live here would mean every
    autopilot case in this file asserted on stage 1c's reason instead of the
    ownership decision it is about.
    """
    from app.services.trade_frequency_controller import GateDecision

    allow = GateDecision(allowed=True, reason="ok", weighted_score=0.9,
                         risk_score=10, expected_value=1.0)
    return (
        patch("app.services.trade_frequency_controller."
              "trade_frequency_controller.evaluate", return_value=allow),
        patch("app.api.routes.trade_desk._fetch_portfolio_state",
              new=AsyncMock(return_value=_clean_portfolio())),
        patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False),
        patch("app.broker.broker_factory.get_broker", return_value=broker),
        patch.object(td.ibkr_coordinator, "submit", new=_run_submitted),
        patch("app.services.trade_recorder.trade_recorder.record_fill",
              new=AsyncMock(return_value=str(uuid.uuid4()))),
    )


async def _seed_open_trade(factory, org_id, ticker="SPY"):
    import datetime as dt
    async with factory() as s, s.begin():
        s.add(Trade(
            id=uuid.uuid4(), organization_id=org_id, strategy="bull_put_spread",
            underlying=ticker, spread_type="put", short_strike=450, long_strike=445,
            expiration=dt.date(2026, 12, 19),
            entry_date=dt.datetime.now(dt.timezone.utc), status="open",
        ))


# ── 1. one tenant's position does not block another's entry ────────────────

async def test_another_organizations_open_position_does_not_block_entry(db):
    """The over-blocking this step removes."""
    await _seed_open_trade(db, ORG_A, "SPY")
    broker = _broker()

    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _as_org(ORG_B):
        res = await _execute_signal(_signal("SPY"), approved_by="user", conn=object())

    assert res["result"] not in ("skipped", "blocked"), res
    broker.place_order.assert_awaited_once()


async def test_your_own_open_position_still_blocks_entry(db):
    """And the guard still guards — the half that must not regress."""
    await _seed_open_trade(db, ORG_A, "SPY")
    broker = _broker()

    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _as_org(ORG_A):
        res = await _execute_signal(_signal("SPY"), approved_by="user", conn=object())

    assert res["result"] == "skipped"
    assert res["reason"] == "already_open"
    broker.place_order.assert_not_awaited()


async def test_an_unattributed_position_does_not_block_an_organization(db):
    """Migration 0041 leaves historical rows NULL; NULL is its own scope."""
    await _seed_open_trade(db, None, "SPY")
    broker = _broker()

    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _as_org(ORG_A):
        res = await _execute_signal(_signal("SPY"), approved_by="user", conn=object())

    assert res["result"] not in ("skipped", "blocked"), res


async def test_the_single_operator_scope_sees_exactly_the_unattributed_rows(db):
    """Auth disabled is a scope, not an absence of one."""
    await _seed_open_trade(db, None, "SPY")
    broker = _broker()

    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _as_org(None):
        res = await _execute_signal(_signal("SPY"), approved_by="user", conn=object())

    assert res["result"] == "skipped"
    assert res["reason"] == "already_open"


# ── 2. the claim is scoped too, and must agree with the read ───────────────

async def test_the_claim_is_taken_in_the_callers_scope(db):
    """The entry path passes the owner's scope to try_claim.

    Asserted by spying on the call rather than by reading the row afterwards.
    The row's lifetime depends on how the submission ends — a successful one
    resolves and deletes it — so an assertion on the table passes or fails
    depending on whether the broker stub's order "filled", which is not what
    this test is about. It failed exactly that way in a full run while passing
    in isolation.
    """
    from app.services import position_claim as pc

    seen = {}
    real = pc.try_claim

    async def _spy(underlying, asset_class, **kw):
        seen["scope"] = kw.get("scope")
        seen["underlying"] = underlying
        return await real(underlying, asset_class, **kw)

    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _as_org(ORG_A), \
         patch("app.services.position_claim.try_claim", new=_spy):
        await _execute_signal(_signal("SPY"), approved_by="user", conn=object())

    assert seen.get("underlying") == "SPY"
    assert seen.get("scope") == str(ORG_A), (
        f"the claim was taken in scope {seen.get('scope')!r}; globally scoped "
        f"it would block every other tenant's entry on this underlying"
    )


async def test_the_single_operator_scope_keeps_the_existing_global_value(db):
    """Claims written before Batch E2 carry "global" and must keep blocking.

    Inventing a new token for the single-operator scope would orphan them: a
    claim taken for a live position would stop matching, and the position it
    guards would be open to a second entry.
    """
    assert trade_scope.claim_scope(None) == "global"
    assert trade_scope.claim_scope(ORG_A) == str(ORG_A)


async def test_two_organizations_can_hold_a_claim_on_the_same_underlying(db):
    """The claim's primary key is (scope, underlying, asset_class).

    Globally scoped, the second of these would have been refused
    `entry_in_flight`.
    """
    from app.services import position_claim

    a = await position_claim.try_claim(
        "SPY", "options", scope=trade_scope.claim_scope(ORG_A))
    b = await position_claim.try_claim(
        "SPY", "options", scope=trade_scope.claim_scope(ORG_B))

    assert a is not None
    assert b is not None, "a second organization was refused a claim on its own position"

    # And within one scope it is still exclusive.
    again = await position_claim.try_claim(
        "SPY", "options", scope=trade_scope.claim_scope(ORG_A))
    assert again is None


# ── 3. an entry with no establishable owner is refused ─────────────────────

async def test_an_autonomous_entry_is_refused_when_the_owner_is_ambiguous(db):
    """Autopilot has no caller. Two organizations means no non-guessing answer.

    Refusing costs a missed automated entry. Guessing puts one customer's
    capital behind another customer's signal and shows them a position that is
    not theirs.
    """
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], \
         patch("app.core.config.settings.auth_enabled", True):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")

    assert res["result"] == "blocked"
    assert "owner_ambiguous" in res["reason"]
    broker.place_order.assert_not_awaited()


async def test_an_autonomous_entry_proceeds_when_exactly_one_owner_exists(db):
    """The unambiguous case — the same test migration 0041 applies."""
    async with db() as s, s.begin():
        await s.execute(text(
            "UPDATE organizations SET personal_for_user_id = NULL WHERE id = :i"
        ), {"i": ORG_B})

    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], \
         patch("app.core.config.settings.auth_enabled", True):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")

    assert res["result"] not in ("blocked",), res


async def test_an_autonomous_entry_in_single_operator_mode_uses_the_null_scope(db):
    """Auth disabled: one operator, nothing to isolate from."""
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], \
         patch("app.core.config.settings.auth_enabled", False):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")

    assert res["result"] not in ("blocked",), res


async def test_a_request_with_auth_on_and_no_identity_is_refused(db):
    """Not silently served the single-operator scope."""
    broker = _broker()

    async def _raises(session, conn):
        raise PermissionError("no authenticated user")

    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], \
         patch("app.services.trade_scope.request_scope", _raises):
        res = await _execute_signal(_signal("SPY"), approved_by="user", conn=object())

    assert res["result"] == "blocked"
    assert "owner_unauthenticated" in res["reason"]
    broker.place_order.assert_not_awaited()


# ── the write path ─────────────────────────────────────────────────────────

async def test_a_recorded_trade_is_stamped_with_the_callers_organization(db):
    """record_fill is the site every entry goes through."""
    import datetime as dt

    from app.services.trade_recorder import trade_recorder

    async with db() as s, s.begin():
        pass

    tid = await trade_recorder.record_fill(
        organization_id=ORG_B,
        strategy="bull_put_spread", underlying="QQQ", option_type="put",
        short_strike=400.0, long_strike=395.0, expiration=dt.date(2026, 12, 19),
        entry_credit=1.0, quantity=1, signal_score=0.8, iv_rank=0.5,
        regime="neutral", approved_by="user", dispatch_id=f"d-{uuid.uuid4().hex[:8]}",
    )
    assert tid is not None

    async with db() as s:
        row = (await s.execute(
            select(Trade).where(Trade.underlying == "QQQ")
        )).scalars().one()
    assert row.organization_id == ORG_B


async def test_record_fill_requires_an_owner(db):
    """No default, so a caller cannot forget and silently file it in one scope."""
    import datetime as dt
    import inspect

    from app.services.trade_recorder import trade_recorder

    sig = inspect.signature(trade_recorder.record_fill)
    param = sig.parameters["organization_id"]
    assert param.default is inspect.Parameter.empty, (
        "organization_id gained a default; every caller that forgets it would "
        "then file its trade in whatever scope that default names"
    )
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


# ── the rule that makes None safe ──────────────────────────────────────────

async def test_owned_by_none_matches_only_unattributed_rows(db):
    """Not every row. This is the mistake that would reinstate the shared query."""
    await _seed_open_trade(db, ORG_A, "AAA")
    await _seed_open_trade(db, None, "BBB")

    async with db() as s:
        null_scope = list((await s.execute(
            select(Trade).where(trade_scope.owned_by(None))
        )).scalars().all())
        org_scope = list((await s.execute(
            select(Trade).where(trade_scope.owned_by(ORG_A))
        )).scalars().all())

    assert [t.underlying for t in null_scope] == ["BBB"]
    assert [t.underlying for t in org_scope] == ["AAA"]
