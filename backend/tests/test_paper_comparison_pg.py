"""PostgreSQL pairing/CAS/isolation and observation races; synthetic broker only."""
import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, text
from app.models.broker_oauth import BrokerOAuthGrant
from app.models.paper_comparison import PaperComparison
from app.services import paper_comparison as svc, broker_oauth_service as oauth, broker_connection_health as health
from test_broker_oauth_pg import sessions, A, B, URL

pytestmark=[pytest.mark.asyncio, pytest.mark.skipif(not URL,reason="requires OLBOS_TEST_DATABASE_URL")]


async def grants(sessions, org=A):
    async with sessions() as db:
        for _ in range(2): await oauth.save(db,org,"synthetic-token",str(uuid.uuid4()))
        return [(row.id,row.account_id) for row in (await db.execute(select(BrokerOAuthGrant).where(BrokerOAuthGrant.organization_id==org))).scalars()]


async def paired(sessions):
    rows=await grants(sessions)
    async with sessions() as db: return await svc.save(db,A,rows[0][0],rows[1][0],None)


async def test_pair_persists_roles_and_foreign_tenant_cannot_read(sessions):
    assert svc.view(None) is None
    pair=await paired(sessions)
    async with sessions() as db:
        assert svc.view(await svc.get(db,A))==pair
        assert await svc.get(db,B) is None
        with pytest.raises(svc.ComparisonChanged): await svc.observe(db,B,uuid.UUID(pair["revision"]))
        swapped=await svc.save(db,A,uuid.UUID(pair["hybrid_grant_id"]),uuid.UUID(pair["system_grant_id"]),uuid.UUID(pair["revision"]))
        assert swapped["revision"]!=pair["revision"]
        with pytest.raises(svc.ComparisonChanged): await svc.observe(db,A,uuid.UUID(pair["revision"]))


@pytest.mark.parametrize("case",["same","foreign","live","revoked","token_missing","broker","same_identity"])
async def test_rejects_invalid_pair_without_creating_config(sessions,case):
    rows=await grants(sessions)
    a,b=rows[0][0],rows[1][0]
    async with sessions() as db:
        if case=="same": b=a
        elif case=="foreign":
            foreign=await grants(sessions,B); b=foreign[0][0]
        else:
            changes={"live":"environment='live'","revoked":"status='revoked'","token_missing":"access_token_enc=NULL","broker":"broker='other'","same_identity":"account_id=:identity"}
            if case=="same_identity":
                # Unique constraint also protects identity within org/broker/env.
                # Construct this corrupt state by using another broker; both
                # filters must still prevent a pair being saved.
                await db.execute(text("UPDATE broker_oauth_grants SET broker='other',account_id=:identity WHERE id=:id"),{"id":b,"identity":rows[0][1]})
            else: await db.execute(text("UPDATE broker_oauth_grants SET "+changes[case]+" WHERE id=:id"),{"id":b})
            await db.commit()
        with pytest.raises(svc.InvalidPair): await svc.save(db,A,a,b,None)
        assert await svc.get(db,A) is None


async def test_one_initial_save_and_one_update_win_under_races(sessions):
    rows=await grants(sessions);a,b=rows[0][0],rows[1][0]
    async def write(revision):
        async with sessions() as db:
            try: return await svc.save(db,A,a,b,revision)
            except svc.ComparisonChanged: return None
    results=await asyncio.gather(write(None),write(None))
    assert sum(r is not None for r in results)==1
    rev=uuid.UUID(next(r for r in results if r)["revision"])
    assert sum(r is not None for r in await asyncio.gather(write(rev),write(rev)))==1


async def install_observation(sessions,monkeypatch,pair):
    async def snapshot(db,org,gid):
        assert org==A and gid in (pair["system_grant_id"],pair["hybrid_grant_id"])
        checked=datetime.now(timezone.utc)
        await db.execute(text("UPDATE broker_oauth_grants SET last_checked_at=:checked WHERE id=:id"),{"checked":checked,"id":uuid.UUID(gid)})
        await db.commit()
        return {"connection_id":gid,"status":"active","last_checked_at":checked.isoformat(),"execution_enabled":False}
    monkeypatch.setattr(svc.broker_paper_snapshot,"snapshot",snapshot)
    return snapshot


async def test_observations_are_read_only_and_distinct(sessions,monkeypatch):
    pair=await paired(sessions)
    await install_observation(sessions,monkeypatch,pair)
    async with sessions() as db:
        result=await svc.observe(db,A,uuid.UUID(pair["revision"]))
        assert result["execution_enabled"] is result["snapshot_atomic"] is False
        assert result["accounts"]["system"]["snapshot"]["connection_id"]==pair["system_grant_id"]
        assert result["accounts"]["hybrid"]["snapshot"]["connection_id"]==pair["hybrid_grant_id"]
        assert "return" not in result and "winner" not in result


@pytest.mark.parametrize("error",[health.ConnectionMissing,health.ConnectionChanged])
async def test_unavailable_never_becomes_empty_holdings(sessions,monkeypatch,error):
    pair=await paired(sessions)
    read=AsyncMock(side_effect=error())
    monkeypatch.setattr(svc.broker_paper_snapshot,"snapshot",read)
    async with sessions() as db:
        result=await svc.observe(db,A,uuid.UUID(pair["revision"]))
        assert all(r["snapshot"] is None for r in result["accounts"].values())
        assert all(r["state"]==("changed" if error is health.ConnectionChanged else "unavailable") for r in result["accounts"].values())


@pytest.mark.parametrize("change",["pair","disconnect","reauthorize","delete"])
async def test_inflight_changes_discard_old_observations(sessions,monkeypatch,change):
    pair=await paired(sessions)
    read=await install_observation(sessions,monkeypatch,pair)
    async def race(db,org,gid):
        result=await read(db,org,gid)
        if gid==pair["hybrid_grant_id"]:
            async with sessions() as other:
                if change=="pair":
                    await svc.save(other,A,uuid.UUID(pair["hybrid_grant_id"]),uuid.UUID(pair["system_grant_id"]),uuid.UUID(pair["revision"]))
                elif change=="disconnect": await oauth.disconnect(other,A,pair["system_grant_id"])
                elif change=="delete":
                    # Simulate deleting only the config during the request.
                    await other.execute(text("DELETE FROM paper_comparisons WHERE organization_id=:org"),{"org":A});await other.commit()
                else:
                    grant=(await other.execute(select(BrokerOAuthGrant).where(BrokerOAuthGrant.id==uuid.UUID(pair["system_grant_id"])))).scalar_one()
                    await oauth.save(other,A,"replacement-token",grant.account_id)
        return result
    monkeypatch.setattr(svc.broker_paper_snapshot,"snapshot",race)
    async with sessions() as db:
        if change in ("pair","delete"):
            with pytest.raises(svc.ComparisonChanged): await svc.observe(db,A,uuid.UUID(pair["revision"]))
        else:
            result=await svc.observe(db,A,uuid.UUID(pair["revision"]))
            assert result["accounts"]["system"]=={"state":"changed","snapshot":None}
            assert result["accounts"]["hybrid"]["state"]=="observed"


async def test_schema_disallows_same_grant_and_downgrade_roundtrip(sessions):
    from sqlalchemy.exc import IntegrityError
    rows=await grants(sessions)
    async with sessions() as db:
        db.add(PaperComparison(organization_id=A,system_grant_id=rows[0][0],hybrid_grant_id=rows[0][0]))
        with pytest.raises(IntegrityError): await db.commit()
        await db.rollback()
    engine=sessions.kw["bind"]
    async with engine.begin() as conn:
        def cycle(sync_conn):
            import importlib.util
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            spec=importlib.util.spec_from_file_location("comparison_migration","alembic/versions/0045_paper_comparison.py")
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(sync_conn)):
                module.downgrade();module.upgrade()
        await conn.run_sync(cycle)
    async with sessions() as db: assert await svc.get(db,A) is None
