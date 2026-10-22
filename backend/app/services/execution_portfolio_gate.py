"""
Execution portfolio gate — Step 8 money-path checks for `_execute_signal`.

Enforced (fail-closed when state is available):
  - max concurrent open positions
  - single-underlying concentration (RiskManager.MAX_SINGLE_UNDERLYING)
  - sector concentration hard block (aligned with options scan; not warn-only)
  - portfolio heat high (HEAT_HIGH) including projected heat after this trade

Explicitly NOT enforced by default:
  - portfolio delta / vega caps (RiskManager.MAX_PORTFOLIO_DELTA / MAX_VEGA_EXPOSURE)
    — miscalibrated for real spreads; enable only via
    settings.execution_enforce_portfolio_greeks

Load failure (cannot read open trades): refuse new entries with an explicit
portfolio_state_unavailable flag. Risk-reducing exits use separately authorized
close paths; never infer zero exposure from missing data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import settings
from app.services.portfolio_engine import (
    HEAT_HIGH, is_cappable_sector, position_risk_dollars, sector_for,
)
from app.services.risk_manager import PortfolioRiskState, ProposedTrade, RiskManager
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PortfolioStateUnavailable(RuntimeError):
    """Required database-backed portfolio exposure could not be verified."""


# Per-regime concurrency tightening — separate from regime_classifier.py's
# REGIME_CONFIG size_multiplier because position-size economics and
# concurrent-position-count risk appetite don't necessarily move together
# (e.g. LOW_VOL_TRENDING options get less premium per trade but that's not
# a headcount-risk signal). Tighten-only: every value must be <= 1.0 — this
# gate must never raise capacity above the mode/global caps computed below.
# Unrecognized/missing regime strings default to 1.0 (no-op) via
# REGIME_CONCURRENCY_MULTIPLIER.get(..., 1.0) at the call site — new logic
# added to an already-live, production-consequential gate must fail toward
# current behavior, not toward maximum restriction.
REGIME_CONCURRENCY_MULTIPLIER: dict[str, float] = {
    "low_vol_trending":   1.0,   # calm/directional — REGIME_CONFIG's own equity_size_multiplier is a full 1.0, no headcount risk elevation
    "normal_mean_revert": 1.0,   # baseline "ideal for premium selling" regime — no tightening
    "high_vol_trending":  0.75,  # "elevated risk" per REGIME_CONFIG — matches its own options_size_multiplier=0.75
    "crisis":             0.0,   # defense-in-depth only; the real CRISIS block is upstream (signal generation already skips CRISIS entirely)
    "unknown":            0.5,   # matches REGIME_CONFIG's own options_size_multiplier=0.5 for uncertain classification
}


@dataclass
class PortfolioGateResult:
    allowed: bool
    reason: Optional[str]
    flags: list[str] = field(default_factory=list)
    proposed_risk_dollars: float = 0.0


def estimate_proposed_risk_dollars(signal: dict[str, Any]) -> float:
    """
    Dollar risk at stake for the proposed order.
    Options: spread.max_loss is per-contract dollars (scanner convention) × qty.
    Equity: |entry−stop|×shares when stop set; else entry×shares (worst-case proxy).
    """
    asset = (signal.get("asset_type") or "equity").lower()
    if asset == "options":
        spread = signal.get("spread") or {}
        max_loss = float(spread.get("max_loss") or 0)
        qty = int(signal.get("quantity") or 1)
        return max(max_loss, 0.0) * max(qty, 0)

    plan = signal.get("trade_plan") or {}
    shares = float(plan.get("shares") or 0)
    entry = float(plan.get("entry_price") or 0)
    stop = plan.get("stop_price")
    if stop is not None and entry:
        return abs(entry - float(stop)) * max(shares, 0)
    return abs(entry) * max(shares, 0)


def evaluate_portfolio_gates(
    signal: dict[str, Any],
    portfolio: PortfolioRiskState,
    *,
    enforce_greeks: bool | None = None,
) -> PortfolioGateResult:
    """
    Pure evaluation against PortfolioRiskState. Does not touch DB/broker.
    """
    if enforce_greeks is None:
        enforce_greeks = bool(getattr(settings, "execution_enforce_portfolio_greeks", False))

    flags: list[str] = []
    ticker = (signal.get("ticker") or "").upper()
    strategy = signal.get("strategy") or (
        "equity" if (signal.get("asset_type") or "equity") == "equity" else "options"
    )
    qty = int(signal.get("quantity") or (signal.get("trade_plan") or {}).get("shares") or 1)
    risk_dollars = estimate_proposed_risk_dollars(signal)
    sector = sector_for(ticker)

    rm = RiskManager()
    # The active trading mode's own max_concurrent (e.g. Scalper=2, tightened
    # for compounding gamma risk on 0-3 DTE) previously had no effect here —
    # only the global settings.max_concurrent_positions (default 5) was
    # enforced, silently allowing more concurrent positions than the mode's
    # own risk design intends. Take the tighter of the two; mode can only
    # restrict further, never loosen past the global default.
    from app.services.trading_mode import trading_mode_manager
    max_pos = min(rm.max_concurrent, trading_mode_manager.config.max_concurrent)

    # Regime can only ever tighten max_pos further, never loosen past the
    # mode/global caps already computed above.
    regime_multiplier = REGIME_CONCURRENCY_MULTIPLIER.get(signal.get("regime"), 1.0)
    regime_tightened = regime_multiplier < 1.0
    if regime_tightened:
        max_pos = max(1, math.floor(max_pos * regime_multiplier))

    if portfolio.open_position_count >= max_pos:
        return PortfolioGateResult(
            allowed=False,
            reason=(
                f"Max concurrent positions reached: "
                f"{portfolio.open_position_count}/{max_pos}"
                + (f" (regime-tightened: {signal.get('regime')})" if regime_tightened else "")
            ),
            flags=["max_positions"] + (["regime_tightened_capacity"] if regime_tightened else []),
            proposed_risk_dollars=risk_dollars,
        )

    if portfolio.portfolio_value > 0:
        current_u = portfolio.positions_by_underlying.get(ticker, 0.0)
        new_u_pct = (current_u + risk_dollars) / portfolio.portfolio_value
        if new_u_pct > RiskManager.MAX_SINGLE_UNDERLYING:
            return PortfolioGateResult(
                allowed=False,
                reason=(
                    f"Single underlying concentration: {ticker} would be "
                    f"{new_u_pct:.1%} of portfolio "
                    f"(max {RiskManager.MAX_SINGLE_UNDERLYING:.0%})"
                ),
                flags=["concentration_limit"],
                proposed_risk_dollars=risk_dollars,
            )

        current_s = portfolio.positions_by_sector.get(sector, 0.0)
        new_s_pct = (current_s + risk_dollars) / portfolio.portfolio_value
        # An unclassified ticker is not in a sector with the other
        # unclassified ones — see portfolio_engine.is_cappable_sector.
        if is_cappable_sector(sector) and new_s_pct > RiskManager.MAX_SECTOR_CONCENTRATION:
            return PortfolioGateResult(
                allowed=False,
                reason=(
                    f"Sector concentration: {sector} ({ticker}) would be "
                    f"{new_s_pct:.1%} of portfolio "
                    f"(max {RiskManager.MAX_SECTOR_CONCENTRATION:.0%})"
                ),
                flags=["sector_concentration_limit"],
                proposed_risk_dollars=risk_dollars,
            )

        total_open_risk = sum(portfolio.positions_by_underlying.values())
        projected_heat = (total_open_risk + risk_dollars) / portfolio.portfolio_value
        if projected_heat > HEAT_HIGH:
            return PortfolioGateResult(
                allowed=False,
                reason=(
                    f"Portfolio heat would be {projected_heat:.0%} "
                    f"(max {HEAT_HIGH:.0%} for new entries)"
                ),
                flags=["portfolio_heat_high"],
                proposed_risk_dollars=risk_dollars,
            )

    if enforce_greeks:
        # Optional — off by default. Uses ProposedTrade with signal Greeks if present.
        intel = signal.get("intelligence") or {}
        delta = float(
            signal.get("delta")
            or intel.get("delta_short")
            or 0.0
        )
        vega = float(signal.get("vega") or intel.get("vega") or 0.0)
        trade = ProposedTrade(
            strategy=strategy,
            underlying=ticker or "?",
            sector=sector,
            delta=delta * max(qty, 1),
            vega=vega * max(qty, 1),
            max_loss_dollars=max(risk_dollars, 1.0),
            contracts=max(qty, 1),
        )
        approval = rm.approve_trade(trade, portfolio)
        # approve_trade also re-checks concentration; if it fails for Greeks:
        if not approval.approved and (
            "delta_limit" in approval.flags or "vega_limit" in approval.flags
        ):
            return PortfolioGateResult(
                allowed=False,
                reason=approval.reason,
                flags=list(approval.flags),
                proposed_risk_dollars=risk_dollars,
            )
        flags.extend(approval.flags)

    return PortfolioGateResult(
        allowed=True,
        reason=None,
        flags=flags,
        proposed_risk_dollars=risk_dollars,
    )


async def load_portfolio_risk_state(portfolio_value: float) -> PortfolioRiskState:
    """
    Build PortfolioRiskState from open trades (same dollars/sectors as /api/portfolio/heat).
    On DB load failure raise PortfolioStateUnavailable instead of fabricating
    an empty portfolio.

    open_position_count is the max of the DB's tracked open-Trade count and
    the broker's own live distinct-underlying position count. The DB count
    alone can silently undercount whenever a position exists at the broker
    with no matching Trade row — confirmed live in production 2026-08-26,
    where several untracked positions consumed real margin/risk capacity
    while this gate believed only 3 positions were open, since every
    position-count check here only ever queried the Trade table. The
    broker read (get_positions(), used below) is a local ib.portfolio()
    cache lookup, not a network round-trip — same convention already used
    by position_rotation.py/kill_switch.py — so this adds no meaningful
    latency and needs no coordinator/timeout handling. A broker-read
    failure fails open to the DB-only count rather than blocking the gate.
    """
    by_underlying: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    open_count = 0
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.trade import Trade
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            open_trades = (
                await session.execute(select(Trade).where(Trade.status == "open"))
            ).scalars().all()
        open_count = len(open_trades)
        for t in open_trades:
            u = (t.underlying or "?").upper()
            s = sector_for(u)
            r = position_risk_dollars(t)
            by_underlying[u] = by_underlying.get(u, 0.0) + r
            by_sector[s] = by_sector.get(s, 0.0) + r

        try:
            from app.broker.broker_factory import get_broker
            live_positions = await get_broker().get_positions()
            broker_open_count = len({
                (p.underlying or "").upper()
                for p in live_positions
                if p.quantity != 0
            })
            open_count = max(open_count, broker_open_count)
        except Exception as broker_exc:
            logger.warning(
                "portfolio gate: could not cross-check live broker position "
                "count (using DB-tracked count only): %s", broker_exc,
            )
    except Exception as exc:
        logger.error("portfolio gate: failed to verify open positions: %s", exc)
        raise PortfolioStateUnavailable("portfolio positions unavailable") from exc

    return PortfolioRiskState(
        net_delta=0.0,
        net_vega=0.0,
        net_theta=0.0,
        open_position_count=open_count,
        portfolio_value=float(portfolio_value or 0),
        positions_by_underlying=by_underlying,
        positions_by_sector=by_sector,
    )


async def check_execution_portfolio(
    signal: dict[str, Any],
    portfolio_value: float,
) -> PortfolioGateResult:
    """Load portfolio risk then evaluate. Used by `_execute_signal`."""
    if not getattr(settings, "execution_portfolio_gate", True):
        return PortfolioGateResult(allowed=True, reason=None, flags=["portfolio_gate_disabled"])

    try:
        portfolio = await load_portfolio_risk_state(portfolio_value)
    except PortfolioStateUnavailable:
        return PortfolioGateResult(
            allowed=False,
            reason="portfolio state unavailable; new entries blocked",
            flags=["portfolio_state_unavailable"],
        )
    return evaluate_portfolio_gates(signal, portfolio)
