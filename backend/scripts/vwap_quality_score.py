"""Candidate D+ — a signal-quality filter on candidate D's entries.

Read-only research. Takes the same 5-minute CSV as vwap_reversion_backtest.py and
asks one question: among the trades D would take, can a simple model, trained
ONLY on earlier trades, pick out the better ones — better than keeping the same
number at random?

    python scripts/vwap_quality_score.py bars.csv [--symbols QQQ SPY]

Design choices that keep this honest:

* No tuning. Features, the L2 penalty, the keep fraction and the minimum
  training size are fixed below BEFORE any real data is seen. One configuration
  means there is no multiple-testing to hide in. D's own parameters default to
  its pre-registered middle values and can be overridden to match whatever
  cell D's grid chose.
* Walk-forward. Each calendar month is scored by a model fitted on trades from
  EARLIER months only. The keep threshold is a percentile of the TRAINING
  scores, never of the month being scored.
* A control. "Keep the same number of trades at random" is run thousands of
  times; the filter's lift is reported against that distribution as a
  permutation p-value, alongside the model's out-of-sample AUC.
* Plain logistic regression, a few features, coefficients printed. No black
  box. (Gradient boosting would need far more labelled trades than a few years
  of one index ETF provide, and would be easier to over-fit.)

The label is "this trade ended with a positive net result after costs". The
features are the ones available at the close of the signal bar.

A result is evidence about the past under these assumptions. It is not a
forecast and it is not approval to trade.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vwap_reversion_backtest as v   # noqa: E402

# ── fixed before any data is looked at ──────────────────────────────────────
FEATURES = ["abs_z", "atr_ratio", "vol_ratio", "trend_aligned", "gap_aligned",
            "ret3_aligned", "tod_sin", "tod_cos"]
L2 = 1.0
KEEP_FRAC = 0.5
MIN_TRAIN = 300
PERMUTATIONS = 5000
SESSION_OPEN_MIN, SESSION_LEN_MIN = 570, 390


# ── features (all known at the close of the signal bar) ─────────────────────

def add_features(feats: pd.DataFrame) -> pd.DataFrame:
    """Add 3-bar return, overnight gap and time-of-day to D's feature frame."""
    f = feats.copy()
    g = f.groupby(["symbol", "date"], sort=False)
    f["ret3"] = f["close"] / g["close"].shift(3) - 1.0
    day_open = g["open"].transform("first")
    last_close = f.groupby(["symbol", "date"], sort=False)["close"].last()
    prev_close = last_close.groupby(level=0).shift(1)
    key = pd.MultiIndex.from_frame(f[["symbol", "date"]])
    f["gap"] = day_open.to_numpy() / prev_close.reindex(key).to_numpy() - 1.0
    angle = 2 * np.pi * (f["end_min"] - SESSION_OPEN_MIN) / SESSION_LEN_MIN
    f["tod_sin"], f["tod_cos"] = np.sin(angle), np.cos(angle)
    return f


def candidate_table(feats: pd.DataFrame, p: v.Params) -> pd.DataFrame:
    """The trades D takes, each with the features of the bar that triggered it.

    Features are oriented by trade direction so one model serves longs and
    shorts: a positive `*_aligned` value means "in the direction of the trade".
    """
    f = add_features(feats)
    t = v.simulate(f, p)
    if t.empty:
        return t.assign(**{k: pd.Series(dtype=float) for k in FEATURES}, y=pd.Series(dtype=int))
    sig_ts = t["entry_ts"] - pd.Timedelta(minutes=v.BAR_MIN)       # the completed signal bar
    idx = f.set_index(["symbol", "ts"])
    rows = idx.loc[list(zip(t["symbol"], sig_ts))].reset_index(drop=True)
    side = np.where(t["side"].to_numpy() == "long", 1.0, -1.0)
    out = t.reset_index(drop=True).copy()
    out["abs_z"] = rows["z"].abs().to_numpy()
    out["atr_ratio"] = rows["atr_ratio"].to_numpy()
    out["vol_ratio"] = rows["vol_ratio"].to_numpy()
    out["trend_aligned"] = side * (rows["h_slope"] / rows["close"]).to_numpy() * 1e4
    out["gap_aligned"] = side * rows["gap"].to_numpy() * 1e2
    out["ret3_aligned"] = side * rows["ret3"].to_numpy() * 1e2
    out["tod_sin"], out["tod_cos"] = rows["tod_sin"].to_numpy(), rows["tod_cos"].to_numpy()
    out["y"] = (out["net_pct"] > 0).astype(int)
    return out.dropna(subset=FEATURES).sort_values("entry_ts").reset_index(drop=True)


# ── logistic regression (numpy, Newton's method, ridge) ─────────────────────

def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = L2, iters: int = 50) -> dict:
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd > 1e-12, sd, 1.0)                  # a constant column cannot produce NaN
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    w = np.zeros(Z.shape[1])
    pen = np.eye(Z.shape[1]) * l2
    pen[0, 0] = 0.0                                     # the intercept is not penalised
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))
        grad = Z.T @ (p - y) + pen @ w
        hess = (Z * (p * (1 - p))[:, None]).T @ Z + pen
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-9:
            break
    return {"mu": mu, "sd": sd, "w": w}


def predict_proba(model: dict, X: np.ndarray) -> np.ndarray:
    Z = np.column_stack([np.ones(len(X)), (X - model["mu"]) / model["sd"]])
    return 1.0 / (1.0 + np.exp(-np.clip(Z @ model["w"], -30, 30)))


def auc(score: np.ndarray, y: np.ndarray) -> float:
    """Mann-Whitney AUC with average ranks for ties. NaN if only one class."""
    y = np.asarray(y)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    ranks = pd.Series(score).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


# ── walk-forward scoring ────────────────────────────────────────────────────

def walk_forward(table: pd.DataFrame, min_train: int = MIN_TRAIN, keep_frac: float = KEEP_FRAC,
                 l2: float = L2) -> tuple[pd.DataFrame, Optional[dict]]:
    """Score each month with a model fitted on strictly earlier months."""
    if table.empty:
        return table.assign(p=pd.Series(dtype=float), keep=pd.Series(dtype=bool)), None
    month = table["entry_ts"].dt.tz_localize(None).dt.to_period("M")
    parts, last = [], None
    for m in sorted(month.unique()):
        train = table[month < m]
        cur = table[month == m]
        if len(train) < min_train or train["y"].nunique() < 2:
            continue
        model = fit_logistic(train[FEATURES].to_numpy(float), train["y"].to_numpy(float), l2)
        thr = np.quantile(predict_proba(model, train[FEATURES].to_numpy(float)), 1 - keep_frac)
        p = predict_proba(model, cur[FEATURES].to_numpy(float))
        parts.append(cur.assign(p=p, keep=p >= thr))
        last = model
    if not parts:
        return table.iloc[0:0].assign(p=pd.Series(dtype=float), keep=pd.Series(dtype=bool)), None
    return pd.concat(parts, ignore_index=True), last


def permutation_p(net: np.ndarray, keep: np.ndarray, n: int = PERMUTATIONS, seed: int = 7) -> float:
    """P(a random subset of the same size has a mean at least as high as the kept set)."""
    k = int(keep.sum())
    if k == 0 or k == len(net):
        return float("nan")
    rng = np.random.default_rng(seed)
    obs = net[keep].mean()
    idx = np.argsort(rng.random((n, len(net))), axis=1)[:, :k]
    return float(((net[idx].mean(axis=1) >= obs).sum() + 1) / (n + 1))


def evaluate(scored: pd.DataFrame, p: v.Params, caps: tuple = v.EXPOSURE_CAPS) -> dict:
    if scored.empty:
        return {"error": "no trades were scored: not enough history to fit a model"}
    net, keep = scored["net_pct"].to_numpy(float), scored["keep"].to_numpy(bool)
    kept, dropped = scored[keep], scored[~keep]
    dates = sorted(scored["date"].unique())
    by_cap = {}
    for cap in caps:
        row = {}
        for name, sub in (("all", scored), ("kept", kept)):
            sized = v.apply_daily_rules(sub, p, max_notional_x=cap)
            row[name] = v.daily_summary(v.daily_pnl(sized, dates))
        by_cap[cap] = row
    return {
        "scored": v.summarize_trades(scored), "kept": v.summarize_trades(kept),
        "dropped": v.summarize_trades(dropped),
        "lift_pct": float(net[keep].mean() - net.mean()),
        "perm_p": permutation_p(net, keep),
        "auc": auc(scored["p"].to_numpy(float), scored["y"].to_numpy()),
        "scored_days": len(dates), "by_cap": by_cap,
    }


def format_report(res: dict, model: Optional[dict], n_candidates: int = 0) -> str:
    if "error" in res:
        return f"NOT ENOUGH DATA: {res['error']} (candidate trades: {n_candidates}, need >{MIN_TRAIN} before the first scored month)"
    a, k, d = res["scored"], res["kept"], res["dropped"]
    f = lambda s: f"n={s['n']} win {s['win_rate']:.1%} mean {s['expectancy_pct']:+.4f}%  PF {s['profit_factor']:.2f}"
    if k["n"] == 0 or res["lift_pct"] <= 0:
        verdict = "No lift: the filter did not improve on taking every trade. Do not proceed with it."
    elif not (res["perm_p"] < 0.05):
        verdict = (f"Kept trades look better but a random subset of the same size does as well "
                   f"{res['perm_p']:.0%} of the time. Not distinguishable from chance.")
    elif k["expectancy_pct"] <= 0:
        verdict = "The filter beats chance but the kept trades still lose after costs. No edge."
    else:
        verdict = ("The filter beats a random subset and the kept trades are positive after costs. One "
                   "walk-forward is not proof: check the AUC and a longer sample before relying on it.")
    coef = ""
    if model is not None:
        coef = "\nfinal-fit standardised coefficients (sign = direction of effect on P(win)):\n  " + "  ".join(
            f"{n}={c:+.3f}" for n, c in zip(FEATURES, model["w"][1:]))
    lines = [
        "Candidate D+ — signal-quality filter on candidate D (walk-forward, monthly refits)",
        f"features {len(FEATURES)}, L2={L2}, keep top {KEEP_FRAC:.0%}, min training trades {MIN_TRAIN}; "
        f"configurations tried: 1",
        f"scored days: {res['scored_days']}   candidate trades before scoring starts are not scored",
        f"all scored trades : {f(a)}",
        f"kept (top {KEEP_FRAC:.0%})    : {f(k)}",
        f"dropped           : {f(d)}",
        f"lift in mean net result: {res['lift_pct']:+.4f}% per trade   permutation p = {res['perm_p']:.3f}   "
        f"out-of-sample AUC = {res['auc']:.3f}  (0.5 = no skill)",
        "daily result, % of equity, by exposure cap (all trades vs kept only):",
    ]
    for cap, row in res["by_cap"].items():
        lines.append(f"  {cap:>4.2f}x: all {row['all']['mean_daily_pct']:+.4f}   kept {row['kept']['mean_daily_pct']:+.4f}"
                     f"   (kept >=0.3% days {row['kept']['days_ge_0.3']:.0%}, maxDD {row['kept']['max_drawdown_pct']:.2f}%)")
    lines.append(verdict + coef)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bars_csv")
    ap.add_argument("--symbols", nargs="*", default=["QQQ", "SPY"])
    ap.add_argument("--z", type=float, default=v.Params().z_entry)
    ap.add_argument("--stop", type=float, default=v.Params().stop_pct)
    ap.add_argument("--target", type=float, default=v.Params().target_pct)
    ap.add_argument("--time-stop", type=int, default=v.Params().time_stop_min)
    ap.add_argument("--trend", choices=["ema_slope", "none"], default=v.Params().trend_filter)
    args = ap.parse_args(argv)
    p = replace(v.Params(), z_entry=args.z, stop_pct=args.stop, target_pct=args.target,
                time_stop_min=args.time_stop, trend_filter=args.trend)
    df = pd.read_csv(args.bars_csv)
    df = df[df["symbol"].isin(args.symbols)]
    table = candidate_table(v.prepare(v.load_bars(df)), p)
    scored, model = walk_forward(table)
    print(format_report(evaluate(scored, p), model, n_candidates=len(table)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
