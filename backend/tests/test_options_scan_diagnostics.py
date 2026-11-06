from unittest.mock import AsyncMock, patch
from types import SimpleNamespace as NS
import inspect
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("regime",[None,NS(options_allowed=False,regime="crisis")])
async def test_regime_gate_is_recorded_without_loading_data(regime):
    import app.main as main
    with patch.object(main,"_current_regime",regime),patch.object(main,"_record_options_rejection",new_callable=AsyncMock) as record,patch.object(main,"_yf_bars",new_callable=AsyncMock) as bars:
        assert await main._run_options_scan("SPY",execute=False) is None
    assert record.await_count==1 and record.call_args.args[1].startswith("regime_gate:")
    bars.assert_not_called()


@pytest.mark.asyncio
async def test_scan_worker_timeouts_are_recorded_and_never_dispatch():
    import app.main as main
    from app.core.config import settings
    from app.services.observability import observability
    with patch.object(type(settings),"get_equity_watchlist",return_value=["SPY"]),patch.object(main,"_run_options_scan",new_callable=AsyncMock,side_effect=TimeoutError()),patch.object(main,"_record_options_rejection",new_callable=AsyncMock) as record,patch("app.api.routes.trade_desk.handle_signal",new_callable=AsyncMock) as handle:
        await main._run_options_scan_watchlist()
    assert record.call_args.args==("SPY","scan_timeout")
    handle.assert_not_called()
    assert observability.recent(name="options_scan_cycle")[0]["candidates"]==0


def test_quality_wiring_does_not_replace_execution_ranker_and_report_is_inconclusive():
    from pathlib import Path
    import app.main as main
    from app.services import options_scan_engine as research
    src=inspect.getsource(main._live_spread_quote)
    assert "score_spread_quality(short_c, long_c" in src
    assert "trade_frequency_controller.rank(candidates)" in inspect.getsource(main._run_options_scan_watchlist)
    assert "contract_quality" in inspect.getsource(main._run_options_scan)
    assert "score_spread_quality(short_c, long_c" in inspect.getsource(research._live_spread_quote)
    report=Path("scripts/options_rejection_report.py").read_text()
    assert len(report)>1000 and "Inconclusive:" in report
    assert "does not establish that any candidate qualified" in report


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["app.main", "app.services.options_scan_engine"])
async def test_live_quote_exposes_quality_without_repricing_or_authorizing(module):
    import importlib
    from datetime import date, datetime, timezone
    from decimal import Decimal
    from app.broker.broker_interface import OptionContract, Greeks

    scanner = importlib.import_module(module)
    stamp = datetime.now(timezone.utc)
    short = OptionContract(
        symbol="SPY261218P00500000", underlying="SPY", expiration=date(2099, 12, 18),
        strike=Decimal("500"), option_type="put", bid=Decimal("2"), ask=Decimal("2.04"),
        quote_timestamp=stamp, quote_feed="opra", last=0, volume=0, open_interest=0,
        greeks=Greeks(delta=-.20, gamma=.01, theta=-.04, vega=.1, implied_vol=.3),
    )
    long = short.model_copy(update={"symbol":"SPY261218P00490000", "strike":Decimal("490"),
        "bid":Decimal("1"), "ask":Decimal("1.02")})
    broker = NS(get_options_chain=AsyncMock(return_value=NS(puts=[short,long],calls=[])))
    result = await scanner._live_spread_quote(broker,"SPY","2099-12-18",500,490,"put")
    assert result["net_credit"] == 101.0
    assert result["contract_quality"]["state"] == "scored"
    assert result["contract_quality"]["execution_authorized"] is False
    long.quote_timestamp = None
    result = await scanner._live_spread_quote(broker,"SPY","2099-12-18",500,490,"put")
    assert result["net_credit"] == 101.0
    assert result["contract_quality"]["score"] is None
    assert result["contract_quality"]["reason"] == "quote_time_unknown"
