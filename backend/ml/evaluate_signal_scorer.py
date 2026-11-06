"""
Does the signal scorer add value?  Run this BEFORE trusting (or deploying) a model.

`train_signal_scorer.py` fits an XGBoost regressor and reports one 80/20 split and a
"directional accuracy" with a 55% warning line. That cannot say whether gating trades
with the model beats not gating, for four reasons this script fixes:

  * One split, with trades that overlap the split date  -> purged walk-forward folds.
  * Directional accuracy against a 55% bar              -> compared with the BASE RATE:
    if most trades win, "always guess win" already beats 55%.
  * No cost                                             -> a per-trade cost, at 1x and 2x.
  * No null distribution                                -> permutation tests: how often does
    a RANDOM gate of the same size do as well as the model's gate?

THE PASS RULE IS FIXED HERE, BEFORE ANY RESULT EXISTS.  Change a constant below only by
editing this file in a commit that says why, never after looking at a result.

    PASS needs ALL of:
      1. enough data       >= MIN_OOF_TRADES out-of-fold trades, >= MIN_GATED_TRADES gated
      2. it ranks          Spearman(prediction, outcome) > 0 with permutation p <= ALPHA
      3. the gate helps    gated mean return-on-risk beats a random gate of the same size
                           (permutation p <= ALPHA). That also means it beats taking every
                           trade: the random null is centred on the all-trades mean.
      4. it pays           gated mean return-on-risk is > 0 after 2x the per-trade cost
      5. it is not luck    the gate beats "take all" in >= FOLD_CONSISTENCY of the folds
                           that have enough gated trades (and at least MIN_QUALIFYING_FOLDS)
    Anything else is NOT_PROVEN. That is "no evidence of an edge", not "proof of none".

Sensitivity (measured on SYNTHETIC trades with this exact rule; real data will differ).
Over 12 datasets each: pure noise passed 0 times, an edge explaining ~1% of outcome variance
passed ~0-1 times, ~5% passed ~5 times, and ~17% passed every time. So the rule is deliberately
conservative: NOT_PROVEN is expected for any modest edge, and the usual real-world edge in noisy
trade outcomes is modest. A model that passes is explaining a lot.

What a PASS does and does not mean.  The labels come from the BACKTESTER (simulated trades
on historical daily bars), not from real historical option quotes or the desk's real fills.
A PASS says the model ranks simulated trades out of sample after costs. It does not say the
desk will make money, and it is a reason to run the model paper-only, not live.

Usage (from backend/, venv active):
    python3 -m ml.evaluate_signal_scorer --trades-csv trades.csv
    python3 -m ml.evaluate_signal_scorer --collect --save-trades trades.csv   # needs broker/data
    python3 -m ml.evaluate_signal_scorer --trades-csv trades.csv --report-json report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from app.services.signal_scorer import DEFAULT_ROR_THRESHOLD, UNCERTAINTY_BAND
from ml.features import FEATURE_NAMES, features_from_trade, ror_label
from ml.train_signal_scorer import STRATEGIES, collect_trades, train

# ── The pre-registered rule ──────────────────────────────────────────────────

#: What the LIVE gate actually requires of a predicted return-on-risk. SignalScorer.score()
#: approves at `raw >= threshold` only when it is also outside the uncertainty band, so the
#: effective cutoff is threshold + band, not the 12% in the docstring.
GATE_CUTOFF = DEFAULT_ROR_THRESHOLD + UNCERTAINTY_BAND

#: Extra cost per trade, in return-on-risk units (slippage and fees the backtest does not
#: model). An assumption, not a measurement: ~$12 on a $400-risk spread. Tested at 1x and 2x.
COST_ROR_PER_TRADE = 0.03
COST_MULTIPLIERS = (1.0, 2.0)

N_FOLDS = 5                 # walk-forward test blocks after an initial training block
MIN_TRAIN_ROWS = 60         # same floor train_signal_scorer.py uses
EMBARGO_DAYS = 5            # train trades must have exited this long before a test block starts
MIN_OOF_TRADES = 150        # out-of-fold trades needed for any conclusion
MIN_GATED_TRADES = 30       # trades the gate must let through
MIN_FOLD_GATED = 5          # gated trades a fold needs to count toward consistency
MIN_QUALIFYING_FOLDS = 3
FOLD_CONSISTENCY = 0.6
N_PERMUTATIONS = 5000
ALPHA = 0.05
SEED = 20261106

PASS = "PASS"
NOT_PROVEN = "NOT_PROVEN"

TRADE_COLUMNS = FEATURE_NAMES + ["ror", "entry_date", "exit_date", "strategy"]


# ── Data ─────────────────────────────────────────────────────────────────────

def trades_to_frame(trades: list) -> pd.DataFrame:
    """BacktestTrade objects -> the evaluation frame (features, label, dates, strategy)."""
    rows = []
    for t in trades:
        row = features_from_trade(t)
        row["ror"] = ror_label(t)
        row["entry_date"] = t.entry_date
        row["exit_date"] = t.exit_date or t.entry_date
        row["strategy"] = t.strategy
        rows.append(row)
    return _clean(pd.DataFrame(rows, columns=TRADE_COLUMNS))


def load_trades_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in TRADE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    return _clean(df[TRADE_COLUMNS])


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    # An unknown exit is treated as the entry date: it cannot make the purge MORE
    # permissive than the data allows, and it is reported rather than hidden.
    df["exit_date"] = pd.to_datetime(df["exit_date"]).fillna(df["entry_date"])
    df = df.dropna(subset=["entry_date", "ror"] + FEATURE_NAMES)
    return df.sort_values("entry_date", kind="stable").reset_index(drop=True)


# ── Purged walk-forward ──────────────────────────────────────────────────────

def make_folds(n: int, n_folds: int) -> list[tuple[int, int]]:
    """(start, end) row ranges of the test blocks, after an initial training block."""
    initial = n // (n_folds + 1)
    block = (n - initial) // n_folds
    if block < 1:
        return []
    folds = []
    for i in range(n_folds):
        start = initial + i * block
        end = n if i == n_folds - 1 else start + block
        folds.append((start, end))
    return folds


def purged_train_index(df: pd.DataFrame, test_start: int, embargo_days: int) -> np.ndarray:
    """Rows usable for training a fold whose test block starts at row `test_start`.

    A trade is usable only if it was entered before the test block AND had already exited
    `embargo_days` before the block's first entry. Without the exit condition a trade still
    open at the split date shares its market outcome with the test trades next to it, and
    the model is graded on information it was effectively given.
    """
    cutoff = df["entry_date"].iloc[test_start] - pd.Timedelta(days=embargo_days)
    before = np.arange(len(df)) < test_start
    exited = (df["exit_date"] < cutoff).to_numpy()
    return np.flatnonzero(before & exited)


def walk_forward_predictions(df: pd.DataFrame, *, n_folds: int = N_FOLDS,
                             embargo_days: int = EMBARGO_DAYS,
                             min_train_rows: int = MIN_TRAIN_ROWS) -> tuple[pd.DataFrame, list[dict]]:
    """Out-of-fold predictions. Every prediction comes from a model that never saw that
    trade or any trade overlapping it."""
    parts, info = [], []
    for k, (start, end) in enumerate(make_folds(len(df), n_folds)):
        idx = purged_train_index(df, start, embargo_days)
        meta = {"fold": k, "test_start": str(df["entry_date"].iloc[start].date()),
                "test_end": str(df["entry_date"].iloc[end - 1].date()),
                "n_train": int(len(idx)), "n_test": int(end - start), "skipped": None}
        if len(idx) < min_train_rows:
            meta["skipped"] = f"only {len(idx)} purged training rows (< {min_train_rows})"
            info.append(meta)
            continue
        train_df = df.iloc[idx]
        meta["latest_train_exit"] = str(train_df["exit_date"].max().date())
        model = train(train_df, FEATURE_NAMES)
        test = df.iloc[start:end].copy()
        test["pred"] = np.asarray(model.predict(test[FEATURE_NAMES]), dtype=float)
        test["fold"] = k
        parts.append(test)
        info.append(meta)
    oof = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(df.columns) + ["pred", "fold"])
    return oof, info


# ── Statistics ───────────────────────────────────────────────────────────────

def subset_permutation_p(actual: np.ndarray, k: int, observed_mean: float,
                         rng: np.random.Generator, n_perm: int) -> float:
    """P(a random subset of k trades has mean >= observed). The gate's null: 'no better than
    choosing k trades at random from the same out-of-fold set'."""
    n = len(actual)
    if k <= 0 or k > n:
        return 1.0
    hits = 0
    for _ in range(n_perm):
        if actual[rng.choice(n, size=k, replace=False)].mean() >= observed_mean:
            hits += 1
    return (1 + hits) / (1 + n_perm)


def spearman_with_p(pred: np.ndarray, actual: np.ndarray, rng: np.random.Generator,
                    n_perm: int) -> tuple[float, float]:
    """Rank correlation of prediction and outcome, and its one-sided permutation p-value."""
    if len(pred) < 3 or np.ptp(pred) == 0 or np.ptp(actual) == 0:
        return 0.0, 1.0
    rp = rankdata(pred) - (len(pred) + 1) / 2
    ra = rankdata(actual) - (len(actual) + 1) / 2
    denom = math.sqrt(float((rp ** 2).sum() * (ra ** 2).sum()))
    ic = float((rp * ra).sum() / denom)
    hits = 0
    for _ in range(n_perm):
        if float((rp * rng.permutation(ra)).sum() / denom) >= ic:
            hits += 1
    return ic, (1 + hits) / (1 + n_perm)


def fold_consistency(oof: pd.DataFrame, gate_cutoff: float) -> dict[str, Any]:
    """Did the gate beat 'take every trade' in enough of the folds, or is one lucky period carrying it?"""
    detail = []
    for k in sorted(oof["fold"].unique()):
        f = oof[oof["fold"] == k]
        fg = f[f["pred"] >= gate_cutoff]
        if len(fg) >= MIN_FOLD_GATED:
            detail.append({"fold": int(k), "n_gated": int(len(fg)),
                           "gated_mean": float(fg["ror"].mean()), "all_mean": float(f["ror"].mean()),
                           "gate_helped": bool(fg["ror"].mean() > f["ror"].mean())})
    helped = sum(d["gate_helped"] for d in detail)
    passes = bool(len(detail) >= MIN_QUALIFYING_FOLDS and helped / len(detail) >= FOLD_CONSISTENCY)
    return {"qualifying_folds": len(detail), "folds_helped": helped, "detail": detail, "passes": passes}


# ── Evaluation ───────────────────────────────────────────────────────────────

def evaluate(df: pd.DataFrame, *, n_folds: int = N_FOLDS, embargo_days: int = EMBARGO_DAYS,
             cost_ror: float = COST_ROR_PER_TRADE, gate_cutoff: float = GATE_CUTOFF,
             n_permutations: int = N_PERMUTATIONS, seed: int = SEED) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    report: dict[str, Any] = {
        "rule": {
            "gate_cutoff": gate_cutoff, "cost_ror_per_trade": cost_ror, "cost_multipliers": list(COST_MULTIPLIERS),
            "n_folds": n_folds, "embargo_days": embargo_days, "min_oof_trades": MIN_OOF_TRADES,
            "min_gated_trades": MIN_GATED_TRADES, "fold_consistency": FOLD_CONSISTENCY,
            "min_qualifying_folds": MIN_QUALIFYING_FOLDS, "alpha": ALPHA,
            "n_permutations": n_permutations, "seed": seed,
        },
        "n_trades": int(len(df)),
        "constant_features": [c for c in FEATURE_NAMES if len(df) and df[c].nunique() <= 1],
    }

    oof, folds = walk_forward_predictions(df, n_folds=n_folds, embargo_days=embargo_days)
    report["folds"] = folds
    report["n_oof"] = int(len(oof))

    reasons: list[str] = []
    checks = {k: False for k in ("enough_data", "ranks", "gate_beats_random",
                                  "pays_at_2x_cost", "consistent_across_folds")}

    if len(oof) < MIN_OOF_TRADES:
        reasons.append(f"only {len(oof)} out-of-fold trades (need {MIN_OOF_TRADES})")
        return _finish(report, checks, reasons)

    actual = oof["ror"].to_numpy(dtype=float)
    pred = oof["pred"].to_numpy(dtype=float)
    gated = pred >= gate_cutoff
    n_gated = int(gated.sum())

    all_mean = float(actual.mean())
    report["all_trades"] = {"n": int(len(actual)), "mean_ror": all_mean, "win_rate": float((actual > 0).mean())}
    report["acceptance_rate"] = n_gated / len(actual)

    # Directional accuracy against the base rate (informational: never part of the pass rule).
    share_pos = float((actual > 0).mean())
    base = max(share_pos, 1 - share_pos)
    acc = float(((pred > 0) == (actual > 0)).mean())
    report["directional"] = {"accuracy": acc, "base_rate_accuracy": base, "lift_over_base_rate": acc - base}

    ic, ic_p = spearman_with_p(pred, actual, rng, n_permutations)
    report["rank"] = {"spearman_ic": ic, "p_value": ic_p}
    checks["ranks"] = bool(ic > 0 and ic_p <= ALPHA)

    if n_gated < MIN_GATED_TRADES:
        reasons.append(f"the gate lets through only {n_gated} trades (need {MIN_GATED_TRADES})")
        report["gated"] = {"n": n_gated}
    else:
        checks["enough_data"] = True
        g = actual[gated]
        gated_mean = float(g.mean())
        p_random = subset_permutation_p(actual, n_gated, gated_mean, rng, n_permutations)
        report["gated"] = {"n": n_gated, "mean_ror": gated_mean, "win_rate": float((g > 0).mean()),
                           "lift_over_all_trades": gated_mean - all_mean, "p_vs_random_gate": p_random}
        checks["gate_beats_random"] = bool(p_random <= ALPHA)
        net = {f"{m:g}x": float(gated_mean - m * cost_ror) for m in COST_MULTIPLIERS}
        report["gated"]["net_mean_ror_after_cost"] = net
        checks["pays_at_2x_cost"] = bool(net[f"{max(COST_MULTIPLIERS):g}x"] > 0)

        fc = fold_consistency(oof, gate_cutoff)
        report["fold_consistency"] = fc
        checks["consistent_across_folds"] = fc["passes"]

        report["by_strategy"] = {
            s: {"n": int(len(d)), "n_gated": int((d["pred"] >= gate_cutoff).sum()),
                "all_mean": float(d["ror"].mean()),
                "gated_mean": (float(d.loc[d["pred"] >= gate_cutoff, "ror"].mean())
                               if (d["pred"] >= gate_cutoff).any() else None)}
            for s, d in oof.groupby("strategy")
        }

    for name, ok in checks.items():
        if not ok and name != "enough_data":
            reasons.append(f"check failed: {name}")
    return _finish(report, checks, reasons)


def _finish(report: dict, checks: dict, reasons: list[str]) -> dict:
    passed = all(checks.values())
    report["checks"] = checks
    report["verdict"] = PASS if passed else NOT_PROVEN
    report["reasons"] = [] if passed else reasons
    return report


# ── Report ───────────────────────────────────────────────────────────────────

def format_report(r: dict) -> str:
    L = ["", "═" * 66, "  Signal scorer evaluation (purged walk-forward, out of sample)", "═" * 66]
    rule = r["rule"]
    L.append(f"  Pre-registered: gate cutoff {rule['gate_cutoff']:.2f} predicted RoR, cost {rule['cost_ror_per_trade']:.2f}/trade "
             f"(tested at {', '.join(f'{m:g}x' for m in rule['cost_multipliers'])}), alpha {rule['alpha']}")
    L.append(f"  Trades: {r['n_trades']}   out-of-fold: {r['n_oof']}   folds: {sum(1 for f in r['folds'] if not f['skipped'])}/{len(r['folds'])} ran")
    for f in r["folds"]:
        if f["skipped"]:
            L.append(f"    fold {f['fold']}: skipped — {f['skipped']}")
    if r["constant_features"]:
        L.append(f"  ⚠ Constant features (carry no information): {', '.join(r['constant_features'])}")
    if "all_trades" in r:
        a, d = r["all_trades"], r["directional"]
        L.append(f"  Take every trade:   mean RoR {a['mean_ror']:+.4f}   win rate {a['win_rate']*100:.1f}%")
        L.append(f"  Direction:          accuracy {d['accuracy']*100:.1f}% vs {d['base_rate_accuracy']*100:.1f}% for always guessing the majority "
                 f"(lift {d['lift_over_base_rate']*100:+.1f} pts — informational only)")
        L.append(f"  Rank:               Spearman IC {r['rank']['spearman_ic']:+.3f}   permutation p = {r['rank']['p_value']:.4f}")
    if "gated" in r and "mean_ror" in r["gated"]:
        g = r["gated"]
        L.append(f"  Model gate:         {g['n']} trades ({r['acceptance_rate']*100:.1f}% accepted)   mean RoR {g['mean_ror']:+.4f}   "
                 f"win rate {g['win_rate']*100:.1f}%")
        L.append(f"                      lift over taking all {g['lift_over_all_trades']:+.4f}   p vs a random gate of the same size = {g['p_vs_random_gate']:.4f}")
        L.append("                      after cost: " + "   ".join(f"{k} → {v:+.4f}" for k, v in g["net_mean_ror_after_cost"].items()))
        fc = r["fold_consistency"]
        L.append(f"  Consistency:        gate beat 'take all' in {fc['folds_helped']} of {fc['qualifying_folds']} qualifying folds")
    L.append("  Checks:             " + "   ".join(f"{'✓' if v else '✗'} {k}" for k, v in r["checks"].items()))
    L.append("")
    L.append(f"  VERDICT: {r['verdict']}")
    for reason in r["reasons"]:
        L.append(f"    - {reason}")
    L.append("")
    L.append("  Reminder: labels are simulated backtest trades on daily bars. PASS means the model ranks")
    L.append("  simulated trades out of sample after costs — a reason to run it paper-only, not proof of profit.")
    L.append("  NOT_PROVEN means no evidence of an edge was found, which is not proof there is none.")
    L.append("═" * 66)
    return "\n".join(L)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--trades-csv", help="Trades saved earlier (columns: features, ror, entry_date, exit_date, strategy)")
    src.add_argument("--collect", action="store_true", help="Run the backtester to generate trades (needs broker/data access)")
    p.add_argument("--save-trades", help="With --collect: write the trades here for repeatable re-evaluation")
    p.add_argument("--strategies", nargs="+", default=STRATEGIES, choices=STRATEGIES)
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--capital", type=float, default=25000.0)
    p.add_argument("--cost-ror", type=float, default=COST_ROR_PER_TRADE)
    p.add_argument("--permutations", type=int, default=N_PERMUTATIONS)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--report-json", help="Also write the full numbers here")
    args = p.parse_args(argv)

    if args.collect:
        trades = asyncio.run(collect_trades(args.strategies, args.start, args.end, args.capital))
        df = trades_to_frame(trades)
        if args.save_trades:
            df.to_csv(args.save_trades, index=False)
            print(f"[saved] {len(df)} trades -> {args.save_trades}")
    else:
        try:
            df = load_trades_csv(args.trades_csv)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    report = evaluate(df, cost_ror=args.cost_ror, n_permutations=args.permutations, seed=args.seed)
    print(format_report(report))
    if args.report_json:
        Path(args.report_json).write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
