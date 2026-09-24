"""
Unified Risk Engine — single gatekeeper for ALL orders (equity and options).
Wraps: GuardrailEngine + RiskManager + EmotionGuard + EarningsGate.
Every order must call UnifiedRiskEngine.check() before touching the broker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from app.core.config import settings
from app.services.guardrails import GuardrailEngine, PortfolioState
from app.services.risk_manager import PortfolioRiskState, ProposedTrade, RiskManager
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RiskDecision:
    """Unified risk check result. allowed must be True to place any order."""
    allowed: bool
    reason: Optional[str]
    flags: list[str] = field(default_factory=list)


@dataclass
class EmotionState:
    """Tracks psychological risk indicators."""
    consecutive_losses: int = 0
    last_trade_at: Optional[datetime] = None
    recent_trade_count_1h: int = 0
    position_size_drift: float = 0.0   # how much size has grown vs baseline
    tilt_score: float = 0.0
    paused_until: Optional[datetime] = None


class EmotionGuard:
    """
    Detects tilt (trading too fast) and revenge trading (ballooning size).
    Pause conditions: 4+ consecutive losses OR tilt_score > 0.75.
    Auto-resumes after 8 hours.
    """

    CONSECUTIVE_LOSS_PAUSE = 4
    TILT_THRESHOLD = 0.75
    PAUSE_HOURS = 8

    def __init__(self) -> None:
        self.state = EmotionState()

    async def rehydrate_from_db(self) -> None:
        """Load consecutive_losses and paused_until from the latest PortfolioSnapshot."""
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.risk_state import PortfolioSnapshot
            from sqlalchemy import select
            async with AsyncSessionLocal() as session:
                row = (await session.execute(
                    select(PortfolioSnapshot).order_by(PortfolioSnapshot.timestamp.desc()).limit(1)
                )).scalar_one_or_none()
            if row is not None:
                self.state.consecutive_losses = row.consecutive_losses or 0
                self.state.paused_until = row.cooling_off_until
                logger.info(
                    "EmotionGuard rehydrated: consecutive_losses=%d paused_until=%s",
                    self.state.consecutive_losses, self.state.paused_until,
                )
        except Exception as exc:
            logger.warning("EmotionGuard rehydrate failed (starting fresh): %s", exc)

    async def _persist_to_db(self) -> None:
        """Persist current consecutive_losses and paused_until after each trade."""
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.risk_state import PortfolioSnapshot
            from sqlalchemy import select
            async with AsyncSessionLocal() as session:
                row = (await session.execute(
                    select(PortfolioSnapshot).order_by(PortfolioSnapshot.timestamp.desc()).limit(1)
                )).scalar_one_or_none()
                if row is not None:
                    row.consecutive_losses = self.state.consecutive_losses
                    row.cooling_off_until  = self.state.paused_until
                    await session.commit()
        except Exception as exc:
            logger.warning("EmotionGuard persist failed: %s", exc)

    def record_trade(self, was_loss: bool, position_size: float, baseline_size: float) -> None:
        now = datetime.now(timezone.utc)

        # Consistent loss definition: pnl < 0 (strictly negative); caller passes was_loss accordingly
        if was_loss:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        # Tilt: too many trades in 1 hour
        if self.state.last_trade_at:
            elapsed = (now - self.state.last_trade_at).total_seconds()
            if elapsed < 3600:
                self.state.recent_trade_count_1h += 1
            else:
                self.state.recent_trade_count_1h = 1
        else:
            self.state.recent_trade_count_1h = 1

        # Tilt score: normalized trade speed + consecutive losses
        speed_factor = min(self.state.recent_trade_count_1h / 5.0, 1.0)
        loss_factor  = min(self.state.consecutive_losses / 4.0, 1.0)
        self.state.tilt_score = (speed_factor * 0.5 + loss_factor * 0.5)

        # Size drift
        if baseline_size > 0:
            self.state.position_size_drift = position_size / baseline_size - 1.0

        self.state.last_trade_at = now

        # Trigger pause?
        if (self.state.consecutive_losses >= self.CONSECUTIVE_LOSS_PAUSE
                or self.state.tilt_score > self.TILT_THRESHOLD):
            self.state.paused_until = now + timedelta(hours=self.PAUSE_HOURS)
            logger.warning(
                "EmotionGuard: trading paused for %dh — consecutive_losses=%d tilt=%.2f",
                self.PAUSE_HOURS,
                self.state.consecutive_losses,
                self.state.tilt_score,
            )

    def check(self) -> tuple[bool, str | None]:
        """Returns (allowed, reason). allowed=False means pause is active."""
        if self.state.paused_until:
            now = datetime.now(timezone.utc)
            if now < self.state.paused_until:
                remaining = int((self.state.paused_until - now).total_seconds() / 3600)
                return False, (
                    f"EmotionGuard pause active — {remaining}h remaining "
                    f"(consecutive_losses={self.state.consecutive_losses}, "
                    f"tilt={self.state.tilt_score:.2f})"
                )
            else:
                self.state.paused_until = None
                self.state.consecutive_losses = 0
                self.state.tilt_score = 0.0
                logger.info("EmotionGuard: auto-resumed after pause period")

        return True, None


class UnifiedRiskEngine:
    """
    Single check() method that ALL orders must pass before broker submission.
    Never let a trade execute without going through this.
    """

    def __init__(self) -> None:
        self._guardrail = GuardrailEngine()
        self._risk_mgr  = RiskManager()
        self._emotion   = EmotionGuard()

    def check(
        self,
        instrument_type: Literal["equity", "option"],
        ticker: str,
        portfolio_state: PortfolioState,
        portfolio_risk_state: PortfolioRiskState,
        proposed_trade: Optional[ProposedTrade] = None,
        size_multiplier: float = 1.0,
        check_earnings: bool = True,
    ) -> RiskDecision:
        """
        Run all risk checks. Returns RiskDecision(allowed, reason, flags).
        allowed must be True for any order to proceed.
        """
        flags: list[str] = []

        # 1. Guardrails (daily/weekly/monthly loss limits, trade cap, cooling off)
        guardrail_status = self._guardrail.check_all(portfolio_state)
        if not guardrail_status.trading_allowed:
            return RiskDecision(
                allowed=False,
                reason=guardrail_status.reason,
                flags=guardrail_status.flags + ["guardrail_block"],
            )
        flags.extend(guardrail_status.flags)

        # 2. EmotionGuard (tilt + consecutive losses)
        emotion_ok, emotion_reason = self._emotion.check()
        if not emotion_ok:
            return RiskDecision(allowed=False, reason=emotion_reason, flags=flags + ["emotion_guard"])

        # 3. Earnings gate for equities
        if instrument_type == "equity" and check_earnings:
            from app.services.equity_signal_engine import earnings_gate
            if earnings_gate(ticker, settings.earnings_gate_days):
                return RiskDecision(
                    allowed=False,
                    reason=f"Earnings gate: {ticker} has earnings within {settings.earnings_gate_days} days",
                    flags=flags + ["earnings_gate"],
                )

        # 4. Portfolio-level risk (Greeks limits, concentration)
        if proposed_trade is not None:
            approval = self._risk_mgr.approve_trade(
                proposed_trade, portfolio_risk_state, size_multiplier
            )
            if not approval.approved:
                return RiskDecision(
                    allowed=False,
                    reason=approval.reason,
                    flags=flags + approval.flags + ["risk_manager_block"],
                )
            flags.extend(approval.flags)

        return RiskDecision(allowed=True, reason=None, flags=flags)

    async def record_trade_result(
        self,
        was_loss: bool,
        position_size: float,
        baseline_size: float = 500.0,
    ) -> None:
        """Call after each trade closes to update and persist emotion state.
        was_loss must be True iff pnl < 0 (strictly negative)."""
        self._emotion.record_trade(was_loss, position_size, baseline_size)
        await self._emotion._persist_to_db()

    @property
    def emotion_state(self) -> EmotionState:
        return self._emotion.state


# ── Module-level singleton ────────────────────────────────────────────────────
# Shared across all callers within a process. Rehydrate on startup; call
# record_trade_result() after each close and check_emotion() before each order.
# Fail-open everywhere: a DB error or import error must never halt trading.

emotion_guard = EmotionGuard()


def check_emotion() -> tuple[bool, str | None]:
    """Thin wrapper so callers don't need to reference the internal field."""
    return emotion_guard.check()
