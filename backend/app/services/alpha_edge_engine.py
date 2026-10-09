"""
Alpha Edge Signal — first vertical slice (docs/trade-desk-2.0/MASTER_SPEC.md
§5.3 "Alpha Edge": Entry Score, Hold Score, Exit Score, Risk Score, a
lifecycle state, a score trend, and evidence, per symbol).

This does NOT build a new prediction model — every score is derived from
signal-scoring code that already exists and is already trusted elsewhere
in this app:
  - Entry/evidence (equity):  equity_signal_engine.score_equity_signal()
  - Entry (options):          OptionsSignalHistory.signal_score (recent)
  - Risk Score (both):        trade_frequency_controller.risk_score()
  - Exit (options):           Trade.mae_pnl, kept live by
                               trade_excursion_tracker.py off real broker marks
  - Exit (equity):            no per-position MAE exists for equities (no
                               Trade row — equities have no options-style
                               spread book); honestly reported as the
                               complement of Hold Score, not independently
                               modeled — see exit_score_basis in the response.

No new table, no background job, no real-time event bus (none of that
infrastructure exists anywhere in this app today) — every score here is
computed fresh, on demand, per request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

NEW = "new"
CONFIRMED = "confirmed"
DECAYING = "decaying"
EXPIRED = "expired"

_TERMINAL_OUTCOME_STATUSES = {"target_hit", "stop_hit", "expired"}
_TERMINAL_TRADE_STATUSES = {"closed", "expired"}


@dataclass
class ScoreTrend:
    direction: str   # "improving" | "declining" | "flat" | "not_tracked"
    delta: Optional[float]
    basis: str


@dataclass
class AlphaEdgeResult:
    ticker: str
    asset_type: str
    entry_score: Optional[int]
    hold_score: Optional[int]
    exit_score: Optional[int]
    exit_score_basis: str
    risk_score: Optional[int]
    lifecycle_state: str
    score_trend: ScoreTrend
    current_action: Optional[str]
    current_confidence: Optional[float]
    position: dict
    supporting_evidence: list = field(default_factory=list)
    deterioration_evidence: list = field(default_factory=list)
    data_sources: dict = field(default_factory=dict)
    error: Optional[str] = None
    opportunity_score: Optional[int] = None


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def split_evidence(reasons: dict, top_n: int = 3) -> tuple[list[dict], list[dict]]:
    """Split score_equity_signal()'s {factor: signed_points} into
    top-N supporting (positive) / deteriorating (negative) lists, matching
    the {feature, impact} shape SignalAttributionData already expects."""
    positive = sorted(
        ((k, v) for k, v in reasons.items() if v > 0), key=lambda kv: -kv[1]
    )[:top_n]
    negative = sorted(
        ((k, v) for k, v in reasons.items() if v < 0), key=lambda kv: kv[1]
    )[:top_n]
    return (
        [{"feature": k, "impact": round(v, 3)} for k, v in positive],
        [{"feature": k, "impact": round(v, 3)} for k, v in negative],
    )


# ── Equity ────────────────────────────────────────────────────────────────

def compute_equity_scores(
    current_action: str,
    current_confidence: float,
    position_direction: Optional[str],
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Pure math: entry_score always computed; hold/exit only meaningful with
    a position (or anchor) present — otherwise None, never a fabricated
    midpoint. Reuses trade_frequency_controller.risk_score() at the call
    site, not reimplemented here.
    """
    entry_score = round(current_confidence * 100)

    if position_direction is None:
        return entry_score, None, None

    if current_action == position_direction:
        alignment = 1
    elif current_action == "HOLD":
        alignment = 0
    else:
        alignment = -1

    hold_score = round(_clamp(50 + alignment * current_confidence * 50))
    exit_score = round(_clamp(100 - hold_score))
    return entry_score, hold_score, exit_score


def equity_lifecycle_state(
    position_direction: Optional[str],
    current_action: str,
    anchor_status: Optional[str],
) -> str:
    """
    anchor_status: the most recent signal_outcomes row's status for this
    ticker, when one exists ("pending" | "target_hit" | "stop_hit" | "expired").
    Advisory only — never auto-closes a position, matching strategy_health.py's
    own posture (state informs, it doesn't act).
    """
    has_context = position_direction is not None or anchor_status is not None

    if anchor_status in _TERMINAL_OUTCOME_STATUSES:
        return EXPIRED
    if position_direction is not None and current_action not in ("HOLD", position_direction):
        return EXPIRED  # thesis reversed
    if not has_context:
        return NEW
    if current_action == "HOLD":
        return DECAYING
    return CONFIRMED


def equity_score_trend(
    current_score: Optional[int],
    anchor_confidence: Optional[float],
    anchor_generated_at: Optional[datetime],
) -> ScoreTrend:
    if current_score is None or anchor_confidence is None:
        return ScoreTrend("not_tracked", None, "no prior signal_outcomes record for this ticker")
    anchor_score = anchor_confidence * 100
    delta = round(current_score - anchor_score, 1)
    direction = "improving" if delta > 5 else "declining" if delta < -5 else "flat"
    basis = f"vs signal recorded {anchor_generated_at.isoformat() if anchor_generated_at else 'unknown time'}"
    return ScoreTrend(direction, delta, basis)


# ── Options ───────────────────────────────────────────────────────────────

def compute_options_exit_score(
    short_strike: float, long_strike: float, credit_received: float, mae_pnl: Optional[float],
) -> int:
    """Live-data-backed: mae_pnl is continuously refreshed from real broker
    marks by trade_excursion_tracker.py — this is not a re-score."""
    max_loss_est = abs(short_strike - long_strike) * 100 - credit_received * 100
    if max_loss_est <= 0 or mae_pnl is None:
        return 0
    adverse = abs(min(mae_pnl, 0.0))
    return round(_clamp(100 * adverse / max_loss_est))


def compute_options_hold_score(trade: Any) -> Optional[int]:
    """
    Sync, no I/O — Position Quality Score building block for an already-held
    options position, used by position_rotation.py. Mirrors
    compute_equity_hold_score's naming for the candidate-building loop but
    needs no await: everything is already on the Trade row (mae_pnl kept
    live by trade_excursion_tracker's 60s scheduler job off real broker
    marks). Never returns None — matches compute_options_exit_score's own
    behavior (mae_pnl=None or non-positive max_loss_est yields exit_score=0,
    i.e. hold_score=100), consistent with how compute_options_alpha_edge
    already uses this same formula.
    """
    short_strike = float(getattr(trade, "short_strike", None) or 0)
    long_strike = float(getattr(trade, "long_strike", None) or 0)
    credit = float(getattr(trade, "credit_received", None) or 0)
    mae = trade.mae_pnl
    mae = float(mae) if mae is not None else None
    exit_score = compute_options_exit_score(short_strike, long_strike, credit, mae)
    return round(_clamp(100 - exit_score))


def options_lifecycle_state(trade_status: Optional[str], exit_score: Optional[int]) -> str:
    if trade_status is None:
        return NEW
    if trade_status in _TERMINAL_TRADE_STATUSES:
        return EXPIRED
    if exit_score is not None and exit_score > 50:
        return DECAYING
    return CONFIRMED


# ── Orchestration (I/O) ──────────────────────────────────────────────────

async def compute_equity_alpha_edge(ticker: str, broker=None) -> AlphaEdgeResult:
    from app.core.database import AsyncSessionLocal
    from app.models.signal_outcome import SignalOutcome
    from app.services.equity_signal_engine import (
        compute_indicators, compute_equity_trade_plan, score_equity_signal,
    )
    from app.services.trade_frequency_controller import risk_score as _risk_score
    from app.services.opportunity_score import compute_opportunity_score
    from sqlalchemy import select

    ticker = ticker.upper()
    data_sources = {"indicators": "unavailable", "position": "unavailable"}

    try:
        from app.main import _yf_bars
        import pandas as pd
        bars = await _yf_bars(ticker, limit=250)
        if len(bars) < 30:
            return AlphaEdgeResult(
                ticker=ticker, asset_type="equity",
                entry_score=None, hold_score=None, exit_score=None,
                exit_score_basis="inverse_of_hold_score", risk_score=None,
                lifecycle_state=NEW, score_trend=ScoreTrend("not_tracked", None, "insufficient bar history"),
                current_action=None, current_confidence=None,
                position={"held": False}, data_sources=data_sources,
                error=f"insufficient bar history for {ticker} ({len(bars)} bars)",
            )
        df = pd.DataFrame([{
            "open": float(b.open), "high": float(b.high),
            "low": float(b.low), "close": float(b.close), "volume": b.volume,
        } for b in bars])
        ind = compute_indicators(df)
        data_sources["indicators"] = "yfinance daily bars"
    except Exception as exc:
        return AlphaEdgeResult(
            ticker=ticker, asset_type="equity",
            entry_score=None, hold_score=None, exit_score=None,
            exit_score_basis="inverse_of_hold_score", risk_score=None,
            lifecycle_state=NEW, score_trend=ScoreTrend("not_tracked", None, "indicators unavailable"),
            current_action=None, current_confidence=None,
            position={"held": False}, data_sources=data_sources,
            error=f"failed to compute indicators: {exc}",
        )

    current_action, current_confidence, reasons = score_equity_signal(ind)
    supporting_evidence, deterioration_evidence = split_evidence(reasons)

    position_direction: Optional[str] = None
    position_info: dict = {"held": False}
    if broker is not None and hasattr(broker, "get_equity_positions"):
        try:
            positions = await broker.get_equity_positions()
            data_sources["position"] = "broker"
            for p in positions:
                if p.symbol.upper() == ticker:
                    position_direction = "BUY" if p.quantity > 0 else "SELL"
                    position_info = {"held": True, "direction": position_direction, "quantity": p.quantity}
                    break
        except Exception:
            pass  # position lookup failing must not block scoring — degrade to "no position known"

    anchor_status: Optional[str] = None
    anchor_confidence: Optional[float] = None
    anchor_generated_at: Optional[datetime] = None
    try:
        async with AsyncSessionLocal() as session:
            anchor = (await session.execute(
                # Equity only: Alpha Edge is an equity orchestrator, and while a
                # crypto symbol's DASH form cannot collide with an equity ticker
                # today, that is an accident of naming rather than a constraint
                # anything enforces. The filter states the intent.
                select(SignalOutcome).where(
                    SignalOutcome.ticker == ticker,
                    SignalOutcome.asset_type == "equity",
                )
                .order_by(SignalOutcome.generated_at.desc()).limit(1)
            )).scalars().first()
        if anchor is not None:
            anchor_status = anchor.status
            anchor_confidence = float(anchor.confidence)
            anchor_generated_at = anchor.generated_at
    except Exception:
        pass  # anchor lookup is enrichment, not required for a valid response

    entry_score, hold_score, exit_score = compute_equity_scores(
        current_action, current_confidence, position_direction,
    )
    lifecycle_state = equity_lifecycle_state(position_direction, current_action, anchor_status)
    current_score_for_trend = hold_score if hold_score is not None else entry_score
    score_trend = equity_score_trend(current_score_for_trend, anchor_confidence, anchor_generated_at)

    risk = None
    opportunity = None
    try:
        trade_plan = compute_equity_trade_plan(ind, current_action, portfolio_value=100_000.0)
        scoring_signal = {
            "confidence": current_confidence,
            "trade_plan": {"risk_reward": trade_plan.get("risk_reward")},
            "indicators": ind,
        }
        risk = _risk_score(scoring_signal)
        opportunity = compute_opportunity_score(scoring_signal)["score"]
    except Exception:
        pass

    return AlphaEdgeResult(
        ticker=ticker, asset_type="equity",
        entry_score=entry_score, hold_score=hold_score, exit_score=exit_score,
        exit_score_basis="inverse_of_hold_score", risk_score=risk,
        lifecycle_state=lifecycle_state, score_trend=score_trend,
        current_action=current_action, current_confidence=round(current_confidence, 4),
        position=position_info,
        supporting_evidence=supporting_evidence, deterioration_evidence=deterioration_evidence,
        data_sources=data_sources, opportunity_score=opportunity,
    )


async def compute_equity_hold_score(ticker: str, position_direction: str) -> Optional[int]:
    """
    Position Quality Score building block for an already-held equity
    position — used by position_rotation.py to rank rotation candidates.

    Composes the same _yf_bars -> compute_indicators -> score_equity_signal
    -> compute_equity_scores() pipeline compute_equity_alpha_edge() uses,
    but skips that function's broker.get_equity_positions() and
    SignalOutcome anchor lookups: the caller already knows the position and
    its direction from the open Trade row, so both would be redundant I/O
    per ticker. Returns None on any data failure (insufficient bars,
    indicator failure) — caller must fall back, never fabricate a score.
    """
    from app.services.equity_signal_engine import compute_indicators, score_equity_signal

    ticker = ticker.upper()
    try:
        from app.main import _yf_bars
        import pandas as pd
        bars = await _yf_bars(ticker, limit=250)
        if len(bars) < 30:
            return None
        df = pd.DataFrame([{
            "open": float(b.open), "high": float(b.high),
            "low": float(b.low), "close": float(b.close), "volume": b.volume,
        } for b in bars])
        ind = compute_indicators(df)
    except Exception as exc:
        # Was a bare `return None`, which blanked the hold score with no
        # trace of why. compute_indicators() returns empty when the `ta`
        # library is missing — a real, observed condition — so a silent None
        # here reads as "no score available" when the truth is "indicators
        # never computed".
        logger.warning("Alpha Edge: indicator computation failed for %s: %s", ticker, exc)
        return None

    current_action, current_confidence, _reasons = score_equity_signal(ind)
    _entry, hold_score, _exit = compute_equity_scores(
        current_action, current_confidence, position_direction,
    )
    return hold_score


async def compute_options_alpha_edge(ticker: str) -> AlphaEdgeResult:
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.models.options_signal_history import OptionsSignalHistory
    from app.services.trade_frequency_controller import risk_score as _risk_score
    from app.services.opportunity_score import compute_opportunity_score
    from sqlalchemy import select
    from datetime import timedelta

    ticker = ticker.upper()
    data_sources = {"position": "database", "entry_history": "database"}

    try:
        async with AsyncSessionLocal() as session:
            trade = (await session.execute(
                select(Trade).where(Trade.underlying == ticker, Trade.status == "open")
                .order_by(Trade.entry_date.desc()).limit(1)
            )).scalars().first()
            history = (await session.execute(
                select(OptionsSignalHistory).where(OptionsSignalHistory.ticker == ticker)
                .order_by(OptionsSignalHistory.generated_at.desc()).limit(1)
            )).scalars().first()
    except Exception as exc:
        return AlphaEdgeResult(
            ticker=ticker, asset_type="options",
            entry_score=None, hold_score=None, exit_score=None,
            exit_score_basis="live_mae_vs_max_loss", risk_score=None,
            lifecycle_state=NEW, score_trend=ScoreTrend("not_tracked", None, "database unavailable"),
            current_action=None, current_confidence=None,
            position={"held": False}, data_sources=data_sources,
            error=f"failed to load position/history: {exc}",
        )

    exit_score: Optional[int] = None
    hold_score: Optional[int] = None
    position_info: dict = {"held": False}
    max_loss_est: Optional[float] = None
    if trade is not None:
        short_strike = float(trade.short_strike or 0)
        long_strike = float(trade.long_strike or 0)
        credit = float(trade.credit_received or 0)
        mae = float(trade.mae_pnl) if trade.mae_pnl is not None else None
        exit_score = compute_options_exit_score(short_strike, long_strike, credit, mae)
        hold_score = round(_clamp(100 - exit_score))
        max_loss_est = abs(short_strike - long_strike) * 100 - credit * 100
        position_info = {
            "held": True, "direction": "SHORT" if credit >= 0 else "LONG",
            "quantity": trade.quantity, "status": trade.status,
        }

    entry_score: Optional[int] = None
    supporting_evidence: list = []
    deterioration_evidence: list = []
    if trade is None and history is not None:
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=5)
        hist_generated = history.generated_at
        if hist_generated.tzinfo is None:
            hist_generated = hist_generated.replace(tzinfo=timezone.utc)
        if hist_generated >= recent_cutoff:
            entry_score = round(_clamp(float(history.signal_score) * 100 if float(history.signal_score) <= 1 else float(history.signal_score)))
            evidence = history.evidence or {}
            supporting_evidence = evidence.get("top_positive_factors", [])[:3]
            deterioration_evidence = evidence.get("top_negative_factors", [])[:3]

    lifecycle_state = options_lifecycle_state(trade.status if trade else None, exit_score)
    score_trend = ScoreTrend("not_tracked", None, "no live-vs-entry comparison available for options in this slice")

    risk = None
    opportunity = None
    try:
        scoring_signal = None
        if trade is not None:
            scoring_signal = {
                "signal_score": float(trade.signal_score) if trade.signal_score is not None else 0.5,
                "spread": {"net_credit": float(trade.credit_received or 0), "max_loss": max_loss_est or 0},
            }
        elif history is not None:
            scoring_signal = {
                "signal_score": float(history.signal_score),
                "spread": {"net_credit": float(history.net_credit), "max_loss": float(history.max_loss)},
            }
        if scoring_signal is not None:
            risk = _risk_score(scoring_signal)
            opportunity = compute_opportunity_score(scoring_signal)["score"]
    except Exception:
        pass

    return AlphaEdgeResult(
        ticker=ticker, asset_type="options",
        entry_score=entry_score, hold_score=hold_score, exit_score=exit_score,
        exit_score_basis="live_mae_vs_max_loss", risk_score=risk,
        lifecycle_state=lifecycle_state, score_trend=score_trend,
        current_action=None, current_confidence=None,
        position=position_info,
        supporting_evidence=supporting_evidence, deterioration_evidence=deterioration_evidence,
        data_sources=data_sources, opportunity_score=opportunity,
    )
