"""OAuth boundary tests. Synthetic accounts; no real broker calls."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit
import httpx
import pytest
from fastapi import FastAPI, Request
from app.core.config import settings
from app.api.routes import broker_oauth as routes
from app.api.broker_oauth_redaction import BrokerOAuthQueryRedaction
from app.services import broker_oauth_service as svc, credential_cipher

ORG, USER = uuid.uuid4(), uuid.uuid4()
KEY = "ZmFrZS1rZXktZm9yLXRlc3RzLW9ubHktMzJieXRlcyE="

@pytest.fixture(autouse=True)
def config(monkeypatch):
    for k,v in dict(alpaca_oauth_client_id="app", alpaca_oauth_client_secret="secret", alpaca_oauth_redirect_uri="https://olbos.example/api/brokers/oauth/alpaca/callback", broker_encryption_key=KEY, auth_enabled=True).items():
        monkeypatch.setattr(settings,k,v)

def db_result(value=None):
    db=NS(execute=AsyncMock(return_value=MagicMock()),add=MagicMock(),commit=AsyncMock())
    db.execute.return_value.scalar_one_or_none.return_value=value
    return db

def mock_http(monkeypatch,body,status=200):
    async def respond(request): return httpx.Response(status,json=body)
    real=httpx.AsyncClient
    monkeypatch.setattr(svc.httpx,"AsyncClient",lambda **kw: real(transport=httpx.MockTransport(respond),**kw))

@pytest.mark.parametrize("uri",["https://[invalid", "", "http://olbos.example/api/brokers/oauth/alpaca/callback", "https://user@olbos.example/api/brokers/oauth/alpaca/callback", "https://olbos.example/evil", "https://olbos.example/api/brokers/oauth/alpaca/callback?x=1", "https://olbos.example/api/brokers/oauth/alpaca/callback#x"])
def test_invalid_callback(monkeypatch,uri):
    monkeypatch.setattr(settings,"alpaca_oauth_redirect_uri",uri)
    assert not svc.providers()[0]["available"]

def test_catalog():
    p=svc.providers()
    assert p[0]["available"] and p[0]["environment"]=="paper"
    assert not p[1]["available"] and not p[2]["available"]

@pytest.mark.asyncio
async def test_start_bound_state():
    db=db_result()
    url=await svc.begin(db,ORG,USER,"session")
    q=parse_qs(urlsplit(url).query)
    assert q["env"]==["paper"] and "scope" not in q
    row=db.add.call_args.args[0]
    assert row.state_hash==svc.digest(q["state"][0])
    assert row.organization_id==ORG and row.user_id==USER
    assert row.session_hash==svc.digest("session")
    assert 590<(row.expires_at-datetime.now(timezone.utc)).total_seconds()<=600
    db.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_unconfigured(monkeypatch):
    monkeypatch.setattr(settings,"alpaca_oauth_client_secret","")
    with pytest.raises(svc.OAuthUnavailable): await svc.begin(db_result(),ORG,USER,"session")

@pytest.mark.asyncio
@pytest.mark.parametrize("state",[None,"tiny","x"*129])
async def test_bad_state(state):
    db=db_result()
    with pytest.raises(svc.OAuthRejected): await svc.consume(db,state,ORG,USER,"session")
    db.execute.assert_not_awaited()

@pytest.mark.asyncio
@pytest.mark.parametrize("found",[None,"hash"])
async def test_consume_commits_atomically(found):
    db=db_result(found)
    if found: await svc.consume(db,"x"*43,ORG,USER,"session")
    else:
        with pytest.raises(svc.OAuthRejected): await svc.consume(db,"x"*43,ORG,USER,"session")
    sql=str(db.execute.call_args.args[0])
    assert "DELETE FROM broker_oauth_attempts" in sql and "RETURNING" in sql
    assert all(k in sql for k in ["organization_id","user_id","session_hash","expires_at"])
    db.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_exchange_contract(monkeypatch):
    seen=[]
    async def respond(r):
        seen.append(r)
        return httpx.Response(200,json={"access_token":"private-token","token_type":"bearer","scope":""})
    real=httpx.AsyncClient
    monkeypatch.setattr(svc.httpx,"AsyncClient",lambda **kw: real(transport=httpx.MockTransport(respond),**kw))
    assert await svc.exchange("private-code")=="private-token"
    assert str(seen[0].url)==svc.TOKEN_URL
    assert seen[0].headers["content-type"]=="application/x-www-form-urlencoded"
    assert parse_qs(seen[0].content.decode())["code"]==["private-code"]

@pytest.mark.asyncio
@pytest.mark.parametrize("body,status",[({},200),({"access_token":"secret","token_type":"bearer","scope":"trading"},200),({"access_token":"secret","token_type":"basic"},200),({"access_token":"bad\nsecret","token_type":"bearer"},200),([],200),({},401)])
async def test_bad_exchange(monkeypatch,body,status):
    mock_http(monkeypatch,body,status)
    with pytest.raises(svc.OAuthRejected) as e: await svc.exchange("private-code")
    assert "private" not in str(e.value) and "secret" not in str(e.value)

@pytest.mark.asyncio
@pytest.mark.parametrize("code",[None,"","x"*2049])
async def test_bad_code(code):
    with pytest.raises(svc.OAuthRejected): await svc.exchange(code)

@pytest.mark.asyncio
async def test_account_identity(monkeypatch):
    mock_http(monkeypatch,{"id":str(USER),"status":"ACTIVE"})
    assert await svc.account_identity("private-token")==str(USER)

@pytest.mark.asyncio
@pytest.mark.parametrize("body,status",[({},200),({"id":"bad","status":"ACTIVE"},200),({"id":str(ORG),"status":"DISABLED"},200),([],200),({},403)])
async def test_bad_account(monkeypatch,body,status):
    mock_http(monkeypatch,body,status)
    with pytest.raises(svc.OAuthRejected): await svc.account_identity("private-token")

@pytest.mark.asyncio
async def test_save_encrypts_and_upserts():
    db=db_result()
    await svc.save(db,ORG,"private-token",str(USER))
    stmt=db.execute.call_args.args[0]; values=stmt.compile().params
    assert "private-token" not in str(values)
    assert credential_cipher.decrypt(values["access_token_enc"])=="private-token"
    assert values["environment"]=="paper" and "ON CONFLICT" in str(stmt)

@pytest.mark.asyncio
async def test_list_does_not_decrypt(monkeypatch):
    db=db_result()
    db.execute.return_value.scalars.return_value.all.return_value=[NS(id=USER,broker="alpaca",environment="paper",account_id=str(ORG),connected_at=datetime.now(timezone.utc))]
    monkeypatch.setattr(credential_cipher,"decrypt",MagicMock(side_effect=AssertionError))
    rows=await svc.list_grants(db,ORG)
    assert rows[0]["account_last4"]==str(ORG)[-4:]
    assert not rows[0]["execution_enabled"] and str(ORG) not in str(rows)

@pytest.mark.asyncio
@pytest.mark.parametrize("found",[False,True])
async def test_disconnect(found):
    row=NS(access_token_enc="encrypted",status="active") if found else None
    db=db_result(row)
    assert await svc.disconnect(db,ORG,str(USER)) is found
    if found: assert row.access_token_enc is None and row.status=="revoked"
    assert not await svc.disconnect(db,ORG,"bad-id")

@pytest.fixture
def web(monkeypatch):
    app=FastAPI(); app.include_router(routes.router); app.add_middleware(BrokerOAuthQueryRedaction)
    @app.middleware("http")
    async def inject(request:Request,call_next):
        request.state.user={"id":str(USER),"tier":"elite","session_id":"session"}
        return await call_next(request)
    db=db_result()
    db.execute.return_value.scalars.return_value.all.return_value=[]
    async def get_db(): yield db
    app.dependency_overrides[routes.get_async_session]=get_db
    app.dependency_overrides[routes.rate_limit]=lambda:None
    monkeypatch.setattr(routes,"personal_org_for",AsyncMock(return_value=NS(id=ORG)))
    return app

@pytest.mark.asyncio
async def test_routes(web):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),base_url="https://olbos.example") as c:
        assert (await c.get("/api/brokers/oauth/providers")).status_code==200
        assert (await c.get("/api/brokers/oauth/connections")).json()["execution_enabled"] is False
        assert (await c.post("/api/brokers/oauth/alpaca/start")).status_code==200
        assert (await c.post("/api/brokers/oauth/schwab/start")).status_code==400

@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["none","consume","exchange","error","duplicate"])
async def test_callback_sanitized(web,monkeypatch,failure):
    save=AsyncMock()
    monkeypatch.setattr(svc,"consume",AsyncMock(side_effect=svc.OAuthRejected() if failure=="consume" else None))
    monkeypatch.setattr(svc,"exchange",AsyncMock(return_value="private-token",side_effect=svc.OAuthRejected() if failure=="exchange" else None))
    monkeypatch.setattr(svc,"account_identity",AsyncMock(return_value=str(USER)))
    monkeypatch.setattr(svc,"save",save)
    q="state="+"s"*43+"&code=private-code"
    if failure=="error": q+="&error=private-error"
    if failure=="duplicate": q+="&code=private-second"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),base_url="https://olbos.example") as c:
        r=await c.get(svc.CALLBACK_PATH+"?"+q)
    assert r.status_code==303 and r.headers["cache-control"]=="no-store"
    assert r.headers["referrer-policy"]=="no-referrer"
    assert ("connected" in r.headers["location"]) is (failure=="none")
    assert "private" not in str(r.headers)+r.text
    assert save.await_count==(1 if failure=="none" else 0)

@pytest.mark.asyncio
async def test_redaction():
    async def inner(scope,receive,send):
        assert scope["query_string"]==b""
        assert scope["broker_oauth_query"]["code"]=="private-code"
    scope={"type":"http","path":svc.CALLBACK_PATH,"query_string":b"code=private-code"}
    await BrokerOAuthQueryRedaction(inner)(scope,None,None)

@pytest.mark.asyncio
async def test_disabled(web,monkeypatch):
    monkeypatch.setattr(settings,"auth_enabled",False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),base_url="https://olbos.example") as c:
        assert (await c.post("/api/brokers/oauth/alpaca/start")).status_code==404

@pytest.mark.asyncio
async def test_routes_unavailable_and_disconnect(web,monkeypatch):
    monkeypatch.setattr(settings,"alpaca_oauth_client_secret","")
    monkeypatch.setattr(svc,"disconnect",AsyncMock(return_value=False))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),base_url="https://olbos.example") as c:
        assert (await c.post("/api/brokers/oauth/alpaca/start")).status_code==503
        assert (await c.delete("/api/brokers/oauth/connections/test-id")).status_code==404
        monkeypatch.setattr(svc,"disconnect",AsyncMock(return_value=True))
        assert (await c.delete("/api/brokers/oauth/connections/test-id")).status_code==200

@pytest.mark.asyncio
async def test_missing_session():
    request=Request({"type":"http","state":{"user":{"id":str(USER),"tier":"elite"}}})
    with pytest.raises(Exception) as e: await routes.identity(request,db_result())
    assert e.value.status_code==401

@pytest.mark.asyncio
async def test_redaction_leaves_other_paths():
    async def inner(scope,receive,send): assert scope["query_string"]==b"x=1"
    await BrokerOAuthQueryRedaction(inner)({"type":"http","path":"/api/health","query_string":b"x=1"},None,None)

@pytest.mark.asyncio
async def test_network_failure_sanitized(monkeypatch):
    async def fail(request): raise httpx.ConnectError("private-code private-token")
    real=httpx.AsyncClient
    monkeypatch.setattr(svc.httpx,"AsyncClient",lambda **kw: real(transport=httpx.MockTransport(fail),**kw))
    for func,arg in [(svc.exchange,"private-code"),(svc.account_identity,"private-token")]:
        with pytest.raises(svc.OAuthRejected) as e: await func(arg)
        assert "private" not in str(e.value)


def test_production_wiring_and_callback_log_protection():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    main=(root/"backend/app/main.py").read_text()
    nginx=(root/"deploy/nginx/olbostrade.conf").read_text()
    assert len(main)>1000 and len(nginx)>1000
    assert "app.add_middleware(BrokerOAuthQueryRedaction)" in main
    assert "app.include_router(broker_oauth_routes.router)" in main
    callback=nginx.split("location = /api/brokers/oauth/alpaca/callback {",1)[1].split("}",1)[0]
    assert "access_log off;" in callback and "error_log /dev/null;" in callback
    assert "proxy_pass http://olbos_backend;" in callback

@pytest.mark.asyncio
async def test_health_route_is_account_scoped_read_only(web,monkeypatch):
    from app.services import broker_connection_health as health
    result={"id":str(USER),"health":"verified","last_checked_at":"2026-01-01T00:00:00+00:00", "execution_enabled":False,"positions_reconciled":False}
    check=AsyncMock(return_value=result)
    monkeypatch.setattr(health,"verify",check)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),base_url="https://olbos.example") as c:
        r=await c.post(f"/api/brokers/oauth/connections/{USER}/verify")
    assert r.status_code==200 and r.json()==result
    assert r.headers["cache-control"]=="no-store"
    assert check.call_args.args[1:]==(ORG,str(USER))

@pytest.mark.asyncio
@pytest.mark.parametrize("changed",[False,True])
async def test_health_route_missing_or_changed(web,monkeypatch,changed):
    from app.services import broker_connection_health as health
    monkeypatch.setattr(health,"verify",AsyncMock(side_effect=health.ConnectionChanged() if changed else health.ConnectionMissing()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web),base_url="https://olbos.example") as c:
        r=await c.post(f"/api/brokers/oauth/connections/{USER}/verify")
    assert r.status_code==(409 if changed else 404)
