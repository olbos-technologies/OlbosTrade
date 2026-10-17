"""
Portfolio performance analytics.

Computes the institutional metric set from closed trades: win rate, profit factor,
expectancy, payoff ratio, average winner/loser, plus equity-curve metrics
(CAGR, max drawdown, Sharpe, Sortino, Calmar, volatility) and average hold time.

Pure functions over a list of trade dicts so it's trivially testable and reusable
by the dashboard, the analytics API, and reports.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(v).date()
    except Exception:
        return None


def _curve_metrics(daily_values: list[float]) -> dict:
    """Sharpe / Sortino / Calmar / CAGR / max-DD / vol from a daily equity curve."""
    if len(daily_values) < 2:
        return {"cagr_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0,
                "sortino": 0.0, "calmar": 0.0, "volatility_pct": 0.0}
    arr = np.asarray(daily_values, dtype=float)
    daily = np.diff(arr) / arr[:-1]
    years = max(len(arr) / 252.0, 1e-9)
    cagr = (arr[-1] / arr[0]) ** (1 / years) - 1.0 if arr[0] > 0 else 0.0
    vol = float(np.std(daily) * np.sqrt(252)) if len(daily) else 0.0
    sharpe = float(np.mean(daily) / np.std(daily) * np.sqrt(252)) if np.std(daily) > 1e-12 else 0.0
    downside = daily[daily < 0]
    dstd = float(np.std(downside)) if len(downside) else 0.0
    sortino = float(np.mean(daily) / dstd * np.sqrt(252)) if dstd > 1e-12 else 0.0
    peak = np.maximum.accumulate(arr)
    max_dd = float(np.min(arr / peak - 1.0))
    calmar = float(cagr / abs(max_dd)) if max_dd < -1e-9 else 0.0
    return {
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(abs(max_dd) * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "volatility_pct": round(vol * 100, 2),
    }


def _trade_drawdowns(pnls: list[float], starting_capital: float,
                     windows: tuple[int, ...] = (30, 90)) -> dict:
    """
    Rolling drawdowns on the *trade-ordered* equity curve (NOT calendar days).

    Calendar drawdowns understate risk for a strategy that trades sporadically —
    a flat week between two losers reads as "no drawdown" on a daily curve. We
    instead walk equity trade-by-trade and report:
      • current_drawdown_pct      — peak-to-latest, the live underwater amount
      • max_drawdown_trades_pct   — worst peak-to-trough across all trades
      • rolling_max_dd_{w}_pct    — worst drawdown within the trailing w trades
    """
    out = {"current_drawdown_pct": 0.0, "max_drawdown_trades_pct": 0.0}
    for w in windows:
        out[f"rolling_max_dd_{w}_pct"] = 0.0
    if not pnls:
        return out

    equity = np.concatenate(([float(starting_capital)],
                             float(starting_capital) + np.cumsum(pnls)))
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    out["current_drawdown_pct"] = round(abs(float(dd[-1])) * 100, 2)
    out["max_drawdown_trades_pct"] = round(abs(float(np.min(dd))) * 100, 2)

    for w in windows:
        # Trailing window of w trades (w+1 equity points); peak resets at the
        # window start so the figure is a genuine rolling-window drawdown.
        recent = equity[-(w + 1):] if len(equity) > w + 1 else equity
        rpeak = np.maximum.accumulate(recent)
        rdd = recent / rpeak - 1.0
        out[f"rolling_max_dd_{w}_pct"] = round(abs(float(np.min(rdd))) * 100, 2)
    return out


def _attribution_block(adopted_pnls: list, attributable_pnls: list) -> dict:
    """How the totals above split between chosen and inherited positions.

    Both halves are already counted in `total_pnl` and in every other figure
    here: an adopted position's P&L is correct arithmetic on the broker's real
    cost basis, so it is real money (see trade_attribution). This breaks the
    total down so a caller can say "the desk's decisions made X while
    inherited positions made Y" — never so it can report X alone as the
    result. Counts cover measured rows only, matching the sums beside them.
    """
    return {
        "attributable_trades": len(attributable_pnls),
        "attributable_pnl": round(float(sum(attributable_pnls)), 2),
        "adopted_positions": len(adopted_pnls),
        "adopted_pnl": round(float(sum(adopted_pnls)), 2),
        "adopted_present": bool(adopted_pnls),
    }


def _empty() -> dict:
    return {
        "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
        "profit_factor": 0.0, "payoff_ratio": 0.0, "avg_hold_days": 0.0,
        "cagr_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0,
        "sortino": 0.0, "calmar": 0.0, "volatility_pct": 0.0,
        "current_drawdown_pct": 0.0, "max_drawdown_trades_pct": 0.0,
        "rolling_max_dd_30_pct": 0.0, "rolling_max_dd_90_pct": 0.0,
        "sample_size_warning": True,
        "unmeasured_trades": 0,
        "measured_pct": 100.0,
        "performance_incomplete": False,
        "attributable_trades": 0,
        "attributable_pnl": 0.0,
        "adopted_positions": 0,
        "adopted_pnl": 0.0,
        "adopted_present": False,
    }


def compute_performance(trades: list[dict], starting_capital: float,
                        min_sample: int = 30) -> dict:
    """
    trades: list of {"pnl": float, "entry_date": date|datetime, "exit_date": ...}.

    Only trades with a non-None pnl can be counted — but a trade whose exit
    price was never captured still happened, and dropping it silently makes
    every figure below read as if it covered the whole book. Production has 8
    such rows (exit_reason "closed_price_unavailable", July 2026): the
    position left the broker before any execution price arrived, and none can
    be recovered — IBKR's execution history does not reach back that far, and
    substituting a historical daily close would invent a fill the desk never
    got. So the count travels with the numbers instead, the same way
    heat_overstated travels with portfolio heat.
    """
    # Reconciler-adopted positions are INCLUDED in every figure below. Their
    # P&L is computed from the broker's real average cost basis, so it is real
    # money and belongs in an account-level total; only attribution and
    # anything derived from entry_date are unsafe on those rows (see
    # trade_attribution). They are broken out alongside, because no strategy
    # chose them and the desk needs to see when its decisions and its account
    # disagree — on production, +3,163 against -1,099.
    #
    # A caller that does not pass `strategy` is unaffected: nothing matches,
    # the partition puts everything in `attributable`, and the breakdown
    # degenerates to "all of it was chosen", which is the right answer absent
    # evidence otherwise.
    from app.services.trade_attribution import partition

    attributable, adopted = partition(trades)
    adopted_pnls = [float(t["pnl"]) for t in adopted if t.get("pnl") is not None]
    attributable_pnls = [
        float(t["pnl"]) for t in attributable if t.get("pnl") is not None
    ]

    total_submitted = len(trades)
    rows = [t for t in trades if t.get("pnl") is not None]
    unmeasured = total_submitted - len(rows)
    if not rows:
        out = _empty()
        # An all-unmeasured book is not an empty one, and must not read as
        # a desk that has never traded.
        out["unmeasured_trades"] = unmeasured
        out["measured_pct"] = 0.0 if unmeasured else 100.0
        out["performance_incomplete"] = unmeasured > 0
        out.update(_attribution_block(adopted_pnls, attributable_pnls))
        return out

    pnls = [float(t["pnl"]) for t in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    total_pnl = float(sum(pnls))
    gross_profit = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0

    # Average hold time (days) where both dates exist.
    holds = []
    for t in rows:
        e, x = _to_date(t.get("entry_date")), _to_date(t.get("exit_date"))
        if e and x:
            holds.append((x - e).days)
    avg_hold = float(np.mean(holds)) if holds else 0.0

    # Daily equity curve from exit dates → curve metrics.
    dated = [(d, float(t["pnl"])) for t in rows
             if (d := _to_date(t.get("exit_date"))) is not None]
    curve_metrics = {"cagr_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0,
                     "sortino": 0.0, "calmar": 0.0, "volatility_pct": 0.0}
    if dated:
        dated.sort(key=lambda x: x[0])
        s = pd.Series({d: p for d, p in _group_by_day(dated)})
        idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
        daily_pnl = s.reindex(idx, fill_value=0.0)
        equity = float(starting_capital) + daily_pnl.cumsum()
        curve_metrics = _curve_metrics([float(starting_capital)] + list(equity.values))

    # Rolling (trade-ordered) drawdowns — chronological by exit then entry date.
    chron = sorted(
        rows,
        key=lambda t: (_to_date(t.get("exit_date")) or _to_date(t.get("entry_date")) or date.min),
    )
    trade_dd = _trade_drawdowns([float(t["pnl"]) for t in chron], starting_capital)

    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(total_pnl / n, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0,
        "payoff_ratio": round(avg_win / abs(avg_loss), 2) if avg_loss < 0 else 0.0,
        "avg_hold_days": round(avg_hold, 1),
        **curve_metrics,
        **trade_dd,
        "sample_size_warning": n < min_sample,
        # total_trades above counts what could be measured, not what was
        # traded. These three say how far apart those are.
        "unmeasured_trades": unmeasured,
        "measured_pct": round(len(rows) / total_submitted * 100, 1),
        "performance_incomplete": unmeasured > 0,
        **_attribution_block(adopted_pnls, attributable_pnls),
    }


def _group_by_day(dated: list[tuple]) -> list[tuple]:
    """Sum P&L per calendar day (pd.Timestamp keys)."""
    agg: dict = {}
    for d, p in dated:
        ts = pd.Timestamp(d)
        agg[ts] = agg.get(ts, 0.0) + p
    return sorted(agg.items())
