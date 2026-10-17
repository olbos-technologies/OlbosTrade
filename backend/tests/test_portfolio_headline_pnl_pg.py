"""The /portfolio headline P&L counts inherited positions as the real money
they are.

Real PostgreSQL, for the reason the other isolation suites give: the thing
under test is a predicate applied to rows that exist. Against a mocked session
the inclusive query and the attributable-only query it replaced return exactly
the same thing, so a mock cannot tell the bug from the fix.

The bug this pins was shipped and caught in review. `/portfolio` filtered
`strategy != 'adopted_untracked'` out of `total_pnl` and the win rate on the
belief that an adopted position's cost basis was invented. It is not — the
adopt path writes the broker's real `avg_cost` into `credit_received`, which
is what equity P&L is computed from. On the production book that filter would
have reported +3,163 on an account that was down 1,099: a sign flip on the
one number the desk steers by, in the direction that reads as "we are fine".

This endpoint builds its own SQL rather than going through
compute_performance, so the unit tests in test_trade_attribution.py do not
cover it. That gap is how the bug reached a branch.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from conftest import single_operator_conn

from app.models.trade import Trade
from app.services.trade_attribution import ADOPTED_STRATEGY

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]

# Production's exact figures, so the assertion below fails with the number the
# desk actually saw rather than a toy one.
EQUITY_PNL = Decimal("3163.51")
ADOPTED_PNL = Decimal("-4263.07")
COMBINED = -1099.56


@pytest_asyncio.fixture
async def sessionmaker_for_test():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.run_sync(Trade.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
    await engine.dispose()


def _closed(strategy: str, pnl: Decimal) -> Trade:
    price = Decimal("100.00")
    return Trade(
        id=uuid.uuid4(),
        organization_id=None,          # single-operator scope
        strategy=strategy,
        underlying="TEST",
        spread_type="equity_long",
        short_strike=price,
        long_strike=price,
        credit_received=price,
        entry_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
        exit_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        expiration=date(2026, 9, 1),
        quantity=10,
        pnl=pnl,
        status="closed",
        mfe_pnl=Decimal("0"),
        mae_pnl=Decimal("0"),
    )


async def _portfolio(sm):
    """Call the handler directly with the broker half stubbed out.

    get_broker is made to fail on purpose: the broker half of this response is
    unscoped live account data and is not what this test is about, and the
    route already degrades to a `broker_error` key when it is unavailable.
    """
    import app.api.routes.paper_trade as pt

    with patch.object(pt, "AsyncSessionLocal", sm), \
         patch.object(pt, "get_broker", side_effect=RuntimeError("no broker in test")):
        resp = await pt.get_portfolio(single_operator_conn())
    # The route answers {"portfolio": {...}}; a `db_error` key there means the
    # handler swallowed an exception and the assertions below would otherwise
    # fail as bare KeyErrors with the real cause hidden.
    body = resp["portfolio"]
    assert "db_error" not in body, f"handler failed: {body.get('db_error')}"
    return body


async def test_headline_pnl_includes_inherited_positions(sessionmaker_for_test):
    sm = sessionmaker_for_test
    async with sm() as s:
        s.add_all([_closed("equity", EQUITY_PNL),
                   _closed(ADOPTED_STRATEGY, ADOPTED_PNL)])
        await s.commit()

    out = await _portfolio(sm)

    assert out["total_pnl"] == pytest.approx(COMBINED), (
        f"total_pnl={out['total_pnl']} — an adopted position's P&L comes off "
        f"the broker's own average cost basis and is real money. Excluding it "
        f"reports a profit on a losing account."
    )
    assert out["total_trades"] == 2


async def test_inherited_positions_are_broken_out_as_well(sessionmaker_for_test):
    """Counted in the total AND reported separately — the desk's decisions and
    its account are different questions."""
    sm = sessionmaker_for_test
    async with sm() as s:
        s.add_all([_closed("equity", EQUITY_PNL),
                   _closed(ADOPTED_STRATEGY, ADOPTED_PNL)])
        await s.commit()

    out = await _portfolio(sm)

    assert out["adopted_closed"] == 1
    assert out["adopted_closed_pnl"] == pytest.approx(float(ADOPTED_PNL))
    # and the two halves reconcile against the headline
    attributable = out["total_pnl"] - out["adopted_closed_pnl"]
    assert attributable == pytest.approx(float(EQUITY_PNL))


async def test_a_book_with_no_inherited_positions_reports_zero(sessionmaker_for_test):
    sm = sessionmaker_for_test
    async with sm() as s:
        s.add(_closed("equity", EQUITY_PNL))
        await s.commit()

    out = await _portfolio(sm)

    assert out["adopted_closed"] == 0
    assert out["adopted_closed_pnl"] == 0.0
    assert out["total_pnl"] == pytest.approx(float(EQUITY_PNL))
