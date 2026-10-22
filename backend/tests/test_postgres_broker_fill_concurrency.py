"""R2-K: real PostgreSQL duplicate execution contention and isolation checks.

Runs on the isolated CI test database; no real brokerage connections.
"""
import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.models.broker_execution_fill import BrokerExecutionFill


@pytest.mark.asyncio
async def test_concurrent_execution_replay_and_account_isolation():
    url = os.getenv("OLBOS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires OLBOS_TEST_DATABASE_URL")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("PostgreSQL asyncpg required")
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    token = uuid4().hex
    a, b = f"DUA-{token[:14]}", f"DUB-{token[:14]}"
    base = dict(
        broker="ibkr", broker_execution_id=f"exec-{token}",
        contract_id="123456", underlying="SPY", expiration="2026-11-20",
        strike=Decimal("500"), option_type="put", signed_filled_quantity=-1,
        fill_price=Decimal("1.25"), filled_at=datetime.now(timezone.utc),
        organization_id=None,
    )
    async with engine.begin() as conn:
        await conn.run_sync(BrokerExecutionFill.__table__.create, checkfirst=True)

    async def write(account):
        async with factory() as session:
            async with session.begin():
                stmt = insert(BrokerExecutionFill).values(
                    id=uuid4(), broker_account_id=account, **base,
                ).on_conflict_do_nothing(
                    constraint="uq_broker_execution_fills_identity"
                ).returning(BrokerExecutionFill.id)
                return (await session.execute(stmt)).scalar_one_or_none()

    try:
        outcomes = await asyncio.gather(*(write(a) for _ in range(4)))
        assert sum(item is not None for item in outcomes) == 1
        # Same execution ID in a different broker account is independent.
        assert await write(b) is not None
        async with factory() as session:
            rows = (await session.execute(select(BrokerExecutionFill).where(
                BrokerExecutionFill.broker_execution_id == base["broker_execution_id"]
            ))).scalars().all()
            assert {row.broker_account_id for row in rows} == {a, b}
            assert all(row.signed_filled_quantity == -1 for row in rows)
    finally:
        async with factory() as session:
            async with session.begin():
                await session.execute(delete(BrokerExecutionFill).where(
                    BrokerExecutionFill.broker_execution_id == base["broker_execution_id"]
                ))
        await engine.dispose()
