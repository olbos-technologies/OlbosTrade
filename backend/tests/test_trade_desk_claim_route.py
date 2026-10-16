"""The route must not reach the broker when the position is claimed.

These drive `_execute_signal` through the REAL position-claim service against
a real PostgreSQL. Nothing here uses the `stub_position_claim` fixture — the
claim is the subject.

These assert on the BROKER'S OWN submission methods — `place_order` and
`place_equity_order`. Two weaker targets were tried first and both are wrong:

  * `record_fill` runs AFTER submission, so it stays unawaited on a run that
    sent an order and then failed to write the row — the case that matters
    most.
  * `ibkr_coordinator.submit` is a general-purpose queue, not an order choke
    point. `_execute_signal` also fetches the account through it, so its
    await_count counts calls that are not orders. Asserting on it reported
    "3 orders" for a run that placed one.

The coordinator stub here therefore RUNS the lambda it is handed, so the
broker method underneath is genuinely called and can be asserted on.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.api.routes.trade_desk as td
import app.models.journal_entry  # noqa: F401  (resolves the trades FK target)
import app.models.organization   # noqa: F401
from app.api.routes.trade_desk import _execute_signal
from app.models.trade import Trade
from app.services.guardrails import PortfolioState
from app.models.position_claim import STATE_SUBMITTED, STATE_UNKNOWN, PositionClaim
from app.services import position_claim

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]


@pytest.fixture(autouse=True)
def _market_open():
    """Pin the market open, because otherwise these tests tell the time.

    `_execute_signal` blocks with `market_closed` before it ever reaches the
    duplicate guard, so without this every test here passes during the US
    session and fails outside it. CI went green at 12:44 ET and would have
    gone red on the same commit after 16:00 — a suite that depends on when it
    runs is not evidence about the code.

    test_trade_desk_routes.py has carried the same fixture for this reason;
    this file was added without it. The market-closed path itself is covered
    there (test_market_closed_blocks_order), so nothing is lost by fixing it
    open here.
    """
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


@pytest_asyncio.fixture
async def claims_table():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.run_sync(PositionClaim.__table__.create)
        # A real, empty trades table: the route's own duplicate read runs for
        # real against it rather than through a fake, so these tests exercise
        # the read and the claim together the way production does.
        await conn.run_sync(Trade.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    # position_claim resolves AsyncSessionLocal inside each function, so this
    # redirects the claim service without touching the trade-read fake the
    # route uses for its own queries.
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
    await engine.dispose()


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
        "confidence": 90,
    }



async def _run_submitted(_priority, fn, **_kw):
    """Stand-in for ibkr_coordinator.submit that actually invokes the work.

    Without this the broker method under the lambda is never called, and a
    test asserting on it would pass whether or not an order was placed.
    """
    return await fn()


def _broker(place_order=None):
    b = MagicMock()
    b.place_order = place_order or AsyncMock(return_value=MagicMock(
        order_id="ORD-1", status="submitted", fill_price=None,
        filled_quantity=None, remaining_quantity=None, message=None))
    b.place_equity_order = AsyncMock()
    b.get_account_summary = AsyncMock(return_value=MagicMock(
        buying_power=100000.0, net_liquidation=100000.0))
    return b


def _clean_portfolio():
    return PortfolioState(current_value=100_000.0, starting_capital=100_000.0,
                          daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
                          consecutive_losses=0, trades_today=0)


async def _rows(factory):
    async with factory() as s:
        return (await s.execute(select(PositionClaim))).scalars().all()


# ── the route does not reach the broker when the claim is refused ──────────

async def test_no_broker_submission_when_the_position_is_already_claimed(claims_table):
    """A real claim is taken first, then the route is driven for the same
    position. `ibkr_coordinator.submit` is the single choke point both order
    paths go through, so asserting on it is what establishes no order was
    sent."""
    held = await position_claim.try_claim("SPY", "options")
    assert held is not None
    broker = _broker()

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock()) as record_fill:
        res = await _execute_signal(_signal(), approved_by="manual")

    assert res["result"] == "skipped" and "entry_in_flight" in res["reason"]
    broker.place_order.assert_not_awaited()          # the assertion that matters
    broker.place_equity_order.assert_not_awaited()
    record_fill.assert_not_awaited()


async def test_no_broker_submission_when_a_previous_outcome_is_unknown(claims_table):
    """An unresolved claim blocks regardless of its lease. This is the case a
    bare expiry would have got wrong: the lease running out says nothing about
    whether an order exists."""
    held = await position_claim.try_claim("SPY", "options", lease_seconds=-5)
    await position_claim.mark_unknown(held, "submit timed out")
    broker = _broker()

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted):
        res = await _execute_signal(_signal(), approved_by="manual")

    assert res["result"] == "skipped" and "entry_in_flight" in res["reason"]
    broker.place_order.assert_not_awaited()


# ── concurrency, through the route, against the real service ───────────────

async def test_two_concurrent_route_calls_submit_exactly_one_order(claims_table):
    """The property the whole change exists for, end to end.

    Two entries for one position race through `_execute_signal` with nothing
    stubbed between them and the database. Exactly one may reach the broker.
    """
    broker = _broker()

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-1")):
        results = await asyncio.gather(
            _execute_signal(_signal(), approved_by="manual"),
            _execute_signal(_signal(), approved_by="manual"),
        )

    assert broker.place_order.await_count == 1, (
        f"{broker.place_order.await_count} orders reached the broker for one "
        "position"
    )
    outcomes = sorted(r["result"] for r in results)
    assert outcomes == ["skipped", "submitted"], outcomes


async def test_the_losing_entry_does_not_disturb_the_winners_claim(claims_table):
    """A refused entry must not resolve or release the claim it lost to."""
    held = await position_claim.try_claim("SPY", "options")

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=_broker()), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted):
        await _execute_signal(_signal(), approved_by="manual")

    rows = await _rows(claims_table)
    assert len(rows) == 1 and rows[0].claim_token == held.token


# ── durable intent is recorded before the broker is called ─────────────────

async def test_intent_is_recorded_before_submission_not_after(claims_table):
    """If the claim were only marked after a successful call, a process killed
    mid-submit would have sent an order and persisted nothing. Assert the row
    already says `submitted` at the moment the broker is invoked."""
    seen = {}

    async def _capture(order):
        # Observed INSIDE the broker call — the coordinator also carries the
        # account fetch, so watching it would sample the wrong moment.
        async with claims_table() as s:
            row = (await s.execute(select(PositionClaim))).scalar_one()
            seen["state"] = row.state
            seen["idempotency_key"] = row.idempotency_key
        seen["client_order_id"] = order.client_order_id
        raise asyncio.TimeoutError("never answered")

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker",
               return_value=_broker(place_order=AsyncMock(side_effect=_capture))), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value=None)):
        res = await _execute_signal(_signal(), approved_by="manual")

    assert seen["state"] == STATE_SUBMITTED, (
        "the broker was called before the intent was durable"
    )
    assert seen["idempotency_key"]
    # ...and the broker was given it as its own dedup key on this path.
    assert seen["client_order_id"] == seen["idempotency_key"]
    assert res["result"] == "pending_confirmation"

    # The timeout left the outcome unknown and the pending-row write failed,
    # so the claim must still be blocking.
    rows = await _rows(claims_table)
    assert len(rows) == 1 and rows[0].state == STATE_UNKNOWN


async def test_a_timeout_whose_pending_row_was_written_releases_the_claim(claims_table):
    """The position is tracked now, so the trade read takes over the blocking.
    Holding the claim too would over-block: if the fill reconciler later
    cancels that pending row, a fresh entry should be allowed."""
    async def _timeout(_order):
        raise asyncio.TimeoutError("never answered")

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker",
               return_value=_broker(place_order=AsyncMock(side_effect=_timeout))), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-pending")):
        res = await _execute_signal(_signal(), approved_by="manual")

    assert res["result"] == "pending_confirmation"
    assert await _rows(claims_table) == []


# ── deployment ordering: the migration must land before this code ──────────

async def test_an_unmigrated_claims_table_blocks_entries_and_says_so(claims_table):
    """Migration 0040 must be applied BEFORE this code runs.

    If it has not been, the claim cannot be taken and Stage 3 fails closed: an
    entry is refused rather than submitted unguarded.

    The reason has to name the entry guard specifically. It previously shared
    `duplicate_check_error` with every other Stage 3 failure, which made this
    test unable to tell "the guard is missing, so we refused" from "something
    unrelated broke, so we refused" — the fail-closed result passed the test
    either way, so an accident counted as proof of the intended behaviour.
    `test_a_broken_duplicate_read_is_not_reported_as_a_missing_guard` is the
    other half of that pair.
    """
    broker = _broker()
    async with claims_table() as s, s.begin():
        await s.execute(text("DROP TABLE position_claims"))
        # Assert the premise rather than trusting it: if the drop silently did
        # nothing, this test would be exercising the healthy path and still
        # expecting a refusal.
        missing = (await s.execute(
            text("SELECT to_regclass('position_claims') IS NULL")
        )).scalar()
        assert missing is True, "the table is still there; this test proves nothing"

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted):
        res = await _execute_signal(_signal(), approved_by="manual")

    assert res["result"] == "blocked"
    assert "entry_guard_unavailable" in res["reason"]
    assert "position_claims" in res["reason"], (
        "the reason should name what is actually missing"
    )
    broker.place_order.assert_not_awaited()


async def test_a_broken_duplicate_read_is_not_reported_as_a_missing_guard(claims_table):
    """The counterpart: a healthy claim table and a failing trades read.

    Both outcomes are `blocked`, and that is correct — but they must be
    distinguishable, or neither test can prove which defect it caught. Here the
    claims table is present and working and the duplicate READ is what fails.
    """
    broker = _broker()
    async with claims_table() as s, s.begin():
        await s.execute(text("DROP TABLE trades CASCADE"))

    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=_clean_portfolio())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td.ibkr_coordinator, "submit", new=_run_submitted):
        res = await _execute_signal(_signal(), approved_by="manual")

    assert res["result"] == "blocked"
    assert "duplicate_check_error" in res["reason"]
    assert "entry_guard_unavailable" not in res["reason"]
    broker.place_order.assert_not_awaited()


async def test_only_the_entry_path_consults_the_claim():
    """Exits and monitoring must keep working when claims are unavailable.

    Rather than assert that by driving every exit, assert the structural fact
    that makes it true: nothing outside the entry path imports the claim
    service at all, so a broken or absent claims table cannot reach a close,
    a fill poll, a reconciler or the kill switch.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    allowed = {
        "models/position_claim.py",
        "services/position_claim.py",
        "api/routes/trade_desk.py",          # the entry path, and only it
        # Reconciliation: exists to RELEASE claims, never to block an exit.
        "services/claim_lookup.py",
        "services/claim_reconciliation.py",
        "api/routes/admin_claims.py",        # the audited operator override
        "main.py",                           # startup + periodic worker wiring
        # Batch E2: converts an owning organization into the claim's `scope`.
        # It imports GLOBAL_SCOPE and nothing else — it takes no claim, holds
        # none, and cannot block an exit.
        "services/trade_scope.py"
    }
    users = {
        str(f.relative_to(root))
        for f in root.rglob("*.py")
        if re.search(r"\bposition_claim\b", f.read_text())
    }
    assert users <= allowed, (
        f"the position claim reached beyond the entry path: {sorted(users - allowed)}"
    )


async def test_nothing_on_the_exit_or_monitoring_path_consults_the_claim():
    """The half of the rule that matters, stated positively.

    The allowlist above grows as reconciliation is wired in, and an allowlist
    that grows can eventually be grown to include anything. These are the
    modules whose job is to get OUT of a position or to observe one; if a
    claim ever becomes reachable from here, a missing claims table could stop
    an exit, which is the failure mode the whole fail-closed design avoids.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    must_never_import = [
        "services/kill_switch.py",
        "services/position_reconciler.py",
        "services/risk_manager.py",
        "services/guardrails.py",
        "services/margin_monitor.py",
        "services/trade_frequency_controller.py",
        "services/account_guard.py",
    ]
    for rel in must_never_import:
        path = root / rel
        assert path.exists(), f"{rel} moved — this guard is reading nothing"
        assert not re.search(r"\bposition_claim\b", path.read_text()), (
            f"{rel} now consults the position claim; an unavailable claims "
            f"table could then block an exit or a monitor"
        )


async def test_equity_orders_still_carry_no_client_order_id():
    """claim_lookup.KEYED_ASSET_CLASSES depends on this and cannot see it.

    Only the options path sends the claim's key to the broker, which is why an
    equity claim is never resolvable by a client-id lookup. If the equity path
    gains a client id, equity claims BECOME resolvable and
    KEYED_ASSET_CLASSES must be updated — otherwise they would stay blocked
    forever for a reason that is no longer true.
    """
    import inspect

    from app.broker.broker_interface import BrokerInterface
    from app.services.claim_lookup import KEYED_ASSET_CLASSES

    sig = inspect.signature(BrokerInterface.place_equity_order)
    has_key = "client_order_id" in sig.parameters
    assert has_key is ("equity" in KEYED_ASSET_CLASSES), (
        "place_equity_order's client_order_id support and "
        "KEYED_ASSET_CLASSES disagree: one of them has changed"
    )
