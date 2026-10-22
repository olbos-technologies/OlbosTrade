"""R2-J PostgreSQL-backed execution ledger tests.

Requires TEST_DATABASE_URL pointing to a disposable PostgreSQL database with
migration 0042 applied. Skips when unset; never touches the default app DB.
"""
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.broker_execution_fill import BrokerExecutionFill


@pytest.mark.asyncio
async def test_postgres_execution_identity_is_atomic_and_account_scoped():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("TEST_DATABASE_URL must be PostgreSQL asyncpg")
    engine = create_async_engine(url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    from sqlalchemy.dialects.postgresql import insert

    token = uuid4().hex
    base = dict(
        broker="ibkr", broker_account_id=f"DU_{token[:16]}",
        broker_execution_id=f"execution-{token}", organization_id=None,
        contract_id="123456", underlying="SPY", expiration="2026-11-20",
        strike=Decimal("500"), option_type="put", signed_filled_quantity=-1,
        fill_price=Decimal("1.25"), filled_at=datetime.now(timezone.utc),
    )
    ids = []
    try:
        async with sessionmaker() as session:
            async with session.begin():
                for _ in range(2):
                    stmt = insert(BrokerExecutionFill).values(id=uuid4(), **base).on_conflict_do_nothing(
                        constraint="uq_broker_execution_fills_identity"
                    ).returning(BrokerExecutionFill.id)
                    ids.append((await session.execute(stmt)).scalar_one_or_none())
                stmt = select(BrokerExecutionFill).where(
                    BrokerExecutionFill.broker == base["broker"],
                    BrokerExecutionFill.broker_account_id == base["broker_account_id"],
                    BrokerExecutionFill.broker_execution_id == base["broker_execution_id"],
                )
                rows = (await session.execute(stmt)).scalars().all()
                assert ids[0] is not None and ids[1] is None
                assert len(rows) == 1 and rows[0].signed_filled_quantity == -1
    finally:
        async with sessionmaker() as session:
            async with session.begin():
                from sqlalchemy import delete
                await session.execute(delete(BrokerExecutionFill).where(
                    BrokerExecutionFill.broker_account_id == base["broker_account_id"]
                ))
        await engine.dispose()
