"""Strategy config and signal routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class StrategyConfig(BaseModel):
    strategy: str
    enabled: bool
    signal_threshold: Optional[float] = None


@router.get("/config")
async def get_strategy_config():
    """Return enabled strategies from current trading mode."""
    try:
        from app.services.trading_mode import trading_mode_manager
        state = trading_mode_manager.current          # TradingModeState
        mode_name = state.active_mode.value if hasattr(state, "active_mode") else "balanced"
        allowed = getattr(state, "strategies_allowed", {}) or {}
        return {
            "strategies": [
                {"name": s, "enabled": allowed.get(s, True) if isinstance(allowed, dict) else True}
                for s in ["bull_put_spread", "bear_call_spread", "iron_condor", "bull_call_debit_spread"]
            ],
            "mode": mode_name,
        }
    except Exception:
        return {
            "strategies": [
                {"name": "bull_put_spread",      "enabled": True},
                {"name": "bear_call_spread",     "enabled": True},
                {"name": "iron_condor",          "enabled": True},
                {"name": "bull_call_debit_spread","enabled": True},
            ]
        }


@router.put("/config")
async def update_strategy_config(config: StrategyConfig):
    """
    No-op stub retained for backwards compatibility with older clients.
    Per-strategy enable/disable is governed by the trading mode, not by this
    endpoint. Use POST /api/mode/set to change the active mode and strategy set.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "message": "This endpoint is deprecated and has no effect.",
            "action": "Use POST /api/mode/set to change the active trading mode and strategy set.",
        },
    )


@router.get("/signals/current")
async def get_current_signals():
    """
    Returns current actionable signals:
    - Equity signals from the equity scan engine (BUY/SELL above confidence threshold)
    - Options signals from regime-gated strategy list
    """
    from datetime import datetime, timezone
    from app.core.config import settings

    signals = []

    # ── Equity signals ────────────────────────────────────────────────────────
    try:
        from app.api.routes.equity import _recent_signals
        from app.main import _current_regime

        min_conf = settings.equity_min_confidence
        equity_sigs = [
            s for s in _recent_signals
            if s.get("action") in ("BUY", "SELL")
            and s.get("confidence", 0) >= min_conf
        ][:5]

        for s in equity_sigs:
            signals.append({
                "type":        "equity",
                "ticker":      s["ticker"],
                "action":      s["action"],
                "confidence":  s["confidence"],
                "generated_at": s["generated_at"],
                "trade_plan":  s.get("trade_plan", {}),
                "indicators":  s.get("indicators", {}),
                "regime_gated": _current_regime.equity_allowed if _current_regime else False,
            })
    except Exception:
        pass

    # ── Options signals ───────────────────────────────────────────────────────
    try:
        from app.main import _current_regime
        if _current_regime and _current_regime.options_allowed:
            for strategy in _current_regime.strategies_allowed:
                signals.append({
                    "type":       "options",
                    "strategy":   strategy,
                    "underlying": "SPY",
                    "action":     "EVALUATE",
                    "confidence": _current_regime.confidence,
                    "generated_at": _current_regime.classified_at.isoformat(),
                    "regime":     _current_regime.regime.value,
                    "size_multiplier": _current_regime.options_size_multiplier,
                })
    except Exception:
        pass

    return {
        "signals":      signals,
        "total":        len(signals),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/signals/{signal_id}/explanation")
async def get_signal_explanation(signal_id: str):
    """Explain the reasoning behind an equity signal."""
    try:
        from app.api.routes.equity import _recent_signals
        sig = next((s for s in _recent_signals if s.get("id") == signal_id), None)
        if not sig:
            return {"signal_id": signal_id, "explanation": None, "error": "Signal not found"}
        return {
            "signal_id":   signal_id,
            "ticker":      sig["ticker"],
            "action":      sig["action"],
            "confidence":  sig["confidence"],
            "explanation": {
                "reasons":         sig.get("reasons", {}),
                "indicators":      sig.get("indicators", {}),
                "orderflow_score": sig.get("orderflow_score"),
                "iv_boost":        sig.get("iv_overlay_boost"),
                "earnings_gated":  sig.get("earnings_gated"),
            },
        }
    except Exception as exc:
        return {"signal_id": signal_id, "explanation": None, "error": str(exc)}


async def _evaluate_health(min_sample: int):
    """Shared loader: grade every strategy from closed trades + promoted baselines."""
    from app.core.config import settings
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.models.research_experiment import ResearchExperiment
    from app.services.research_lab import baselines_from_experiments, PROMOTED
    from app.services.strategy_health import evaluate_all
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        trades = (await session.execute(
            select(Trade).where(Trade.status == "closed", Trade.pnl.isnot(None))
        )).scalars().all()
        promoted = (await session.execute(
            select(ResearchExperiment).where(ResearchExperiment.stage == PROMOTED)
        )).scalars().all()
        overrides = baselines_from_experiments([e.as_dict() for e in promoted])

    by_strategy: dict[str, list[dict]] = {}
    for t in trades:
        by_strategy.setdefault(str(t.strategy), []).append(
            {"pnl": float(t.pnl), "entry_date": t.entry_date, "exit_date": t.exit_date}
        )
    return evaluate_all(by_strategy, settings.starting_capital,
                        overrides=overrides, min_sample=min_sample)


async def _load_registry_cards(min_sample: int = 20) -> list[dict]:
    """
    Merge seeded StrategyProfile rows (static eligibility/lifecycle
    metadata — see migration 0022) with live health from the same
    _evaluate_health() the /health route already uses. health_score/
    health_status/sample_size are None/0 for a strategy with no closed
    trades yet — never fabricated as a real score.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.strategy_profile import StrategyProfile
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        profiles = (await session.execute(select(StrategyProfile))).scalars().all()

    try:
        health = await _evaluate_health(min_sample)
    except Exception:
        health = []
    health_by_strategy = {h.strategy: h for h in health}

    cards = []
    for p in profiles:
        h = health_by_strategy.get(p.strategy_id)
        cards.append({
            "strategy_id": p.strategy_id,
            "name": p.name,
            "version": p.version,
            "asset_class": p.asset_class,
            "supported_symbols": p.supported_symbols,
            "supported_regimes": p.supported_regimes,
            "supported_volatility_regimes": p.supported_volatility_regimes,
            "risk_profile_compatibility": p.risk_profile_compatibility,
            "manual_eligible": p.manual_eligible,
            "copilot_eligible": p.copilot_eligible,
            "autopilot_supported": p.autopilot_supported,
            "lifecycle_status": p.lifecycle_status,
            "enabled": p.enabled,
            "allocation_limit_pct": float(p.allocation_limit_pct) if p.allocation_limit_pct is not None else None,
            "main_risk_warning": p.main_risk_warning,
            "health_score": h.score if h else None,
            "health_status": h.status if h else None,
            "sample_size": h.sample_size if h else 0,
        })
    return cards


@router.get("/registry")
async def get_strategy_registry(min_sample: int = 20):
    """Strategy Cards data: seeded profile metadata merged with live health."""
    cards = await _load_registry_cards(min_sample)
    return {"strategies": cards, "total": len(cards)}


@router.get("/registry/{strategy_id}")
async def get_strategy_registry_one(strategy_id: str, min_sample: int = 20):
    cards = await _load_registry_cards(min_sample)
    for card in cards:
        if card["strategy_id"] == strategy_id:
            return card
    raise HTTPException(404, f"Unknown strategy_id: {strategy_id}")


@router.get("/health")
async def get_strategy_health(min_sample: int = 20):
    """
    Per-strategy health: live win rate / expectancy / drawdown vs. baseline, a
    0–100 score, a degradation grade (healthy / watch / degraded /
    insufficient_data), and a recommended action. Suspended strategies accept no
    new entries.
    """
    try:
        health = await _evaluate_health(min_sample)
    except Exception as exc:
        return {"strategies": [], "error": str(exc)}
    suspended = [h.strategy for h in health if h.status == "degraded"]
    return {
        "strategies": [h.as_dict() for h in health],
        "suspended": suspended,
        "total_strategies": len(health),
    }


@router.get("/meta")
async def get_meta_strategy(min_sample: int = 20):
    """
    Meta-strategy decisions: given the active regime + each strategy's health,
    which strategies are active and at what allocation tilt. Feeds the Capital
    Allocation Engine.
    """
    from app.services.meta_strategy import decide, active_strategies
    try:
        health = await _evaluate_health(min_sample)
    except Exception as exc:
        return {"regime": None, "decisions": [], "error": str(exc)}

    from app.main import _current_regime
    regime = getattr(getattr(_current_regime, "regime", None), "value", None) or "unknown"
    decisions = decide(regime, [h.as_dict() for h in health])
    return {
        "regime": regime,
        "decisions": [d.as_dict() for d in decisions],
        "active_strategies": active_strategies(decisions),
        "total": len(decisions),
    }
