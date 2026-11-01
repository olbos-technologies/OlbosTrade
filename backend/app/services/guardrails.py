"""
Psychological Guardrail Engine — the most important module in the platform.
ALL trading decisions must pass through GuardrailEngine.check_all().
No trade can execute if trading_allowed is False.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


#: How far the configured capital base may drift from the real account before
#: the percentage limits stop meaning what the policy says they mean. At 2x,
#: a "2% daily loss" limit is already only 1% of the book being guarded.
CAPITAL_BASE_DIVERGENCE_FACTOR = 2.0

#: Flag raised on GuardrailStatus when that happens. Not a block: the limits
#: are unchanged and trading_allowed is untouched.
CAPITAL_BASE_DIVERGENT_FLAG = "capital_base_divergent"
ACCOUNT_VALUE_UNKNOWN_FLAG = "account_value_unknown"
PNL_UNKNOWN_FLAG = "pnl_unknown"


def capital_base_is_divergent(
    starting_capital: float,
    current_value: float,
    factor: float = CAPITAL_BASE_DIVERGENCE_FACTOR,
) -> bool:
    """Has the configured base drifted far enough to distort every limit?

    Every percentage this module produces — daily, weekly, monthly, capital
    remaining — divides by `starting_capital`, a configured constant. A fixed
    base is deliberate: it stops the limits loosening as the account grows.
    But it only means what TRADING_POLICY.md says while the constant still
    resembles the account it guards.

    Observed in production: STARTING_CAPITAL left at its 25,000 default while
    the account held about 245,000. A -$2,927 day — ordinary at that size, and
    -1.2% of the real book — was computed as -11.71% and tripped the 2% daily
    limit, suspending bull_put_spread across 101 of 102 tickers for a month.
    Nothing was broken; every number was correctly derived from a base that
    was ten times too small, which is exactly why it went unnoticed.

    Returns False when either figure is non-positive: a missing account read
    is not evidence of divergence, and must not be reported as though it were.
    """
    if starting_capital <= 0 or current_value <= 0 or factor <= 0:
        return False
    ratio = current_value / starting_capital
    return ratio >= factor or ratio <= (1.0 / factor)


@dataclass
class GuardrailStatus:
    """Result of a full guardrail check. trading_allowed must be True to place any trade."""
    trading_allowed: bool
    trading_mode: str           # "normal" | "capital_preservation" | "suspended"
    reason: Optional[str]       # Human-readable reason if blocked
    cooling_off_until: Optional[datetime]
    suspended_until: Optional[datetime]
    daily_loss_pct: float
    weekly_loss_pct: float
    monthly_loss_pct: float
    drawdown_pct: float          # peak-to-trough decline, always >= 0
    consecutive_losses: int
    trades_today: int
    capital_pct_remaining: float  # 1.0 = full capital, 0.85 = 85% remaining
    flags: list[str] = field(default_factory=list)


@dataclass
class PortfolioState:
    """Snapshot of portfolio state passed into guardrail checks.
    PnL fields are Decimal to preserve monetary precision through comparisons.
    """
    current_value: float
    starting_capital: float
    daily_pnl: Decimal
    weekly_pnl: Decimal
    monthly_pnl: Decimal
    consecutive_losses: int
    trades_today: int
    cooling_off_until: Optional[datetime] = None
    peak_value: float = 0.0  # 0.0 = untracked; treated as current_value (0% drawdown)
    #: False when the broker read for net liquidation failed. Every percentage
    #: below is derived from `current_value`, so a fabricated one does not
    #: produce a cautious answer — it produces a confident wrong one.
    account_value_known: bool = True
    #: False when the database read for P&L and the loss/trade counters failed.
    #: Those fields then hold ZERO, which is indistinguishable from "no losses
    #: and no trades today" — so every limit that divides or compares against
    #: them passes. A fallback to zero is not a neutral default, it is a claim.
    pnl_known: bool = True
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class GuardrailEngine:
    """
    Enforces all psychological and risk-based trading limits.
    Rules are loaded from settings (never hardcoded here).

    Hierarchy of checks (stops at first violation):
    1. Cooling off period active
    2. Daily loss limit
    3. Weekly loss limit
    4. Monthly loss limit
    5. Maximum drawdown limit (peak-to-trough, not period-based)
    6. Consecutive loss limit
    7. Daily trade cap
    8. Capital preservation mode check
    """

    def __init__(self) -> None:
        self.max_daily_loss_pct = settings.effective_max_daily_loss_pct
        self.max_weekly_loss_pct = settings.effective_max_weekly_loss_pct
        self.max_monthly_loss_pct = settings.effective_max_monthly_loss_pct
        self.max_drawdown_pct = settings.effective_max_drawdown_pct
        self.cooling_off_hours = settings.effective_cooling_off_hours
        self.max_trades_per_day = settings.effective_max_trades_per_day
        self.max_consecutive_losses = settings.effective_max_consecutive_losses
        self.preservation_threshold = settings.effective_capital_preservation_threshold

    def check_all(self, portfolio: PortfolioState) -> GuardrailStatus:
        """
        Run all guardrail checks against current portfolio state.
        Returns GuardrailStatus — caller must check trading_allowed before placing any trade.
        """
        flags: list[str] = []

        # ── 0. Is the account value even known? ────────────────────────────
        #
        # This comes first because every number below divides by
        # `current_value` or compares against it, and the caller's fallback
        # when the broker read fails is `current_value = starting_capital` —
        # a configured constant presented as a measurement.
        #
        # That does not fail safe. Compared against the persisted all-time
        # peak it manufactures an enormous drawdown and blocks the desk while
        # reporting a loss that never happened ("MAX DRAWDOWN LIMIT HIT:
        # 81.82%" on a disconnected gateway, observed in production
        # 2026-10-31). Set the constant HIGH instead and the same substitution
        # reports a healthy account, and the drawdown gate passes when nobody
        # knows what the book is worth. Both are the repository's documented
        # trap — a failed read wearing a safe value.
        #
        # An unknown account value is its own answer. Block, say so, and do
        # not derive a percentage from a number nobody measured.
        if not portfolio.account_value_known:
            flags.append(ACCOUNT_VALUE_UNKNOWN_FLAG)
            logger.warning(
                "Account value unavailable — blocking entries. No drawdown or "
                "capital percentage is computed, because the only input to them "
                "would be the configured STARTING_CAPITAL standing in for a "
                "measurement that did not happen."
            )
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="suspended",
                reason=(
                    "Account value is unavailable — the broker read failed. "
                    "Entries are blocked until net liquidation can be read "
                    "again. This is not a drawdown or a loss limit: the account "
                    "value is unknown, not low."
                ),
                cooling_off_until=portfolio.cooling_off_until,
                suspended_until=None,
                daily_loss_pct=0.0,
                weekly_loss_pct=0.0,
                monthly_loss_pct=0.0,
                drawdown_pct=0.0,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=0.0,
                flags=flags,
            )

        # ── 0b. Is the P&L history even known? ─────────────────────────────
        #
        # The mirror image of check 0. The status route reads daily, weekly and
        # monthly P&L plus the consecutive-loss and trades-today counters from
        # the database, and on ANY exception used to substitute zeros and carry
        # on. Zero P&L and zero counters are exactly what a quiet, profitable
        # day looks like, so the daily-loss, weekly-loss, monthly-loss,
        # consecutive-loss and daily-trade-cap limits all PASSED on a database
        # outage. check 0 stops a failed account read inventing a breach; this
        # stops a failed P&L read inventing safety — the same defect pointing
        # the other way, and the more dangerous direction because nobody
        # investigates a limit that did not fire.
        #
        # Blocks, says so, and reports no percentage derived from the zeros.
        if not portfolio.pnl_known:
            flags.append(PNL_UNKNOWN_FLAG)
            logger.warning(
                "P&L history unavailable — blocking entries. The daily, weekly "
                "and monthly loss limits and the loss and trade counters cannot "
                "be evaluated, and treating them as zero would pass all five."
            )
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="suspended",
                reason=(
                    "P&L history is unavailable — the database read failed. "
                    "Entries are blocked until daily, weekly and monthly P&L and "
                    "the loss and trade counters can be read again. This is not a "
                    "loss limit: the P&L is unknown, not zero."
                ),
                cooling_off_until=portfolio.cooling_off_until,
                suspended_until=None,
                daily_loss_pct=0.0,
                weekly_loss_pct=0.0,
                monthly_loss_pct=0.0,
                drawdown_pct=0.0,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=0.0,
                flags=flags,
            )

        base = Decimal(str(portfolio.starting_capital)) if portfolio.starting_capital > 0 else Decimal("1")
        capital_pct = float(portfolio.current_value) / float(base)

        # Raised before the first return below so it survives every exit path,
        # including the cooling-off one. It changes no limit and no decision —
        # it only refuses to let a distorted base stay silent.
        if capital_base_is_divergent(portfolio.starting_capital, portfolio.current_value):
            flags.append(CAPITAL_BASE_DIVERGENT_FLAG)
            logger.warning(
                "Capital base divergent: STARTING_CAPITAL=%.2f against an account of "
                "%.2f (%.1fx). Every percentage limit below is a share of the "
                "configured base, not of the account — a %.2f%% daily limit is "
                "%.2f%% of the real book. Set STARTING_CAPITAL to match.",
                portfolio.starting_capital, portfolio.current_value,
                (portfolio.current_value / portfolio.starting_capital)
                if portfolio.starting_capital > 0 else float("inf"),
                self.max_daily_loss_pct * 100,
                (self.max_daily_loss_pct * portfolio.starting_capital
                 / portfolio.current_value * 100) if portfolio.current_value > 0 else 0.0,
            )

        # Use Decimal arithmetic for monetary comparisons — avoids float rounding errors
        daily_loss_pct   = float(Decimal(str(portfolio.daily_pnl))   / base)
        weekly_loss_pct  = float(Decimal(str(portfolio.weekly_pnl))  / base)
        monthly_loss_pct = float(Decimal(str(portfolio.monthly_pnl)) / base)

        # Peak-to-trough drawdown — distinct from the period-based loss
        # checks above (which reset each day/week/month): a book that ran
        # up then fell back near/above starting capital can still have
        # breached a real drawdown limit that daily/weekly/monthly never see.
        peak = portfolio.peak_value if portfolio.peak_value > 0 else portfolio.current_value
        drawdown_pct = max(0.0, (peak - portfolio.current_value) / peak) if peak > 0 else 0.0

        # ── 1. Cooling off period ──────────────────────────────────────────
        if portfolio.cooling_off_until:
            now = datetime.now(timezone.utc)
            if now < portfolio.cooling_off_until:
                remaining = portfolio.cooling_off_until - now
                reason = (
                    f"Cooling off period active — {int(remaining.total_seconds() / 3600)}h remaining. "
                    f"Trading resumes at {portfolio.cooling_off_until.strftime('%Y-%m-%d %H:%M UTC')}"
                )
                logger.warning("GUARDRAIL BLOCK — cooling off: %s", reason)
                return GuardrailStatus(
                    trading_allowed=False,
                    trading_mode="suspended",
                    reason=reason,
                    cooling_off_until=portfolio.cooling_off_until,
                    suspended_until=portfolio.cooling_off_until,
                    daily_loss_pct=daily_loss_pct,
                    weekly_loss_pct=weekly_loss_pct,
                    monthly_loss_pct=monthly_loss_pct,
                    drawdown_pct=drawdown_pct,
                    consecutive_losses=portfolio.consecutive_losses,
                    trades_today=portfolio.trades_today,
                    capital_pct_remaining=capital_pct,
                    flags=flags + ["cooling_off_active"],
                )

        # ── 2. Daily loss limit ────────────────────────────────────────────
        if daily_loss_pct <= -self.max_daily_loss_pct:
            suspended_until = datetime.now(timezone.utc) + timedelta(hours=self.cooling_off_hours)
            reason = (
                f"Daily loss limit hit: {daily_loss_pct:.2%} "
                f"(limit: -{self.max_daily_loss_pct:.2%}). "
                f"Trading suspended for {self.cooling_off_hours}h."
            )
            logger.warning("GUARDRAIL BLOCK — daily loss: %s", reason)
            flags.append("daily_loss_limit")
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="suspended",
                reason=reason,
                cooling_off_until=suspended_until,
                suspended_until=suspended_until,
                daily_loss_pct=daily_loss_pct,
                weekly_loss_pct=weekly_loss_pct,
                monthly_loss_pct=monthly_loss_pct,
                drawdown_pct=drawdown_pct,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=capital_pct,
                flags=flags,
            )

        # ── 3. Weekly loss limit ───────────────────────────────────────────
        if weekly_loss_pct <= -self.max_weekly_loss_pct:
            suspended_until = datetime.now(timezone.utc) + timedelta(days=3)
            reason = (
                f"Weekly loss limit hit: {weekly_loss_pct:.2%} "
                f"(limit: -{self.max_weekly_loss_pct:.2%}). "
                f"Trading paused for 3 days."
            )
            logger.warning("GUARDRAIL BLOCK — weekly loss: %s", reason)
            flags.append("weekly_loss_limit")
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="suspended",
                reason=reason,
                cooling_off_until=suspended_until,
                suspended_until=suspended_until,
                daily_loss_pct=daily_loss_pct,
                weekly_loss_pct=weekly_loss_pct,
                monthly_loss_pct=monthly_loss_pct,
                drawdown_pct=drawdown_pct,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=capital_pct,
                flags=flags,
            )

        # ── 4. Monthly loss limit ──────────────────────────────────────────
        if monthly_loss_pct <= -self.max_monthly_loss_pct:
            suspended_until = datetime.now(timezone.utc) + timedelta(days=30)
            reason = (
                f"Monthly loss limit hit: {monthly_loss_pct:.2%} "
                f"(limit: -{self.max_monthly_loss_pct:.2%}). "
                f"Full review required. Trading suspended for 30 days."
            )
            logger.warning("GUARDRAIL BLOCK — monthly loss: %s", reason)
            flags.append("monthly_loss_limit")
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="suspended",
                reason=reason,
                cooling_off_until=suspended_until,
                suspended_until=suspended_until,
                daily_loss_pct=daily_loss_pct,
                weekly_loss_pct=weekly_loss_pct,
                monthly_loss_pct=monthly_loss_pct,
                drawdown_pct=drawdown_pct,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=capital_pct,
                flags=flags,
            )

        # ── 5. Maximum drawdown limit ────────────────────────────────────────
        if drawdown_pct >= self.max_drawdown_pct:
            suspended_until = datetime.now(timezone.utc) + timedelta(days=30)
            reason = (
                f"Max drawdown limit hit: {drawdown_pct:.2%} from peak "
                f"(limit: {self.max_drawdown_pct:.2%}). "
                f"Full review required. Trading suspended for 30 days."
            )
            logger.warning("GUARDRAIL BLOCK — drawdown: %s", reason)
            flags.append("max_drawdown_limit")
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="suspended",
                reason=reason,
                cooling_off_until=suspended_until,
                suspended_until=suspended_until,
                daily_loss_pct=daily_loss_pct,
                weekly_loss_pct=weekly_loss_pct,
                monthly_loss_pct=monthly_loss_pct,
                drawdown_pct=drawdown_pct,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=capital_pct,
                flags=flags,
            )

        # ── 6. Consecutive loss limit ──────────────────────────────────────
        if portfolio.consecutive_losses >= self.max_consecutive_losses:
            suspended_until = datetime.now(timezone.utc) + timedelta(hours=48)
            reason = (
                f"{portfolio.consecutive_losses} consecutive losses "
                f"(limit: {self.max_consecutive_losses}). "
                f"Trading paused for 48 hours."
            )
            logger.warning("GUARDRAIL BLOCK — consecutive losses: %s", reason)
            flags.append("consecutive_loss_limit")
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="suspended",
                reason=reason,
                cooling_off_until=suspended_until,
                suspended_until=suspended_until,
                daily_loss_pct=daily_loss_pct,
                weekly_loss_pct=weekly_loss_pct,
                monthly_loss_pct=monthly_loss_pct,
                drawdown_pct=drawdown_pct,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=capital_pct,
                flags=flags,
            )

        # ── 7. Daily trade cap ─────────────────────────────────────────────
        if portfolio.trades_today >= self.max_trades_per_day:
            reason = (
                f"Daily trade cap reached: {portfolio.trades_today}/{self.max_trades_per_day}. "
                f"No more trades today."
            )
            logger.info("GUARDRAIL BLOCK — trade cap: %s", reason)
            flags.append("daily_trade_cap")
            return GuardrailStatus(
                trading_allowed=False,
                trading_mode="normal",
                reason=reason,
                cooling_off_until=None,
                suspended_until=None,
                daily_loss_pct=daily_loss_pct,
                weekly_loss_pct=weekly_loss_pct,
                monthly_loss_pct=monthly_loss_pct,
                drawdown_pct=drawdown_pct,
                consecutive_losses=portfolio.consecutive_losses,
                trades_today=portfolio.trades_today,
                capital_pct_remaining=capital_pct,
                flags=flags,
            )

        # ── 8. Capital preservation mode ──────────────────────────────────
        trading_mode = "normal"
        if capital_pct <= self.preservation_threshold:
            trading_mode = "capital_preservation"
            flags.append("capital_preservation_mode")
            logger.info(
                "Capital preservation mode active — account at %.1f%% of starting capital",
                capital_pct * 100,
            )

        logger.debug(
            "Guardrail check passed — mode=%s capital=%.1f%% daily=%.2f%% weekly=%.2f%%",
            trading_mode, capital_pct * 100, daily_loss_pct * 100, weekly_loss_pct * 100,
        )

        return GuardrailStatus(
            trading_allowed=True,
            trading_mode=trading_mode,
            reason=None,
            cooling_off_until=None,
            suspended_until=None,
            daily_loss_pct=daily_loss_pct,
            weekly_loss_pct=weekly_loss_pct,
            monthly_loss_pct=monthly_loss_pct,
            drawdown_pct=drawdown_pct,
            consecutive_losses=portfolio.consecutive_losses,
            trades_today=portfolio.trades_today,
            capital_pct_remaining=capital_pct,
            flags=flags,
        )

    def is_strategy_allowed(self, strategy: str, status: GuardrailStatus) -> bool:
        """
        In capital preservation mode, only credit spreads are allowed.
        Iron Condor and Debit Spreads are suspended.
        """
        if not status.trading_allowed:
            return False
        if status.trading_mode == "capital_preservation":
            allowed = {"bull_put_spread", "bear_call_spread"}
            return strategy in allowed
        return True

    def get_signal_threshold(self, status: GuardrailStatus) -> float:
        """Return the minimum AI signal score required to trade."""
        if status.trading_mode == "capital_preservation":
            return settings.effective_signal_score_preservation_mode
        return settings.effective_signal_score_threshold

    def get_position_size_multiplier(self, status: GuardrailStatus) -> float:
        """In capital preservation mode, cut position size by 50%."""
        if status.trading_mode == "capital_preservation":
            return 0.50
        return 1.0
