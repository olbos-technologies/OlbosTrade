"""
Unit tests for IBKRClient using mocked ib_insync responses.
Run with: pytest tests/test_ibkr_client.py -v
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import asyncio
import pytest

from app.broker.ibkr_client import IBKRClient


@pytest.fixture(autouse=True)
def _reap_account_subscribe_tasks():
    """Cancel the account-subscription task these tests leave behind.

    `get_account_summary` spawns `_subscribe_account_updates` via
    `ensure_future` and stores it on the instance. In production `disconnect()`
    cancels it through `_reset_account_subscription`; these tests build clients
    and never disconnect, so the task was still pending when the event loop
    closed and printed "Task was destroyed but it is pending!".

    This cannot live in the shared conftest fixture, which stops SERVICES —
    module-level singletons with their own shutdown API. An IBKRClient is
    per-instance and per-test, and nothing global holds the ones a test
    created.

    It is still targeted rather than a sweep: it cancels tasks running ONE
    named coroutine function, not whatever happens to be pending. A new
    leaking task from somewhere else is not silently tidied up by this — it
    still shows up as a warning and in CI, which is the point.
    """
    yield

    target = IBKRClient._subscribe_account_updates
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        return
    if loop.is_closed():
        return

    doomed = []
    for task in asyncio.all_tasks(loop) if loop.is_running() else _pending(loop):
        coro = getattr(task, "get_coro", lambda: None)()
        if getattr(coro, "cr_code", None) is getattr(target, "__code__", None):
            task.cancel()
            doomed.append(task)
    if doomed:
        loop.run_until_complete(asyncio.gather(*doomed, return_exceptions=True))


def _pending(loop):
    """Tasks on a loop that is not currently running."""
    try:
        return [t for t in asyncio.all_tasks(loop) if not t.done()]
    except RuntimeError:
        return []


@pytest.fixture
def client():
    c = IBKRClient()
    c._connected = True
    c.ib.isConnected = MagicMock(return_value=True)
    return c


@pytest.mark.asyncio
async def test_connect_succeeds_on_first_attempt():
    """connect() should succeed without retrying when TWS responds."""
    client = IBKRClient()
    with patch.object(client.ib, "connectAsync", new_callable=AsyncMock) as mock_connect, \
         patch.object(client.ib, "reqMarketDataType"):
        await client.connect()
    mock_connect.assert_called_once()
    assert client._connected is True


@pytest.mark.asyncio
async def test_connect_retries_on_failure_then_raises():
    """connect() should retry MAX_RETRIES times then raise ConnectionError."""
    client = IBKRClient()
    with patch.object(client.ib, "connectAsync",
                      new_callable=AsyncMock,
                      side_effect=ConnectionRefusedError("TWS not running")):
        with patch("app.broker.ibkr_client.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError, match="Failed to connect"):
                await client.connect()


@pytest.mark.asyncio
async def test_get_account_summary_maps_fields_correctly(client):
    """get_account_summary should map IBKR account tags to AccountSummary fields."""
    mock_values = [
        MagicMock(tag="NetLiquidation", value="25000.00", currency="USD"),
        MagicMock(tag="TotalCashValue", value="20000.00", currency="USD"),
        MagicMock(tag="OptionBuyingPower", value="15000.00", currency="USD"),
    ]
    client.ib.accountValues = MagicMock(return_value=mock_values)
    client.ib.managedAccounts = MagicMock(return_value=["DU123456"])
    client.ib.reqAccountUpdatesAsync = AsyncMock(return_value=None)

    summary = await client.get_account_summary()

    assert summary.account_id == "DU123456"
    assert summary.net_liquidation == Decimal("25000.00")
    assert summary.cash_balance == Decimal("20000.00")
    assert summary.buying_power == Decimal("15000.00")
    assert summary.trading_mode == "paper"


@pytest.mark.asyncio
async def test_get_account_summary_subscribes_once_not_every_call(client):
    """The account-updates subscription must be established exactly once,
    not repeated on every call — repeating it caused IBKR error 322, and
    unsubscribing right after (the previous behavior) froze accountValues()
    at a single point-in-time snapshot forever. Two consecutive calls must
    read live values without triggering a second subscribe."""
    client.ib.managedAccounts = MagicMock(return_value=["DU123456"])

    first_snapshot = [MagicMock(tag="NetLiquidation", value="25000.00", currency="USD")]
    second_snapshot = [MagicMock(tag="NetLiquidation", value="25500.00", currency="USD")]

    # Model the real lifecycle rather than a fixed call count: accountValues()
    # is EMPTY until a subscribe succeeds, then reflects the live push. A
    # plain side_effect list assumed exactly one read per call and seeded a
    # populated cache on a fresh client — impossible in practice, and it broke
    # when get_account_summary() gained a cache check before the subscribe.
    _state = {"subscribed": False, "reads": 0}

    async def _subscribe(_acct):
        _state["subscribed"] = True
    client.ib.reqAccountUpdatesAsync = AsyncMock(side_effect=_subscribe)

    def _account_values():
        if not _state["subscribed"]:
            return []
        _state["reads"] += 1
        return first_snapshot if _state["reads"] == 1 else second_snapshot
    client.ib.accountValues = MagicMock(side_effect=_account_values)

    first = await client.get_account_summary()
    second = await client.get_account_summary()

    assert first.net_liquidation == Decimal("25000.00")
    assert second.net_liquidation == Decimal("25500.00")  # proves the second call sees fresh data
    # ib_insync's real reqAccountUpdatesAsync(self, account: str) takes only
    # one positional argument — a plain AsyncMock() doesn't enforce that
    # signature, which is exactly how a 2-arg call (True, account_id) shipped
    # to production and crashed with "takes 2 positional arguments but 3
    # were given" on every account fetch. Pin the exact call shape so a
    # regression back to the wrong signature fails here, not in prod.
    client.ib.reqAccountUpdatesAsync.assert_called_once_with("DU123456")


@pytest.mark.asyncio
async def test_get_account_summary_concurrent_calls_subscribe_only_once(client):
    """Regression test for a real production incident (2026-08-25): several
    IBKRRequestCoordinator workers can all call get_account_summary() at
    once right after a fresh connect() (when _account_subscribed is still
    False and a burst of ACCOUNT_SUMMARY jobs typically lands together).
    Without single-flight coordination, every one of them sees False and
    fires its own reqAccountUpdatesAsync concurrently on the same shared
    `ib` connection — observed live as every coordinator worker piling up
    and never completing. An artificial delay on the mocked call holds the
    race window open long enough that, without single-flighting the real
    attempt via self._account_subscribe_task, this test would see more
    than one call."""
    client.ib.managedAccounts = MagicMock(return_value=["DU123456"])
    client.ib.accountValues = MagicMock(return_value=[])

    import asyncio as _asyncio

    async def _slow_subscribe(_account: str) -> None:
        await _asyncio.sleep(0.05)

    client.ib.reqAccountUpdatesAsync = AsyncMock(side_effect=_slow_subscribe)

    await _asyncio.gather(*(client.get_account_summary() for _ in range(6)))

    client.ib.reqAccountUpdatesAsync.assert_called_once_with("DU123456")
    assert client._account_subscribed is True


@pytest.mark.asyncio
async def test_get_account_summary_hung_subscribe_allows_retry_after_giving_up(client):
    """Second regression test for the same 2026-08-25 incident: the shared
    subscribe task itself must eventually give up if IBKR genuinely never
    responds — ACCOUNT_SUBSCRIBE_TIMEOUT_SECONDS bounds the ONE real
    attempt (self._account_subscribe_task), so a permanent hang ends that
    task with an exception rather than leaving it pending forever. Once
    it's .done(), the next caller must discard it and start a fresh
    attempt instead of re-attaching to a dead task forever."""
    client.ib.managedAccounts = MagicMock(return_value=["DU123456"])
    client.ib.accountValues = MagicMock(return_value=[])

    import asyncio as _asyncio

    from app.broker import ibkr_client as _ibkr_client_module

    async def _hangs_forever(_account: str) -> None:
        await _asyncio.sleep(3600)

    async def _succeeds(_account: str) -> None:
        return None

    client.ib.reqAccountUpdatesAsync = AsyncMock(side_effect=_hangs_forever)

    with patch.object(_ibkr_client_module, "ACCOUNT_SUBSCRIBE_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(_asyncio.TimeoutError):
            await client.get_account_summary()

    assert client._account_subscribed is False  # first attempt's failure must not poison the flag
    assert client._account_subscribe_task.done()

    client.ib.reqAccountUpdatesAsync = AsyncMock(side_effect=_succeeds)
    await client.get_account_summary()  # would hang here too if the dead task were reused

    assert client._account_subscribed is True


@pytest.mark.asyncio
async def test_get_account_summary_caller_timeout_does_not_kill_shared_subscribe(client):
    """The single-flight design's core guarantee, and the specific gap a
    lock-based design doesn't cover: one caller giving up on ITS OWN
    timeout must never cancel the shared subscribe task for anyone else
    still waiting on it (or arriving right after) — asyncio.shield() is
    what makes this hold. Mirrors paper_trade.py/account_state.py, which
    wrap get_account_summary() in a bare asyncio.wait_for() with no shield
    of their own — if the shared task weren't shielded, THEIR timeout
    would cancel the one real in-flight IBKR request, forcing yet another
    redundant resend for whoever calls next (the exact production
    pathology this design replaces)."""
    client.ib.managedAccounts = MagicMock(return_value=["DU123456"])
    client.ib.accountValues = MagicMock(return_value=[])

    import asyncio as _asyncio

    async def _slow_but_succeeds(_account: str) -> None:
        await _asyncio.sleep(0.08)

    client.ib.reqAccountUpdatesAsync = AsyncMock(side_effect=_slow_but_succeeds)

    # First caller times out on ITS OWN bare wait_for, well before the
    # real subscribe attempt (still running in the background) finishes.
    with pytest.raises(_asyncio.TimeoutError):
        await _asyncio.wait_for(client.get_account_summary(), timeout=0.02)

    assert client._account_subscribed is False  # not resolved yet — still legitimately in flight

    # Give the shared task time to finish on its own, unmolested by the
    # first caller's cancelled wait.
    await _asyncio.sleep(0.1)
    assert client._account_subscribed is True

    # A second, later caller must reuse that same completed result rather
    # than triggering a second real subscribe attempt.
    await client.get_account_summary()
    client.ib.reqAccountUpdatesAsync.assert_called_once_with("DU123456")


@pytest.mark.asyncio
async def test_get_account_summary_matches_real_ib_insync_signature(client):
    """A plain AsyncMock() doesn't enforce the real method's signature —
    that gap is exactly how a call passing 2 positional args into
    ib_insync's 1-arg reqAccountUpdatesAsync(account: str) shipped to
    production. Autospec the mock against client.ib's real bound method so
    a wrong-arity call raises TypeError here instead of in prod."""
    import asyncio as _asyncio

    client.ib.managedAccounts = MagicMock(return_value=["DU123456"])
    client.ib.accountValues = MagicMock(return_value=[])
    # The real reqAccountUpdatesAsync is a plain `def` returning an
    # Awaitable[None] (an ib_insync Future), not an `async def` — autospec
    # a synchronous mock and give it an already-resolved Future to return,
    # matching what our code actually awaits.
    done_future: "_asyncio.Future" = _asyncio.get_event_loop().create_future()
    done_future.set_result(None)
    client.ib.reqAccountUpdatesAsync = create_autospec(
        client.ib.reqAccountUpdatesAsync, return_value=done_future
    )

    await client.get_account_summary()  # would raise TypeError if the call shape were wrong

    client.ib.reqAccountUpdatesAsync.assert_called_once_with("DU123456")


@pytest.mark.asyncio
async def test_connect_resets_account_subscription_flag():
    """A fresh connect() must reset _account_subscribed — the old
    subscription (if any) lived on a socket that's now gone, so the next
    get_account_summary() call must resubscribe rather than trust a stale
    flag from a previous connection."""
    client = IBKRClient()
    client._account_subscribed = True  # simulate a prior live connection
    with patch.object(client.ib, "connectAsync", new_callable=AsyncMock), \
         patch.object(client.ib, "reqMarketDataType"):
        await client.connect()
    assert client._account_subscribed is False


@pytest.mark.asyncio
async def test_connect_cancels_stale_subscribe_task():
    """A subscribe task tied to the old socket's Future will never resolve
    on a fresh connection — connect() must discard (and cancel) it rather
    than leave it referenced, or get_account_summary() would incorrectly
    treat a subscribe as still legitimately in flight forever."""
    import asyncio as _asyncio

    client = IBKRClient()
    stale_task = _asyncio.ensure_future(_asyncio.sleep(3600))
    client._account_subscribe_task = stale_task
    with patch.object(client.ib, "connectAsync", new_callable=AsyncMock), \
         patch.object(client.ib, "reqMarketDataType"):
        await client.connect()
    assert client._account_subscribe_task is None
    await _asyncio.sleep(0)  # let the event loop actually deliver the cancellation
    assert stale_task.cancelled()


@pytest.mark.asyncio
async def test_disconnect_resets_account_subscription_flag(client):
    client._account_subscribed = True
    client.ib.disconnect = MagicMock()
    await client.disconnect()
    assert client._account_subscribed is False


@pytest.mark.asyncio
async def test_get_positions_filters_options_only(client):
    """get_positions should return only OPT contracts, not stocks."""
    stock_contract = MagicMock()
    stock_contract.secType = "STK"
    stock_contract.symbol = "SPY"

    opt_contract = MagicMock()
    opt_contract.secType = "OPT"
    opt_contract.symbol = "SPY"
    opt_contract.localSymbol = "SPY240119P00450000"
    opt_contract.strike = 450.0
    opt_contract.lastTradeDateOrContractMonth = "20240119"
    opt_contract.right = "P"

    mock_items = [
        MagicMock(contract=stock_contract, position=100, averageCost=450.0,
                   marketPrice=455.0, unrealizedPNL=500.0),
        MagicMock(contract=opt_contract, position=-1, averageCost=125.0,
                   marketPrice=110.0, unrealizedPNL=15.0),
    ]

    with patch.object(client.ib, "portfolio", return_value=mock_items):
        positions = await client.get_positions()

    # get_positions returns all OPT + STK/ETF; filter to options only
    options = [p for p in positions if p.strike > 0]
    assert len(options) == 1
    assert options[0].option_type == "put"
    assert options[0].strike == Decimal("450.0")
    assert options[0].quantity == -1


@pytest.mark.asyncio
async def test_get_positions_carries_live_price_and_unrealized_pnl(client):
    """current_price/unrealized_pnl must come from IBKR's own portfolio() marks —
    this is what MFE/MAE excursion tracking (and the journal's recorded values)
    depend on; reqPositionsAsync() never carried these fields at all."""
    stock_contract = MagicMock()
    stock_contract.secType = "STK"
    stock_contract.symbol = "AAPL"

    mock_items = [
        MagicMock(contract=stock_contract, position=10, averageCost=150.0,
                   marketPrice=162.5, unrealizedPNL=125.0),
    ]

    with patch.object(client.ib, "portfolio", return_value=mock_items):
        positions = await client.get_positions()

    assert len(positions) == 1
    assert positions[0].current_price == Decimal("162.5")
    assert positions[0].unrealized_pnl == Decimal("125.0")


@pytest.mark.asyncio
async def test_get_equity_positions_carries_price_and_pnl(client):
    stock_contract = MagicMock()
    stock_contract.secType = "STK"
    stock_contract.symbol = "MSFT"

    mock_items = [
        MagicMock(contract=stock_contract, position=5, averageCost=300.0,
                   marketPrice=310.0, unrealizedPNL=50.0),
    ]

    with patch.object(client.ib, "portfolio", return_value=mock_items):
        equity_positions = await client.get_equity_positions()

    assert len(equity_positions) == 1
    assert equity_positions[0].current_price == Decimal("310.0")
    assert equity_positions[0].unrealized_pnl == Decimal("50.0")
    assert equity_positions[0].market_value == Decimal("1550.0")


# ── cancel_open_orders (manual close: clear a bracket's stop/target legs) ─────

@pytest.mark.asyncio
async def test_cancel_open_orders_only_cancels_matching_symbol(client):
    aapl_trade = MagicMock()
    aapl_trade.contract.symbol = "AAPL"
    msft_trade = MagicMock()
    msft_trade.contract.symbol = "MSFT"
    client.ib.openTrades = MagicMock(return_value=[aapl_trade, msft_trade])
    client.ib.cancelOrder = MagicMock()

    with patch("app.broker.ibkr_client.asyncio.sleep", new_callable=AsyncMock):
        count = await client.cancel_open_orders("aapl")  # lowercase input

    assert count == 1
    client.ib.cancelOrder.assert_called_once_with(aapl_trade.order)


@pytest.mark.asyncio
async def test_cancel_open_orders_returns_zero_when_none_match(client):
    other_trade = MagicMock()
    other_trade.contract.symbol = "TSLA"
    client.ib.openTrades = MagicMock(return_value=[other_trade])
    client.ib.cancelOrder = MagicMock()

    count = await client.cancel_open_orders("AAPL")

    assert count == 0
    client.ib.cancelOrder.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_open_orders_continues_after_one_cancel_fails(client):
    """One order's cancel throwing must not stop the rest from being cancelled."""
    bad_trade = MagicMock()
    bad_trade.contract.symbol = "AAPL"
    bad_trade.order = MagicMock(orderId=1)
    good_trade = MagicMock()
    good_trade.contract.symbol = "AAPL"
    good_trade.order = MagicMock(orderId=2)
    client.ib.openTrades = MagicMock(return_value=[bad_trade, good_trade])
    client.ib.cancelOrder = MagicMock(side_effect=[RuntimeError("boom"), None])

    with patch("app.broker.ibkr_client.asyncio.sleep", new_callable=AsyncMock):
        count = await client.cancel_open_orders("AAPL")

    assert count == 1  # only the second (successful) cancel counted
    assert client.ib.cancelOrder.call_count == 2


@pytest.mark.asyncio
async def test_get_options_chain_batches_and_maps_contracts_correctly(client):
    """get_options_chain should qualify+fetch in batched calls (one call for
    the whole chain, not one request per strike) and correctly map each
    qualified contract back to calls/puts using the contract's own
    right/strike fields (qualifyContractsAsync mutates in place and may
    drop contracts IBKR can't resolve, so positional correspondence with
    the original candidate list can't be assumed)."""
    expiry = "2026-09-18"
    expiry_ib = "20260918"

    chain_param = MagicMock()
    chain_param.expirations = [expiry_ib]
    chain_param.strikes = [100.0, 105.0]

    def _is_underlying(contracts) -> bool:
        return len(contracts) == 1 and getattr(contracts[0], "secType", None) == "STK"

    async def _qualify_side_effect(*contracts):
        return list(contracts)  # pretend every contract qualifies, in place

    def _make_option_ticker(contract):
        t = MagicMock()
        t.contract = contract
        t.bid, t.ask, t.last = 1.0, 1.2, 1.1
        t.volume, t.callOpenInterest, t.putOpenInterest = 10, 50, 0
        g = MagicMock()
        g.delta, g.gamma, g.theta, g.vega, g.impliedVol = 0.5, 0.02, -0.01, 0.1, 0.25
        t.modelGreeks = g
        return t

    async def _tickers_side_effect(*contracts):
        if _is_underlying(contracts):
            underlying_ticker = MagicMock()
            underlying_ticker.last = 250.0
            return [underlying_ticker]
        return [_make_option_ticker(c) for c in contracts]

    with patch.object(client.ib, "qualifyContractsAsync", new_callable=AsyncMock,
                      side_effect=_qualify_side_effect) as mock_qualify, \
         patch.object(client.ib, "reqSecDefOptParamsAsync", new_callable=AsyncMock,
                      return_value=[chain_param]), \
         patch.object(client.ib, "reqTickersAsync", new_callable=AsyncMock,
                      side_effect=_tickers_side_effect) as mock_tickers:
        result = await client.get_options_chain("SPY", expiry)

    assert len(result.calls) == 2
    assert len(result.puts) == 2
    assert {float(c.strike) for c in result.calls} == {100.0, 105.0}
    assert {float(p.strike) for p in result.puts} == {100.0, 105.0}
    assert result.calls[0].greeks.delta == 0.5
    assert float(result.underlying_price) == 250.0

    # One qualify + one tickers call for the underlying, and exactly one more
    # of each for the whole options batch (4 contracts, under the 50-contract
    # chunk size) — not one pair per strike as the old sequential loop did.
    assert mock_qualify.call_count == 2
    assert mock_tickers.call_count == 2


@pytest.mark.asyncio
async def test_get_bars_returns_parsed_bars(client):
    """get_bars() should map ib_insync BarData into our Bar model."""
    mock_bar = MagicMock(date="2026-08-20", open=440.0, high=442.0, low=439.0, close=441.5, volume=1_000_000)
    client.ib.qualifyContractsAsync = AsyncMock(return_value=None)
    client.ib.reqHistoricalDataAsync = AsyncMock(return_value=[mock_bar])

    bars = await client.get_bars("SPY", limit=5)

    assert len(bars) == 1
    assert bars[0].close == Decimal("441.5")
    assert bars[0].volume == 1_000_000


@pytest.mark.asyncio
async def test_get_bars_raises_clear_timeout_when_ibkr_hangs(client):
    """A stuck reqHistoricalDataAsync call must not hang forever — it should
    raise a TimeoutError with a real message (not the blank default), so
    DataFetcher.fetch_ohlcv()'s existing except-Exception fallback to
    yfinance actually engages instead of the caller hanging indefinitely."""
    import asyncio as _asyncio

    async def _never_returns(*args, **kwargs):
        await _asyncio.sleep(3600)

    client.ib.qualifyContractsAsync = AsyncMock(return_value=None)
    client.ib.reqHistoricalDataAsync = _never_returns

    with patch("app.broker.ibkr_client.HISTORICAL_DATA_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(_asyncio.TimeoutError, match="SPY"):
            await client.get_bars("SPY", limit=5)


@pytest.mark.asyncio
async def test_get_bars_raises_clear_timeout_when_qualify_hangs(client):
    """The timeout must cover qualifyContractsAsync too, not just
    reqHistoricalDataAsync — confirmed live in production that a degraded
    IBKR connection can hang at the qualify step specifically, before the
    historical-data request is even sent."""
    import asyncio as _asyncio

    async def _never_returns(*args, **kwargs):
        await _asyncio.sleep(3600)

    client.ib.qualifyContractsAsync = _never_returns
    client.ib.reqHistoricalDataAsync = AsyncMock(return_value=[])

    with patch("app.broker.ibkr_client.HISTORICAL_DATA_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(_asyncio.TimeoutError, match="SPY"):
            await client.get_bars("SPY", limit=5)


@pytest.mark.asyncio
async def test_get_index_bars_raises_clear_timeout_when_ibkr_hangs(client):
    """Same fix applies to get_index_bars() (VIX etc.) — identical
    unguarded reqHistoricalDataAsync call, same hang risk."""
    import asyncio as _asyncio

    async def _never_returns(*args, **kwargs):
        await _asyncio.sleep(3600)

    client.ib.qualifyContractsAsync = AsyncMock(return_value=None)
    client.ib.reqHistoricalDataAsync = _never_returns

    with patch("app.broker.ibkr_client.HISTORICAL_DATA_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(_asyncio.TimeoutError, match="VIX"):
            await client.get_index_bars("VIX")


def test_chunked_splits_into_bounded_groups():
    from app.broker.ibkr_client import _chunked

    assert _chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert _chunked([], 2) == []
    assert _chunked([1, 2], 10) == [[1, 2]]


def test_safe_int_and_safe_decimal_handle_nan_and_none():
    import math
    from decimal import Decimal
    from app.broker.ibkr_client import _safe_int, _safe_decimal

    assert _safe_int(float("nan")) == 0
    assert _safe_int(None) == 0
    assert _safe_int(42) == 42
    assert _safe_decimal(float("nan")) == Decimal("0")
    assert _safe_decimal(None) == Decimal("0")
    assert _safe_decimal(1.5) == Decimal("1.5")
    assert not math.isnan(float(_safe_decimal(float("nan"))))


# ── Account-updates pre-cancel (2026-08-27 blackout) ──────────────────────────
# reqAccountUpdates is a subscription and IBKR allows one per connection. When
# it believes one is already open it silently ignores a new request — accepted,
# never answered — so wait_for cancels the inner future at the timeout and
# raises TimeoutError with no error from IBKR at all. Every account read failed
# this way for a full day. Cancelling first makes the request go out from a
# known-clean state.

@pytest.mark.asyncio
async def test_subscribe_cancels_any_existing_subscription_first():
    client = IBKRClient()
    client._connected = True
    client.ib = MagicMock()
    client.ib.isConnected = MagicMock(return_value=True)
    client.ib.managedAccounts = MagicMock(return_value=["DU6720"])
    client.ib.reqAccountUpdates = MagicMock()
    client.ib.reqAccountUpdatesAsync = AsyncMock(return_value=None)

    await client._subscribe_account_updates()

    client.ib.reqAccountUpdates.assert_called_once_with(False, "DU6720")
    client.ib.reqAccountUpdatesAsync.assert_awaited_once_with("DU6720")
    assert client._account_subscribed is True


@pytest.mark.asyncio
async def test_subscribe_proceeds_when_the_pre_cancel_raises():
    """The cancel is a best-effort reset, not a precondition — a failure there
    must not block the subscribe it exists to enable."""
    client = IBKRClient()
    client._connected = True
    client.ib = MagicMock()
    client.ib.isConnected = MagicMock(return_value=True)
    client.ib.managedAccounts = MagicMock(return_value=["DU6720"])
    client.ib.reqAccountUpdates = MagicMock(side_effect=Exception("no active sub"))
    client.ib.reqAccountUpdatesAsync = AsyncMock(return_value=None)

    await client._subscribe_account_updates()

    client.ib.reqAccountUpdatesAsync.assert_awaited_once_with("DU6720")
    assert client._account_subscribed is True


@pytest.mark.asyncio
async def test_pre_cancel_happens_before_the_subscribe_not_after():
    """Ordering is the whole point: cancelling after would leave the new
    subscription torn down, and cancelling nothing would not clear the wedge."""
    client = IBKRClient()
    client._connected = True
    calls: list[str] = []
    client.ib = MagicMock()
    client.ib.isConnected = MagicMock(return_value=True)
    client.ib.managedAccounts = MagicMock(return_value=["DU6720"])
    client.ib.reqAccountUpdates = MagicMock(side_effect=lambda *a: calls.append("cancel"))

    async def _sub(_acct):
        calls.append("subscribe")
    client.ib.reqAccountUpdatesAsync = _sub

    await client._subscribe_account_updates()

    assert calls == ["cancel", "subscribe"], calls


# ── Subscribe failure must not withhold already-cached data ───────────────────
# 2026-08-27: get_account_summary() awaited the subscribe before reading, so a
# failing subscribe meant the read below was never reached. It failed for a full
# day while accountValues() held 184 entries including NetLiquidation. Every
# account read returned nothing and the margin guardrails ran blind. The
# subscribe only has to succeed once per connection to populate the cache; a
# later failure says nothing about whether the numbers are readable.

def _acct_val(tag, value, currency="USD"):
    return SimpleNamespace(tag=tag, value=str(value), currency=currency, account="DU6720")


def _client_with_failing_subscribe(cached):
    client = IBKRClient()
    client._connected = True
    client._account_subscribed = False
    client.ib = MagicMock()
    client.ib.isConnected = MagicMock(return_value=True)
    client.ib.managedAccounts = MagicMock(return_value=["DU6720"])
    client.ib.accountValues = MagicMock(return_value=cached)
    client.ib.reqAccountUpdates = MagicMock()
    client.ib.reqAccountUpdatesAsync = AsyncMock(side_effect=asyncio.TimeoutError())
    return client


@pytest.mark.asyncio
async def test_cached_values_are_served_when_the_subscribe_fails():
    cached = [
        _acct_val("NetLiquidation", "254029.86"),
        _acct_val("TotalCashValue", "-87618.91"),
        _acct_val("BuyingPower", "380535.17"),
    ]
    client = _client_with_failing_subscribe(cached)

    summary = await client.get_account_summary()

    assert float(summary.net_liquidation) == 254029.86, (
        "a failed subscribe must not withhold account values IBKR already streamed"
    )


@pytest.mark.asyncio
async def test_subscribe_failure_still_raises_when_nothing_is_cached():
    """The guard only covers the case where data exists. With an empty cache
    there is nothing to serve, so the failure must still surface."""
    client = _client_with_failing_subscribe([])

    with pytest.raises(Exception):
        await client.get_account_summary()


@pytest.mark.asyncio
async def test_populated_cache_returns_without_waiting_for_the_subscribe():
    """The previous fix served cached values but still awaited the doomed
    subscribe first — 30s, against caller timeouts of ~5s. It produced the
    right answer long after everyone stopped listening. A populated cache
    must short-circuit the wait entirely."""
    client = IBKRClient()
    client._connected = True
    client._account_subscribed = False
    client.ib = MagicMock()
    client.ib.isConnected = MagicMock(return_value=True)
    client.ib.managedAccounts = MagicMock(return_value=["DU6720"])
    client.ib.accountValues = MagicMock(return_value=[
        _acct_val("NetLiquidation", "254029.86"),
        _acct_val("TotalCashValue", "-87618.91"),
        _acct_val("BuyingPower", "380535.17"),
    ])
    client.ib.reqAccountUpdates = MagicMock()

    subscribe_started = asyncio.Event()

    async def _never_returns(_acct):
        subscribe_started.set()
        await asyncio.sleep(3600)      # stands in for the 30s timeout path
    client.ib.reqAccountUpdatesAsync = _never_returns

    # Hard bound: if this awaits the subscribe at all, it cannot finish in 1s.
    summary = await asyncio.wait_for(client.get_account_summary(), timeout=1.0)

    assert float(summary.net_liquidation) == 254029.86


# ── Background-subscribe exception handling ─────────────────────────────────
#
# get_account_summary() launches _subscribe_account_updates() as a background
# task and, once the account cache is populated, stops awaiting it. That left
# the task's TimeoutError unretrieved, so asyncio logged
# "Task exception was never retrieved" with a full traceback at ERROR level
# every ~30s — a condition the code deliberately tolerates, presented as a
# crash. It masked real failures: watching the first autopilot scan, the
# tracebacks flooded the log filter and had to be excluded by hand.

def _own(caplog):
    """Only this module's log records — caplog collects the whole process."""
    return [r for r in caplog.records if r.name == "app.broker.ibkr_client"]


async def _raise(exc):
    raise exc


async def _done_task(exc):
    t = asyncio.ensure_future(_raise(exc))
    await asyncio.sleep(0)
    return t


@pytest.mark.asyncio
async def test_subscribe_timeout_is_retrieved_and_logged_at_debug(caplog):
    """The expected timeout must be marked retrieved (so asyncio stays quiet)
    and must not be logged at WARNING or above."""
    task = await _done_task(asyncio.TimeoutError("no answer"))

    with caplog.at_level("DEBUG", logger="app.broker.ibkr_client"):
        IBKRClient._on_subscribe_task_done(task)

    # The actual anti-noise assertion: asyncio only logs the traceback for a
    # task whose exception was never retrieved.
    assert task._log_traceback is False
    # Scoped to this logger — caplog captures every logger in the process, so
    # an unrelated warning from another test's teardown would otherwise fail
    # this (it did, in the full-suite run).
    assert not [r for r in _own(caplog) if r.levelno >= 30]


@pytest.mark.asyncio
async def test_unexpected_subscribe_failure_still_warns(caplog):
    """Silencing the timeout must not silence anything else."""
    task = await _done_task(RuntimeError("gateway rejected the subscription"))

    with caplog.at_level("DEBUG", logger="app.broker.ibkr_client"):
        IBKRClient._on_subscribe_task_done(task)

    warnings = [r for r in _own(caplog) if r.levelno >= 30]
    assert len(warnings) == 1
    assert "gateway rejected the subscription" in warnings[0].getMessage()
    assert task._log_traceback is False


@pytest.mark.asyncio
async def test_cancelled_subscribe_is_not_logged():
    """_reset_account_subscription() cancels this task on reconnect. Calling
    .exception() on a cancelled task raises CancelledError, so the callback
    must check cancelled() first or it breaks the reconnect path."""
    async def _forever():
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_forever())
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    IBKRClient._on_subscribe_task_done(task)   # must not raise


@pytest.mark.asyncio
async def test_retrieving_the_exception_does_not_hide_it_from_awaiters():
    """The regression that would matter: get_account_summary() still awaits
    this task when the cache is empty, and must still see the failure."""
    task = await _done_task(asyncio.TimeoutError("no answer"))
    IBKRClient._on_subscribe_task_done(task)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.shield(task)


@pytest.mark.asyncio
async def test_get_account_summary_attaches_the_callback():
    """Wiring test — the callback is worthless if the launch site skips it."""
    client = IBKRClient()
    client._connected = True
    client.ib.isConnected = MagicMock(return_value=True)
    client.ib.managedAccounts = MagicMock(return_value=["DU6720"])
    client.ib.accountValues = MagicMock(return_value=[
        _acct_val("NetLiquidation", "254029.86"),
        _acct_val("TotalCashValue", "-87618.91"),
        _acct_val("BuyingPower", "380535.17"),
    ])
    client.ib.reqAccountUpdates = MagicMock()

    async def _times_out(_acct):
        raise asyncio.TimeoutError("no answer")
    client.ib.reqAccountUpdatesAsync = _times_out

    await client.get_account_summary()
    task = client._account_subscribe_task
    assert task is not None

    # _subscribe_account_updates wraps the call in asyncio.wait_for, so the
    # task needs more than a tick to settle; the done-callback is then
    # scheduled with call_soon, needing one more.
    for _ in range(100):
        if task.done():
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0)

    assert task.done()
    # Retrieved by the callback, not left for asyncio to shout about.
    assert task._log_traceback is False
