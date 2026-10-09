"""
Alpaca broker implementation — Trading API + Market Data API over plain HTTPS.

Unlike IBKR (a persistent Gateway/TWS socket connection that must be running
as its own process, one per account), Alpaca is authenticated per-request via
a single API key pair in the request headers — there is no "connect" step and
no persistent session to lose. That's also why Alpaca is the practical broker
choice for multi-tenant SaaS: each customer supplies their own key pair, and
there's no per-customer Gateway container to run (see IBKRClient's docstring,
which requires exactly that).

Docs: https://docs.alpaca.markets/reference — Trading API (orders, positions,
account) and Market Data API (bars, quotes, options snapshots). The options
endpoints here (get_options_chain, get_greeks, place_order) are newer and
less battle-tested in Alpaca's own API than the equity endpoints — verify
against Alpaca's current docs before relying on them in production; this
client has not been exercised against a live/paper Alpaca account.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Literal, Optional

import httpx

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
    OrderLookup,
    Position,
    Quote,
    SpreadOrder,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

DATA_BASE_URL = "https://data.alpaca.markets"
REQUEST_TIMEOUT_SECONDS = 15.0


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_occ_symbol(occ: str) -> tuple[str, date, Literal["call", "put"], Decimal]:
    """
    Parse a standard OCC option symbol, e.g. "AAPL240119C00190000" →
    (underlying, expiration, option_type, strike). The trailing 15 characters
    are always date(6) + type(1) + strike(8) regardless of root symbol length,
    so slicing from the end is robust without needing a fixed-width root.
    """
    tail = occ[-15:]
    root = occ[:-15]
    exp = datetime.strptime(tail[:6], "%y%m%d").date()
    option_type: Literal["call", "put"] = "call" if tail[6].upper() == "C" else "put"
    strike = Decimal(tail[7:]) / Decimal("1000")
    return root, exp, option_type, strike


def _build_occ_symbol(root: str, expiry: str, option_type: str, strike: float) -> str:
    """Inverse of _parse_occ_symbol — builds the OCC symbol Alpaca expects."""
    exp = date.fromisoformat(expiry)
    date_part = exp.strftime("%y%m%d")
    type_char = "C" if option_type.lower() == "call" else "P"
    strike_part = f"{int(round(strike * 1000)):08d}"
    return f"{root.upper()}{date_part}{type_char}{strike_part}"


def _map_equity_status(alpaca_status: str) -> Literal["submitted", "filled", "cancelled", "rejected", "pending"]:
    s = (alpaca_status or "").lower()
    if s == "filled":
        return "filled"
    if s in ("canceled", "expired", "replaced", "stopped"):
        return "cancelled"
    if s in ("rejected", "suspended"):
        return "rejected"
    if s in ("pending_cancel", "pending_replace"):
        return "pending"
    return "submitted"  # new, accepted, pending_new, partially_filled, accepted_for_bidding, calculated, ...


def _map_spread_status(alpaca_status: str) -> Literal["submitted", "filled", "partial", "cancelled", "rejected", "pending"]:
    s = (alpaca_status or "").lower()
    if s == "filled":
        return "filled"
    if s == "partially_filled":
        return "partial"
    if s in ("canceled", "expired", "replaced", "stopped"):
        return "cancelled"
    if s in ("rejected", "suspended"):
        return "rejected"
    if s in ("pending_cancel", "pending_replace"):
        return "pending"
    return "submitted"


class AlpacaClient(BrokerInterface):
    """
    Alpaca broker implementation. Trading API base is
    https://paper-api.alpaca.markets (paper) or https://api.alpaca.markets
    (live), set via settings.alpaca_base_url. Market data (bars/quotes/options
    snapshots) always comes from https://data.alpaca.markets regardless of
    paper/live, per Alpaca's own API design.
    """

    supports_options: bool = True
    supports_equities: bool = True

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        connection_id: Optional[str] = None,
        connection_version: Optional[int] = None,
    ) -> None:
        """Credentials are arguments now, with the operator settings as the
        default — so `AlpacaClient()` behaves exactly as it always has.

        Passing them in is what makes one process able to act for more than
        one organization (MASTER_ARCHITECTURE §21 Phase 2, "connection-scoped
        Alpaca clients"). Alpaca authenticates per request with no session, so
        a per-connection client is just a different set of headers; IBKR
        cannot work this way, which is why that stays a per-customer stack.

        `connection_id` and `connection_version` are carried, not used here.
        §8 has the worker revalidate the version immediately before submitting:
        an order evaluated against one set of credentials must not execute
        against another. A client that does not know which connection it came
        from cannot be checked that way.
        """
        self._trading_base = (base_url or settings.alpaca_base_url).rstrip("/")
        self._data_base = DATA_BASE_URL
        self._headers = {
            "APCA-API-KEY-ID": api_key if api_key is not None else settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": (
                secret_key if secret_key is not None else settings.alpaca_secret_key
            ),
        }
        self.connection_id = connection_id
        self.connection_version = connection_version
        # No persistent connection to lose — every call is an independent
        # authenticated HTTPS request. True here so the scheduler's generic
        # `getattr(broker, "_connected", False)` reconnect check (main.py)
        # treats this broker as always connected — there's nothing to
        # reconnect; a failed call just raises on that call.
        self._connected = True

    def __repr__(self) -> str:
        """Never the key.

        §24's Phase 2 exit criterion is that "credentials never appear in
        browser, logs, or events". The default dataclass-ish repr would put
        _headers -- and therefore a live trading secret -- into any log line
        that interpolated this object, which is the kind of leak nobody writes
        deliberately and everybody writes accidentally.
        """
        env = "paper" if "paper" in self._trading_base.lower() else "live"
        return (f"AlpacaClient(env={env}, connection_id={self.connection_id}, "
                f"version={self.connection_version})")

    __str__ = __repr__

    async def _get(self, base: str, path: str, params: Optional[dict] = None) -> dict | list:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{base}{path}", headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _post(self, base: str, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{base}{path}", headers=self._headers, json=body)
            resp.raise_for_status()
            return resp.json()

    async def _delete(self, base: str, path: str) -> None:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.delete(f"{base}{path}", headers=self._headers)
            if resp.status_code not in (200, 204, 207):
                resp.raise_for_status()

    # ── Account ──────────────────────────────────────────────────────────────
    async def get_account_summary(self) -> AccountSummary:
        data = await self._get(self._trading_base, "/v2/account")
        is_paper = "paper" in self._trading_base.lower()
        daytrade_count = int(data.get("daytrade_count", 0) or 0)
        return AccountSummary(
            account_id=data["account_number"],
            net_liquidation=Decimal(str(data["equity"])),
            cash_balance=Decimal(str(data["cash"])),
            buying_power=Decimal(str(data["buying_power"])),
            day_trades_remaining=(
                None if data.get("pattern_day_trader") else max(0, 3 - daytrade_count)
            ),
            trading_mode="paper" if is_paper else "live",
            maintenance_margin=(
                Decimal(str(data["maintenance_margin"])) if data.get("maintenance_margin") else None
            ),
            excess_liquidity=None,
            init_margin=(
                Decimal(str(data["initial_margin"])) if data.get("initial_margin") else None
            ),
        )

    # ── Positions ────────────────────────────────────────────────────────────
    async def get_positions(self) -> List[Position]:
        """
        All open positions (equity + options), normalized into the shared
        Position model. Equities use the strike=0 sentinel — the same
        convention IBKRClient uses, since this app treats an equity as an
        "option with strike 0" throughout (see paper_trade.py).
        """
        rows = await self._get(self._trading_base, "/v2/positions")
        positions: List[Position] = []
        for p in rows:
            qty = int(float(p["qty"]))
            if p.get("side") == "short":
                qty = -abs(qty)
            current_price = Decimal(str(p["current_price"])) if p.get("current_price") else None
            unrealized_pnl = Decimal(str(p["unrealized_pl"])) if p.get("unrealized_pl") is not None else None
            avg_cost = Decimal(str(p["avg_entry_price"]))

            if p.get("asset_class") == "us_option":
                underlying, expiration, option_type, strike = _parse_occ_symbol(p["symbol"])
                positions.append(Position(
                    symbol=p["symbol"], underlying=underlying, strike=strike,
                    expiration=expiration, option_type=option_type, quantity=qty,
                    avg_cost=avg_cost, current_price=current_price, unrealized_pnl=unrealized_pnl,
                    asset_type="option",
                ))
            else:
                positions.append(Position(
                    symbol=p["symbol"], underlying=p["symbol"], strike=Decimal("0"),
                    expiration=date.today(), option_type="call", quantity=qty,
                    avg_cost=avg_cost, current_price=current_price, unrealized_pnl=unrealized_pnl,
                    asset_type="equity",
                ))
        return positions

    # ── Orders ───────────────────────────────────────────────────────────────
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
        body: dict = {
            "symbol": ticker.upper(),
            "qty": str(qty),
            "side": "buy" if side == "BUY" else "sell",
            "type": order_type,
            "time_in_force": "day",
        }
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if order_type in ("stop", "stop_limit") and stop is not None:
            body["stop_price"] = str(stop)

        # Bracket order — a stop-loss and/or take-profit attached to the
        # entry, mirroring IBKRClient's bracket support. Alpaca's mechanism
        # for this is order_class="bracket" with nested take_profit/stop_loss.
        if stop is not None or take_profit is not None:
            body["order_class"] = "bracket"
            if take_profit is not None:
                body["take_profit"] = {"limit_price": str(take_profit)}
            if stop is not None:
                body["stop_loss"] = {"stop_price": str(stop)}

        data = await self._post(self._trading_base, "/v2/orders", body)
        return EquityOrderResult(
            order_id=data["id"],
            status=_map_equity_status(data.get("status", "new")),
            fill_price=Decimal(str(data["filled_avg_price"])) if data.get("filled_avg_price") else None,
            filled_at=_parse_ts(data.get("filled_at")),
            message=None,
        )

    async def place_order(self, spread: SpreadOrder) -> OrderResult:
        """
        Submit a multi-leg options spread via Alpaca's order_class="mleg".
        See the module docstring — verify this against Alpaca's current
        multi-leg options order docs before relying on it live.
        """
        legs_payload = [
            {
                "symbol": _build_occ_symbol(
                    spread.underlying, leg.expiration.isoformat(), leg.option_type, float(leg.strike)
                ),
                "side": "buy" if leg.action == "BUY" else "sell",
                "ratio_qty": str(leg.quantity),
                # Intent is NOT derivable from side. SELL is sell_to_open on a
                # new short and sell_to_close on a long being flattened, and
                # Alpaca rejects an order whose stated intent contradicts the
                # one it infers from the account ("position intent mismatch").
                # This previously hardcoded *_to_open for every leg, so the
                # kill switch's flatten asked to open a naked short in the
                # contract it was trying to close.
                "position_intent": (
                    f"{'buy' if leg.action == 'BUY' else 'sell'}_to_{leg.intent}"
                ),
            }
            for leg in spread.legs
        ]
        total_qty = spread.legs[0].quantity if spread.legs else 1

        body: dict = {
            "order_class": "mleg",
            "type": "market" if spread.order_type == "MKT" else "limit",
            "time_in_force": "day" if spread.time_in_force == "DAY" else "gtc",
            "qty": str(total_qty),
            "legs": legs_payload,
        }
        if spread.order_type == "LMT":
            body["limit_price"] = str(spread.limit_price)
        if spread.client_order_id:
            body["client_order_id"] = spread.client_order_id

        data = await self._post(self._trading_base, "/v2/orders", body)
        return OrderResult(
            order_id=data["id"],
            status=_map_spread_status(data.get("status", "new")),
            fill_price=Decimal(str(data["filled_avg_price"])) if data.get("filled_avg_price") else None,
            filled_at=_parse_ts(data.get("filled_at")),
            filled_quantity=int(float(data["filled_qty"])) if data.get("filled_qty") else None,
            remaining_quantity=None,
            message=None,
        )

    async def find_order_by_client_order_id(self, client_order_id: str) -> OrderLookup:
        """Look an order up by the client id we supplied when placing it.

        `/v2/orders:by_client_order_id` is indexed by client id, so a 404 from
        it is a real statement about that id rather than an empty page of a
        filtered list. That is what makes NOT_FOUND usable as evidence.

        Everything else is UNDETERMINED, including a 5xx, a 429, a timeout, a
        transport error and any 4xx we did not expect. "The broker did not
        answer" must not arrive at the caller wearing the same clothes as "the
        broker says no".

        Note what this method does NOT know: whether the id was ever actually
        sent to Alpaca. Equity orders here carry no client id, so a 404 for an
        equity key means only that nothing was ever tagged with it. The caller
        is responsible for that distinction — see services/claim_lookup.py.
        """
        url = f"{self._trading_base}/v2/orders:by_client_order_id"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    url, headers=self._headers,
                    params={"client_order_id": client_order_id},
                )
        except Exception as exc:
            logger.warning(
                "Alpaca order lookup for %s could not be completed: %s",
                client_order_id, exc,
            )
            return OrderLookup.UNDETERMINED

        if resp.status_code == 200:
            return OrderLookup.FOUND
        if resp.status_code == 404:
            return OrderLookup.NOT_FOUND
        logger.warning(
            "Alpaca order lookup for %s returned %s — treating as undetermined",
            client_order_id, resp.status_code,
        )
        return OrderLookup.UNDETERMINED

    async def cancel_all_open_orders(self) -> CancelSweep:
        """Cancel every working order on the account, reporting what survived.

        Per-order failures are recorded in `unresolved`, not warned about and
        dropped from the tally the way `cancel_open_orders` does — during an
        emergency stop the orders that did NOT cancel are the whole story.
        """
        sweep = CancelSweep()
        try:
            orders = await self._get(
                self._trading_base, "/v2/orders",
                params={"status": "open", "nested": "true"},
            )
        except Exception as exc:
            # Nothing may be assumed cancelled, including that there was
            # nothing to cancel.
            sweep.enumeration_error = f"{type(exc).__name__}: {exc}"
            logger.error("cancel_all_open_orders: could not list open orders: %s", exc)
            return sweep

        sweep.requested = len(orders)
        for order in orders:
            oid = str(order.get("id", "?"))
            try:
                await self._delete(self._trading_base, f"/v2/orders/{oid}")
                sweep.cancelled += 1
            except Exception as exc:
                sweep.unresolved[oid] = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "cancel_all_open_orders: %s (%s) did not cancel: %s",
                    oid, order.get("symbol", "?"), exc,
                )
        return sweep

    async def cancel_open_orders(self, symbol: str) -> int:
        """Cancel all working orders for `symbol` — used before a manual
        position close, same purpose as IBKRClient's implementation."""
        orders = await self._get(
            self._trading_base, "/v2/orders",
            params={"status": "open", "symbols": symbol.upper()},
        )
        cancelled = 0
        for order in orders:
            try:
                await self._delete(self._trading_base, f"/v2/orders/{order['id']}")
                cancelled += 1
            except Exception:
                logger.warning(
                    "cancel_open_orders: failed to cancel order %s for %s",
                    order.get("id", "?"), symbol,
                )
        return cancelled

    # ── Market data ──────────────────────────────────────────────────────────
    async def get_bars(self, ticker: str, timeframe: str = "1Day", limit: int = 100) -> List[Bar]:
        # Free-tier market data uses the IEX feed — SIP (full consolidated
        # tape) requires a paid Alpaca data subscription.
        data = await self._get(
            self._data_base, f"/v2/stocks/{ticker.upper()}/bars",
            params={"timeframe": timeframe, "limit": limit, "adjustment": "raw", "feed": "iex"},
        )
        bars = data.get("bars", []) if isinstance(data, dict) else []
        return [
            Bar(
                timestamp=_parse_ts(b["t"]) or datetime.now(timezone.utc),
                open=Decimal(str(b["o"])), high=Decimal(str(b["h"])),
                low=Decimal(str(b["l"])), close=Decimal(str(b["c"])),
                volume=int(b["v"]),
            )
            for b in bars
        ]

    async def get_latest_quote(self, ticker: str) -> Quote:
        data = await self._get(
            self._data_base, f"/v2/stocks/{ticker.upper()}/quotes/latest",
            params={"feed": "iex"},
        )
        q = data.get("quote", {}) if isinstance(data, dict) else {}
        return Quote(
            symbol=ticker.upper(),
            bid_price=Decimal(str(q.get("bp", 0) or 0)),
            ask_price=Decimal(str(q.get("ap", 0) or 0)),
            bid_size=int(q.get("bs", 0) or 0),
            ask_size=int(q.get("as", 0) or 0),
            timestamp=_parse_ts(q.get("t")) or datetime.now(timezone.utc),
        )

    async def get_options_chain(self, symbol: str, expiry: str) -> OptionsChain:
        """See the module docstring re: options endpoints being unverified
        against a live Alpaca account."""
        contracts_data = await self._get(
            self._trading_base, "/v2/options/contracts",
            params={"underlying_symbols": symbol.upper(), "expiration_date": expiry, "limit": 500},
        )
        contracts = contracts_data.get("option_contracts", []) if isinstance(contracts_data, dict) else []
        if not contracts:
            raise ValueError(f"No option contracts found for {symbol} {expiry}")

        occ_symbols = [c["symbol"] for c in contracts]
        snapshots = await self._get(
            self._data_base, "/v1beta1/options/snapshots",
            params={"symbols": ",".join(occ_symbols)},
        )
        snap_map = snapshots.get("snapshots", {}) if isinstance(snapshots, dict) else {}

        try:
            uq = await self.get_latest_quote(symbol)
            underlying_price = (
                (uq.bid_price + uq.ask_price) / 2 if uq.bid_price and uq.ask_price else Decimal("0")
            )
        except Exception:
            underlying_price = Decimal("0")

        calls: List[OptionContract] = []
        puts: List[OptionContract] = []
        for c in contracts:
            snap = snap_map.get(c["symbol"], {}) or {}
            quote = snap.get("latestQuote", {}) or {}
            trade = snap.get("latestTrade", {}) or {}
            greeks_data = snap.get("greeks")
            option_type: Literal["call", "put"] = "call" if c["type"] == "call" else "put"
            oc = OptionContract(
                symbol=c["symbol"], underlying=symbol.upper(),
                expiration=date.fromisoformat(c["expiration_date"]),
                strike=Decimal(str(c["strike_price"])),
                option_type=option_type,
                bid=Decimal(str(quote.get("bp", 0) or 0)),
                ask=Decimal(str(quote.get("ap", 0) or 0)),
                last=Decimal(str(trade.get("p", 0) or 0)),
                volume=int((snap.get("dailyBar") or {}).get("v", 0) or 0),
                open_interest=int(c.get("open_interest", 0) or 0),
                greeks=Greeks(
                    delta=float(greeks_data.get("delta", 0)),
                    gamma=float(greeks_data.get("gamma", 0)),
                    theta=float(greeks_data.get("theta", 0)),
                    vega=float(greeks_data.get("vega", 0)),
                    implied_vol=float(snap.get("impliedVolatility", 0) or 0),
                ) if greeks_data else None,
            )
            (calls if option_type == "call" else puts).append(oc)

        return OptionsChain(
            underlying=symbol.upper(), expiration=date.fromisoformat(expiry),
            underlying_price=underlying_price, calls=calls, puts=puts,
            fetched_at=datetime.now(timezone.utc),
        )

    async def get_greeks(self, symbol: str, strike: float, expiry: str, option_type: str) -> Greeks:
        occ = _build_occ_symbol(symbol, expiry, option_type, strike)
        snapshots = await self._get(self._data_base, "/v1beta1/options/snapshots", params={"symbols": occ})
        snap = (snapshots.get("snapshots", {}) if isinstance(snapshots, dict) else {}).get(occ, {}) or {}
        g = snap.get("greeks") or {}
        return Greeks(
            delta=float(g.get("delta", 0)), gamma=float(g.get("gamma", 0)),
            theta=float(g.get("theta", 0)), vega=float(g.get("vega", 0)),
            implied_vol=float(snap.get("impliedVolatility", 0) or 0),
        )
