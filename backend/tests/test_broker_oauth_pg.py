"""Real PostgreSQL: single-use state races and tenant-bound grants."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
import pytest
import pytest_asyncio
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.core.config import settings
from app.models.broker_oauth import BrokerOAuthAttempt, BrokerOAuthGrant
from app.services import broker_oauth_service as svc

URL=os.getenv("OLBOS_TEST_DATABASE_URL")
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(not URL,reason="requires OLBOS_TEST_DATABASE_URL")]
A,B,U=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()

@pytest_asyncio.fixture
async def sessions(monkeypatch):
    monkeypatch.setattr(settings,"broker_encryption_key","ZmFrZS1rZXktZm9yLXRlc3RzLW9ubHktMzJieXRlcyE=")
    monkeypatch.setattr(settings,"alpaca_oauth_client_id","app")
    monkeypatch.setattr(settings,"alpaca_oauth_client_secret","test-secret")
    monkeypatch.setattr(settings,"alpaca_oauth_redirect_uri","https://olbos.example/api/brokers/oauth/alpaca/callback")
    engine=create_async_engine(URL,poolclass=NullPool)
    # Isolated schema avoids disturbing other PG suites' fixture tables.
    schema="oauth_test_"+uuid.uuid4().hex
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await engine.dispose()
    engine=create_async_engine(URL,poolclass=NullPool,connect_args={"server_settings":{"search_path":schema}})
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE organizations (id uuid PRIMARY KEY)"))
        await conn.execute(text("CREATE TABLE users (id uuid PRIMARY KEY)"))
        await conn.execute(text("INSERT INTO organizations VALUES (:a),(:b)"),{"a":A,"b":B})
        await conn.execute(text("INSERT INTO users VALUES (:u)"),{"u":U})
        def migrate(sync_conn):
            import importlib.util
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            spec = importlib.util.spec_from_file_location("oauth_migration", "alembic/versions/0043_broker_oauth_onboarding.py")
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(sync_conn)):
                migration.upgrade()
        await conn.run_sync(migrate)
    yield async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await engine.dispose()

async def state_for(sessions):
    async with sessions() as db:
        return parse_qs(urlsplit(await svc.begin(db,A,U,"session")).query)["state"][0]

async def test_one_winner_under_callback_race(sessions):
    state=await state_for(sessions)
    async def consume():
        async with sessions() as db:
            try:
                await svc.consume(db,state,A,U,"session")
                return True
            except svc.OAuthRejected: return False
    assert sorted(await asyncio.gather(consume(),consume()))==[False,True]

@pytest.mark.parametrize("org,user,session",[(B,U,"session"),(A,uuid.uuid4(),"session"),(A,U,"another-session")])
async def test_wrong_owner_cannot_consume(sessions,org,user,session):
    state=await state_for(sessions)
    async with sessions() as db:
        with pytest.raises(svc.OAuthRejected): await svc.consume(db,state,org,user,session)
        await svc.consume(db,state,A,U,"session")

async def test_expired_attempt_refused(sessions):
    state=await state_for(sessions)
    async with sessions() as db:
        await db.execute(text("UPDATE broker_oauth_attempts SET expires_at=:t"),{"t":datetime.now(timezone.utc)-timedelta(seconds=1)})
        await db.commit()
        with pytest.raises(svc.OAuthRejected): await svc.consume(db,state,A,U,"session")

async def test_tenant_list_revoke_and_reauthorize(sessions):
    account=str(uuid.uuid4())
    async with sessions() as db:
        await svc.save(db,A,"test-token",account)
        rows=await svc.list_grants(db,A)
        assert len(rows)==1 and not rows[0]["execution_enabled"]
        gid=rows[0]["id"]
        assert await svc.list_grants(db,B)==[]
        assert not await svc.disconnect(db,B,gid)
        assert await svc.disconnect(db,A,gid)
        row=(await db.execute(select(BrokerOAuthGrant))).scalar_one()
        assert row.access_token_enc is None
        await svc.save(db,A,"replacement-token",account)
        assert len(await svc.list_grants(db,A))==1

async def test_concurrent_reauthorize_upserts_one_account(sessions):
    account=str(uuid.uuid4())
    async def save(token):
        async with sessions() as db: await svc.save(db,A,token,account)
    await asyncio.gather(save("test-one"),save("test-two"))
    async with sessions() as db: assert len(await svc.list_grants(db,A))==1
