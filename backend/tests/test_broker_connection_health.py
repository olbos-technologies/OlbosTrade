"""Account health is read-only, account-bound and race-safe."""
import uuid
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from app.services import broker_connection_health as svc, credential_cipher

ORG, GRANT, ACCOUNT = uuid.uuid4(), uuid.uuid4(), str(uuid.uuid4())
TOKEN="test-private-token"


def account(**overrides):
    return dict(id=ACCOUNT, status="ACTIVE", account_blocked=False, trading_blocked=False,
                trade_suspended_by_user=False, **overrides)


def mock_http(monkeypatch, body, status=200):
    requests=[]
    async def respond(request):
        requests.append(request)
        return httpx.Response(status,json=body)
    real=httpx.AsyncClient
    monkeypatch.setattr(svc.httpx,"AsyncClient",lambda **kw: real(transport=httpx.MockTransport(respond), **kw))
    return requests


@pytest.mark.asyncio
async def test_fixed_paper_endpoint_get_only(monkeypatch):
    seen=mock_http(monkeypatch,account())
    assert await svc.probe(TOKEN,ACCOUNT)=="verified"
    assert len(seen)==1 and seen[0].method=="GET"
    assert str(seen[0].url)=="https://paper-api.alpaca.markets/v2/account"
    assert seen[0].headers["Authorization"]=="Bearer "+TOKEN
    assert "APCA-API-KEY-ID" not in seen[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected",[(401,"reconnect_required"),(403,"reconnect_required"),(429,"unavailable"),(500,"unavailable"),(302,"unavailable")])
async def test_http_failures(monkeypatch,status,expected):
    seen=mock_http(monkeypatch,{"detail":TOKEN},status)
    assert await svc.probe(TOKEN,ACCOUNT)==expected
    assert len(seen)==1


@pytest.mark.asyncio
async def test_wrong_account(monkeypatch):
    data=account(); data["id"]=str(uuid.uuid4())
    mock_http(monkeypatch,data)
    assert await svc.probe(TOKEN,ACCOUNT)=="identity_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag",["account_blocked","trading_blocked","trade_suspended_by_user","status"])
async def test_restricted_accounts(monkeypatch,flag):
    data=account(); data[flag]="ACCOUNT_UPDATED" if flag=="status" else True
    mock_http(monkeypatch,data)
    assert await svc.probe(TOKEN,ACCOUNT)=="restricted"


@pytest.mark.asyncio
@pytest.mark.parametrize("data",[{},[],{"id":"bad"},{"id":ACCOUNT},dict(id=ACCOUNT, account_blocked="false", trading_blocked=False, trade_suspended_by_user=False)])
async def test_malformed_account_is_unknown(monkeypatch,data):
    mock_http(monkeypatch,data)
    assert await svc.probe(TOKEN,ACCOUNT)=="unavailable"


@pytest.mark.asyncio
async def test_timeout(monkeypatch):
    real=httpx.AsyncClient
    async def fail(request): raise httpx.ReadTimeout(TOKEN)
    monkeypatch.setattr(svc.httpx,"AsyncClient",lambda **kw: real(transport=httpx.MockTransport(fail),**kw))
    assert await svc.probe(TOKEN,ACCOUNT)=="unavailable"


def db_for(row, written=GRANT):
    one=MagicMock(); one.scalar_one_or_none.return_value=row
    two=MagicMock(); two.scalar_one_or_none.return_value=written
    return NS(execute=AsyncMock(side_effect=[one,two]),commit=AsyncMock())


def connection():
    return NS(access_token_enc="test-ciphertext",account_id=ACCOUNT,last_checked_at=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("health",["verified","unavailable","restricted","identity_mismatch","reconnect_required"])
async def test_health_is_never_execution_authority(monkeypatch,health):
    db=db_for(connection())
    monkeypatch.setattr(credential_cipher,"decrypt",lambda _:TOKEN)
    monkeypatch.setattr(svc,"probe",AsyncMock(return_value=health))
    result=await svc.verify(db,ORG,GRANT)
    assert result["health"]==health and result["execution_enabled"] is False and result["positions_reconciled"] is False
    assert TOKEN not in str(result) and ACCOUNT not in str(result)
    stmt=db.execute.call_args_list[1].args[0]
    sql=str(stmt); params=stmt.compile().params
    assert all(k in sql for k in ["organization_id","account_id","environment","last_checked_at","access_token_enc"])
    if health in ("identity_mismatch","reconnect_required"):
        assert params["status"]=="reconnect_required" and params["access_token_enc"] is None
    else: assert "status" not in params


@pytest.mark.asyncio
async def test_changed_connection_never_returns_success(monkeypatch):
    db=db_for(connection(),None)
    monkeypatch.setattr(credential_cipher,"decrypt",lambda _:TOKEN)
    monkeypatch.setattr(svc,"probe",AsyncMock(return_value="verified"))
    with pytest.raises(svc.ConnectionChanged): await svc.verify(db,ORG,GRANT)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_connection_does_not_decrypt(monkeypatch):
    decrypt=MagicMock(side_effect=AssertionError)
    monkeypatch.setattr(credential_cipher,"decrypt",decrypt)
    for row,gid in [(None,GRANT),(connection(),"bad"),(NS(access_token_enc=None),GRANT)]:
        with pytest.raises(svc.ConnectionMissing): await svc.verify(db_for(row),ORG,gid)
    decrypt.assert_not_called()


@pytest.mark.asyncio
async def test_cipher_failure_is_unavailable(monkeypatch):
    monkeypatch.setattr(credential_cipher,"decrypt",MagicMock(side_effect=credential_cipher.CredentialDecryptionError()))
    monkeypatch.setattr(svc,"probe",AsyncMock(side_effect=AssertionError))
    result=await svc.verify(db_for(connection()),ORG,GRANT)
    assert result["health"]=="unavailable"
