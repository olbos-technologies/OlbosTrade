"""R2-L: real PostgreSQL rollback and conflicting-replay resilience.

Uses only OLBOS_TEST_DATABASE_URL, the isolated CI database.
"""
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.models.broker_execution_fill import BrokerExecutionFill
from app.services.broker_fill_evidence import FillEvidence
from app.services.broker_execution_ingestion import record_authenticated_execution


@pytest.mark.asyncio
async def test_rollback_replay_conflict_and_recovery(monkeypatch):
    url = os.getenv("OLBOS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL test URL")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("PostgreSQL asyncpg required")
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from app.services import broker_execution_ingestion as ingestion
    monkeypatch.setattr(ingestion, "AsyncSessionLocal", factory)
    account = "DU" + uuid4().hex[:16]
    exec_id = "r2l-" + uuid4().hex
    timestamp = datetime.now(timezone.utc)
    def evidence():
        return FillEvidence(
            broker_account_id=account, broker_execution_id=exec_id,
            contract_id="123456", underlying="SPY", expiration="2026-11-20",
            strike=Decimal("500"), option_type="put", signed_filled_quantity=-2,
            filled_at=timestamp, order_id="order-r2l", organization_id=None,
        )
    async def record(price):
        return await record_authenticated_execution(
            evidence(), broker="ibkr", expected_account_id=account,
            expected_organization_id=None, broker_fill_price=Decimal(price),
        )
    async with engine.begin() as connection:
        await connection.run_sync(BrokerExecutionFill.__table__.create, checkfirst=True)
    try:
        # An interrupted transaction leaves no committed execution row.
        with pytest.raises(RuntimeError, match="simulated interruption"):
            async with factory() as session:
                async with session.begin():
                    session.add(BrokerExecutionFill(
                        id=uuid4(), broker="ibkr", broker_account_id=account,
                        broker_execution_id=exec_id, contract_id="123456",
                        underlying="SPY", expiration="2026-11-20",
                        strike=Decimal("500"), option_type="put",
                        signed_filled_quantity=-2, fill_price=Decimal("1.25"),
                        filled_at=timestamp,
                    ))
                    await session.flush()
                    raise RuntimeError("simulated interruption")
        async with factory() as session:
            assert (await session.execute(select(BrokerExecutionFill).where(
                BrokerExecutionFill.broker_account_id == account,
                BrokerExecutionFill.broker_execution_id == exec_id,
            ))).scalar_one_or_none() is None
        # After rollback, replay inserts once; identical replay is idempotent.
        assert (await record("1.25")).inserted is True
        assert (await record("1.25")).inserted is False
        # A conflicting replay cannot overwrite the committed record.
        with pytest.raises(ValueError, match="conflicting broker execution payload"):
            await record("1.50")
        async with factory() as session:
            rows = (await session.execute(select(BrokerExecutionFill).where(
                BrokerExecutionFill.broker_account_id == account,
                BrokerExecutionFill.broker_execution_id == exec_id,
            ))).scalars().all()
            assert len(rows) == 1
            assert rows[0].fill_price == Decimal("1.25")
            assert rows[0].signed_filled_quantity == -2
    finally:
        from sqlalchemy import delete
        async with factory() as session:
            async with session.begin():
                await session.execute(delete(BrokerExecutionFill).where(
                    BrokerExecutionFill.broker_account_id == account
                ))
        await engine.dispose()
