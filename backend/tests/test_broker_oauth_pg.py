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
                spec = importlib.util.spec_from_file_location("health_migration", "alembic/versions/0044_broker_connection_health.py")
                health_migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(health_migration)
                health_migration.upgrade()
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

async def health_grant(sessions):
    async with sessions() as db:
        await svc.save(db,A,"test-token",str(uuid.uuid4()))
        return (await svc.list_grants(db,A))[0]["id"]

async def test_health_rejects_foreign_owner_without_broker_call(sessions,monkeypatch):
    from app.services import broker_connection_health as health
    from unittest.mock import AsyncMock
    gid=await health_grant(sessions)
    probe=AsyncMock(side_effect=AssertionError)
    monkeypatch.setattr(health,"probe",probe)
    async with sessions() as db:
        with pytest.raises(health.ConnectionMissing): await health.verify(db,B,gid)
    probe.assert_not_called()

@pytest.mark.parametrize("change",["disconnect","reauthorize","faster_check"])
async def test_inflight_health_cannot_overwrite_connection_change(sessions,monkeypatch,change):
    from app.services import broker_connection_health as health
    gid=await health_grant(sessions)
    async def race(token,account_id):
        async with sessions() as other:
            if change=="disconnect": await svc.disconnect(other,A,gid)
            elif change=="reauthorize": await svc.save(other,A,"replacement-token",account_id)
            else:
                await other.execute(text("UPDATE broker_oauth_grants SET health='unavailable',last_checked_at=now() WHERE id=:gid"),{"gid":uuid.UUID(gid)})
                await other.commit()
        return "reconnect_required" if change=="reauthorize" else "verified"
    monkeypatch.setattr(health,"probe",race)
    async with sessions() as db:
        with pytest.raises(health.ConnectionChanged): await health.verify(db,A,gid)
    async with sessions() as db:
        row=(await db.execute(select(BrokerOAuthGrant))).scalar_one()
        if change=="disconnect": assert row.status=="revoked" and row.access_token_enc is None
        elif change=="reauthorize":
            assert row.health=="unverified" and row.last_checked_at is None
            from app.services import credential_cipher
            assert credential_cipher.decrypt(row.access_token_enc)=="replacement-token"
        else: assert row.health=="unavailable"

@pytest.mark.parametrize("result",["verified","unavailable","restricted","identity_mismatch","reconnect_required"])
async def test_health_persists_and_can_disconnect_rejected_grant(sessions,monkeypatch,result):
    from app.services import broker_connection_health as health
    from unittest.mock import AsyncMock
    gid=await health_grant(sessions)
    monkeypatch.setattr(health,"probe",AsyncMock(return_value=result))
    async with sessions() as db:
        checked=await health.verify(db,A,gid)
        assert checked["execution_enabled"] is False
        rows=await svc.list_grants(db,A)
        assert rows[0]["health"]==result and rows[0]["last_checked_at"]
        row=(await db.execute(select(BrokerOAuthGrant))).scalar_one()
        if result in ("identity_mismatch","reconnect_required"):
            assert row.access_token_enc is None and row.status=="reconnect_required"
            with pytest.raises(health.ConnectionMissing): await health.verify(db,A,gid)
        assert await svc.disconnect(db,A,gid)


async def test_snapshot_foreign_owner_never_reads_broker(sessions,monkeypatch):
    from app.services import broker_paper_snapshot as snapshots, broker_connection_health as health
    from unittest.mock import AsyncMock
    gid=await health_grant(sessions)
    read=AsyncMock(side_effect=AssertionError)
    monkeypatch.setattr(snapshots,"read",read)
    async with sessions() as db:
        with pytest.raises(health.ConnectionMissing): await snapshots.snapshot(db,B,gid)
    read.assert_not_called()

async def test_snapshot_is_bound_to_stored_account_and_updates_health(sessions,monkeypatch):
    from app.services import broker_paper_snapshot as snapshots
    from test_broker_paper_snapshot import broker,account
    gid=await health_grant(sessions)
    async with sessions() as db:
        row=(await db.execute(select(BrokerOAuthGrant))).scalar_one()
        expected=row.account_id
        data=account();data["id"]=expected
        broker(monkeypatch,{"/v2/account":data,"/v2/positions":[],"/v2/orders":[]})
        result=await snapshots.snapshot(db,A,gid)
        assert result["positions"]["state"]=="available" and result["positions"]["data"]==[]
        assert expected not in str(result) and result["account_last4"]==expected[-4:]
        await db.refresh(row)
        assert row.health=="verified" and row.last_checked_at
        assert result["execution_enabled"] is False

@pytest.mark.parametrize("change",["disconnect","reauthorize","newer_check"])
async def test_snapshot_discards_data_after_connection_change(sessions,monkeypatch,change):
    from app.services import broker_paper_snapshot as snapshots, broker_connection_health as health
    from test_broker_paper_snapshot import broker,account
    gid=await health_grant(sessions)
    async with sessions() as db:
        expected=(await db.execute(select(BrokerOAuthGrant))).scalar_one().account_id
    broker(monkeypatch)
    async def read(client,path,token):
        if path=="/v2/account":
            data=account();data["id"]=expected;return data
        if path=="/v2/positions":
            async with sessions() as other:
                if change=="disconnect": await svc.disconnect(other,A,gid)
                elif change=="reauthorize": await svc.save(other,A,"replacement-snapshot-token",expected)
                else:
                    await other.execute(text("UPDATE broker_oauth_grants SET health='restricted',last_checked_at=now() WHERE id=:gid"),{"gid":uuid.UUID(gid)})
                    await other.commit()
        return []
    monkeypatch.setattr(snapshots,"read",read)
    async with sessions() as db:
        with pytest.raises(health.ConnectionChanged): await snapshots.snapshot(db,A,gid)

async def test_snapshot_rejected_access_clears_token_in_database(sessions,monkeypatch):
    from app.services import broker_paper_snapshot as snapshots
    from test_broker_paper_snapshot import broker
    import httpx
    gid=await health_grant(sessions)
    broker(monkeypatch,{"/v2/account":httpx.Response(401,json={"detail":"synthetic rejection"})})
    async with sessions() as db:
        result=await snapshots.snapshot(db,A,gid)
        assert all(result[key]["data"] is None for key in ("balances","positions","open_orders"))
        row=(await db.execute(select(BrokerOAuthGrant))).scalar_one()
        assert row.status=="reconnect_required" and row.access_token_enc is None
