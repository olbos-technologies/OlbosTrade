"""
Trading Mode System — Conservative, Balanced, Aggressive, Scalper.

Each mode is a complete configuration profile that overrides strategy
parameters, position sizing, DTE targets, and signal thresholds.

IMMUTABLE GUARDRAILS (never changed by any mode):
  - Daily/weekly/monthly loss limits
  - Kill switch
  - Position reconciliation
  - Consecutive loss cooling off
  - Capital preservation threshold

WHAT MODES CONTROL:
  - DTE range (target days to expiration)
  - Profit target % (when to close winners)
  - Stop loss multiplier (when to close losers)
  - Signal score threshold (how selective the AI is)
  - Max trades per day
  - Position size (% of portfolio per trade)
  - Which strategies are allowed
  - Whether human monitoring is required
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


class TradingModeType(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED     = "balanced"
    AGGRESSIVE   = "aggressive"
    SCALPER      = "scalper"


@dataclass(frozen=True)
class TradingModeConfig:
    """
    Complete configuration for a single trading mode.
    Frozen — cannot be modified after creation.
    All modes share the same immutable financial guardrails.
    """
    mode:                  TradingModeType
    display_name:          str
    description:           str

    # ── DTE ────────────────────────────────────────────────────────────────
    dte_min:               int
    dte_target:            int
    dte_max:               int
    dte_exit:              int         # Close position at this DTE regardless

    # ── Exit rules ─────────────────────────────────────────────────────────
    profit_target_pct:     float       # Close when this % of max profit reached
    stop_loss_multiplier:  float       # Close when loss = this × credit received

    # ── Entry filters ──────────────────────────────────────────────────────
    signal_threshold:      float       # Min AI score to enter
    min_iv_rank:           float       # Min IV rank to enter
    min_credit_to_width:   float       # Min credit/width ratio

    # ── Position sizing ────────────────────────────────────────────────────
    risk_per_trade_pct:    float       # % of portfolio risked per trade
    max_trades_per_day:    int
    max_concurrent:        int

    # ── Strategy permissions ────────────────────────────────────────────────
    bull_put_allowed:      bool
    bear_call_allowed:     bool
    iron_condor_allowed:   bool
    debit_spread_allowed:  bool

    # ── Monitoring requirement ─────────────────────────────────────────────
    requires_monitoring:   bool        # If True, warn user to watch screen
    monitoring_warning:    str

    # ── Color for UI ──────────────────────────────────────────────────────
    ui_color:              str         # green / blue / orange / red
    ui_icon:               str


# ── Mode definitions ───────────────────────────────────────────────────────────

TRADING_MODES: dict[TradingModeType, TradingModeConfig] = {

    TradingModeType.CONSERVATIVE: TradingModeConfig(
        mode=TradingModeType.CONSERVATIVE,
        display_name="Conservative",
        description=(
            "Capital preservation first. Fewer trades, wider DTE, "
            "higher signal requirements. Best for volatile markets "
            "or when starting out."
        ),
        # DTE — widest range, most forgiving
        dte_min=30, dte_target=45, dte_max=60, dte_exit=21,
        # Exit rules — patient
        profit_target_pct=0.50,
        stop_loss_multiplier=2.0,
        # Entry — very selective
        signal_threshold=0.72,
        min_iv_rank=35.0,
        min_credit_to_width=0.25,
        # Sizing — smallest risk
        risk_per_trade_pct=0.01,   # 1% per trade
        max_trades_per_day=1,
        max_concurrent=3,
        # Strategies — credit only, no debit
        bull_put_allowed=True,
        bear_call_allowed=True,
        iron_condor_allowed=True,
        debit_spread_allowed=False,
        # Monitoring — fully automated
        requires_monitoring=False,
        monitoring_warning="",
        ui_color="green",
        ui_icon="shield",
    ),

    # 2026-07-28 risk-ladder rebalance (operator request): Balanced now runs
    # what was previously Conservative's full profile. Conservative itself is
    # unchanged; Aggressive/Scalper shifted down and were loosened slightly —
    # see those blocks below.
    TradingModeType.BALANCED: TradingModeConfig(
        mode=TradingModeType.BALANCED,
        display_name="Balanced",
        description=(
            "The default OlbosTrade mode. Capital preservation first — "
            "fewer trades, wider DTE, higher signal requirements. "
            "Recommended for most traders."
        ),
        # DTE — widest range, most forgiving
        dte_min=30, dte_target=45, dte_max=60, dte_exit=21,
        # Exit rules — patient
        profit_target_pct=0.50,
        stop_loss_multiplier=2.0,
        # Entry — very selective
        signal_threshold=0.72,
        min_iv_rank=35.0,
        min_credit_to_width=0.25,
        # Sizing — smallest risk
        risk_per_trade_pct=0.01,   # 1% per trade
        max_trades_per_day=5,
        max_concurrent=3,
        # Strategies — credit only, no debit
        bull_put_allowed=True,
        bear_call_allowed=True,
        iron_condor_allowed=True,
        debit_spread_allowed=False,
        # Monitoring — fully automated
        requires_monitoring=False,
        monitoring_warning="",
        ui_color="blue",
        ui_icon="activity",
    ),

    # Aggressive now runs what was previously Balanced's DTE/exit/strategy
    # profile, loosened on entry threshold and sizing/frequency (signal
    # threshold, risk per trade, trades/day, concurrent positions).
    TradingModeType.AGGRESSIVE: TradingModeConfig(
        mode=TradingModeType.AGGRESSIVE,
        display_name="Aggressive",
        description=(
            "22 DTE sweet spot for the volatility risk premium — the profile "
            "Balanced used to run, loosened for more trades. Fully automated."
        ),
        # DTE — sweet spot (was Balanced's before the 2026-07-28 rebalance)
        dte_min=15, dte_target=22, dte_max=30, dte_exit=10,
        # Exit rules — standard
        profit_target_pct=0.50,
        stop_loss_multiplier=2.0,
        # Entry — loosened from the old Balanced's 0.65
        signal_threshold=0.62,
        min_iv_rank=30.0,
        min_credit_to_width=0.20,
        # Sizing — loosened from the old Balanced's 2% / 3 / 5
        risk_per_trade_pct=0.02,  # 2% per trade — the TRADING_POLICY.md charter ceiling
        max_trades_per_day=10,
        max_concurrent=6,
        # Strategies — all allowed (DTE is no longer the 7-15 danger zone
        # that excluded iron_condor before this rebalance)
        bull_put_allowed=True,
        bear_call_allowed=True,
        iron_condor_allowed=True,
        debit_spread_allowed=True,
        # Monitoring — fully automated
        requires_monitoring=False,
        monitoring_warning="",
        ui_color="orange",
        ui_icon="zap",
    ),

    TradingModeType.SCALPER: TradingModeConfig(
        mode=TradingModeType.SCALPER,
        display_name="Scalper (0–3 DTE)",
        description=(
            "Zero to 3 DTE options. Maximum theta decay but maximum "
            "gamma risk. A single 1% SPY move can cause max loss. "
            "REQUIRES active monitoring during market hours. "
            "Not suitable for passive automated trading."
        ),
        # DTE — extreme short
        dte_min=0, dte_target=1, dte_max=3, dte_exit=0,
        # Exit rules — lightning fast
        profit_target_pct=0.25,    # 25% profit — get out quickly
        stop_loss_multiplier=1.0,  # Stop at 1x credit — immediate
        # Entry — highest threshold of any mode, loosened slightly on
        # 2026-07-28 (was 0.70) to compensate a thin edge without shutting
        # it out entirely
        signal_threshold=0.65,
        min_iv_rank=35.0,
        min_credit_to_width=0.30,  # Higher credit requirement
        # Sizing — smallest (gamma can cause instant max loss), loosened
        # slightly on 2026-07-28 (was 0.5% / 3 / 2)
        risk_per_trade_pct=0.0075,  # 0.75% per trade
        max_trades_per_day=4,
        max_concurrent=3,
        # Strategies — puts only (directional is too dangerous)
        bull_put_allowed=True,
        bear_call_allowed=True,
        iron_condor_allowed=False,
        debit_spread_allowed=False,
        # Monitoring — MANDATORY
        requires_monitoring=True,
        monitoring_warning=(
            "⚠️ SCALPER MODE REQUIRES ACTIVE MONITORING.\n\n"
            "0–3 DTE options can go from full profit to max loss in under "
            "60 minutes on a normal SPY move. The automated stop loss "
            "will attempt to protect you, but fill quality degrades "
            "rapidly during fast markets.\n\n"
            "DO NOT run Scalper mode if you cannot watch the market "
            "between 9:30am and 4:00pm ET. Use Balanced or Conservative "
            "mode for passive automated trading."
        ),
        ui_color="red",
        ui_icon="alert-triangle",
    ),
}


@dataclass
class TradingModeState:
    """
    Current active trading mode with metadata.
    Persisted to DB so it survives restarts.
    """
    active_mode:    TradingModeType
    config:         TradingModeConfig
    activated_at:   datetime
    activated_by:   str                 # "user" | "auto_capital_preservation"
    previous_mode:  Optional[TradingModeType] = None
    override_reason: Optional[str] = None


class TradingModeManager:
    """
    Manages the active trading mode.
    Single source of truth for all mode-related configuration.

    Integration points:
      - paper_trader.py reads mode config for DTE and sizing
      - strategy_engine.py reads profit_target and stop_loss
      - guardrails.py reads signal_threshold and max_trades
      - frontend reads mode for display and user switching
    """

    def __init__(self) -> None:
        # Safe default until rehydrate() restores the real last-set mode from
        # the DB. Previously restored synchronously from
        # /tmp/olbostrade_trading_mode.json — wiped on every deploy (the
        # container's /tmp is not persisted across image rebuilds), so the
        # trading mode silently reset to Balanced on every restart. Same bug
        # class as execution_mode's pre-fix behavior (see execution_mode.py);
        # never got the same fix. Confirmed in production: the mode reset to
        # Balanced (activated_by="restored") at the exact timestamp of a
        # routine backend deploy, silently overriding whatever mode
        # (Aggressive) had actually been chosen.
        self._current = TradingModeState(
            active_mode=TradingModeType.BALANCED,
            config=TRADING_MODES[TradingModeType.BALANCED],
            activated_at=datetime.now(timezone.utc),
            activated_by="default",
        )

    @property
    def current(self) -> TradingModeState:
        return self._current

    @property
    def config(self) -> TradingModeConfig:
        return self._current.config

    async def rehydrate(self) -> None:
        """Restore the last-set mode from the DB on startup. Defaults to
        Balanced (the safe default) if there's no history or the read fails.
        Mirrors ExecutionModeManager.rehydrate()'s DB-backed pattern."""
        from app.core.database import AsyncSessionLocal
        from app.models.execution_event import ExecutionEvent
        from sqlalchemy import select

        try:
            async with AsyncSessionLocal() as session:
                row = (await session.execute(
                    select(ExecutionEvent)
                    .where(ExecutionEvent.kind == "trading_mode_change")
                    .order_by(ExecutionEvent.created_at.desc())
                    .limit(1)
                )).scalar_one_or_none()
            if row is not None:
                mode = TradingModeType(row.status)
                self._current = TradingModeState(
                    active_mode=mode,
                    config=TRADING_MODES[mode],
                    activated_at=row.created_at,
                    activated_by=(row.payload or {}).get("activated_by", "restored"),
                )
                logger.info("TradingModeManager restored — mode: %s", mode.value.upper())
            else:
                logger.info("TradingModeManager initialized — mode: BALANCED (default, no history)")
        except Exception as exc:
            logger.error(
                "TradingModeManager rehydrate failed (defaulting to BALANCED): %s", exc
            )

    async def set_mode(
        self,
        mode: TradingModeType,
        activated_by: str = "user",
        override_reason: Optional[str] = None,
    ) -> TradingModeState:
        """
        Switch to a new trading mode.

        For Scalper mode: logs a critical warning.
        Mode changes take effect on the NEXT signal cycle —
        never mid-trade.

        Returns the new TradingModeState.
        """
        previous = self._current.active_mode
        new_config = TRADING_MODES[mode]

        # Hard warning for Scalper mode
        if mode == TradingModeType.SCALPER:
            logger.warning(
                "SCALPER MODE ACTIVATED by %s. "
                "0-3 DTE requires active human monitoring. "
                "Automated stops are in place but fill quality "
                "degrades during fast markets.",
                activated_by,
            )

        self._current = TradingModeState(
            active_mode=mode,
            config=new_config,
            activated_at=datetime.now(timezone.utc),
            activated_by=activated_by,
            previous_mode=previous,
            override_reason=override_reason,
        )

        logger.info(
            "Trading mode changed: %s → %s (by %s)",
            previous.value, mode.value, activated_by,
        )
        # Persist so it survives backend restarts — writes to the same
        # execution_events table execution_mode.py uses, keyed by a distinct
        # kind, rather than a container-local file.
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.execution_event import ExecutionEvent

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    session.add(ExecutionEvent(
                        kind="trading_mode_change",
                        status=mode.value,
                        payload={
                            "mode": mode.value,
                            "activated_by": activated_by,
                            "override_reason": override_reason,
                        },
                    ))
        except Exception as e:
            logger.warning("Could not persist trading mode: %s", e)
        return self._current

    async def auto_downgrade(self, reason: str) -> TradingModeState:
        """
        Automatically downgrade to Conservative mode.
        Called by capital preservation logic when account
        drops below threshold.

        This is the only automatic mode change — always
        downgrade, never auto-upgrade.
        """
        if self._current.active_mode == TradingModeType.CONSERVATIVE:
            return self._current  # Already conservative

        logger.warning(
            "AUTO-DOWNGRADE to Conservative: %s "
            "(was: %s)",
            reason, self._current.active_mode.value,
        )
        return await self.set_mode(
            TradingModeType.CONSERVATIVE,
            activated_by="auto_capital_preservation",
            override_reason=reason,
        )

    def get_dte_target_for_chain(self) -> str:
        """
        Returns the target expiry date string for chain fetching.
        Format: YYYY-MM-DD
        """
        from datetime import date, timedelta
        target = date.today() + timedelta(days=self.config.dte_target)
        return target.strftime("%Y-%m-%d")

    def summary(self) -> dict:
        """Human-readable summary for API and logging."""
        c = self.config
        return {
            "mode":                c.mode.value,
            "display_name":        c.display_name,
            "description":         c.description,
            "dte_range":           f"{c.dte_min}–{c.dte_max} days",
            "dte_target":          c.dte_target,
            "profit_target":       f"{c.profit_target_pct:.0%}",
            "stop_loss":           f"{c.stop_loss_multiplier}x credit",
            "signal_threshold":    c.signal_threshold,
            "risk_per_trade":      f"{c.risk_per_trade_pct:.1%}",
            "max_trades_per_day":  c.max_trades_per_day,
            "max_concurrent":      c.max_concurrent,
            "strategies_allowed":  {
                "bull_put_spread":      c.bull_put_allowed,
                "bear_call_spread":     c.bear_call_allowed,
                "iron_condor":          c.iron_condor_allowed,
                "bull_call_debit":      c.debit_spread_allowed,
            },
            "requires_monitoring": c.requires_monitoring,
            "monitoring_warning":  c.monitoring_warning,
            "ui_color":            c.ui_color,
            "activated_at":        self._current.activated_at.isoformat(),
            "activated_by":        self._current.activated_by,
        }


# ── Singleton ──────────────────────────────────────────────────────────────────
trading_mode_manager = TradingModeManager()
