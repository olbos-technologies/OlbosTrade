"""
Abstract broker interface + all shared Pydantic v2 models.
Every broker implementation must satisfy this contract exactly.
Swap IBKR ↔ Alpaca by changing BROKER= in .env — nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ── Shared Pydantic Models ──────────────────────────────────────────────────

class Greeks(BaseModel):
    """Option Greeks for a single contract."""
    delta: float = Field(description="Directional risk (-1 to 1)")
    gamma: float = Field(description="Rate of delta change")
    theta: float = Field(description="Time decay per day (negative for long options)")
    vega: float = Field(description="Sensitivity to 1% IV change")
    rho: float = Field(default=0.0, description="Sensitivity to interest rate change")
    implied_vol: float = Field(description="Implied volatility as decimal (e.g. 0.25 = 25%)")


class OptionContract(BaseModel):
    """A single row in an options chain."""
    symbol: str
    underlying: str
    expiration: date
    strike: Decimal
    option_type: Literal["call", "put"]
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    open_interest: int
    greeks: Optional[Greeks] = None


class OptionsChain(BaseModel):
    """Full options chain for one underlying + expiry."""
    underlying: str
    expiration: date
    underlying_price: Decimal
    calls: List[OptionContract]
    puts: List[OptionContract]
    fetched_at: datetime


class SpreadLeg(BaseModel):
    """One leg of a multi-leg spread order."""
    symbol: str
    strike: Decimal
    expiration: date
    option_type: Literal["call", "put"]
    action: Literal["BUY", "SELL"]
    quantity: int
    # Whether this leg OPENS new exposure or CLOSES existing exposure.
    #
    # Alpaca requires a per-leg position_intent and rejects an order whose
    # stated intent contradicts the one it infers from the account's existing
    # position ("position intent mismatch, inferred: …, specified: …"). Action
    # alone cannot carry this: SELL means sell_to_open on a new short and
    # sell_to_close on a long being flattened, and sending the former while
    # holding the latter is a request to go naked short, not to close.
    #
    # Defaults to "open" so every existing caller keeps the behaviour it had;
    # only paths that genuinely close — the kill switch's flatten — set
    # "close".
    intent: Literal["open", "close"] = "open"


class SpreadOrder(BaseModel):
    """A complete multi-leg options spread order."""
    strategy: str
    underlying: str
    legs: List[SpreadLeg]
    limit_price: Decimal = Field(description="Net credit (positive) or debit (negative)")
    order_type: Literal["LMT", "MKT"] = "LMT"
    time_in_force: Literal["DAY", "GTC"] = "DAY"
    client_order_id: Optional[str] = None


class CancelSweep(BaseModel):
    """Outcome of cancelling every working order on an account.

    `unresolved` is the field that matters during an emergency stop. An order
    that could not be cancelled is still live at the broker, and the caller
    has to be able to say so instead of reporting a smaller success count and
    letting the difference pass as "nothing to do".
    """
    requested: int = 0
    cancelled: int = 0
    # order id -> why it is still outstanding
    unresolved: dict[str, str] = Field(default_factory=dict)
    # Set when the working-order list itself could not be read, which means
    # `requested` is not a count of anything and nothing may be assumed flat.
    enumeration_error: Optional[str] = None

    @property
    def fully_resolved(self) -> bool:
        return self.enumeration_error is None and not self.unresolved


class OrderResult(BaseModel):
    """Result returned after submitting an order.

    ``status`` semantics:
      - ``filled``    — the full requested quantity was filled.
      - ``partial``   — some, but not all, of the requested quantity filled.
                        ``filled_quantity`` / ``remaining_quantity`` describe how
                        much. Callers MUST handle the residual exposure.
      - ``submitted`` — accepted by the broker and still working (no fill yet).
      - ``cancelled`` / ``rejected`` — terminated with no fill.
    """
    order_id: str
    status: Literal["submitted", "filled", "partial", "cancelled", "rejected", "pending"]
    fill_price: Optional[Decimal] = None
    filled_at: Optional[datetime] = None
    filled_quantity: Optional[int] = None
    remaining_quantity: Optional[int] = None
    message: Optional[str] = None


class Position(BaseModel):
    """A live position held in the account."""
    symbol: str
    underlying: str
    strike: Decimal
    expiration: date
    option_type: Literal["call", "put"]
    quantity: int = Field(description="Positive = long, negative = short")
    avg_cost: Decimal
    current_price: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    greeks: Optional[Greeks] = None
    # Explicit, broker-reported asset class — do not infer this from
    # strike==0 (the equity/ETF branch's placeholder value) downstream,
    # that placeholder is indistinguishable from a genuine degenerate
    # option and was the root cause of untracked equity positions
    # displaying as "OPTIONS" in the Positions UI.
    asset_type: Literal["equity", "option"] = "option"


class EquityPosition(BaseModel):
    """A live equity (stock/ETF) position."""
    symbol: str
    quantity: int = Field(description="Positive = long, negative = short")
    avg_cost: Decimal
    current_price: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    market_value: Optional[Decimal] = None


class AccountSummary(BaseModel):
    """Top-level account snapshot."""
    account_id: str
    net_liquidation: Decimal
    cash_balance: Decimal
    buying_power: Decimal
    day_trades_remaining: Optional[int] = None
    trading_mode: Literal["live", "paper", "sandbox"] = "paper"
    # Margin figures (None when the broker doesn't report them). Used by the
    # margin monitor for buying-power-reduction / utilization tracking.
    maintenance_margin: Decimal | None = None
    excess_liquidity: Decimal | None = None
    init_margin: Decimal | None = None
    # How long ago the broker last refreshed these figures, and whether that
    # is long enough ago to stop trusting them. IBKR serves account data from
    # a locally-cached push stream, so a read always succeeds and always looks
    # authoritative — even when the stream died hours earlier. None means the
    # broker doesn't report an age (non-IBKR brokers), not that it is fresh.
    data_age_seconds: float | None = None
    is_stale: bool = False


class Bar(BaseModel):
    """A single OHLCV bar."""
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class Quote(BaseModel):
    """Latest bid/ask quote for an equity."""
    symbol: str
    bid_price: Decimal
    ask_price: Decimal
    bid_size: int
    ask_size: int
    timestamp: datetime


class EquityOrderResult(BaseModel):
    """Result of an equity order submission."""
    order_id: str
    status: Literal["submitted", "filled", "cancelled", "rejected", "pending"]
    fill_price: Optional[Decimal] = None
    filled_at: Optional[datetime] = None
    message: Optional[str] = None


# ── Abstract Interface ──────────────────────────────────────────────────────

class BrokerInterface(ABC):
    """
    All broker clients must implement this interface.
    No strategy or risk code should ever import a concrete client directly —
    always depend on BrokerInterface.
    """

    @property
    @abstractmethod
    def supports_options(self) -> bool:
        """True if this broker supports options trading."""
        ...

    @property
    @abstractmethod
    def supports_equities(self) -> bool:
        """True if this broker supports equity trading."""
        ...

    @abstractmethod
    async def get_options_chain(self, symbol: str, expiry: str) -> OptionsChain:
        """Fetch the full options chain for a symbol at a given expiry (YYYY-MM-DD)."""
        ...

    @abstractmethod
    async def get_greeks(
        self, symbol: str, strike: float, expiry: str, option_type: str
    ) -> Greeks:
        """Fetch Greeks for a specific contract."""
        ...

    @abstractmethod
    async def place_order(self, spread: SpreadOrder) -> OrderResult:
        """Submit a spread order. Must raise on rejection."""
        ...

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Return all currently open options positions."""
        ...

    @abstractmethod
    async def cancel_all_open_orders(self) -> "CancelSweep":
        """Cancel every working order on the account, across all symbols.

        Distinct from `cancel_open_orders(symbol)`, and not expressible in
        terms of it: a symbol-by-symbol sweep can only visit symbols the
        caller already knows about, which during an emergency stop means the
        symbols that still have positions. A working order for a symbol with
        no position — a resting entry, or a bracket leg whose position already
        closed — is invisible to that loop and survives the stop, then fills
        afterwards and re-opens the exposure the stop existed to remove.

        MUST report outcomes rather than a bare count. A cancel request that
        the broker refused, or that errored in transit, leaves a live order;
        returning only a success tally makes that indistinguishable from
        having had nothing to cancel.
        """
        ...

    @abstractmethod
    async def cancel_open_orders(self, symbol: str) -> int:
        """
        Cancel all working (unfilled) orders for a symbol — used before a
        manual position close to avoid leaving a bracket's stop/take-profit
        legs live at the broker with no corresponding position (which could
        otherwise fire unexpectedly against a later, unrelated position in
        the same symbol). Returns the number of orders cancelled.
        """
        ...

    @abstractmethod
    async def get_account_summary(self) -> AccountSummary:
        """Return top-level account figures."""
        ...

    @abstractmethod
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
        """Submit an equity order with optional bracket (stop + take-profit)."""
        ...

    @abstractmethod
    async def get_bars(
        self, ticker: str, timeframe: str = "1Day", limit: int = 100
    ) -> List[Bar]:
        """Fetch OHLCV bars for an equity symbol."""
        ...

    @abstractmethod
    async def get_latest_quote(self, ticker: str) -> Quote:
        """Fetch the latest bid/ask quote for an equity."""
        ...
