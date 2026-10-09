"""Research routes — strategy comparison, model performance, and the Research Lab."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

# In-memory store for comparison results
_comparison_result: dict | None = None
_comparison_running: bool = False


async def _run_comparison_task(start_date: str, end_date: str, capital: float) -> None:
    global _comparison_result, _comparison_running
    _comparison_running = True
    try:
        from app.broker.broker_factory import get_broker
        from app.services.data_fetcher import DataFetcher
        from app.services.backtester import Backtester

        broker  = get_broker()
        fetcher = DataFetcher(broker)
        bt      = Backtester(fetcher)

        strategies = ["bull_put_spread", "bear_call_spread", "iron_condor", "bull_call_debit_spread"]
        results = {}
        for strat in strategies:
            try:
                r = await bt.run(strat, start_date, end_date, capital)
                m = r.metrics
                total_pnl = r.ending_capital - r.starting_capital
                results[strat] = {
                    "total_trades":     m.total_trades if m else len(r.trades),
                    "win_rate":         round(m.win_rate, 4) if m else 0.0,
                    "total_pnl":        round(total_pnl, 2),
                    "total_return_pct": round(m.total_return_pct, 4) if m else 0.0,
                    "max_drawdown_pct": round(m.max_drawdown_pct, 4) if m else 0.0,
                    "sharpe_ratio":     round(m.sharpe_ratio, 4) if m else 0.0,
                    "profit_factor":    round(m.profit_factor, 4) if m else 0.0,
                }
            except Exception as exc:
                results[strat] = {"error": str(exc)}

        # Rank by total_pnl
        ranked = sorted(
            [(s, d) for s, d in results.items() if "error" not in d],
            key=lambda x: x[1].get("total_pnl", 0), reverse=True
        )
        _comparison_result = {
            "status":       "completed",
            "start_date":   start_date,
            "end_date":     end_date,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "results":      results,
            "ranking":      [s for s, _ in ranked],
            "best_strategy": ranked[0][0] if ranked else None,
        }
    except Exception as exc:
        _comparison_result = {"status": "failed", "error": str(exc)}
    finally:
        _comparison_running = False


@router.get("/comparison")
async def get_comparison():
    if _comparison_result is None:
        return {"comparison": None, "message": "Run POST /api/research/run-comparison first."}
    return {"comparison": _comparison_result}


@router.post("/run-comparison")
async def run_comparison(
    start_date: str = "2023-01-01",
    end_date:   str = "2024-12-31",
    starting_capital: float = 25000.0,
):
    global _comparison_running
    if _comparison_running:
        return {"status": "already_running", "message": "Comparison already in progress."}
    asyncio.create_task(_run_comparison_task(start_date, end_date, starting_capital))
    return {
        "status":     "running",
        "start_date": start_date,
        "end_date":   end_date,
        "message":    "Poll GET /api/research/comparison for results.",
    }


@router.get("/model-performance")
async def get_model_performance():
    """Return signal scorer model metadata."""
    try:
        from app.services.signal_scorer import SignalScorer
        from app.core.config import settings
        import os
        scorer = SignalScorer()
        trained = os.path.exists(settings.model_path)
        # "auc" doesn't apply to this model — it's a RoR regressor, not a
        # classifier — kept as null for any older frontend expecting the key;
        # validation_metrics carries the metrics that actually apply
        # (MAE / R² / directional accuracy), written by ml/train_signal_scorer.py.
        return {
            "model_version":  scorer.model_version,
            "model_type":     scorer.model_type,
            "model_path":     settings.model_path,
            "trained":        trained,
            "auc":            None,
            "last_trained":   scorer.last_trained,
            "validation_metrics": scorer.validation_metrics,
            "retrain_schedule": settings.retrain_schedule,
        }
    except Exception as exc:
        return {"model_version": "untrained", "auc": None, "last_trained": None, "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# Research Lab — strategy promotion funnel (draft → backtested → paper → promoted)
# ══════════════════════════════════════════════════════════════════════════════
class CreateExperiment(BaseModel):
    name:       str
    strategy:   str
    hypothesis: Optional[str] = None
    spec:       Optional[dict] = None
    # Registry metadata (Phase 2) — optional at creation, required before promotion.
    version:     Optional[str] = None
    author:      Optional[str] = None
    asset_class: Optional[str] = None
    market_type: Optional[str] = None
    supported_regimes: Optional[list] = None
    risk_profile: Optional[str] = None


class TransitionRequest(BaseModel):
    target:     str                    # backtested | walk_forward | paper | promoted | archived | draft
    # The id of a COMPLETED BacktestRun. The server reads that run's own
    # metrics and writes the provenance; this is the only way to establish a
    # backtested stage.
    backtest_run_id: Optional[str] = None
    # Client-supplied metric dicts. Retained so an older client gets a clear
    # refusal instead of a 422, and deliberately NOT forwarded to the gates:
    # the UI used to post sharpe 1.0 / oos_sharpe 0.9 literals here and the
    # gates, which evaluate whatever dict they are given, cleared them.
    metrics:    Optional[dict] = None
    wf_metrics: Optional[dict] = None
    perf:       Optional[dict] = None    # paper performance (→ promoted)


async def _backtest_evidence(session, run_id: str, strategy: str):
    """Build backtest evidence from a stored BacktestRun, or say why not.

    Returns (evidence, None) or (None, reason). The metrics come from the run
    row, never from the request, and the provenance block records what a
    reader needs to decide whether the number applies: which engine, which
    run, which strategy, which window, and the configuration it ran under
    (commissions and slippage live in `parameters`).

    The run's strategy is checked against the experiment's. Evidence from a
    backtest of something else is not evidence about this strategy, and
    nothing downstream would have noticed.
    """
    from app.models.backtest_result import BacktestRun
    from app.services.research_lab import PROVENANCE_KEY
    from sqlalchemy import select
    import uuid as _uuid

    try:
        key = _uuid.UUID(str(run_id))
    except (ValueError, AttributeError, TypeError):
        return None, f"backtest_run_id {run_id!r} is not a valid run id"

    run = (await session.execute(
        select(BacktestRun).where(BacktestRun.id == key)
    )).scalar_one_or_none()
    if run is None:
        return None, f"backtest run {run_id} not found"
    if not run.metrics:
        return None, (
            f"backtest run {run_id} has no metrics — it has not completed, "
            "or it failed"
        )
    if str(run.strategy) != str(strategy):
        return None, (
            f"backtest run {run_id} ran strategy {run.strategy!r}, but this "
            f"experiment is {strategy!r}"
        )

    metrics = dict(run.metrics)
    # evaluate_backtest_gate reads "sharpe"; the engine's own key is
    # "sharpe_ratio". Mapping this on the server means a client can no longer
    # get it wrong — nor quietly right by sending its own number.
    if "sharpe" not in metrics and "sharpe_ratio" in metrics:
        metrics["sharpe"] = metrics["sharpe_ratio"]
    metrics[PROVENANCE_KEY] = {
        "source": "backtester",
        "run_id": str(run.id),
        "strategy": run.strategy,
        "start_date": run.start_date.isoformat() if run.start_date else None,
        "end_date": run.end_date.isoformat() if run.end_date else None,
        "starting_capital": str(run.starting_capital) if run.starting_capital is not None else None,
        "parameters": run.parameters or {},
        "recorded_at": run.created_at.isoformat() if run.created_at else None,
    }
    return metrics, None


async def _experiment_or_none(session, exp_id: str):
    from app.models.research_experiment import ResearchExperiment
    from sqlalchemy import select
    return (await session.execute(
        select(ResearchExperiment).where(ResearchExperiment.id == exp_id)
    )).scalar_one_or_none()


@router.get("/lab/experiments")
async def list_experiments():
    """All Research Lab experiments, newest first."""
    from app.core.database import AsyncSessionLocal
    from app.models.research_experiment import ResearchExperiment
    from sqlalchemy import select
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(ResearchExperiment).order_by(ResearchExperiment.created_at.desc())
            )).scalars().all()
        return {"experiments": [r.as_dict() for r in rows], "total": len(rows)}
    except Exception as exc:
        return {"experiments": [], "total": 0, "error": str(exc)}


@router.post("/lab/experiments")
async def create_experiment(req: CreateExperiment):
    """Create a DRAFT experiment from a hypothesis + Symphony spec."""
    from app.core.database import AsyncSessionLocal
    from app.models.research_experiment import ResearchExperiment
    from app.services.research_lab import DRAFT
    try:
        exp = ResearchExperiment(
            name=req.name, strategy=req.strategy, hypothesis=req.hypothesis,
            stage=DRAFT, spec=req.spec, version=req.version, author=req.author,
            asset_class=req.asset_class, market_type=req.market_type,
            supported_regimes=req.supported_regimes, risk_profile=req.risk_profile,
        )
        async with AsyncSessionLocal() as session:
            session.add(exp)
            await session.commit()
            await session.refresh(exp)
        return exp.as_dict()
    except Exception as exc:
        return {"error": str(exc)}


@router.post("/lab/experiments/{exp_id}/transition")
async def transition_experiment(exp_id: str, req: TransitionRequest):
    """
    Advance an experiment through the funnel. Gates are enforced server-side:
    a move to PAPER needs a passing backtest, to PROMOTED a passing paper record.
    """
    from app.core.database import AsyncSessionLocal
    from app.services.research_lab import transition
    try:
        async with AsyncSessionLocal() as session:
            exp = await _experiment_or_none(session, exp_id)
            if exp is None:
                return {"error": "experiment not found"}

            # Evidence is built here, from a stored run, or not at all. A
            # client cannot supply metrics: numbers that arrive in a request
            # body have no provenance, and the gates cannot tell a measured
            # Sharpe from a typed one.
            if req.metrics or req.wf_metrics:
                return {
                    "ok": False,
                    "reason": (
                        "metrics may not be supplied by the caller. Pass "
                        "backtest_run_id referencing a completed backtest; the "
                        "server reads that run's results and records their "
                        "provenance."
                    ),
                    "experiment": exp.as_dict(),
                }

            metrics = None
            if req.backtest_run_id:
                metrics, problem = await _backtest_evidence(
                    session, req.backtest_run_id, exp.strategy
                )
                if problem:
                    return {"ok": False, "reason": problem, "experiment": exp.as_dict()}

            result = transition(exp.as_dict(), req.target,
                                metrics=metrics, wf_metrics=None,
                                perf=req.perf)
            if not result.ok:
                return {"ok": False, "reason": result.reason, "experiment": exp.as_dict()}
            for k, v in result.patch.items():
                setattr(exp, k, v)
            await session.commit()
            await session.refresh(exp)
        return {"ok": True, "reason": result.reason, "experiment": exp.as_dict()}
    except Exception as exc:
        return {"ok": False, "reason": f"error: {exc}", "experiment": None}


@router.get("/lab/baselines")
async def lab_baselines():
    """
    StrategyBaseline overrides derived from PROMOTED experiments — the bridge that
    feeds the Strategy Health Monitor real, evidence-based baselines.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.research_experiment import ResearchExperiment
    from app.services.research_lab import baselines_from_experiments, PROMOTED
    from sqlalchemy import select
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(ResearchExperiment).where(ResearchExperiment.stage == PROMOTED)
            )).scalars().all()
        baselines = baselines_from_experiments([r.as_dict() for r in rows])
        return {"baselines": {k: {"win_rate": v.win_rate, "expectancy": v.expectancy,
                                  "max_drawdown_pct": v.max_drawdown_pct}
                              for k, v in baselines.items()}}
    except Exception as exc:
        return {"baselines": {}, "error": str(exc)}


@router.get("/registry")
async def get_registry():
    """
    Canonical Strategy Registry view: every strategy's most-advanced lifecycle
    stage, experiment counts, production set, and metadata-completeness flags.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.research_experiment import ResearchExperiment
    from app.services import strategy_registry as reg
    from sqlalchemy import select
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(ResearchExperiment).order_by(ResearchExperiment.created_at.desc())
            )).scalars().all()
        exps = [r.as_dict() for r in rows]
        strategies = reg.by_strategy(exps)
        # Annotate each experiment's metadata completeness for the UI.
        for s in strategies.values():
            members = [e for e in exps if e.get("strategy") == s["strategy"]]
            complete = any(reg.validate_metadata(e)[0] for e in members)
            s["metadata_complete"] = complete
        return {"summary": reg.summarize(exps),
                "strategies": list(strategies.values())}
    except Exception as exc:
        return {"summary": {}, "strategies": [], "error": str(exc)}


@router.get("/features")
async def get_features():
    """Feature Store catalog — the single registry of indicator/feature formulas."""
    from app.services.feature_store import catalog
    return {"features": catalog(), "total": len(catalog())}


# ══════════════════════════════════════════════════════════════════════════════
# AI Research Assistant — read-only Q&A grounded in live system data
# ══════════════════════════════════════════════════════════════════════════════
class AssistantRequest(BaseModel):
    question: str


async def _assistant_context() -> str:
    """Compact, grounded snapshot the LLM may reason over (no raw PII)."""
    import json
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.journal_intelligence import analyze
    from sqlalchemy import select

    parts: list[str] = []
    try:
        async with AsyncSessionLocal() as session:
            trades = (await session.execute(
                select(Trade).where(Trade.status == "closed", Trade.pnl.isnot(None))
            )).scalars().all()
        rows = [{"pnl": float(t.pnl), "strategy": t.strategy, "regime": t.regime
                 if hasattr(t, "regime") else None,
                 "signal_score": float(t.signal_score) if t.signal_score is not None else None,
                 "entry_date": t.entry_date, "exit_date": t.exit_date} for t in trades]
        parts.append("JOURNAL_INTELLIGENCE:\n" + json.dumps(analyze(rows), default=str))
    except Exception as exc:
        parts.append(f"JOURNAL_INTELLIGENCE: unavailable ({exc})")

    try:
        from app.main import _current_regime
        regime = getattr(getattr(_current_regime, "regime", None), "value", None)
        parts.append(f"CURRENT_REGIME: {regime}")
    except Exception:
        pass
    return "\n\n".join(parts)


@router.post("/assistant")
async def research_assistant(req: AssistantRequest):
    """
    Conversational research over the platform's own data. Provider-agnostic
    (Gemini by default; Claude optional). Degrades gracefully when no key is set.
    """
    from app.core.config import settings
    from app.services.llm_provider import generate, LLMUnavailable

    if not req.question.strip():
        return {"error": "empty question"}

    context = await _assistant_context()
    try:
        result = generate(
            req.question, context,
            provider=settings.llm_provider,
            anthropic_key=settings.anthropic_api_key or None,
            gemini_key=settings.gemini_api_key or None,
            model=settings.llm_model or None,
        )
        return result
    except LLMUnavailable as exc:
        return {"error": f"assistant unavailable: {exc}",
                "hint": "Set GEMINI_API_KEY (free tier) or ANTHROPIC_API_KEY in .env.prod"}
    except Exception as exc:
        return {"error": f"assistant error: {exc}"}
