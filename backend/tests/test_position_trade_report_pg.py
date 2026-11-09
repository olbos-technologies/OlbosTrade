"""The report reads across owners, against a real database.

The point of the page: a row the strict Positions screen cannot see must still show
up here, labelled, with the reason. Real PostgreSQL, because the thing being proved
is what the query returns for rows of different owners.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.position_claim import PositionClaim
from app.models.trade import Trade
from app.services import position_trade_report as rep

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not TEST_DB_URL, reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL")]

OP = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
        await conn.run_sync(Trade.__table__.create)
        await conn.run_sync(PositionClaim.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS trades CASCADE"))
    await engine.dispose()


def _trade(underlying, org, status="open", direction="equity_long", qty=10, strategy="equity"):
    return Trade(
        id=uuid.uuid4(), strategy=strategy, organization_id=org, underlying=underlying,
        spread_type=direction, short_strike=1, long_strike=1, expiration=datetime(2026, 11, 6).date(),
        entry_date=datetime(2026, 11, 5, tzinfo=timezone.utc), status=status, quantity=qty,
        credit_received=1, approved_by="autopilot",
    )


def _broker(*positions):
    b = MagicMock()
    b.get_positions = AsyncMock(return_value=[
        SimpleNamespace(underlying=u, quantity=q, asset_type="equity") for u, q in positions
    ])
    return b


async def test_a_row_of_another_owner_is_returned_labelled_not_hidden(db):
    async with db() as s:
        s.add_all([_trade("AMZN", None), _trade("QQQ", OP, qty=5)])
        await s.commit()
    with patch("app.broker.broker_factory.get_broker", return_value=_broker(("AMZN", 10), ("QQQ", 5))):
        out = await rep.load_report(viewer=OP, operator=OP)
    by = {i["symbol"]: i for i in out["items"]}
    assert by["AMZN"]["code"] == "hidden_from_positions_page"
    assert by["AMZN"]["positions_page_shows"] == "UNKNOWN"
    assert by["AMZN"]["trades"][0]["owner"].startswith("unattributed")
    assert by["QQQ"]["code"] == "tracked" and by["QQQ"]["positions_page_shows"] == "equity"
    assert out["summary"]["attention"] == 1 and out["summary"]["tracked"] == 1


async def test_nothing_recorded_and_closed_rows_are_told_apart(db):
    async with db() as s:
        s.add(_trade("FER", OP, status="closed", direction="equity_short", qty=337))
        await s.commit()
    with patch("app.broker.broker_factory.get_broker", return_value=_broker(("FER", -337), ("SPCX", -18))):
        out = await rep.load_report(viewer=OP, operator=OP)
    by = {i["symbol"]: i for i in out["items"]}
    assert by["FER"]["code"] == "only_closed_rows"
    assert by["SPCX"]["code"] == "no_trade_row"


async def test_an_open_row_with_nothing_at_the_broker_is_listed(db):
    async with db() as s:
        s.add(_trade("GHOST", OP))
        await s.commit()
    with patch("app.broker.broker_factory.get_broker", return_value=_broker(("AAPL", 3))):
        out = await rep.load_report(viewer=OP, operator=OP)
    ghost = next(i for i in out["items"] if i["symbol"] == "GHOST")
    assert ghost["code"] == "row_without_broker_position" and ghost["broker_quantity"] is None


async def test_claims_for_a_holding_are_shown_with_their_state(db):
    async with db() as s:
        s.add(PositionClaim(
            scope="global", underlying="SPCX", asset_class="equity", claim_token=uuid.uuid4(),
            state="unknown", idempotency_key="k", lease_expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            unresolved_reason="submit timed out",
        ))
        await s.commit()
    with patch("app.broker.broker_factory.get_broker", return_value=_broker(("SPCX", -18))):
        out = await rep.load_report(viewer=OP, operator=OP)
    spcx = out["items"][0]
    assert spcx["claims"][0]["state"] == "unknown" and spcx["claims"][0]["reason"] == "submit timed out"


async def test_the_report_changes_nothing(db):
    async with db() as s:
        s.add(_trade("AMZN", None))
        await s.commit()
    with patch("app.broker.broker_factory.get_broker", return_value=_broker(("AMZN", 10))):
        await rep.load_report(viewer=OP, operator=OP)
    async with db() as s:
        n = (await s.execute(text("select count(*) from trades"))).scalar_one()
        c = (await s.execute(text("select count(*) from position_claims"))).scalar_one()
    assert (n, c) == (1, 0)
