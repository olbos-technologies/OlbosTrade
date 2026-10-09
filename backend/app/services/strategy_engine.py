"""
Strategy engine — base class and all four spread strategies.
No strategy may trade without passing GuardrailEngine + RiskManager + approve_trade().
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from app.broker.broker_interface import OptionsChain
from app.core.config import settings
from app.services.guardrails import GuardrailEngine, GuardrailStatus, PortfolioState
from app.services.risk_manager import PortfolioRiskState, ProposedTrade, RiskManager
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _active_risk_pct() -> float:
    """Return risk_per_trade_pct from the currently active trading mode."""
    try:
        from app.services.trading_mode import trading_mode_manager
        return trading_mode_manager.current.config.risk_per_trade_pct
    except Exception:
        return 0.02  # safe default if trading_mode not yet initialised


# ── Pydantic-style dataclasses for strategy outputs ───────────────────────────

@dataclass
class Signal:
    strategy: str
    underlying: str
    direction: str           # "bullish" | "bearish" | "neutral"
    entry_allowed: bool
    reason: str
    short_strike: Optional[float] = None
    long_strike: Optional[float] = None
    short_strike_2: Optional[float] = None
    long_strike_2: Optional[float] = None
    expiration: Optional[date] = None
    target_delta: Optional[float] = None
    iv_rank: Optional[float] = None
    rsi: Optional[float] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PositionSize:
    contracts: int
    risk_per_contract: float
    total_risk: float
    size_multiplier: float


@dataclass
class ExitDecision:
    should_exit: bool
    reason: Optional[str]
    urgency: str = "normal"   # "normal" | "immediate"


@dataclass
class StrategyExitParams:
    """
    Optional override for a strategy's exit-rule literals. All fields
    default to each strategy's own current hardcoded value — passing None
    (the default, everywhere in live/production code) reproduces today's
    exact behavior. Only strategy_optimizer.py's grid search ever passes
    non-default values.
    """
    profit_target_pct:    float = 0.50
    stop_loss_multiplier: float = 2.0
    dte_exit:              int  = 21
    min_iv_rank:           float = 30.0  # not yet wired into any generate_signal() — see strategy_optimizer.py


# ── Base Strategy ──────────────────────────────────────────────────────────────

class BaseStrategy(ABC):
    """
    Abstract base for all spread strategies.
    Subclasses implement generate_signal(), size_position(), check_exit().
    """

    # Each subclass's own original hardcoded exit-rule values, as a class
    # attribute — passing params=None must reproduce that exact strategy's
    # existing behavior, not some other strategy's defaults. Only override
    # in a subclass when its own hardcoded values differ from these.
    DEFAULT_PARAMS: StrategyExitParams = StrategyExitParams()

    def __init__(self, params: Optional[StrategyExitParams] = None) -> None:
        self.guardrails = GuardrailEngine()
        self.risk_manager = RiskManager()
        self.params = params or self.DEFAULT_PARAMS

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy identifier — must match DB strategy column."""
        ...

    @abstractmethod
    def generate_signal(
        self,
        chain: OptionsChain,
        iv_rank: float,
        rsi: float,
        adx: float,
        above_sma20: bool,
        vix: float,
        portfolio_state: PortfolioState,
    ) -> Signal:
        """Evaluate current market conditions and return entry Signal."""
        ...

    @abstractmethod
    def size_position(
        self,
        signal: Signal,
        portfolio: PortfolioRiskState,
        guardrail_status: GuardrailStatus,
    ) -> PositionSize:
        """Calculate position size respecting guardrail size multiplier."""
        ...

    @abstractmethod
    def check_exit(
        self,
        entry_credit: float,
        current_value: float,
        dte_remaining: int,
        short_strike_breached: bool,
    ) -> ExitDecision:
        """Evaluate whether an open position should be closed."""
        ...

    def _find_strike_by_delta(
        self, chain: OptionsChain, target_delta: float, option_type: str
    ) -> Optional[float]:
        """Find the strike closest to target_delta in the chain."""
        contracts = chain.puts if option_type == "put" else chain.calls
        best = None
        best_diff = float("inf")
        for c in contracts:
            if c.greeks:
                d = abs(abs(c.greeks.delta) - abs(target_delta))
                if d < best_diff:
                    best_diff = d
                    best = float(c.strike)
        return best


# ── Strategy 1: Bull Put Spread ────────────────────────────────────────────────

class BullPutSpread(BaseStrategy):
    """
    Direction: Bullish / neutral
    Entry: IV rank > 30, SPY above 20-day SMA, RSI > 45
    Short: 0.20 delta put | Long: 10 pts lower
    DTE: 30–45 days
    Exit: 50% profit OR 21 DTE OR 2x credit loss
    """

    @property
    def name(self) -> str:
        return "bull_put_spread"

    def generate_signal(self, chain, iv_rank, rsi, adx, above_sma20, vix, portfolio_state) -> Signal:
        guardrail_status = self.guardrails.check_all(portfolio_state)

        if not self.guardrails.is_strategy_allowed(self.name, guardrail_status):
            return Signal(
                strategy=self.name, underlying=chain.underlying,
                direction="bullish", entry_allowed=False,
                reason=guardrail_status.reason or "Strategy not allowed in current mode",
                iv_rank=iv_rank, rsi=rsi,
            )

        # Entry conditions
        if iv_rank < 5:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason=f"IV rank too low: {iv_rank:.1f} (need >5)", iv_rank=iv_rank, rsi=rsi)
        if not above_sma20:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason="Price below 20-day SMA — no bullish bias", iv_rank=iv_rank, rsi=rsi)
        if rsi < 45:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason=f"RSI too weak: {rsi:.1f} (need >45)", iv_rank=iv_rank, rsi=rsi)

        short_strike = self._find_strike_by_delta(chain, 0.20, "put")
        if not short_strike:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason="Could not find 0.20 delta put strike", iv_rank=iv_rank, rsi=rsi)

        long_strike = short_strike - 10.0

        return Signal(
            strategy=self.name, underlying=chain.underlying, direction="bullish",
            entry_allowed=True, reason="All entry conditions met",
            short_strike=short_strike, long_strike=long_strike,
            expiration=chain.expiration, target_delta=0.20, iv_rank=iv_rank, rsi=rsi,
        )

    def size_position(self, signal, portfolio, guardrail_status) -> PositionSize:
        multiplier = self.guardrails.get_position_size_multiplier(guardrail_status)
        spread_width = 10.0
        max_loss = spread_width * 100  # per contract
        contracts = self.risk_manager.calculate_position_size(
            portfolio.portfolio_value, max_loss, risk_pct=_active_risk_pct(), size_multiplier=multiplier
        )
        return PositionSize(
            contracts=contracts,
            risk_per_contract=max_loss,
            total_risk=contracts * max_loss,
            size_multiplier=multiplier,
        )

    def check_exit(self, entry_credit, current_value, dte_remaining, short_strike_breached) -> ExitDecision:
        profit_pct = (entry_credit - current_value) / entry_credit if entry_credit else 0
        if profit_pct >= self.params.profit_target_pct:
            return ExitDecision(should_exit=True, reason=f"{self.params.profit_target_pct:.0%} profit target reached")
        if dte_remaining <= self.params.dte_exit:
            return ExitDecision(should_exit=True, reason=f"{self.params.dte_exit} DTE time stop")
        if current_value >= entry_credit * self.params.stop_loss_multiplier:
            return ExitDecision(should_exit=True, reason=f"{self.params.stop_loss_multiplier}x credit stop loss hit", urgency="immediate")
        return ExitDecision(should_exit=False, reason=None)


# ── Strategy 2: Bear Call Spread ───────────────────────────────────────────────

class BearCallSpread(BaseStrategy):
    """
    Direction: Bearish / neutral
    Entry: IV rank > 30, SPY below 20-day SMA, RSI < 55
    Short: 0.20 delta call | Long: 10 pts higher
    DTE: 30–45 days
    """

    @property
    def name(self) -> str:
        return "bear_call_spread"

    def generate_signal(self, chain, iv_rank, rsi, adx, above_sma20, vix, portfolio_state) -> Signal:
        guardrail_status = self.guardrails.check_all(portfolio_state)
        if not self.guardrails.is_strategy_allowed(self.name, guardrail_status):
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason=guardrail_status.reason or "Not allowed", iv_rank=iv_rank, rsi=rsi)

        if iv_rank < 30:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason=f"IV rank too low: {iv_rank:.1f} (need >30)", iv_rank=iv_rank, rsi=rsi)
        if above_sma20:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason="Price above 20-day SMA — no bearish bias", iv_rank=iv_rank, rsi=rsi)
        if rsi > 55:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason=f"RSI too high: {rsi:.1f} (need <55)", iv_rank=iv_rank, rsi=rsi)

        short_strike = self._find_strike_by_delta(chain, 0.20, "call")
        if not short_strike:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason="Could not find 0.20 delta call strike", iv_rank=iv_rank, rsi=rsi)
        long_strike = short_strike + 10.0

        return Signal(
            strategy=self.name, underlying=chain.underlying, direction="bearish",
            entry_allowed=True, reason="All entry conditions met",
            short_strike=short_strike, long_strike=long_strike,
            expiration=chain.expiration, target_delta=0.20, iv_rank=iv_rank, rsi=rsi,
        )

    def size_position(self, signal, portfolio, guardrail_status) -> PositionSize:
        multiplier = self.guardrails.get_position_size_multiplier(guardrail_status)
        max_loss = 10.0 * 100
        contracts = self.risk_manager.calculate_position_size(
            portfolio.portfolio_value, max_loss, risk_pct=_active_risk_pct(), size_multiplier=multiplier
        )
        return PositionSize(contracts=contracts, risk_per_contract=max_loss,
                           total_risk=contracts * max_loss, size_multiplier=multiplier)

    def check_exit(self, entry_credit, current_value, dte_remaining, short_strike_breached) -> ExitDecision:
        profit_pct = (entry_credit - current_value) / entry_credit if entry_credit else 0
        if profit_pct >= self.params.profit_target_pct:
            return ExitDecision(should_exit=True, reason=f"{self.params.profit_target_pct:.0%} profit target reached")
        if dte_remaining <= self.params.dte_exit:
            return ExitDecision(should_exit=True, reason=f"{self.params.dte_exit} DTE time stop")
        if current_value >= entry_credit * self.params.stop_loss_multiplier:
            return ExitDecision(should_exit=True, reason=f"{self.params.stop_loss_multiplier}x credit stop loss", urgency="immediate")
        return ExitDecision(should_exit=False, reason=None)


# ── Strategy 3: Iron Condor ────────────────────────────────────────────────────

class IronCondor(BaseStrategy):
    """
    Direction: Range-bound / neutral
    Entry: IV rank > 40, ADX < 20, VIX 15–25
    Short put + call: 0.15 delta | Wings: 10 pts
    DTE: 30–45 days
    SUSPENDED in capital preservation mode.
    """

    # 25% profit target, not BaseStrategy's 50% — this strategy's own
    # original hardcoded value. stop_loss_multiplier is unused (this
    # strategy exits on strike breach, not a credit multiple).
    DEFAULT_PARAMS = StrategyExitParams(profit_target_pct=0.25)

    @property
    def name(self) -> str:
        return "iron_condor"

    def generate_signal(self, chain, iv_rank, rsi, adx, above_sma20, vix, portfolio_state) -> Signal:
        guardrail_status = self.guardrails.check_all(portfolio_state)
        if not self.guardrails.is_strategy_allowed(self.name, guardrail_status):
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="neutral", entry_allowed=False,
                         reason="Iron Condor suspended in capital preservation mode",
                         iv_rank=iv_rank, rsi=rsi)

        if iv_rank < 5:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="neutral", entry_allowed=False,
                         reason=f"IV rank too low: {iv_rank:.1f} (need >5)", iv_rank=iv_rank, rsi=rsi)
        if adx >= 20:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="neutral", entry_allowed=False,
                         reason=f"ADX too high: {adx:.1f} (need <20, range-bound required)", iv_rank=iv_rank, rsi=rsi)
        if not (15 <= vix <= 25):
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="neutral", entry_allowed=False,
                         reason=f"VIX out of range: {vix:.1f} (need 15–25)", iv_rank=iv_rank, rsi=rsi)

        short_put = self._find_strike_by_delta(chain, 0.15, "put")
        short_call = self._find_strike_by_delta(chain, 0.15, "call")
        if not short_put or not short_call:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="neutral", entry_allowed=False,
                         reason="Could not find 0.15 delta strikes", iv_rank=iv_rank, rsi=rsi)

        return Signal(
            strategy=self.name, underlying=chain.underlying, direction="neutral",
            entry_allowed=True, reason="All entry conditions met",
            short_strike=short_put, long_strike=short_put - 10,
            short_strike_2=short_call, long_strike_2=short_call + 10,
            expiration=chain.expiration, target_delta=0.15, iv_rank=iv_rank, rsi=rsi,
        )

    def size_position(self, signal, portfolio, guardrail_status) -> PositionSize:
        multiplier = self.guardrails.get_position_size_multiplier(guardrail_status)
        max_loss = 10.0 * 100  # max loss on worst wing
        contracts = self.risk_manager.calculate_position_size(
            portfolio.portfolio_value, max_loss, risk_pct=_active_risk_pct(), size_multiplier=multiplier
        )
        return PositionSize(contracts=contracts, risk_per_contract=max_loss,
                           total_risk=contracts * max_loss, size_multiplier=multiplier)

    def check_exit(
        self, entry_credit, current_value, dte_remaining,
        short_strike_breached,           # put side breach (price below short put)
        short_call_strike_breached=False,  # call side breach (price above short call)
    ) -> ExitDecision:
        profit_pct = (entry_credit - current_value) / entry_credit if entry_credit else 0
        if profit_pct >= self.params.profit_target_pct:
            return ExitDecision(should_exit=True, reason=f"{self.params.profit_target_pct:.0%} profit target reached")
        if dte_remaining <= self.params.dte_exit:
            return ExitDecision(should_exit=True, reason=f"{self.params.dte_exit} DTE time stop")
        if short_strike_breached:
            return ExitDecision(should_exit=True, reason="Short put strike breached", urgency="immediate")
        if short_call_strike_breached:
            return ExitDecision(should_exit=True, reason="Short call strike breached", urgency="immediate")
        return ExitDecision(should_exit=False, reason=None)


# ── Strategy 4: Bull Call Debit Spread ────────────────────────────────────────

class BullCallDebitSpread(BaseStrategy):
    """
    Direction: Strong bullish
    Entry: above SMA, RSI > 55, IV rank < 30
    Long: ATM | Short: 0.30 delta call
    DTE: 21–30 days
    SUSPENDED in capital preservation mode.
    """

    # 75% profit target / 10 DTE exit — this strategy's own original
    # hardcoded values, not BaseStrategy's 50%/21. stop_loss_multiplier is
    # unused (this strategy's stop is a fraction of net debit paid, not a
    # multiple of credit received — semantically different, left hardcoded).
    DEFAULT_PARAMS = StrategyExitParams(profit_target_pct=0.75, dte_exit=10)

    @property
    def name(self) -> str:
        return "bull_call_debit_spread"

    def generate_signal(self, chain, iv_rank, rsi, adx, above_sma20, vix, portfolio_state) -> Signal:
        guardrail_status = self.guardrails.check_all(portfolio_state)
        if not self.guardrails.is_strategy_allowed(self.name, guardrail_status):
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason="Debit spread suspended in capital preservation mode",
                         iv_rank=iv_rank, rsi=rsi)

        if iv_rank >= 100:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason=f"IV rank too high: {iv_rank:.1f} (need <100 for debit spread)",
                         iv_rank=iv_rank, rsi=rsi)
        if not above_sma20:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason="Price below 20-day SMA", iv_rank=iv_rank, rsi=rsi)
        if rsi <= 55:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason=f"RSI not strong enough: {rsi:.1f} (need >55)", iv_rank=iv_rank, rsi=rsi)

        underlying_price = float(chain.underlying_price)
        long_strike = round(underlying_price)  # ATM
        short_strike = self._find_strike_by_delta(chain, 0.30, "call")
        if not short_strike:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bullish", entry_allowed=False,
                         reason="Could not find 0.30 delta call strike", iv_rank=iv_rank, rsi=rsi)

        return Signal(
            strategy=self.name, underlying=chain.underlying, direction="bullish",
            entry_allowed=True, reason="All entry conditions met",
            short_strike=short_strike, long_strike=float(long_strike),
            expiration=chain.expiration, target_delta=0.30, iv_rank=iv_rank, rsi=rsi,
        )

    def size_position(self, signal, portfolio, guardrail_status) -> PositionSize:
        multiplier = self.guardrails.get_position_size_multiplier(guardrail_status)
        spread_width = abs((signal.short_strike or 10) - (signal.long_strike or 0))
        max_loss = spread_width * 100
        contracts = self.risk_manager.calculate_position_size(
            portfolio.portfolio_value, max(max_loss, 100),
            risk_pct=_active_risk_pct(), size_multiplier=multiplier
        )
        return PositionSize(contracts=contracts, risk_per_contract=max_loss,
                           total_risk=contracts * max_loss, size_multiplier=multiplier)

    def check_exit(self, entry_credit, current_value, dte_remaining, short_strike_breached) -> ExitDecision:
        # For debit spreads, entry_credit is actually net_debit paid
        net_debit = abs(entry_credit)
        current_profit_pct = (current_value - net_debit) / net_debit if net_debit else 0
        if current_profit_pct >= self.params.profit_target_pct:
            return ExitDecision(should_exit=True, reason=f"{self.params.profit_target_pct:.0%} max profit target reached")
        if dte_remaining <= self.params.dte_exit:
            return ExitDecision(should_exit=True, reason=f"{self.params.dte_exit} DTE time stop")
        if current_value <= net_debit * 0.50:
            return ExitDecision(should_exit=True, reason="50% loss stop hit", urgency="immediate")
        return ExitDecision(should_exit=False, reason=None)


class BearPutSpread(BaseStrategy):
    """
    Direction: Strong bearish
    Entry: below SMA, RSI < 45, IV rank < 40 (debit is expensive at high IV)
    Long: ATM put | Short: 0.20 delta put (lower strike)
    DTE: 21–30 days
    SUSPENDED in capital preservation mode.
    """

    DEFAULT_PARAMS = StrategyExitParams(profit_target_pct=0.75, dte_exit=10)

    @property
    def name(self) -> str:
        return "bear_put_spread"

    def generate_signal(self, chain, iv_rank, rsi, adx, above_sma20, vix, portfolio_state) -> Signal:
        guardrail_status = self.guardrails.check_all(portfolio_state)
        if not self.guardrails.is_strategy_allowed(self.name, guardrail_status):
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason="Debit spread suspended in capital preservation mode",
                         iv_rank=iv_rank, rsi=rsi)

        if iv_rank >= 40:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason=f"IV rank too high for debit spread: {iv_rank:.1f} (need <40)",
                         iv_rank=iv_rank, rsi=rsi)
        if above_sma20:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason="Price above 20-day SMA — no bearish bias",
                         iv_rank=iv_rank, rsi=rsi)
        if rsi >= 45:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason=f"RSI not weak enough: {rsi:.1f} (need <45)",
                         iv_rank=iv_rank, rsi=rsi)

        underlying_price = float(chain.underlying_price)
        long_strike  = round(underlying_price)            # ATM put (buy)
        short_strike = self._find_strike_by_delta(chain, 0.20, "put")  # lower strike (sell)
        if not short_strike:
            return Signal(strategy=self.name, underlying=chain.underlying,
                         direction="bearish", entry_allowed=False,
                         reason="Could not find 0.20 delta put strike",
                         iv_rank=iv_rank, rsi=rsi)

        return Signal(
            strategy=self.name, underlying=chain.underlying, direction="bearish",
            entry_allowed=True, reason="All entry conditions met",
            short_strike=short_strike, long_strike=float(long_strike),
            expiration=chain.expiration, target_delta=0.20, iv_rank=iv_rank, rsi=rsi,
        )

    def size_position(self, signal, portfolio, guardrail_status) -> PositionSize:
        multiplier = self.guardrails.get_position_size_multiplier(guardrail_status)
        spread_width = abs((signal.long_strike or 0) - (signal.short_strike or 0))
        max_loss = max(spread_width, 5.0) * 100
        contracts = self.risk_manager.calculate_position_size(
            portfolio.portfolio_value, max_loss,
            risk_pct=_active_risk_pct(), size_multiplier=multiplier,
        )
        return PositionSize(contracts=contracts, risk_per_contract=max_loss,
                           total_risk=contracts * max_loss, size_multiplier=multiplier)

    def check_exit(self, entry_credit, current_value, dte_remaining, short_strike_breached) -> ExitDecision:
        # entry_credit is actually net_debit paid for this debit spread
        net_debit = abs(entry_credit)
        current_profit_pct = (current_value - net_debit) / net_debit if net_debit else 0
        if current_profit_pct >= self.params.profit_target_pct:
            return ExitDecision(should_exit=True, reason=f"{self.params.profit_target_pct:.0%} max profit target reached")
        if dte_remaining <= self.params.dte_exit:
            return ExitDecision(should_exit=True, reason=f"{self.params.dte_exit} DTE time stop")
        if current_value <= net_debit * 0.50:
            return ExitDecision(should_exit=True, reason="50% loss stop hit", urgency="immediate")
        return ExitDecision(should_exit=False, reason=None)


def approve_trade(
    signal: Signal,
    portfolio_state: PortfolioState,
    portfolio_risk: PortfolioRiskState,
    signal_score: float,
    guardrail_engine: GuardrailEngine,
    risk_manager: RiskManager,
) -> tuple[bool, str]:
    """
    Master pre-trade checklist enforcer.
    ALL checks must pass — no exceptions, no manual overrides.

    Returns (approved: bool, reason: str)
    """
    # 1. Guardrails
    guardrail_status = guardrail_engine.check_all(portfolio_state)
    if not guardrail_status.trading_allowed:
        return False, f"Guardrail: {guardrail_status.reason}"

    # 2. Strategy allowed in current mode
    if not guardrail_engine.is_strategy_allowed(signal.strategy, guardrail_status):
        return False, f"Strategy {signal.strategy} not allowed in {guardrail_status.trading_mode} mode"

    # 3. Signal score threshold
    threshold = guardrail_engine.get_signal_threshold(guardrail_status)
    if signal_score < threshold:
        return False, f"Signal score {signal_score:.3f} below threshold {threshold:.3f}"

    # 4. Trade cap
    if portfolio_state.trades_today >= settings.max_trades_per_day:
        return False, f"Daily trade cap reached: {portfolio_state.trades_today}/{settings.max_trades_per_day}"

    # 5. Position count
    if portfolio_risk.open_position_count >= settings.max_concurrent_positions:
        return False, f"Max positions: {portfolio_risk.open_position_count}/{settings.max_concurrent_positions}"

    return True, "All pre-trade checks passed"
