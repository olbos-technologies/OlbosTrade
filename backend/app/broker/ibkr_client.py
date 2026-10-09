"""
Interactive Brokers client via ib_insync.
Connects to TWS paper on port 7497 by default.
All methods are async-compatible via ib_insync's asyncio event loop.
"""

from __future__ import annotations


import asyncio
import logging
import math as _math
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Tuple

from ib_insync import IB, Contract, Index, LimitOrder, Option, Stock

from app.broker.broker_interface import (
    CancelSweep,
    AccountSummary,
    Bar,
    BrokerInterface,
    EquityOrderResult,
    Greeks,
    OptionContract,
    OptionsChain,
    OrderResult,
    Position,
    Quote,
    SpreadLeg,
    SpreadOrder,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def _safe_int(value, default: int = 0) -> int:
    """int(value or default), but NaN-safe — IBKR returns NaN (not None) for
    volume/open-interest on untraded contracts, and int(nan) raises ValueError."""
    if value is None or (isinstance(value, float) and _math.isnan(value)):
        return default
    return int(value)


def _safe_decimal(value, default: float = 0) -> Decimal:
    """Decimal(str(value or default)), but NaN-safe — IBKR returns NaN (not
    None) for bid/ask/last on untraded contracts. Decimal('nan') doesn't
    raise, but OptionContract (a Pydantic model) rejects non-finite Decimals."""
    if value is None or (isinstance(value, float) and _math.isnan(value)):
        return Decimal(str(default))
    return Decimal(str(value))


# IBKR caps concurrent market-data lines per connection tier — batch chain
# qualify/ticker requests in conservative chunks rather than one giant call.
_CHAIN_BATCH_SIZE = 50


def _chunked(items: list, size: int) -> "list[list]":
    return [items[i:i + size] for i in range(0, len(items), size)]


# How long to wait for an IBKR historical-data response before giving up.
# Neither qualifyContractsAsync nor reqHistoricalDataAsync has a timeout of
# its own — either can hang the caller indefinitely on a slow/stuck TWS
# response (confirmed live: a degraded connection hung qualifyContractsAsync
# specifically, not just the reqHistoricalDataAsync call this was first
# written to guard). get_bars()/get_index_bars() wrap their whole fetch body
# (qualify + historical request) in one bounded wait_for so either step can
# trip it. They're the only callers of this timeout; on timeout they raise,
# and their one caller (DataFetcher.fetch_ohlcv) already falls back to
# yfinance on any exception.
HISTORICAL_DATA_TIMEOUT_SECONDS = 30.0

# Overall bound on the ONE real, shared reqAccountUpdatesAsync attempt that
# backs get_account_summary()'s subscribe (see that method's docstring for
# the single-flight design this guards). ib_insync's reqAccountUpdatesAsync
# is not idempotent — every call sends a fresh IBKR message and creates a
# fresh Future under a shared fixed key, silently orphaning any earlier
# still-outstanding attempt's Future (confirmed by reading ib_insync's own
# Wrapper.startReq(), which does `self._futures[key] = future`, no reuse).
# Retrying independently every few seconds (the previous design here)
# compounded this: each retry sent another redundant request and abandoned
# the previous one, so a real IBKR response arriving late would resolve
# whichever attempt happened to currently hold the key — not the one that
# sent it — observed live as wildly variable multi-second delays that had
# nothing to do with any per-caller timeout. 30s matches this module's
# existing convention for a generous real IBKR round-trip bound (see
# HISTORICAL_DATA_TIMEOUT_SECONDS) — long enough that a genuinely slow but
# live response still resolves normally instead of being needlessly
# abandoned and retried, since retrying here is the expensive part.
ACCOUNT_SUBSCRIBE_TIMEOUT_SECONDS = 30.0

# How old the account-values cache may get before it stops counting as live.
#
# ib.accountValues() is a local dict TWS pushes into; nothing about reading it
# can fail or time out, so a dead push stream is indistinguishable from a quiet
# one by inspection alone — the last-known numbers are returned forever, with
# full confidence, at any age. That is the failure this bound exists to make
# visible: on 2026-08-27 the subscribe had been failing all day while the cache
# still held 184 values, and once get_account_summary() started serving that
# cache (correctly — the data was live, confirmed by watching NetLiquidation
# move) the only thing left separating "live" from "frozen" was that nobody was
# looking at the clock.
#
# reqAccountUpdates pushes on change and re-sends the full set about every 3
# minutes, so 10 minutes is roughly three missed cycles — long enough that
# ordinary jitter or a genuinely idle account never trips it, short enough that
# a stream which has actually stopped is caught inside one scan interval.
ACCOUNT_VALUES_STALE_AFTER_SECONDS = 600.0

# reqAllOpenOrders is a read request and normally answers immediately; this is
# only here so a wedged gateway degrades to the local cache instead of hanging
# an operator-facing route.
OPEN_ORDERS_TIMEOUT_SECONDS = 10.0

# ── Fill timeout & retry settings (overridden by .env via settings) ────────────
# How long to wait for a fill before cancelling and retrying at a better price.
FILL_TIMEOUT_SECONDS = 60        # Wait up to 60s for the first fill attempt
RETRY_PRICE_STEP = 0.05          # On retry, lower limit by $0.05 (accept less credit)
MAX_ORDER_RETRIES = 2            # Cancel + retry up to this many times
# Options price in a narrow $ range so a flat $ step works; equities span
# ~$50-$800/share, so retries reprice by a percentage of the current limit
# instead. 30bps roughly matches the drift a fast-moving name (AMZN, NVDA)
# can see within IBKR's free 15-min-delayed data window between attempts.
EQUITY_RETRY_STEP_PCT = 0.003

# How long to wait for the Gateway to acknowledge a bracket's parent order
# before submitting its children. The children reference the parent via
# parentId, and IBKR rejects them with "Error 135: Can't find order with id"
# if they arrive before the Gateway has registered the parent -- this bounds
# that wait rather than racing it.
BRACKET_ACK_TIMEOUT_SECONDS = 3.0

def _fill_timeout() -> int:
    try:
        from app.core.config import settings
        return settings.fill_timeout_seconds
    except Exception:
        return FILL_TIMEOUT_SECONDS

def _retry_step() -> float:
    try:
        from app.core.config import settings
        return settings.retry_price_step
    except Exception:
        return RETRY_PRICE_STEP

def _max_retries() -> int:
    try:
        from app.core.config import settings
        return settings.max_order_retries
    except Exception:
        return MAX_ORDER_RETRIES

def _equity_retry_step_pct() -> float:
    try:
        from app.core.config import settings
        return settings.equity_retry_step_pct
    except Exception:
        return EQUITY_RETRY_STEP_PCT


class IBKRClient(BrokerInterface):
    """
    IBKR broker implementation using ib_insync.
    Paper trading port: 7497 | Live port: 7496
    Gateway paper: 4002  | Gateway live: 4001
    """

    supports_options: bool = True
    supports_equities: bool = True

    def __init__(self) -> None:
        self.ib = IB()
        self._connected = False
        # Tracks the one-shot account-updates subscription used by
        # get_account_summary() — see that method's docstring. Reset here
        # and on every fresh connect(): a new physical session invalidates
        # whatever subscription existed on the old socket, even if this
        # flag was never explicitly cleared by an intervening disconnect()
        # (e.g. a silent connection drop caught by the reconnect watchdog).
        self._account_subscribed = False
        # Single-flight handle for the in-progress subscribe attempt, if
        # any — see get_account_summary()'s docstring for why this exists
        # instead of a lock around independent per-caller attempts.
        self._account_subscribe_task = None
        # When TWS last pushed an account value, as a monotonic timestamp.
        # None means "nothing has ever arrived on this client" — distinct
        # from an old timestamp, which means the stream ran and then stopped.
        self._account_values_last_push: Optional[float] = None
        # Registered on the IB object rather than per-connection: self.ib
        # outlives every connect()/disconnect() cycle (they reuse the same
        # instance), so hooking once here avoids stacking a duplicate handler
        # on every reconnect — ib_insync's Event would call each copy.
        self.ib.accountValueEvent += self._on_account_value

    def _on_account_value(self, _value: Any = None) -> None:
        """Stamp the arrival of a pushed account value.

        Runs inside ib_insync's event dispatch, so it must never raise: an
        exception here would propagate into the client's message loop, which
        is a far worse failure than a missing timestamp.
        """
        try:
            self._account_values_last_push = time.monotonic()
        except Exception:  # pragma: no cover — defensive only
            pass

    def account_values_age_seconds(self) -> Optional[float]:
        """Seconds since TWS last pushed an account value, or None if it never
        has on this client. Local read of a monotonic clock — no I/O."""
        if self._account_values_last_push is None:
            return None
        return max(0.0, time.monotonic() - self._account_values_last_push)

    def account_values_are_stale(self) -> bool:
        """True when the push stream has gone quiet past the point where the
        cache can still be trusted as live.

        A never-populated cache is NOT stale — it is empty, which callers
        already handle as "no data". Staleness is specifically the dangerous
        case: real-looking numbers that stopped being true.
        """
        age = self.account_values_age_seconds()
        if age is None:
            return False
        return age > ACCOUNT_VALUES_STALE_AFTER_SECONDS

    def _reset_account_subscription(self) -> None:
        """Discard any in-flight subscribe attempt tied to the old socket —
        its Future will never resolve on a fresh connection, so leaving it
        referenced would just leak a permanently-pending task."""
        self._account_subscribed = False
        if self._account_subscribe_task is not None:
            self._account_subscribe_task.cancel()
            self._account_subscribe_task = None
        # The push timestamp belongs to the old socket's stream. Carrying it
        # across a reconnect would report the new, empty cache as freshly
        # updated — the precise lie this tracking exists to prevent.
        self._account_values_last_push = None

    async def connect(self) -> None:
        """Connect to TWS/Gateway with retry logic (max 3 attempts, 5s delay)."""
        self._reset_account_subscription()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await self.ib.connectAsync(
                    host=settings.ibkr_host,
                    port=settings.ibkr_port,
                    clientId=settings.ibkr_client_id,
                )
                self._connected = True
                logger.info(
                    "IBKR connected — host=%s port=%s clientId=%s",
                    settings.ibkr_host,
                    settings.ibkr_port,
                    settings.ibkr_client_id,
                )
                # Request delayed market data (type 3 = delayed 15-min, free).
                # Without this, reqTickersAsync returns error 10168 for accounts
                # without a live market data subscription.
                # Type 4 = delayed-frozen: returns last available price when
                # market is closed, so the UI always shows a number.
                #   1 = live (requires subscription)
                #   2 = frozen (last close, requires subscription)
                #   3 = delayed 15-min (free)
                #   4 = delayed-frozen (free, works outside market hours)
                self.ib.reqMarketDataType(4)
                logger.info("IBKR market data type set to 4 (delayed-frozen, free)")
                return
            except Exception as exc:
                logger.warning(
                    "IBKR connection attempt %d/%d failed: %s",
                    attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)

        raise ConnectionError(
            f"Failed to connect to IBKR after {MAX_RETRIES} attempts. "
            f"Is TWS/Gateway running on {settings.ibkr_host}:{settings.ibkr_port}?"
        )

    async def disconnect(self) -> None:
        """Gracefully disconnect from TWS/Gateway."""
        if self._connected:
            self.ib.disconnect()
            self._connected = False
            self._reset_account_subscription()
            logger.info("IBKR disconnected")

    def _require_connection(self) -> None:
        if not self._connected or not self.ib.isConnected():
            raise ConnectionError("IBKR client is not connected. Call connect() first.")

    # ── BrokerInterface implementation ──────────────────────────────────────

    async def get_options_chain(self, symbol: str, expiry: str) -> OptionsChain:
        """
        Fetch options chain via IBKR's reqSecDefOptParams + batched reqTickers.
        expiry: 'YYYY-MM-DD'

        Previously fetched one strike at a time (qualify + reqTickers per
        strike, per call/put) — dozens of sequential round-trips per chain,
        which is what made a single interactive chain request queue for
        minutes behind background scan traffic. Now qualifies every
        call/put contract for the expiry in a few batched calls, then
        fetches tickers for all qualified contracts the same way —
        ib_insync's own multi-contract API, not ad-hoc parallelism, and
        still fully subject to the client's built-in message-rate pacing.
        Chunked (not one giant call) to stay under IBKR's per-connection
        concurrent market-data-line limits.
        """
        self._require_connection()

        expiry_ib = expiry.replace("-", "")  # IBKR uses YYYYMMDD
        underlying = Stock(symbol, "SMART", "USD")
        await self.ib.qualifyContractsAsync(underlying)

        # Fetch chain parameters to get available strikes
        chains = await self.ib.reqSecDefOptParamsAsync(
            symbol, "", underlying.secType, underlying.conId
        )
        target_chain = next(
            (c for c in chains if expiry_ib in c.expirations), None
        )
        if not target_chain:
            raise ValueError(f"No IBKR chain found for {symbol} on {expiry}")

        candidates = [
            Option(symbol, expiry_ib, strike, option_type, "SMART")
            for strike in sorted(target_chain.strikes)
            for option_type in ("C", "P")
        ]

        # qualifyContractsAsync silently drops any contract IBKR can't
        # resolve (e.g. no security definition) rather than raising — the
        # returned list may be shorter than `candidates`, which is fine,
        # we only build OptionContracts for what actually qualified.
        qualified: List[Option] = []
        for chunk in _chunked(candidates, _CHAIN_BATCH_SIZE):
            qualified.extend(await self.ib.qualifyContractsAsync(*chunk))

        tickers = []
        for chunk in _chunked(qualified, _CHAIN_BATCH_SIZE):
            tickers.extend(await self.ib.reqTickersAsync(*chunk))

        calls: List[OptionContract] = []
        puts: List[OptionContract] = []
        for ticker in tickers:
            c = ticker.contract
            option_type = "call" if c.right == "C" else "put"
            g = ticker.modelGreeks
            greeks = Greeks(
                delta=float(g.delta or 0) if g else 0.0,
                gamma=float(g.gamma or 0) if g else 0.0,
                theta=float(g.theta or 0) if g else 0.0,
                vega=float(g.vega or 0) if g else 0.0,
                implied_vol=float(g.impliedVol or 0) if g else 0.0,
            )
            contract = OptionContract(
                symbol=c.localSymbol or c.symbol,
                underlying=symbol,
                expiration=date.fromisoformat(expiry),
                strike=Decimal(str(c.strike)),
                option_type=option_type,
                bid=_safe_decimal(ticker.bid),
                ask=_safe_decimal(ticker.ask),
                last=_safe_decimal(ticker.last),
                volume=_safe_int(ticker.volume),
                open_interest=_safe_int(ticker.callOpenInterest) or _safe_int(ticker.putOpenInterest),
                greeks=greeks,
            )
            (calls if option_type == "call" else puts).append(contract)

        # Underlying price
        under_ticker = await self.ib.reqTickersAsync(underlying)
        underlying_price = _safe_decimal(under_ticker[0].last) if under_ticker else Decimal("0")

        return OptionsChain(
            underlying=symbol,
            expiration=date.fromisoformat(expiry),
            underlying_price=underlying_price,
            calls=calls,
            puts=puts,
            fetched_at=datetime.now(timezone.utc),
        )

    async def get_greeks(
        self, symbol: str, strike: float, expiry: str, option_type: str
    ) -> Greeks:
        """Fetch Greeks for a specific contract."""
        self._require_connection()

        expiry_ib = expiry.replace("-", "")
        opt = Option(symbol, expiry_ib, strike, "C" if option_type == "call" else "P", "SMART")
        contracts = await self.ib.qualifyContractsAsync(opt)
        if not contracts:
            raise ValueError(f"Contract not found: {symbol} {expiry} {strike} {option_type}")

        tickers = await self.ib.reqTickersAsync(*contracts)
        g = tickers[0].modelGreeks if tickers else None
        if not g:
            raise ValueError(f"No Greeks returned for {symbol} {expiry} {strike} {option_type}")

        return Greeks(
            delta=float(g.delta or 0),
            gamma=float(g.gamma or 0),
            theta=float(g.theta or 0),
            vega=float(g.vega or 0),
            implied_vol=float(g.impliedVol or 0),
        )

    @staticmethod
    def _combo_sizing(legs: List[SpreadLeg]) -> Tuple[int, List[int]]:
        """
        Convert absolute per-leg contract counts into IB combo terms: a single
        ``totalQuantity`` (the number of spreads) plus an integer ratio per leg.

        Example: legs of 5/5 → ``(5, [1, 1])``; legs of 5/10 → ``(5, [1, 2])``.

        This prevents the classic combo sizing bug where ``totalQuantity`` is set
        to the per-leg quantity AND each combo leg ratio is also set to the
        per-leg quantity — which sends quantity² contracts per leg.
        """
        from functools import reduce
        from math import gcd

        quantities = [max(1, int(leg.quantity)) for leg in legs]
        spread_qty = reduce(gcd, quantities)
        if spread_qty <= 0:
            spread_qty = 1
        ratios = [q // spread_qty for q in quantities]
        return spread_qty, ratios

    async def _await_order(self, trade, total_qty: int, timeout: int) -> Tuple[str, int, float]:
        """
        Poll an order until it fully fills, terminates, or the timeout expires.

        Returns ``(outcome, filled_qty, avg_price)`` where ``outcome`` is one of:
          - ``"filled"``    — full quantity filled
          - ``"partial"``   — terminated (cancelled/rejected) with a partial fill
          - ``"cancelled"`` / ``"rejected"`` — terminated with no fill
          - ``"timeout"``   — still working when the timeout elapsed (``filled_qty``
                              may be > 0, i.e. a partial that is still working)
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        cancel_states = {"Cancelled", "ApiCancelled", "Inactive"}
        while loop.time() < deadline:
            await asyncio.sleep(1)
            st = trade.orderStatus
            status = st.status
            filled = int(st.filled or 0)
            avg = float(st.avgFillPrice or 0.0)
            if status == "Filled" or (total_qty and filled >= total_qty):
                return "filled", filled, avg
            if status == "Rejected":
                return ("partial" if filled > 0 else "rejected"), filled, avg
            if status in cancel_states:
                return ("partial" if filled > 0 else "cancelled"), filled, avg
        st = trade.orderStatus
        return "timeout", int(st.filled or 0), float(st.avgFillPrice or 0.0)

    async def place_order(self, spread: SpreadOrder) -> OrderResult:
        """
        Submit an options order to IBKR as a SINGLE order (never legged out):
          - 1 leg  → a plain Option order
          - >1 leg → one BAG combo order containing every leg

        Honours ``spread.order_type``:
          - ``"MKT"`` → MarketOrder (used by the kill switch to flatten at any
            price). No price retries — a working market order is left live.
          - ``"LMT"`` → LimitOrder with cancel-and-retry at a more aggressive
            price (up to MAX_ORDER_RETRIES).

        Combo limit-price sign convention: ``spread.limit_price`` is a net credit
        when positive / net debit when negative. A combo is submitted as a BUY of
        the BAG, so the IB limit price is the *net price paid* = ``-limit_price``
        (a credit is a negative price to pay). Single-leg orders use the leg's own
        action and price as-is.

        Partial fills are reported explicitly via ``OrderResult.status == "partial"``
        with ``filled_quantity`` / ``remaining_quantity`` — they are NEVER reported
        as a clean fill.
        """
        self._require_connection()

        from ib_insync import ComboLeg, Contract as IBContract, MarketOrder

        is_market = spread.order_type == "MKT"

        # ── Qualify every leg once (reused across retries) ─────────────────────
        qualified: List[Tuple[SpreadLeg, Contract]] = []
        for leg in spread.legs:
            expiry_ib = leg.expiration.strftime("%Y%m%d")
            opt = Option(leg.symbol, expiry_ib, float(leg.strike),
                         "C" if leg.option_type == "call" else "P", "SMART")
            q = await self.ib.qualifyContractsAsync(opt)
            if not q:
                raise ValueError(f"Could not qualify leg: {leg}")
            qualified.append((leg, q[0]))

        if not qualified:
            raise ValueError("Cannot place an order with no legs")

        spread_qty, ratios = self._combo_sizing(spread.legs)
        single_leg = len(qualified) == 1

        # ── Build the order contract ───────────────────────────────────────────
        if single_leg:
            leg, contract = qualified[0]
            order_action = leg.action          # BUY or SELL the single option
        else:
            combo_legs = []
            for (leg, qc), ratio in zip(qualified, ratios):
                combo_legs.append(ComboLeg(
                    conId=qc.conId,
                    ratio=ratio,
                    action=leg.action,
                    exchange="SMART",
                ))
            contract = IBContract()
            contract.symbol = spread.underlying
            contract.secType = "BAG"
            contract.currency = "USD"
            contract.exchange = "SMART"
            contract.comboLegs = combo_legs
            order_action = "BUY"               # combo direction encoded in legs

        total_qty = spread_qty
        # explicit DAY default matches the IB Gateway preset (avoids Error 10349)
        tif = spread.time_in_force or "DAY"

        def _build_order(net_price: float):
            if is_market:
                o = MarketOrder(action=order_action, totalQuantity=total_qty)
                o.tif = tif
                return o
            lmt = round(net_price if single_leg else -net_price, 2)
            return LimitOrder(action=order_action, totalQuantity=total_qty,
                              lmtPrice=lmt, tif=tif)

        limit_price = float(spread.limit_price)
        last_trade = None
        _timeout = _fill_timeout()
        _step = _retry_step()
        _retries = 0 if is_market else _max_retries()

        for attempt in range(1, _retries + 2):  # +1: initial; +N retries (LMT only)
            order = _build_order(limit_price)
            logger.info(
                "Submitting %s %s order (attempt %d/%d): %s qty=%d%s",
                "single-leg" if single_leg else "combo",
                "MKT" if is_market else "LMT",
                attempt, _retries + 1, spread.underlying, total_qty,
                "" if is_market else f" net_limit={limit_price:.2f}",
            )

            trade = self.ib.placeOrder(contract, order)
            last_trade = trade

            # ── Real-time status + fill event logging ──────────────────────────
            def _on_status(t=trade):
                logger.info(
                    "Order %s — status: %-12s  filled: %s/%s  avg_price: %.4f",
                    t.order.orderId, t.orderStatus.status,
                    t.orderStatus.filled, t.order.totalQuantity,
                    t.orderStatus.avgFillPrice or 0.0,
                )
            trade.statusEvent += _on_status

            def _on_fill(trade_, fill_, holding_=None):
                logger.info(
                    "FILL: order %s — %s x%s @ $%.4f  execution_id=%s",
                    trade_.order.orderId, fill_.contract.localSymbol,
                    fill_.execution.shares, fill_.execution.price,
                    fill_.execution.execId,
                )
            trade.fillEvent += _on_fill

            outcome, filled, avg = await self._await_order(trade, total_qty, _timeout)

            # ── Fully filled ───────────────────────────────────────────────────
            if outcome == "filled":
                logger.info("Order %s fully filled: %d @ avg $%.4f",
                            trade.order.orderId, filled, avg)
                return OrderResult(
                    order_id=str(trade.order.orderId),
                    status="filled",
                    fill_price=Decimal(str(round(avg, 4))),
                    filled_at=datetime.now(timezone.utc),
                    filled_quantity=filled,
                    remaining_quantity=0,
                    message=f"Filled {filled} @ ${avg:.4f}",
                )

            # ── Partial fill — cancel the remainder and report it explicitly ───
            if outcome == "partial":
                logger.warning(
                    "Order %s PARTIAL fill: %d/%d filled — cancelling remainder, "
                    "not retrying (would double the filled portion)",
                    trade.order.orderId, filled, total_qty,
                )
                self.ib.cancelOrder(trade.order)
                await asyncio.sleep(1)
                return OrderResult(
                    order_id=str(trade.order.orderId),
                    status="partial",
                    fill_price=Decimal(str(round(avg, 4))) if filled else None,
                    filled_at=datetime.now(timezone.utc) if filled else None,
                    filled_quantity=filled,
                    remaining_quantity=max(0, total_qty - filled),
                    message=f"Partial fill {filled}/{total_qty} @ ${avg:.4f}",
                )

            # ── Terminated with no fill ────────────────────────────────────────
            if outcome in ("cancelled", "rejected"):
                logger.warning("Order %s %s — not retrying",
                               trade.order.orderId, outcome)
                return OrderResult(
                    order_id=str(trade.order.orderId),
                    status=outcome,
                    filled_quantity=0,
                    remaining_quantity=total_qty,
                    message=outcome,
                )

            # ── Timeout (outcome == "timeout") ─────────────────────────────────
            if filled > 0:
                # Still working but partially filled — cancel remainder, report partial.
                logger.warning(
                    "Order %s timed out PARTIALLY filled: %d/%d — cancelling remainder",
                    trade.order.orderId, filled, total_qty,
                )
                self.ib.cancelOrder(trade.order)
                await asyncio.sleep(1)
                return OrderResult(
                    order_id=str(trade.order.orderId),
                    status="partial",
                    fill_price=Decimal(str(round(avg, 4))),
                    filled_at=datetime.now(timezone.utc),
                    filled_quantity=filled,
                    remaining_quantity=max(0, total_qty - filled),
                    message=f"Partial fill {filled}/{total_qty} @ ${avg:.4f} (timeout)",
                )

            if is_market:
                # A market order with no fill is still working (e.g. market closed).
                # Leave it live — cancelling a flatten could strand naked risk.
                logger.warning(
                    "Market order %s still working after %ds — leaving it live",
                    trade.order.orderId, _timeout,
                )
                return OrderResult(
                    order_id=str(trade.order.orderId),
                    status="submitted",
                    filled_quantity=0,
                    remaining_quantity=total_qty,
                    message=f"Market order working — no fill after {_timeout}s",
                )

            # LMT timeout with no fill → cancel and retry at a better net price
            if attempt <= _retries:
                new_price = round(limit_price - _step, 2)
                logger.warning(
                    "Order %s timed out after %ds — cancelling and retrying at "
                    "lower net limit (%.2f → %.2f)",
                    trade.order.orderId, _timeout, limit_price, new_price,
                )
                self.ib.cancelOrder(trade.order)
                await asyncio.sleep(2)  # let cancel propagate
                limit_price = new_price
                if limit_price <= 0:
                    logger.error("Net limit reached zero — aborting retries")
                    break
            else:
                logger.error("Order %s timed out on final attempt — cancelling",
                             trade.order.orderId)
                self.ib.cancelOrder(trade.order)
                await asyncio.sleep(2)

        # All attempts exhausted with no fill
        order_id = str(last_trade.order.orderId) if last_trade else "unknown"
        return OrderResult(
            order_id=order_id,
            status="cancelled",
            filled_quantity=0,
            remaining_quantity=total_qty,
            message=f"No fill after {_retries + 1} attempt(s)",
        )

    async def cancel_all_open_orders(self) -> CancelSweep:
        """Cancel every working order on the account, reporting what survived.

        Unlike cancel_open_orders(symbol) this does not filter, so a resting
        order for a symbol with no position — the case a position-driven loop
        cannot see — is included.
        """
        sweep = CancelSweep()
        try:
            self._require_connection()
            trades = list(self.ib.openTrades())
        except Exception as exc:
            sweep.enumeration_error = f"{type(exc).__name__}: {exc}"
            logger.error("cancel_all_open_orders: could not list open trades: %s", exc)
            return sweep

        sweep.requested = len(trades)
        for trade in trades:
            oid = str(getattr(trade.order, "orderId", "?"))
            try:
                self.ib.cancelOrder(trade.order)
                sweep.cancelled += 1
            except Exception as exc:
                sweep.unresolved[oid] = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "cancel_all_open_orders: %s (%s) did not cancel: %s",
                    oid, getattr(trade.contract, "symbol", "?"), exc,
                )
        if sweep.cancelled:
            # cancelOrder is a request, not an acknowledgement. Give IBKR a
            # moment to register them before anything reads the book back.
            await asyncio.sleep(1)
        return sweep

    async def cancel_open_orders(self, symbol: str) -> int:
        """Cancel all working orders for `symbol` (e.g. a bracket's still-live
        stop/take-profit legs before a manual close). Best-effort per order —
        one already-filled/already-cancelled order doesn't block the rest."""
        self._require_connection()

        cancelled = 0
        for trade in self.ib.openTrades():
            if trade.contract.symbol.upper() != symbol.upper():
                continue
            try:
                self.ib.cancelOrder(trade.order)
                cancelled += 1
            except Exception:
                logger.warning(
                    "cancel_open_orders: failed to cancel order %s for %s",
                    getattr(trade.order, "orderId", "?"), symbol,
                )
        if cancelled:
            await asyncio.sleep(1)  # let cancellations register before the close order
        return cancelled

    async def cancel_orders_by_id(self, order_ids: list[int]) -> list[dict]:
        """Cancel specific orders by IBKR order id. Best-effort per order.

        Distinct from cancel_open_orders(symbol), which is blunt by design for
        the close path. This one takes explicit ids so an operator can retire
        named orphans without touching a live position's protective legs, and
        reports per-order what actually happened rather than a bare count —
        an out-of-hours rejection and a successful cancel must not look alike.
        """
        self._require_connection()

        wanted = {int(i) for i in order_ids}
        by_id = {
            int(getattr(t.order, "orderId", -1)): t
            for t in self.ib.openTrades()
        }
        out: list[dict] = []
        for oid in sorted(wanted):
            trade = by_id.get(oid)
            if trade is None:
                out.append({"order_id": oid, "result": "not_found",
                            "detail": "not in the broker's open-order book"})
                continue
            try:
                self.ib.cancelOrder(trade.order)
                out.append({"order_id": oid, "result": "cancel_sent",
                            "symbol": trade.contract.symbol})
            except Exception as exc:
                out.append({"order_id": oid, "result": "error",
                            "symbol": trade.contract.symbol,
                            "detail": f"{type(exc).__name__}: {exc}"})
        if any(o["result"] == "cancel_sent" for o in out):
            # cancelOrder is fire-and-forget; give IBKR a moment so the
            # verification re-read below reflects reality rather than racing it.
            await asyncio.sleep(2)
        return out

    async def get_positions(self) -> List[Position]:
        """Return all open positions from IBKR account.

        Uses ib.portfolio() rather than reqPositionsAsync() specifically because
        it carries IBKR's own live marketPrice/unrealizedPNL for each holding —
        reqPositionsAsync() only returns the static contract/quantity/avgCost and
        left current_price/unrealized_pnl permanently unset, which silently broke
        every MFE/MAE excursion feature downstream (trade_recorder.update_excursion
        and trade_excursion_tracker both depend on these fields and were dead code
        as a result — every journal entry showed mfe/mae as null regardless of the
        trade's actual price excursion).
        """
        self._require_connection()

        portfolio_items = self.ib.portfolio()
        positions = []
        for item in portfolio_items:
            c = item.contract
            if c.secType not in ("OPT", "STK", "ETF"):
                continue  # skip futures, FX, bonds, etc.
            current_price = (
                Decimal(str(item.marketPrice)) if item.marketPrice else None
            )
            unrealized_pnl = (
                Decimal(str(item.unrealizedPNL))
                if item.unrealizedPNL is not None else None
            )
            if c.secType == "OPT":
                positions.append(
                    Position(
                        symbol=c.localSymbol or c.symbol,
                        underlying=c.symbol,
                        strike=Decimal(str(c.strike or 0)),
                        expiration=datetime.strptime(
                            c.lastTradeDateOrContractMonth, "%Y%m%d"
                        ).date() if c.lastTradeDateOrContractMonth else date.today(),
                        option_type="call" if c.right == "C" else "put",
                        quantity=int(item.position),
                        avg_cost=Decimal(str(item.averageCost)),
                        current_price=current_price,
                        unrealized_pnl=unrealized_pnl,
                        asset_type="option",
                    )
                )
            else:
                # Equity / ETF — use synthetic option_type and strike=0
                positions.append(
                    Position(
                        symbol=c.symbol,
                        underlying=c.symbol,
                        strike=Decimal("0"),
                        expiration=date.today(),
                        option_type="call",       # placeholder — not used for equities
                        quantity=int(item.position),
                        avg_cost=Decimal(str(item.averageCost)),
                        current_price=current_price,
                        unrealized_pnl=unrealized_pnl,
                        asset_type="equity",
                    )
                )
        return positions

    async def get_equity_positions(self):
        """Return only equity (STK/ETF) positions — delegates to get_positions()."""
        from app.broker.broker_interface import EquityPosition
        all_positions = await self.get_positions()
        result = []
        for pos in all_positions:
            if pos.strike == __import__("decimal").Decimal("0"):  # equity sentinel
                market_value = (
                    pos.current_price * pos.quantity
                    if pos.current_price is not None else None
                )
                result.append(EquityPosition(
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    avg_cost=pos.avg_cost,
                    current_price=pos.current_price,
                    unrealized_pnl=pos.unrealized_pnl,
                    market_value=market_value,
                ))
        return result

    async def place_equity_order(
        self,
        ticker: str,
        qty: int,
        side: Literal["BUY", "SELL"],
        order_type: Literal["market", "limit", "stop", "stop_limit"] = "market",
        limit_price: Optional[float] = None,
        stop: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> EquityOrderResult:
        """Submit an equity order via IBKR.

        LMT orders retry on a timeout: cancel, reprice more aggressively, and
        resubmit — mirroring place_order's (options) existing behavior. This
        account runs on IBKR's free delayed (15-min) market data feed, so the
        "live" quote used to set the initial limit is itself already stale;
        for steadier names the small initial buffer is normally still close
        enough to fill, but for faster-moving names (confirmed in production:
        AMZN/NVDA/QQQ) the price can drift past a stale limit within the
        15-minute delay window. Previously a single attempt just gave up after
        one _fill_timeout() wait and returned "submitted", leaving the order
        working at IBKR indefinitely with no further management — the DB-side
        30-minute grace period would later mark the trade "cancelled" without
        ever telling the broker to cancel the resting order. Retrying with a
        widening price step closes the gap for these names instead of
        silently failing every attempt.
        """
        self._require_connection()
        stock = Stock(ticker, "SMART", "USD")
        await self.ib.qualifyContractsAsync(stock)

        from ib_insync import MarketOrder, StopOrder

        ib_action = side
        is_market = order_type == "market"
        _retries = 0 if is_market else _max_retries()
        _timeout = _fill_timeout()
        _step_pct = _equity_retry_step_pct()
        px = limit_price

        def _build_entry(price: Optional[float]) -> object:
            if is_market:
                return MarketOrder(action=ib_action, totalQuantity=qty, tif="DAY")
            # explicit DAY to match IB Gateway order preset — avoids Error 10349 cancel
            return LimitOrder(action=ib_action, totalQuantity=qty,
                              lmtPrice=price or 0.0, tif="DAY")

        # A stop and/or take-profit means a bracket: a parent entry order with
        # OCO child exit orders attached via parentId. The previous code set
        # `order.takeProfitPrice` / `order.stopLossPrice` attributes that
        # ib_insync ignores entirely — so protective exits NEVER reached IBKR.
        use_bracket = (stop is not None) or (take_profit is not None)

        async def _submit(price: Optional[float]):
            if use_bracket:
                exit_action = "SELL" if ib_action == "BUY" else "BUY"
                parent_id = self.ib.client.getReqId()

                parent = _build_entry(price)
                parent.orderId = parent_id
                parent.transmit = False   # hold until all children are queued

                children = []
                if take_profit:
                    tp = LimitOrder(action=exit_action, totalQuantity=qty,
                                    lmtPrice=round(float(take_profit), 2), tif="GTC")
                    tp.orderId = self.ib.client.getReqId()
                    tp.parentId = parent_id
                    # transmit the whole bracket on the last child only
                    tp.transmit = stop is None
                    children.append(tp)
                if stop:
                    sl = StopOrder(action=exit_action, totalQuantity=qty,
                                   stopPrice=round(float(stop), 2), tif="GTC")
                    sl.orderId = self.ib.client.getReqId()
                    sl.parentId = parent_id
                    sl.transmit = True   # last leg fires the entire bracket
                    children.append(sl)

                logger.info(
                    "Submitting equity BRACKET: %s %s x%d @ %s | TP=%s SL=%s",
                    side, ticker, qty,
                    f"${price:.2f}" if price else "MKT",
                    f"${take_profit:.2f}" if take_profit else "—",
                    f"${stop:.2f}" if stop else "—",
                )
                t = self.ib.placeOrder(stock, parent)
                # Gateway registration of the parent is asynchronous — submitting
                # the children immediately can beat it there, and IBKR rejects
                # both with "Error 135: Can't find order with id" (parentId not
                # yet recognized), leaving the entry order dangling with no
                # protective exits attached. Wait for the parent to reach an
                # acknowledged status (or a bounded timeout) before firing the
                # bracket's children.
                ack_deadline = asyncio.get_event_loop().time() + BRACKET_ACK_TIMEOUT_SECONDS
                while (
                    not t.orderStatus.status
                    and asyncio.get_event_loop().time() < ack_deadline
                ):
                    await asyncio.sleep(0.05)
                if not t.orderStatus.status:
                    logger.warning(
                        "Bracket parent %s %s not acknowledged after %.0fs — "
                        "submitting children anyway (may hit Error 135)",
                        ticker, parent.orderId, BRACKET_ACK_TIMEOUT_SECONDS,
                    )
                for child in children:
                    self.ib.placeOrder(stock, child)
            else:
                logger.info("Submitting equity order: %s %s x%d @ %s",
                            side, ticker, qty, f"${price:.2f}" if price else "MKT")
                t = self.ib.placeOrder(stock, _build_entry(price))

            # ── Real-time status/fill logging ────────────────────────────────
            def _on_status(tr=t):
                logger.info(
                    "Equity order %s — status: %-12s  filled: %s/%s  avg: %.4f",
                    tr.order.orderId, tr.orderStatus.status, tr.orderStatus.filled,
                    tr.order.totalQuantity, tr.orderStatus.avgFillPrice or 0.0,
                )
            t.statusEvent += _on_status

            def _on_fill(trade_, fill_, holding_=None):
                logger.info(
                    "EQUITY FILL: order %s — %s x%s @ $%.4f",
                    trade_.order.orderId, fill_.contract.symbol,
                    fill_.execution.shares, fill_.execution.price,
                )
            t.fillEvent += _on_fill
            return t

        last_trade = None
        for attempt in range(1, _retries + 2):  # +1 initial; +N retries (LMT only)
            trade = await _submit(px)
            last_trade = trade
            logger.info("Equity order attempt %d/%d for %s x%d",
                        attempt, _retries + 1, ticker, qty)

            outcome, filled, avg = await self._await_order(trade, qty, _timeout)

            if outcome == "filled":
                logger.info("Equity order %s fully filled: %d @ avg $%.4f",
                            trade.order.orderId, filled, avg)
                return EquityOrderResult(
                    order_id=str(trade.order.orderId),
                    status="filled",
                    fill_price=Decimal(str(round(avg, 4))),
                    filled_at=datetime.now(timezone.utc),
                    message=f"Filled {filled} shares @ ${avg:.4f}",
                )

            if outcome == "partial":
                # Terminated (cancelled/rejected) with a real, non-zero fill —
                # some shares genuinely bought/sold at the broker. Report it
                # as "filled" (the closest honest status this result type
                # supports) rather than "cancelled", which would wrongly
                # suggest nothing happened.
                logger.warning(
                    "Equity order %s PARTIAL: %d/%d filled — not retrying "
                    "(would double the filled portion)",
                    trade.order.orderId, filled, qty,
                )
                return EquityOrderResult(
                    order_id=str(trade.order.orderId),
                    status="filled",
                    fill_price=Decimal(str(round(avg, 4))) if filled else None,
                    filled_at=datetime.now(timezone.utc) if filled else None,
                    message=f"Partial fill {filled}/{qty} @ ${avg:.4f}",
                )

            if outcome in ("cancelled", "rejected"):
                logger.warning("Equity order %s %s — not retrying",
                               trade.order.orderId, outcome)
                return EquityOrderResult(
                    order_id=str(trade.order.orderId),
                    status="rejected" if outcome == "rejected" else "cancelled",
                    message=outcome,
                )

            # ── outcome == "timeout" ─────────────────────────────────────────
            if is_market:
                # A market order with no fill is still working (e.g. market
                # closed). Leave it live — cancelling a flatten could strand
                # naked risk.
                logger.warning(
                    "Equity market order %s still working after %ds — leaving it live",
                    trade.order.orderId, _timeout,
                )
                return EquityOrderResult(
                    order_id=str(trade.order.orderId),
                    status="submitted",
                    message=f"Order working — no fill after {_timeout}s",
                )

            if attempt <= _retries:
                base_px = px or 0
                step = round(base_px * _step_pct, 2) or _retry_step()
                new_px = round(base_px + step, 2) if ib_action == "BUY" else round(base_px - step, 2)
                logger.warning(
                    "Equity order %s timed out after %ds — cancelling and retrying "
                    "at a more aggressive limit (%.2f → %.2f)",
                    trade.order.orderId, _timeout, base_px, new_px,
                )
                self.ib.cancelOrder(trade.order)
                await asyncio.sleep(2)  # let cancel propagate
                px = new_px
                if px <= 0:
                    logger.error("Equity retry price reached zero — aborting retries")
                    break
            else:
                logger.error("Equity order %s timed out on final attempt — cancelling",
                             trade.order.orderId)
                self.ib.cancelOrder(trade.order)
                await asyncio.sleep(2)

        order_id = str(last_trade.order.orderId) if last_trade else "unknown"
        return EquityOrderResult(
            order_id=order_id,
            status="cancelled",
            message=f"No fill after {_retries + 1} attempt(s)",
        )

    async def get_bars(
        self, ticker: str, timeframe: str = "1 day", limit: int = 100,
        end_date: str = "",
    ) -> List[Bar]:
        """
        Fetch OHLCV daily bars from IBKR historical data.

        Args:
            ticker:   Equity symbol
            timeframe: Ignored (always 1 day)
            limit:    Number of calendar days to look back
            end_date: End date "YYYY-MM-DD" (default = today / latest available)
        """
        self._require_connection()
        stock = Stock(ticker, "SMART", "USD")

        # Format end date for IBKR: "YYYYMMDD 23:59:59" or "" for latest
        end_dt = ""
        if end_date:
            end_dt = end_date.replace("-", "") + " 23:59:59"

        async def _fetch() -> list:
            await self.ib.qualifyContractsAsync(stock)
            return await self.ib.reqHistoricalDataAsync(
                stock,
                endDateTime=end_dt,
                durationStr=f"{limit} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
            )

        try:
            bars_data = await asyncio.wait_for(_fetch(), timeout=HISTORICAL_DATA_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"IBKR historical data request timed out after "
                f"{HISTORICAL_DATA_TIMEOUT_SECONDS:.0f}s (symbol={ticker})"
            ) from None
        result = []
        for b in bars_data:
            result.append(Bar(
                timestamp=datetime.strptime(str(b.date), "%Y-%m-%d"),
                open=Decimal(str(b.open)),
                high=Decimal(str(b.high)),
                low=Decimal(str(b.low)),
                close=Decimal(str(b.close)),
                volume=int(b.volume) if (b.volume == b.volume) else 0,  # guard NaN
            ))
        return result

    async def get_index_bars(self, symbol: str, exchange: str = "CBOE", limit: int = 60) -> List[Bar]:
        """
        Fetch historical bars for an index (e.g. VIX on CBOE).
        Uses secType=IND instead of STK — required for $VIX, $SPX, etc.
        """
        self._require_connection()
        contract = Index(symbol, exchange, "USD")

        async def _fetch() -> list:
            await self.ib.qualifyContractsAsync(contract)
            return await self.ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=f"{limit} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
            )

        try:
            bars_data = await asyncio.wait_for(_fetch(), timeout=HISTORICAL_DATA_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"IBKR historical data request timed out after "
                f"{HISTORICAL_DATA_TIMEOUT_SECONDS:.0f}s (symbol={symbol})"
            ) from None
        result = []
        for b in bars_data:
            result.append(Bar(
                timestamp=datetime.strptime(str(b.date), "%Y-%m-%d"),
                open=Decimal(str(b.open)),
                high=Decimal(str(b.high)),
                low=Decimal(str(b.low)),
                close=Decimal(str(b.close)),
                volume=int(b.volume) if (b.volume == b.volume) else 0,
            ))
        return result

    async def get_latest_quote(self, ticker: str) -> Quote:
        """Fetch latest bid/ask quote for an equity from IBKR."""
        self._require_connection()
        stock = Stock(ticker, "SMART", "USD")
        await self.ib.qualifyContractsAsync(stock)
        tickers = await self.ib.reqTickersAsync(stock)
        t = tickers[0] if tickers else None
        if not t:
            raise ValueError(f"No quote returned for {ticker}")
        def _safe_decimal(v) -> Decimal:
            """Convert IBKR float to Decimal, treating NaN/None as 0."""
            if v is None:
                return Decimal("0")
            try:
                f = float(v)
                return Decimal("0") if f != f else Decimal(str(f))  # NaN check
            except Exception:
                return Decimal("0")

        def _safe_int(v) -> int:
            """Convert IBKR float size to int, treating NaN/None as 0."""
            if v is None:
                return 0
            try:
                f = float(v)
                return 0 if f != f else int(f)  # NaN check
            except Exception:
                return 0

        return Quote(
            symbol=ticker,
            bid_price=_safe_decimal(t.bid),
            ask_price=_safe_decimal(t.ask),
            bid_size=_safe_int(t.bidSize),
            ask_size=_safe_int(t.askSize),
            timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def _on_subscribe_task_done(task: "asyncio.Task") -> None:
        """Retrieve the background subscribe's exception so asyncio stops
        logging it as an unhandled error.

        Against this gateway the subscribe reliably fails to return inside
        ACCOUNT_SUBSCRIBE_TIMEOUT_SECONDS, and get_account_summary() handles
        that by reading the already-populated cache instead. But an
        un-awaited task's exception surfaces as an ERROR-level
        "Task exception was never retrieved" traceback, so a condition the
        code deliberately tolerates was filling the log with what looks like a
        crash, roughly twice a minute.

        That is not cosmetic. It buries real failures: while watching the first
        autopilot scan these tracebacks flooded the alerting filter and had to
        be excluded by hand before genuine errors could be seen.

        Calling task.exception() marks it retrieved without swallowing it —
        anything awaiting the task still sees the exception, so the
        serve-the-cache fallback below is unaffected.
        """
        if task.cancelled():
            return  # reset_account_subscription() cancels on reconnect
        exc = task.exception()
        if exc is None:
            return
        if isinstance(exc, asyncio.TimeoutError):
            # Expected against this gateway. Visible at DEBUG for anyone
            # actually investigating the subscription, silent otherwise.
            logger.debug(
                "Account-updates subscribe timed out in the background "
                "(expected; cached values are served instead)",
            )
        else:
            # Anything else is genuinely unexpected and must stay loud.
            logger.warning(
                "Account-updates subscribe failed in the background: %s: %s",
                type(exc).__name__, exc,
            )

    async def _subscribe_account_updates(self) -> None:
        """The ONE real reqAccountUpdatesAsync attempt backing
        get_account_summary()'s subscribe — see that method's docstring
        for why every caller must share this single attempt rather than
        each firing an independent one. Bounded here (not per-caller) so
        the shared task itself eventually gives up and lets a later caller
        start a fresh attempt if IBKR genuinely never responds."""
        account_id_temp = (self.ib.managedAccounts() or [""])[0]

        # Cancel any subscription IBKR may still consider open before asking
        # for a new one. reqAccountUpdates is a *subscription* and IBKR allows
        # only one active account-updates subscription per connection: if it
        # believes one is already open, a fresh request is silently ignored —
        # accepted, never answered, so wait_for below cancels the inner future
        # at the timeout and raises TimeoutError with no error from IBKR at
        # all. Confirmed live 2026-08-27: every account read failed this way
        # for a full day (net_liquidation None, equity unavailable) after ~90
        # gateway restarts left the subscription state indeterminate.
        #
        # This is the plain sync call, not the *Async variant: the cancel is
        # fire-and-forget (IBKR sends no acknowledgement for it, so there is
        # nothing to await) and it must not be able to hang ahead of the real
        # request. Wrapped because a cancel that fails is not a reason to skip
        # the subscribe — it is a best-effort reset, not a precondition.
        try:
            self.ib.reqAccountUpdates(False, account_id_temp)
        except Exception as exc:
            logger.debug(
                "Account-updates pre-cancel failed (continuing to subscribe): %s", exc,
            )

        await asyncio.wait_for(
            self.ib.reqAccountUpdatesAsync(account_id_temp),
            timeout=ACCOUNT_SUBSCRIBE_TIMEOUT_SECONDS,
        )
        self._account_subscribed = True

    async def get_open_orders(self, refresh: bool = True) -> dict:
        """Resting orders at IBKR, as plain dicts. Read-only — never places,
        modifies or cancels anything.

        This exists because the system had no way to see its own protective
        orders. Equity entries are submitted as brackets (parent entry + GTC
        stop/take-profit children, see place_equity_order), but nothing read
        them back, `trades` has no stop column, and positions adopted from the
        broker by the reconciler never had a bracket in the first place. So
        "does this position have a stop?" was unanswerable from inside the app.

        `refresh` issues reqAllOpenOrdersAsync — a *read* request, not an order
        action. It defaults on because ib.openTrades() is a local cache, and an
        empty cache is ambiguous in exactly the way the account-values cache
        was: it means either "no resting orders" or "nobody ever asked". The
        returned `source` says which answer the caller is holding, so an empty
        list is never silently read as "confirmed no stops".

        Returns {"source": str, "orders": [ ... ]}.
        """
        self._require_connection()

        source = "cache"
        if refresh:
            try:
                await asyncio.wait_for(
                    self.ib.reqAllOpenOrdersAsync(), timeout=OPEN_ORDERS_TIMEOUT_SECONDS,
                )
                source = "refreshed"
            except Exception as exc:
                # Serve the cache rather than failing outright, but say so —
                # a stale answer presented as current is the failure mode this
                # whole area has been bitten by.
                logger.warning(
                    "reqAllOpenOrders failed (%s) — falling back to the local "
                    "open-order cache, which may be incomplete",
                    exc or type(exc).__name__,
                )
                source = "cache_after_refresh_failed"

        out: list[dict] = []
        for t in self.ib.openTrades():
            o, c, st = t.order, t.contract, t.orderStatus
            out.append({
                "order_id": getattr(o, "orderId", None),
                "parent_id": getattr(o, "parentId", 0) or None,
                "symbol": getattr(c, "symbol", None),
                "sec_type": getattr(c, "secType", None),
                "action": getattr(o, "action", None),
                "order_type": getattr(o, "orderType", None),
                "quantity": float(getattr(o, "totalQuantity", 0) or 0),
                # IBKR carries the stop trigger in auxPrice and the limit in
                # lmtPrice; a STP LMT populates both.
                "limit_price": float(getattr(o, "lmtPrice", 0) or 0) or None,
                "stop_price": float(getattr(o, "auxPrice", 0) or 0) or None,
                "tif": getattr(o, "tif", None),
                "status": getattr(st, "status", None),
                "filled": float(getattr(st, "filled", 0) or 0),
                "remaining": float(getattr(st, "remaining", 0) or 0),
                # A protective exit is a stop-flavoured order, however it was
                # created — bracket child or placed by hand at the broker.
                "is_protective": str(getattr(o, "orderType", "")).upper().startswith("STP"),
            })

        out.sort(key=lambda r: (r["symbol"] or "", r["order_id"] or 0))
        return {"source": source, "orders": out}

    async def get_account_summary(self) -> AccountSummary:
        """
        Return account summary from IBKR.

        accountValues() reads ib_insync's local cache, kept fresh by TWS's
        background account-update push — but only while subscribed via
        reqAccountUpdatesAsync(account). ib_insync's reqAccountUpdatesAsync
        takes a single account-id argument and always subscribes (it wraps
        client.reqAccountUpdates(True, account) internally — there is no
        unsubscribe via this method). Subscribe exactly once per connection
        (tracked by self._account_subscribed, reset in connect()/
        disconnect()) and never call it again: the previous
        subscribe-then-immediately-unsubscribe-every-call pattern avoided
        IBKR error 322 (subscription accumulation from repeated subscribe
        calls) but meant the cache only ever reflected a single point-in-
        time snapshot from the first call after each connect — every
        subsequent call silently returned the same frozen numbers.
        Subscribing once and holding it open avoids error 322 (which comes
        from *repeated* subscribe calls, not from one that stays open) while
        keeping the cache genuinely live for the life of the connection.

        Single-flight, not a lock: reqAccountUpdatesAsync is not idempotent
        — ib_insync's own implementation (Wrapper.startReq) creates a fresh
        Future under a shared fixed key on every call, silently orphaning
        any earlier still-outstanding attempt's Future. A per-caller retry
        loop (even one serialized behind a lock) would have each attempt
        send its own redundant IBKR message and abandon the previous one,
        so a real-but-slow IBKR response ends up resolving whichever
        attempt happens to currently hold the key — not the one that sent
        it — producing wildly variable, effectively random per-caller
        delays (confirmed live). Instead: at most one real subscribe
        attempt is ever in flight (self._account_subscribe_task); every
        caller that arrives while one is outstanding awaits that SAME
        task via asyncio.shield() rather than starting its own — shield()
        matters here specifically because callers race their own outer
        timeout against this call (some via the coordinator's
        wait_for(shield(job)), some via a bare wait_for with no shield of
        their own, e.g. paper_trade.py/account_state.py) — without it, the
        first caller to give up on ITS OWN timeout would cancel the shared
        attempt for everyone else still waiting on it, forcing yet another
        redundant resend — exactly the problem this design replaces.
        """
        self._require_connection()

        if not self._account_subscribed:
            if self._account_subscribe_task is None or self._account_subscribe_task.done():
                task = asyncio.ensure_future(self._subscribe_account_updates())
                # Once the cache is populated the branch below stops awaiting
                # this task, so nothing retrieves its exception and asyncio
                # logs "Task exception was never retrieved" with a full
                # traceback at ERROR level — every ~30s, forever, for a
                # timeout that is expected and already handled. See the
                # callback for why that matters.
                task.add_done_callback(self._on_subscribe_task_done)
                self._account_subscribe_task = task
            # Only block on the subscribe when there is nothing to serve
            # without it. The subscribe takes up to
            # ACCOUNT_SUBSCRIBE_TIMEOUT_SECONDS (30s) to fail, while callers
            # bound this call far tighter — paper_trade.py and the dashboard
            # route both use ~5s. Awaiting a doomed subscribe therefore
            # produced the right answer ~25s after every caller had already
            # given up: the fall-through below fired correctly and the data
            # still never reached the UI (confirmed live 2026-08-27, the log
            # line "serving 184 already-cached account values" appearing while
            # every route still reported None). When accountValues() is
            # already populated there is nothing to wait for — read it now and
            # let the subscribe keep retrying in the background to refresh the
            # stream.
            _cached = self.ib.accountValues()
            if _cached:
                logger.debug(
                    "Account-updates subscribe still in flight — serving %d "
                    "already-cached values without waiting", len(_cached),
                )
            else:
                try:
                    await asyncio.shield(self._account_subscribe_task)
                except Exception as exc:
                    # A failed subscribe is not a reason to withhold data IBKR has
                    # already streamed. The subscribe only needs to succeed *once*
                    # per connection to populate accountValues(); if the cache is
                    # already populated, a later failed re-subscribe tells us
                    # nothing about whether the numbers are readable.
                    #
                    # This await used to propagate, so the read below was never
                    # reached. Confirmed live 2026-08-27: the subscribe had been
                    # failing for a full day while accountValues() held 184 entries
                    # including NetLiquidation, TotalCashValue, BuyingPower,
                    # MaintMarginReq and ExcessLiquidity. Every account read
                    # returned nothing, margin guardrails ran blind with autopilot
                    # on, and the dashboard fell back to a synthetic equity figure
                    # ~$8.5k below the real account value — all because the code
                    # refused to read data it already had.
                    #
                    # Fail only when there is genuinely nothing to return.
                    if not self.ib.accountValues():
                        raise
                    logger.warning(
                        "Account-updates subscribe failed (%s) — serving %d already-cached "
                        "account values; the push stream may be stale",
                        exc or type(exc).__name__, len(self.ib.accountValues()),
                    )

        account_values = self.ib.accountValues()

        # Age the numbers before returning them. Reading this cache cannot
        # fail, so without an explicit clock a stopped push stream is
        # indistinguishable from a calm market: the last-known figures keep
        # being served, forever, with no signal that they stopped tracking
        # reality. Callers get the age and a verdict; nothing is withheld
        # here, because a stale figure is still the best available answer for
        # display — it just must never be mistaken for a current one.
        _age = self.account_values_age_seconds()
        _stale = self.account_values_are_stale()
        if _stale:
            logger.warning(
                "Account values are stale — last push %.0fs ago (limit %.0fs). "
                "Serving them for display, but margin and risk checks must not "
                "treat these figures as current.",
                _age or 0.0, ACCOUNT_VALUES_STALE_AFTER_SECONDS,
            )

        values: Dict[str, str] = {}
        account_id = (self.ib.managedAccounts() or ["unknown"])[0]
        for v in account_values:
            if v.currency in ("USD", "BASE", ""):
                values[v.tag] = v.value

        def _dec(*tags: str) -> Decimal | None:
            for t in tags:
                v = values.get(t)
                if v not in (None, ""):
                    return Decimal(v)
            return None

        return AccountSummary(
            account_id=account_id,
            net_liquidation=Decimal(values.get("NetLiquidation", "0") or "0"),
            cash_balance=Decimal(values.get("TotalCashValue", "0") or "0"),
            buying_power=Decimal(values.get("BuyingPower", values.get("OptionBuyingPower", "0")) or "0"),
            trading_mode="paper" if settings.ibkr_port in (7497, 4002) else "live",
            maintenance_margin=_dec("MaintMarginReq", "FullMaintMarginReq"),
            excess_liquidity=_dec("ExcessLiquidity", "FullExcessLiquidity"),
            init_margin=_dec("InitMarginReq", "FullInitMarginReq"),
            data_age_seconds=_age,
            is_stale=_stale,
        )
