"""Synthetic broker reads: no orders, unknown failures, exact account scope."""
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from app.services import broker_paper_snapshot as svc, broker_connection_health as health, credential_cipher

ORG, GID, ACCOUNT = uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
TOKEN = "synthetic-private-token"


def account():
    return dict(id=ACCOUNT, status="ACTIVE", account_blocked=False, trading_blocked=False,
        trade_suspended_by_user=False, currency="USD", cash="-100.25", equity="25000.12", buying_power="40000.00", account_number="synthetic-private-account")


def position():
    return dict(asset_id=str(uuid.uuid4()),symbol="MSFT",qty="-1.25",side="short",
        avg_entry_price="100.123456",current_price="99.50",market_value="-124.375",unrealized_pl="0.77932")


def order():
    return dict(id=str(uuid.uuid4()),symbol="SPY",side="buy",type="limit",status="partially_filled",
        qty="3",filled_qty="1",limit_price="50.25",stop_price=None,notional=None,legs=None)


@pytest.fixture
def context(monkeypatch):
    grant = health.PaperGrant(GID,ORG,ACCOUNT,"synthetic-ciphertext",None)
    load=AsyncMock(return_value=grant)
    record=AsyncMock(side_effect=lambda db,g,label: {"id":str(GID),"health":label,"last_checked_at":datetime.now(timezone.utc).isoformat(),"status":"reconnect_required" if label in ("identity_mismatch","reconnect_required") else "active"})
    monkeypatch.setattr(health,"load_grant",load)
    monkeypatch.setattr(health,"record_health",record)
    monkeypatch.setattr(credential_cipher,"decrypt",lambda ciphertext:TOKEN)
    return NS(grant=grant,load=load,record=record)


def broker(monkeypatch, overrides=None):
    responses={"/v2/account":account(),"/v2/positions":[position()],"/v2/orders":[order()]}
    responses.update(overrides or {})
    requests=[]
    async def respond(request):
        requests.append(request)
        body=responses[request.url.path]
        if isinstance(body,httpx.Response): return body
        if isinstance(body,Exception): raise body
        return httpx.Response(200,json=body)
    real=httpx.AsyncClient
    monkeypatch.setattr(svc.httpx,"AsyncClient",lambda **kw: real(transport=httpx.MockTransport(respond),**kw))
    return requests


@pytest.mark.asyncio
async def test_exact_account_bounded_gets_and_no_secret_fields(context,monkeypatch):
    seen=broker(monkeypatch)
    result=await svc.snapshot(None,ORG,GID)
    context.load.assert_awaited_once_with(None,ORG,GID)
    assert len(seen)==4 and all(r.method=="GET" and r.url.host=="paper-api.alpaca.markets" for r in seen)
    query=next(r.url.params for r in seen if r.url.path=="/v2/orders")
    assert dict(query)=={"status":"open","limit":"500","nested":"true"}
    assert all(r.headers["authorization"]=="Bearer "+TOKEN for r in seen)
    assert not any("APCA-API-KEY-ID" in r.headers for r in seen)
    assert result["balances"]["data"]["cash"]=="-100.25"
    assert result["positions"]["data"][0]["qty"]=="-1.25"
    assert result["open_orders"]["data"][0]["filled_qty"]=="1"
    assert result["snapshot_atomic"] is result["execution_enabled"] is result["positions_reconciled"] is False
    assert all(v not in str(result) for v in (TOKEN,ACCOUNT,"synthetic-private-account","synthetic-ciphertext"))


@pytest.mark.asyncio
async def test_successful_empty_is_explicit(context,monkeypatch):
    broker(monkeypatch,{"/v2/positions":[],"/v2/orders":[]})
    result=await svc.snapshot(None,ORG,GID)
    assert result["positions"]=={"state":"available","data":[],"reason":None}
    assert result["open_orders"]["data"]==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,key",[("/v2/positions","positions"),("/v2/orders","open_orders")])
async def test_partial_failure_is_unknown_not_empty(context,monkeypatch,path,key):
    broker(monkeypatch,{path:httpx.Response(503,json={"detail":TOKEN})})
    result=await svc.snapshot(None,ORG,GID)
    assert result[key]["state"]=="unavailable" and result[key]["data"] is None
    assert result["balances"]["state"]=="available" and result["health"]=="verified"


@pytest.mark.asyncio
@pytest.mark.parametrize("path",["/v2/account","/v2/positions","/v2/orders"])
async def test_access_rejection_clears_all_data(context,monkeypatch,path):
    broker(monkeypatch,{path:httpx.Response(401,json={"detail":TOKEN})})
    result=await svc.snapshot(None,ORG,GID)
    assert result["health"]=="reconnect_required"
    assert all(result[k]["data"] is None for k in ("balances","positions","open_orders"))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad",[True,False])
async def test_identity_failure_stops_positions_and_order_reads(context,monkeypatch,bad):
    data=account();data["id"]=str(uuid.uuid4()) if bad else "malformed"
    seen=broker(monkeypatch,{"/v2/account":data})
    result=await svc.snapshot(None,ORG,GID)
    assert len(seen)==1 and result["health"]==("identity_mismatch" if bad else "unavailable")


@pytest.mark.asyncio
async def test_restricted_account_can_be_read_but_never_authorized(context,monkeypatch):
    data=account();data["trading_blocked"]=True
    broker(monkeypatch,{"/v2/account":data})
    result=await svc.snapshot(None,ORG,GID)
    assert result["health"]=="restricted" and result["balances"]["state"]=="available"
    assert result["execution_enabled"] is False


@pytest.mark.asyncio
async def test_invalid_balance_does_not_fake_zero(context,monkeypatch):
    data=account();data["equity"]="NaN"
    broker(monkeypatch,{"/v2/account":data})
    result=await svc.snapshot(None,ORG,GID)
    assert result["balances"]["data"] is None and result["positions"]["state"]=="available"


@pytest.mark.asyncio
async def test_cap_reports_incomplete(context,monkeypatch):
    broker(monkeypatch,{"/v2/orders":[order() for _ in range(500)]})
    result=await svc.snapshot(None,ORG,GID)
    assert result["open_orders"]["state"]=="incomplete" and len(result["open_orders"]["data"])==500


@pytest.mark.asyncio
async def test_changed_connection_discards_response(context,monkeypatch):
    broker(monkeypatch)
    context.record.side_effect=health.ConnectionChanged()
    with pytest.raises(health.ConnectionChanged): await svc.snapshot(None,ORG,GID)


@pytest.mark.asyncio
async def test_foreign_missing_or_revoked_connection_never_calls_broker(context,monkeypatch):
    context.load.side_effect=health.ConnectionMissing()
    read=AsyncMock(side_effect=AssertionError)
    monkeypatch.setattr(svc,"read",read)
    with pytest.raises(health.ConnectionMissing): await svc.snapshot(None,ORG,GID)
    read.assert_not_called()


@pytest.mark.asyncio
async def test_cipher_failure_does_not_query_broker(context,monkeypatch):
    def fail(_): raise credential_cipher.CredentialDecryptionError()
    monkeypatch.setattr(credential_cipher,"decrypt",fail)
    read=AsyncMock(side_effect=AssertionError);monkeypatch.setattr(svc,"read",read)
    result=await svc.snapshot(None,ORG,GID)
    assert result["health"]=="unavailable" and result["positions"]["data"] is None
    read.assert_not_called()


@pytest.mark.asyncio
async def test_whole_collection_has_deadline(context,monkeypatch):
    broker(monkeypatch)
    async def slow(*args): await asyncio.sleep(1)
    monkeypatch.setattr(svc,"COLLECTION_TIMEOUT",0.001)
    monkeypatch.setattr(svc,"read",slow)
    result=await svc.snapshot(None,ORG,GID)
    assert result["health"]=="unavailable" and result["balances"]["data"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["oversize","duplicate","invalid_json","redirect","timeout"])
async def test_bad_reads_are_sanitized(monkeypatch,kind):
    responses={"oversize":httpx.Response(200,content=b"x"*30),"duplicate":httpx.Response(200,content=b'{"id":"a","id":"b"}'),
        "invalid_json":httpx.Response(200,content=b"not-json"),"redirect":httpx.Response(302,headers={"Location":"https://evil.example"}),
        "timeout":httpx.ReadTimeout(TOKEN)}
    monkeypatch.setattr(svc,"MAX_BYTES",25 if kind=="oversize" else 1000)
    seen=broker(monkeypatch,{"/v2/account":responses[kind]})
    async with httpx.AsyncClient() as client:
        with pytest.raises(svc.ReadUnavailable) as e: await svc.read(client,"/v2/account",TOKEN)
    assert TOKEN not in str(e.value) and len(seen)==1


@pytest.mark.parametrize("value",[None,1,True,"","NaN","Infinity","oops","1e99","1e-99","1e18","x"*81])
def test_invalid_numbers(value):
    with pytest.raises(ValueError): svc.number(value)


def test_decimal_and_optional_values():
    assert svc.number(None,True) is None
    assert svc.number("0.123456789012345678")=="0.123456789012345678"
    assert svc.number("-10.50")=="-10.50"
    assert svc.symbol(None,True) is None
    assert svc.symbol("SPY261218C00500000")=="SPY261218C00500000"
    with pytest.raises(ValueError): svc.symbol("<script>")
    with pytest.raises(ValueError): svc.balances(dict(currency="not-a-currency"))


@pytest.mark.parametrize("data",[{},[{}],[dict(position(),qty="0")],[dict(position(),side="invalid")]])
def test_invalid_positions(data):
    with pytest.raises((ValueError,KeyError)): svc.positions(data)


def test_duplicate_positions_and_bounds(monkeypatch):
    p=position()
    with pytest.raises(ValueError): svc.positions([p,p])
    monkeypatch.setattr(svc,"MAX_POSITIONS",0)
    with pytest.raises(ValueError): svc.positions([p])


def test_order_legs_preserved_and_missing_values_stay_unknown():
    parent=order();child=order();parent.update(symbol=None,side=None,qty=None,legs=[child])
    result=svc.orders([parent])[0]
    assert result["qty"] is None and result["symbol"] is None
    assert result["legs"][0]["id"]==child["id"]
    parent.update(type="future_order",status="future_status",legs=[])
    assert svc.orders([parent])[0]["status"]=="unknown"


@pytest.mark.parametrize("field,value",[("side","invalid"),("legs",{}),("legs",[{}]*33)])
def test_invalid_orders(field,value):
    o=order();o[field]=value
    with pytest.raises(ValueError): svc.orders([o])


def test_order_bounds_duplicate_depth_and_budget():
    o=order()
    for data in ({},[o,o],[order() for _ in range(501)]):
        with pytest.raises(ValueError): svc.orders(data)
    a,b,c,d=order(),order(),order(),order();a["legs"]=[b];b["legs"]=[c];c["legs"]=[d]
    with pytest.raises(ValueError): svc.orders([a])
    many=[]
    for _ in range(65):
        p=order();p["legs"]=[order() for _ in range(31)];many.append(p)
    with pytest.raises(ValueError): svc.orders(many)


def test_grant_repr_never_exposes_identity_or_ciphertext(context):
    assert ACCOUNT not in repr(context.grant) and "synthetic-ciphertext" not in repr(context.grant)


@pytest.mark.asyncio
@pytest.mark.parametrize("last",["changed","invalid","rejected"])
async def test_broker_side_account_change_discards_collected_values(context,monkeypatch,last):
    count=0
    async def respond(request):
        nonlocal count
        if request.url.path=="/v2/account":
            count+=1
            if count==2:
                if last=="rejected": return httpx.Response(403,json={})
                if last=="invalid": return httpx.Response(200,json={})
                data=account();data["id"]=str(uuid.uuid4());return httpx.Response(200,json=data)
            return httpx.Response(200,json=account())
        return httpx.Response(200,json=[])
    real=httpx.AsyncClient
    monkeypatch.setattr(svc.httpx,"AsyncClient",lambda **kw:real(transport=httpx.MockTransport(respond),**kw))
    result=await svc.snapshot(None,ORG,GID)
    assert count==2 and all(result[k]["data"] is None for k in ("balances","positions","open_orders"))
    assert result["health"]=={"changed":"identity_mismatch","invalid":"unavailable","rejected":"reconnect_required"}[last]
