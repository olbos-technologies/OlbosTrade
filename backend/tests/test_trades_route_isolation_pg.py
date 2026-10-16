"""A caller sees, and can close, only their own trades.

Batch E2 step 3. Real PostgreSQL throughout, for the reason the journal
isolation tests give: what is under test is a *predicate applied to rows that
exist*, and against a mocked session a scoped query and an unscoped one are
indistinguishable — both return whatever the mock was told to return. A suite
of these written against mocks would pass with every filter deleted.

So every test here seeds rows owned by two organizations plus a row owned by
nobody, and asserts on which ones come back.

What this file does NOT cover, deliberately:

* **The risk gate.** `trade_desk._fetch_portfolio_state` and
  `_portfolio_heat_fraction` stay desk-wide — the limits in TRADING_POLICY.md
  are per-account caps on one broker account, and dividing them per tenant
  would multiply the desk's real exposure by the tenant count. The *displays*
  of those numbers are scoped and tested here; the gate is not, and
  `test_the_risk_gate_is_still_desk_wide` pins that difference so it reads as
  a decision rather than an oversight.
* **Background workers.** Fill polling, excursion tracking, stop backfill and
  reconciliation still process every row. Those are steps 4 and 6, held back.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from contextlib import ExitStack
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
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

#: Every module that binds AsyncSessionLocal at import time. Patching only
#: `app.core.database.AsyncSessionLocal` leaves these pointing at the real
#: engine, and the test then reads the developer's own database — or passes
#: against an empty one, which is worse.
_SESSION_BINDINGS = (
    "app.core.database.AsyncSessionLocal",
    "app.api.routes.paper_trade.AsyncSessionLocal",
    "app.api.routes.risk.AsyncSessionLocal",
    "app.api.routes.analytics.AsyncSessionLocal",
    "app.api.routes.strategy.AsyncSessionLocal",
    "app.api.routes.journal.AsyncSessionLocal",
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS journal_entries"))
        await conn.execute(text("DROP TABLE IF EXISTS research_experiments CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
        await conn.execute(text(
            "CREATE TABLE organizations (id uuid PRIMARY KEY, "
            "personal_for_user_id uuid NULL)"
        ))
        await conn.run_sync(Trade.__table__.create)
        # strategy health merges promoted research baselines with live trade
        # outcomes; the baselines are shared configuration, not owned rows.
        from app.models.research_experiment import ResearchExperiment
        await conn.run_sync(ResearchExperiment.__table__.create)
        for org in (ORG_A, ORG_B):
            await conn.execute(
                text("INSERT INTO organizations (id, personal_for_user_id) "
                     "VALUES (:i, :u)"),
                {"i": org, "u": uuid.uuid4()},
            )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with ExitStack() as stack:
        for target in _SESSION_BINDINGS:
            stack.enter_context(patch(target, factory))
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS research_experiments CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
    await engine.dispose()


def _conn(user_id=None):
    """HTTPConnection stand-in. `state.user` is what `current_user` reads."""
    return NS(state=NS(user={"id": str(user_id)} if user_id else {}))


def _as_org(org_id):
    """Pin the request scope.

    The user -> organization mapping is covered by test_organization_service.
    What matters here is that whatever scope comes back is APPLIED.
    """
    async def _scope(session, conn):
        return org_id
    return patch("app.services.trade_scope.request_scope", _scope)


async def _seed(factory, org_id, ticker, *, status="open", pnl=None,
                exit_reason=None, strategy="bull_put_spread"):
    tid = uuid.uuid4()
    now = dt.datetime.now(dt.timezone.utc)
    async with factory() as s, s.begin():
        s.add(Trade(
            id=tid, organization_id=org_id, strategy=strategy,
            underlying=ticker, spread_type="equity_long",
            entry_date=now - dt.timedelta(days=3),
            exit_date=(now if status == "closed" else None),
            short_strike=450, long_strike=445,
            expiration=dt.date(2026, 12, 19),
            status=status, pnl=pnl, exit_reason=exit_reason,
            quantity=10,
        ))
    return tid


# ══════════════════════════════════════════════════════════════════════════
# 1. Listing reads return only the caller's own rows
# ══════════════════════════════════════════════════════════════════════════

async def test_open_positions_list_returns_only_your_own(db):
    await _seed(db, ORG_A, "AAA")
    await _seed(db, ORG_B, "BBB")
    from app.api.routes.paper_trade import get_positions

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.api.routes.paper_trade.get_broker", return_value=broker), \
         _as_org(ORG_A):
        out = await get_positions(_conn(ORG_A))

    tickers = {p.get("symbol") or p.get("underlying") for p in out["positions"]}
    assert "AAA" in tickers, "the caller's own position vanished"
    assert "BBB" not in tickers, "another organization's position was listed"


async def test_history_and_its_total_are_scoped_together(db):
    """A scoped page with an unscoped total is a pager that lies."""
    for t in ("A1", "A2"):
        await _seed(db, ORG_A, t, status="closed", pnl=10.0)
    for t in ("B1", "B2", "B3"):
        await _seed(db, ORG_B, t, status="closed", pnl=10.0)
    from app.api.routes.paper_trade import get_trade_history

    with _as_org(ORG_A):
        out = await get_trade_history(_conn(ORG_A), limit=50, offset=0, status=None)

    assert len(out["trades"]) == 2
    assert out["total"] == 2, (
        f"total={out['total']} counts rows the caller is not shown"
    )


async def test_portfolio_heat_excludes_other_tenants_positions(db):
    await _seed(db, ORG_A, "AAA")
    await _seed(db, ORG_B, "BBB")
    from app.api.routes.portfolio import portfolio_heat

    with patch("app.api.routes.portfolio.get_account_value",
               new=AsyncMock(return_value=100_000.0)), _as_org(ORG_A):
        out = await portfolio_heat(_conn(ORG_A))

    names = {p["underlying"] for p in out.get("positions", [])} if "positions" in out else None
    assert out.get("error") is None, out.get("error")
    # The engine's own shape varies; assert on what every shape carries.
    assert "BBB" not in str(out), "another organization's ticker reached the response"


async def test_scenarios_stress_only_your_own_book(db):
    await _seed(db, ORG_A, "AAA")
    await _seed(db, ORG_B, "BBB")
    from app.api.routes.risk import get_scenarios

    with patch("app.services.spot_price_cache.get_spot",
               new=AsyncMock(return_value=100.0)), _as_org(ORG_A):
        out = await get_scenarios(_conn(ORG_A))

    assert "BBB" not in str(out), "another organization's ticker was stressed"


async def test_rotation_performance_counts_only_your_own_rotations(db):
    await _seed(db, ORG_A, "AAA", status="closed", pnl=50.0,
                exit_reason="position_rotation")
    await _seed(db, ORG_B, "BBB", status="closed", pnl=99.0,
                exit_reason="position_rotation")
    from app.api.routes.portfolio import portfolio_rotation_performance

    with _as_org(ORG_A):
        out = await portfolio_rotation_performance(_conn(ORG_A))

    assert out["total"] == 1, f"counted {out['total']} rotations, expected 1"
    assert out["total_pnl"] == 50.0


# ══════════════════════════════════════════════════════════════════════════
# 2. Aggregates do not aggregate across organizations
# ══════════════════════════════════════════════════════════════════════════

async def test_performance_does_not_aggregate_across_organizations(db):
    await _seed(db, ORG_A, "AAA", status="closed", pnl=100.0)
    await _seed(db, ORG_B, "BBB", status="closed", pnl=-1000.0)
    from app.api.routes.analytics import get_performance

    with _as_org(ORG_A):
        out = await get_performance(_conn(ORG_A))

    assert out.get("error") is None, out.get("error")
    assert out["total_trades"] == 1, (
        f"{out['total_trades']} trades in a one-trade book — the other "
        f"organization's loss is in this caller's performance"
    )


async def test_portfolio_state_counts_only_your_own_open_positions(db):
    await _seed(db, ORG_A, "AAA")
    for t in ("B1", "B2", "B3"):
        await _seed(db, ORG_B, t)
    from app.api.routes.risk import get_portfolio_state

    acct = MagicMock(net_liquidation=100_000.0, buying_power=50_000.0,
                     cash_balance=50_000.0)
    broker = MagicMock()
    broker.get_account_summary = AsyncMock(return_value=acct)
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit",
               new=AsyncMock(return_value=acct)), _as_org(ORG_A):
        out = await get_portfolio_state(_conn(ORG_A))

    state = out["state"]
    assert state["open_positions"] == 1, (
        f"open_positions={state['open_positions']} includes another tenant's book"
    )


async def test_a_losing_streak_is_not_visible_to_another_tenant(db):
    """consecutive_losses left desk-wide is a plain cross-tenant channel."""
    for t in ("B1", "B2", "B3"):
        await _seed(db, ORG_B, t, status="closed", pnl=-100.0)
    from app.api.routes.risk import get_portfolio_state

    acct = MagicMock(net_liquidation=100_000.0, buying_power=50_000.0,
                     cash_balance=50_000.0)
    broker = MagicMock()
    broker.get_account_summary = AsyncMock(return_value=acct)
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit",
               new=AsyncMock(return_value=acct)), _as_org(ORG_A):
        out = await get_portfolio_state(_conn(ORG_A))

    assert out["state"]["consecutive_losses"] == 0, (
        "organization A sees organization B's three-loss streak"
    )


async def test_strategy_health_grades_only_your_own_results(db):
    """A strategy grade is computed from trade outcomes, so it is owned."""
    for _ in range(5):
        await _seed(db, ORG_B, "BBB", status="closed", pnl=-500.0,
                    strategy="bull_put_spread")
    from app.api.routes.strategy import _evaluate_health

    mine = await _evaluate_health(1, ORG_A)
    theirs = await _evaluate_health(1, ORG_B)

    def sample(health, strat="bull_put_spread"):
        for h in health:
            if h.strategy == strat:
                return h.as_dict().get("sample_size") or 0
        return 0

    assert sample(theirs) == 5, "the owner's own results were filtered out too"
    assert sample(mine) == 0, (
        "organization A's health panel is graded on organization B's trades"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. A foreign id answers exactly like a missing one — 404, never 403
# ══════════════════════════════════════════════════════════════════════════

async def test_closing_another_organizations_trade_is_404(db):
    theirs = await _seed(db, ORG_B, "BBB")
    from app.api.routes.trade_desk import ClosePositionRequest, close_position

    broker = MagicMock()
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         _as_org(ORG_A):
        with pytest.raises(HTTPException) as err:
            await close_position(ClosePositionRequest(trade_id=str(theirs)),
                                 _conn(ORG_A))

    assert err.value.status_code == 404, (
        f"got {err.value.status_code}; a 403 confirms the row exists, which "
        f"is itself the disclosure"
    )


async def test_a_foreign_id_and_a_missing_id_are_indistinguishable(db):
    theirs = await _seed(db, ORG_B, "BBB")
    missing = uuid.uuid4()
    from app.api.routes.trade_desk import ClosePositionRequest, close_position

    async def _attempt(trade_id):
        with patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
             _as_org(ORG_A):
            with pytest.raises(HTTPException) as err:
                await close_position(ClosePositionRequest(trade_id=str(trade_id)),
                                     _conn(ORG_A))
        return err.value.status_code, err.value.detail

    assert await _attempt(theirs) == await _attempt(missing), (
        "the two answers differ, so the response distinguishes a trade that "
        "exists from one that does not"
    )


async def test_closing_another_organizations_trade_submits_no_order(db):
    """The 404 must come from the lookup, not from a check after the order."""
    theirs = await _seed(db, ORG_B, "BBB")
    from app.api.routes.trade_desk import ClosePositionRequest, close_position

    broker = MagicMock()
    broker.place_order = AsyncMock()
    broker.place_equity_order = AsyncMock()
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         _as_org(ORG_A):
        with pytest.raises(HTTPException):
            await close_position(ClosePositionRequest(trade_id=str(theirs)),
                                 _conn(ORG_A))

    broker.place_order.assert_not_awaited()
    broker.place_equity_order.assert_not_awaited()

    async with db() as s:
        row = (await s.execute(select(Trade).where(Trade.id == theirs))).scalar_one()
    assert row.status == "open", "another organization's trade was modified"


# ══════════════════════════════════════════════════════════════════════════
# 4. The unattributed rows, and the single-operator scope
# ══════════════════════════════════════════════════════════════════════════

async def test_unattributed_rows_are_invisible_to_an_organization(db):
    """Migration 0041 declined to guess an owner; nobody inherits those rows."""
    await _seed(db, None, "ORPHAN")
    from app.api.routes.paper_trade import get_trade_history

    with _as_org(ORG_A):
        out = await get_trade_history(_conn(ORG_A), limit=50, offset=0, status=None)

    assert out["total"] == 0, "an unattributed trade leaked into an organization"


async def test_the_single_operator_scope_sees_exactly_the_unattributed_rows(db):
    """None is a scope — `organization_id IS NULL` — and not "no filter"."""
    await _seed(db, None, "ORPHAN")
    await _seed(db, ORG_A, "AAA")
    from app.api.routes.paper_trade import get_trade_history

    with _as_org(None):
        out = await get_trade_history(_conn(), limit=50, offset=0, status=None)

    tickers = {t["underlying"] for t in out["trades"]}
    assert tickers == {"ORPHAN"}, (
        f"the single-operator scope returned {tickers}; None was treated as "
        f"a wildcard, which is the shared query this change removes"
    )


# ══════════════════════════════════════════════════════════════════════════
# 5. Auth on with no identity is a refusal, not a default scope
# ══════════════════════════════════════════════════════════════════════════

#: Every scoped read route, as a callable taking only the connection. The 401
#: test below is parametrised over all of them rather than over the one or two
#: a reader would think of, because the way this breaks is per-route and
#: invisible: most of these handlers wrap their work in
#: `except Exception: return {...}`, and HTTPException IS an Exception. A
#: refusal raised inside one of those blocks comes back as an empty,
#: successful-looking payload. Each route therefore re-raises HTTPException
#: explicitly, and this is what proves it — one entry per route, so adding a
#: scoped route without the guard fails here.
def _scoped_route_calls():
    from app.api.routes import analytics, paper_trade, portfolio, risk, strategy

    return {
        "paper_trade./portfolio": lambda c: paper_trade.get_portfolio(c),
        "paper_trade./positions": lambda c: paper_trade.get_positions(c),
        "paper_trade./history": lambda c: paper_trade.get_trade_history(
            c, limit=50, offset=0, status=None),
        "portfolio./heat": lambda c: portfolio.portfolio_heat(c),
        "portfolio./open-orders": lambda c: portfolio.portfolio_open_orders(c),
        "portfolio./correlation": lambda c: portfolio.portfolio_correlation(c),
        "portfolio./rotation-performance":
            lambda c: portfolio.portfolio_rotation_performance(c),
        "analytics./performance": lambda c: analytics.get_performance(c),
        "analytics./by-mode": lambda c: analytics.get_analytics_by_mode(
            c, date_from=None, date_to=None),
        "analytics./mode/{name}": lambda c: analytics.get_mode_detail("balanced", c),
        "analytics./signal-score-impact":
            lambda c: analytics.get_signal_score_impact(c, regime=None),
        "risk./portfolio-state": lambda c: risk.get_portfolio_state(c),
        "risk./scenarios": lambda c: risk.get_scenarios(c),
        "strategy./health": lambda c: strategy.get_strategy_health(c, min_sample=20),
        "strategy./meta": lambda c: strategy.get_meta_strategy(c, min_sample=20),
        "strategy./registry": lambda c: strategy.get_strategy_registry(c),
    }


@pytest.mark.parametrize("route", sorted(_scoped_route_calls()))
async def test_missing_identity_with_auth_on_is_401_not_a_default_scope(db, route):
    """The failure that would be worst: answering with the unattributed rows.

    In a single-operator deployment those ARE every open position, so a
    fallback here would show the whole book to an unauthenticated caller —
    and it would arrive as a normal 200, which nothing downstream would flag.
    """
    await _seed(db, None, "ORPHAN")
    call = _scoped_route_calls()[route]

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    broker.get_account_summary = AsyncMock(return_value=MagicMock(
        net_liquidation=100_000.0, buying_power=50_000.0, cash_balance=50_000.0))

    with patch("app.core.config.settings.auth_enabled", True), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.api.routes.paper_trade.get_broker", return_value=broker), \
         patch("app.api.routes.portfolio.get_account_value",
               new=AsyncMock(return_value=100_000.0)):
        with pytest.raises(HTTPException) as err:
            await call(_conn())          # no identity

    assert err.value.status_code == 401, (
        f"{route} answered {err.value.status_code} instead of refusing"
    )


# ══════════════════════════════════════════════════════════════════════════
# 6. The boundary this step deliberately does not cross
# ══════════════════════════════════════════════════════════════════════════

async def test_the_risk_gate_is_still_desk_wide(db):
    """A decision, not an oversight — so it is asserted rather than assumed.

    `TRADING_POLICY.md` sets 5 concurrent positions and a 2% daily loss as
    caps on one broker account. Evaluating them per tenant would let N tenants
    hold 5N positions against the same account, which relaxes a hard limit
    rather than isolating anything. Changing it is a policy decision with an
    owner, not a mechanical scoping change, so the gate stays desk-wide while
    the displays of those numbers are scoped.

    If this test is ever deliberately inverted, TRADING_POLICY.md's risk table
    has to change in the same commit.
    """
    import inspect

    from app.api.routes import trade_desk

    for name in ("_fetch_portfolio_state", "_portfolio_heat_fraction"):
        src = inspect.getsource(getattr(trade_desk, name))
        assert "owned_by" not in src, (
            f"{name} now filters by owner, making a per-account hard limit "
            f"per-tenant. See this test's docstring."
        )
