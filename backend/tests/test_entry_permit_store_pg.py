"""PostgreSQL-only R2-Q atomic consumption tests.

These insert synthetic records directly in an isolated test database: there is
no permit issuer and no broker call. Never substitute SQLite or mocks for the
row-update concurrency property.
"""
import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.entry_authorization_record import EntryAuthorizationRecord
from app.services.entry_permit_store import consume_stored_permit

DB = os.getenv("OLBOS_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not DB, reason="requires real PostgreSQL")]


@pytest.fixture
async def permit_database():
    engine = create_async_engine(DB, pool_size=5, max_overflow=0)
    async with engine.begin() as conn:
        await conn.run_sync(EntryAuthorizationRecord.__table__.create, checkfirst=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.run_sync(EntryAuthorizationRecord.__table__.drop, checkfirst=True)
    await engine.dispose()


async def _seed(factory, *, expired=False):
    org, principal, connection, claim = (uuid.uuid4() for _ in range(4))
    token = uuid.uuid4().hex + uuid.uuid4().hex
    kwargs = dict(
        token=token, organization_id=org, principal_id=principal,
        broker_connection_id=connection, broker_account_id="DU-TEST",
        environment="paper", intent_digest="a" * 64,
        claim_token=claim, risk_revision="risk-v1",
        kill_switch_revision="stop-v1",
    )
    now = datetime.now(timezone.utc)
    async with factory() as session, session.begin():
        await session.execute(insert(EntryAuthorizationRecord).values(
            id=uuid.uuid4(), organization_id=org, principal_id=principal,
            broker_connection_id=connection, broker="test",
            broker_account_id="DU-TEST", environment="paper",
            asset_class="options", position_identity="SPY:options",
            intent_digest="a" * 64,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            evidence_id="synthetic-test-evidence",
            evidence_observed_at=now, risk_revision="risk-v1",
            kill_switch_revision="stop-v1", position_claim_token=claim,
            idempotency_key=uuid.uuid4().hex, state="issued",
            issued_at=now - timedelta(minutes=4) if expired else now,
            expires_at=now - timedelta(minutes=1) if expired else now + timedelta(minutes=5),
        ))
    return kwargs


async def test_simultaneous_consumers_only_one_wins_and_replay_fails(permit_database):
    args = await _seed(permit_database)
    outcomes = await asyncio.gather(
        consume_stored_permit(**args),
        consume_stored_permit(**args),
    )
    assert sorted(outcomes) == [False, True]
    assert not await consume_stored_permit(**args)
    async with permit_database() as session:
        row = (await session.execute(select(EntryAuthorizationRecord))).scalar_one()
        assert row.state == "consumed" and row.consumed_at is not None


async def test_wrong_account_or_revision_cannot_consume(permit_database):
    args = await _seed(permit_database)
    for override in (
        {"broker_account_id": "DU-OTHER"},
        {"organization_id": uuid.uuid4()},
        {"risk_revision": "changed"},
        {"kill_switch_revision": "disabled"},
        {"intent_digest": "b" * 64},
        {"claim_token": uuid.uuid4()},
        {"token": uuid.uuid4().hex + uuid.uuid4().hex},
    ):
        assert not await consume_stored_permit(**(args | override))
    assert await consume_stored_permit(**args)


async def test_expired_record_cannot_be_consumed(permit_database):
    args = await _seed(permit_database, expired=True)
    assert not await consume_stored_permit(**args)
