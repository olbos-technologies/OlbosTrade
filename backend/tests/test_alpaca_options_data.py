"""Real adapter entry point; all HTTP reads mocked, never broker orders."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from app.broker.alpaca_client import AlpacaClient
from app.broker import alpaca_options_data as data
from app.core.config import settings


def contract(i):
    return {"symbol":f"SPY261218C{(400+i)*1000:08d}","expiration_date":"2026-12-18",
            "type":"call","strike_price":str(400+i),"underlying_symbol":"SPY"}


@pytest.mark.asyncio
async def test_adapter_batches_251_contracts_and_reads_both_page_types(monkeypatch):
    rows=[contract(i) for i in range(251)]
    calls=[]
    async def get(base,path,params):
        calls.append((path,params))
        if path=="/v2/options/contracts":
            return {"option_contracts":rows[:150] if "page_token" not in params else rows[150:],
                    "next_page_token":"next-contracts" if "page_token" not in params else None}
        batch=params["symbols"].split(",")
        assert len(batch)<=100 and params["feed"]=="opra"
        subset=batch[:1] if "page_token" not in params else batch[1:]
        return {"snapshots":{s:{"latestQuote":{"bp":1.0,"ap":1.05,"t":"2026-11-06T14:00:00Z"}} for s in subset},
                "next_page_token":"next-snapshots" if "page_token" not in params else None}
    client=AlpacaClient(api_key="synthetic",secret_key="synthetic",base_url="https://paper-api.alpaca.markets")
    monkeypatch.setattr(client,"_get",get)
    monkeypatch.setattr(client,"get_latest_quote",AsyncMock(side_effect=RuntimeError()))
    monkeypatch.setattr(settings,"alpaca_options_feed","opra")
    chain=await client.get_options_chain("spy","2026-12-18")
    assert len(chain.calls)==251 and len(calls)==8
    assert chain.calls[-1].quote_timestamp==datetime(2026,11,6,14,tzinfo=timezone.utc)
    assert chain.calls[-1].quote_feed=="opra" and chain.calls[-1].greeks is None


@pytest.mark.asyncio
async def test_missing_snapshot_stays_unknown_and_provider_default_not_called_opra():
    get=AsyncMock(side_effect=[{"option_contracts":[contract(0)]},{"snapshots":{}}])
    contracts,snaps=await data.collect_options(get,"paper","data","SPY","2026-12-18")
    assert len(contracts)==1 and snaps=={}
    assert "feed" not in get.call_args.kwargs["params"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["repeat","token_type","malformed","scope","conflict","cap","timeout","bad_feed","snap_scope","snap_conflict","bad_contract","page_cap","bad_snap_page"])
async def test_incomplete_or_wrong_scope_collections_raise(monkeypatch,kind):
    counter=0
    async def get(base,path,params):
        nonlocal counter
        counter+=1
        if kind=="timeout": raise TimeoutError()
        if path=="/v2/options/contracts":
            c=contract(0)
            if kind=="bad_contract": return {"option_contracts":[None]}
            if kind=="scope": c["underlying_symbol"]="OTHER"
            if kind=="conflict" and counter>1: c["strike_price"]="1"
            if kind=="malformed":return {"option_contracts":{}}
            token="repeat" if kind in ("repeat","conflict","page_cap") else (42 if kind=="token_type" else None)
            if kind=="page_cap":token=str(counter)
            return {"option_contracts":[c],"next_page_token":token}
        if kind=="snap_scope":return {"snapshots":{"OTHER":{}}}
        if kind=="bad_snap_page":return {"snapshots":[]}
        if kind=="snap_conflict":return {"snapshots":{contract(0)["symbol"]:{"v":counter}},"next_page_token":str(counter)}
        return {"snapshots":{}}
    if kind=="cap":monkeypatch.setattr(data,"MAX_CONTRACTS",0)
    if kind=="page_cap":monkeypatch.setattr(data,"MAX_PAGES",2)
    with pytest.raises((data.OptionsDataUnavailable,TimeoutError)):
        await data.collect_options(get,"paper","data","SPY","2026-12-18",feed="bad" if kind=="bad_feed" else None)


@pytest.mark.asyncio
async def test_later_http_failure_does_not_return_a_partial_chain():
    get=AsyncMock(side_effect=[{"option_contracts":[contract(0)],"next_page_token":"next"},RuntimeError("synthetic failure")])
    with pytest.raises(RuntimeError): await data.collect_options(get,"paper","data","SPY","2026-12-18")


@pytest.mark.asyncio
async def test_overall_deadline_cancels_a_stalled_read(monkeypatch):
    import asyncio
    cancelled = False
    async def get(*args, **kwargs):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True
    monkeypatch.setattr(data, "COLLECTION_TIMEOUT", .01)
    with pytest.raises(TimeoutError):
        await data.collect_options(get, "paper", "data", "SPY", "2026-12-18")
    assert cancelled
