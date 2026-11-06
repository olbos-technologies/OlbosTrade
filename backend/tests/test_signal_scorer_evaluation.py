"""The signal-scorer evaluation must be hard to fool.

`ml/evaluate_signal_scorer.py` decides whether a trained model may be trusted. A test
harness that says PASS to noise is worse than none, so these tests plant a known answer:

* a real signal in the features  -> must PASS,
* pure noise, shuffled labels, an imbalanced "always win" world, a gross edge eaten by
  costs                          -> must NOT_PROVEN,
* trades that overlap the split   -> must be purged from training,
* each check wired in `evaluate`  -> shown able to fail ON ITS OWN, using hand-built
  out-of-fold sets so one broken check cannot hide behind another.

What is real: the module, the XGBoost model it trains (same `train()` the training script
uses), the permutation tests. What is synthetic: the trades. These tests prove the
harness's logic, not that any real model has an edge.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import ml.evaluate_signal_scorer as ev
from ml.features import FEATURE_NAMES
from ml.train_signal_scorer import STRATEGIES

FAST = dict(n_permutations=300)


# ── synthetic data ───────────────────────────────────────────────────────────

def make_frame(n=900, *, signal=0.0, base=0.0, noise=0.5, seed=0, hold_days=3):
    """One trade per business day. ror = base + signal * f(features) + noise."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-02", periods=n)
    X = rng.normal(size=(n, len(FEATURE_NAMES)))
    z = X[:, 0] + 0.5 * X[:, 1]
    df = pd.DataFrame(X, columns=FEATURE_NAMES)
    df["ror"] = base + signal * z + rng.normal(scale=noise, size=n)
    df["entry_date"] = dates
    df["exit_date"] = dates + pd.Timedelta(days=hold_days)
    df["strategy"] = rng.choice(STRATEGIES, n)
    return df


def crafted_oof(n=400, folds=5, *, seed=0, ror_fn=None):
    """A hand-built out-of-fold frame: predictions and outcomes we control exactly."""
    rng = np.random.default_rng(seed)
    pred = rng.normal(scale=0.25, size=n)
    ror = ror_fn(pred, rng) if ror_fn else 0.8 * pred + rng.normal(scale=0.02, size=n)
    return pd.DataFrame({
        "pred": pred, "ror": ror, "fold": np.repeat(np.arange(folds), int(np.ceil(n / folds)))[:n],
        "strategy": "bull_put_spread",
    })


def run_with_oof(oof, **kw):
    """evaluate() with the walk-forward step replaced by a crafted out-of-fold set."""
    df = make_frame(n=len(oof) + 10)
    with patch.object(ev, "walk_forward_predictions", return_value=(oof, [])):
        return ev.evaluate(df, **{**FAST, **kw})


# ── the pre-registered gate ──────────────────────────────────────────────────

def test_the_gate_cutoff_is_the_live_gates_effective_cutoff_not_the_docstring_threshold():
    from app.services.signal_scorer import DEFAULT_ROR_THRESHOLD, UNCERTAINTY_BAND

    assert ev.GATE_CUTOFF == pytest.approx(DEFAULT_ROR_THRESHOLD + UNCERTAINTY_BAND)
    assert ev.GATE_CUTOFF == pytest.approx(0.17)      # not 0.12: the band rejects scores just above it


def test_it_trains_the_same_model_the_training_script_trains():
    import ml.train_signal_scorer as t

    assert ev.train is t.train


# ── statistics ───────────────────────────────────────────────────────────────

def test_a_random_subset_has_no_edge_over_the_random_null():
    rng = np.random.default_rng(1)
    a = rng.normal(size=500)
    p = ev.subset_permutation_p(a, 100, a.mean(), np.random.default_rng(2), 2000)
    assert 0.35 < p < 0.65, p                          # the overall mean is the median of the null


def test_a_genuinely_good_subset_is_significant():
    a = np.random.default_rng(1).normal(size=500)
    top = np.sort(a)[-100:].mean()
    assert ev.subset_permutation_p(a, 100, top, np.random.default_rng(2), 2000) < 0.01


def test_subset_p_handles_an_empty_or_oversized_subset():
    a = np.arange(10.0)
    assert ev.subset_permutation_p(a, 0, 1.0, np.random.default_rng(0), 50) == 1.0
    assert ev.subset_permutation_p(a, 11, 1.0, np.random.default_rng(0), 50) == 1.0


def test_spearman_sees_a_monotone_relation_and_not_its_inverse():
    x = np.arange(200.0)
    ic, p = ev.spearman_with_p(x, x * 2 + 1, np.random.default_rng(0), 500)
    assert ic == pytest.approx(1.0) and p < 0.01
    ic2, p2 = ev.spearman_with_p(x, -x, np.random.default_rng(0), 500)
    assert ic2 == pytest.approx(-1.0) and p2 > 0.9


def test_spearman_of_a_constant_prediction_is_zero_not_a_crash():
    assert ev.spearman_with_p(np.ones(50), np.arange(50.0), np.random.default_rng(0), 50) == (0.0, 1.0)


def test_noise_is_rarely_significant():
    """Across 40 noise datasets, a p <= 0.05 should appear about 5% of the time, not often."""
    hits = 0
    for seed in range(40):
        r = np.random.default_rng(seed)
        _, p = ev.spearman_with_p(r.normal(size=150), r.normal(size=150), np.random.default_rng(seed + 99), 300)
        hits += p <= 0.05
    assert hits <= 8, hits


# ── folds and purging ────────────────────────────────────────────────────────

def test_folds_are_contiguous_after_an_initial_block_and_end_at_n():
    folds = ev.make_folds(600, 5)
    assert len(folds) == 5 and folds[0][0] == 100 and folds[-1][1] == 600
    assert all(folds[i][1] == folds[i + 1][0] for i in range(4))


def test_too_few_rows_make_no_folds():
    assert ev.make_folds(3, 5) == []


def test_training_excludes_trades_still_open_at_the_split():
    """Trades with long holds straddle the split: their outcome is shared with the test block."""
    n = 40
    dates = pd.bdate_range("2020-01-01", periods=n)
    df = pd.DataFrame({"entry_date": dates, "exit_date": dates + pd.Timedelta(days=1)})
    df.loc[30:34, "exit_date"] = dates[30:35] + pd.Timedelta(days=30)     # still open past the split
    idx = ev.purged_train_index(df, test_start=35, embargo_days=0)
    assert set(range(30, 35)).isdisjoint(idx)
    assert set(idx) == set(range(0, 30)) | set()                        # 34's neighbours short-held only if exited
    cutoff = df["entry_date"].iloc[35]
    assert (df.loc[idx, "exit_date"] < cutoff).all()


def test_the_embargo_is_honoured_and_strict():
    dates = pd.bdate_range("2020-01-01", periods=12)
    df = pd.DataFrame({"entry_date": dates, "exit_date": dates})
    test_start = 10
    cutoff = dates[test_start] - pd.Timedelta(days=5)
    df.loc[3, "exit_date"] = cutoff - pd.Timedelta(days=1)    # exited before the embargo: usable
    df.loc[4, "exit_date"] = cutoff                          # exactly at the cutoff: NOT usable (strict)
    df.loc[5, "exit_date"] = cutoff + pd.Timedelta(days=1)   # inside the embargo: not usable
    idx = set(ev.purged_train_index(df, test_start, embargo_days=5))
    assert 3 in idx and 4 not in idx and 5 not in idx


def test_every_fold_trains_strictly_before_it_tests():
    df = make_frame(n=900, signal=0.3, hold_days=10)
    oof, folds = ev.walk_forward_predictions(df)
    ran = [f for f in folds if not f["skipped"]]
    assert len(ran) >= 3
    for f in ran:
        latest_train_exit = pd.Timestamp(f["latest_train_exit"])
        test_start = pd.Timestamp(f["test_start"])
        assert latest_train_exit < test_start - pd.Timedelta(days=ev.EMBARGO_DAYS), f
    # and every prediction is out of sample: test rows never appear in their own fold's training
    assert oof["fold"].between(0, ev.N_FOLDS - 1).all()


def test_a_fold_with_too_little_purged_training_data_is_skipped_and_said_so():
    df = make_frame(n=120, hold_days=400)        # everything stays open: nothing is usable
    oof, folds = ev.walk_forward_predictions(df)
    assert len(oof) == 0 and all(f["skipped"] for f in folds)


# ── end to end, real model, planted answers ──────────────────────────────────

def test_a_planted_signal_passes():
    r = ev.evaluate(make_frame(n=900, signal=0.3, noise=0.3, seed=1), **FAST)
    assert r["verdict"] == ev.PASS, r["reasons"]
    assert all(r["checks"].values())
    assert r["rank"]["spearman_ic"] > 0.2 and r["rank"]["p_value"] <= ev.ALPHA
    assert r["gated"]["mean_ror"] > r["all_trades"]["mean_ror"]
    assert r["gated"]["net_mean_ror_after_cost"]["2x"] > 0


@pytest.mark.parametrize("seed", range(6))
def test_pure_noise_is_not_proven(seed):
    r = ev.evaluate(make_frame(n=900, signal=0.0, base=0.1, noise=0.5, seed=seed), **FAST)
    assert r["verdict"] == ev.NOT_PROVEN, r


def test_noise_rarely_clears_even_the_individual_checks():
    """If the permutation baselines were broken, 'gate beats random' would be true for noise."""
    beats_random = 0
    for seed in range(6):
        r = ev.evaluate(make_frame(n=900, signal=0.0, base=0.1, noise=0.5, seed=seed), **FAST)
        beats_random += r["checks"]["gate_beats_random"]
    assert beats_random <= 1


def test_shuffling_the_labels_destroys_a_real_signal():
    """The canary against leakage: same features, same distribution, outcomes unlinked."""
    df = make_frame(n=900, signal=0.3, noise=0.3, seed=1)
    df["ror"] = np.random.default_rng(7).permutation(df["ror"].to_numpy())
    assert ev.evaluate(df, **FAST)["verdict"] == ev.NOT_PROVEN


def test_an_edge_eaten_by_costs_does_not_pass_even_though_it_ranks():
    r = ev.evaluate(make_frame(n=900, signal=0.3, noise=0.3, seed=1), cost_ror=1.0, **FAST)
    assert r["verdict"] == ev.NOT_PROVEN
    assert r["checks"]["ranks"] and not r["checks"]["pays_at_2x_cost"]


def test_the_always_win_base_rate_is_reported_and_does_not_pass_noise():
    """80% of trades win a little and 20% lose a lot, unrelated to the features. 'Directional
    accuracy' of ~80% is just the base rate; it must not read as skill."""
    rng = np.random.default_rng(3)
    df = make_frame(n=900, signal=0.0, seed=3)
    df["ror"] = np.where(rng.random(900) < 0.8, 0.03, -0.4)
    r = ev.evaluate(df, **FAST)
    assert r["directional"]["base_rate_accuracy"] > 0.75
    assert r["directional"]["lift_over_base_rate"] < 0.1
    assert r["verdict"] == ev.NOT_PROVEN


def test_too_little_data_is_not_proven_not_a_crash():
    r = ev.evaluate(make_frame(n=40), **FAST)
    assert r["verdict"] == ev.NOT_PROVEN and any("out-of-fold" in x for x in r["reasons"])
    empty = ev.evaluate(make_frame(n=0), **FAST)
    assert empty["verdict"] == ev.NOT_PROVEN


def test_a_gate_that_lets_nothing_through_is_not_proven_and_says_why():
    r = ev.evaluate(make_frame(n=900, signal=0.3, noise=0.3, seed=1), gate_cutoff=100.0, **FAST)
    assert r["verdict"] == ev.NOT_PROVEN and any("lets through only 0" in x for x in r["reasons"])


def test_the_result_is_deterministic_for_a_given_seed():
    """On noise the p-values are continuous, so an unseeded generator would show immediately
    (on a strong signal every p-value sits at its floor whatever the seed)."""
    df = make_frame(n=900, signal=0.0, base=0.1, noise=0.5, seed=4)
    a = json.dumps(ev.evaluate(df, seed=7, **FAST), sort_keys=True, default=str)
    b = json.dumps(ev.evaluate(df, seed=7, **FAST), sort_keys=True, default=str)
    assert a == b


def test_the_seed_is_actually_used():
    df = make_frame(n=900, signal=0.0, base=0.1, noise=0.5, seed=4)
    p1 = ev.evaluate(df, seed=1, **FAST)["rank"]["p_value"]
    p2 = ev.evaluate(df, seed=2, **FAST)["rank"]["p_value"]
    assert p1 != p2


# ── each check able to fail alone (hand-built out-of-fold sets) ──────────────

def test_a_clean_crafted_set_passes_so_the_isolation_tests_mean_something():
    r = run_with_oof(crafted_oof())
    assert r["verdict"] == ev.PASS and all(r["checks"].values()), r["reasons"]


def test_costs_alone_can_fail_the_verdict():
    oof = crafted_oof(ror_fn=lambda p, rng: 0.1 * p + rng.normal(scale=0.005, size=len(p)))
    r = run_with_oof(oof)
    assert not r["checks"]["pays_at_2x_cost"]
    assert r["checks"]["ranks"] and r["checks"]["gate_beats_random"]
    assert r["verdict"] == ev.NOT_PROVEN


def test_the_cost_assumption_is_actually_applied():
    oof = crafted_oof()
    cheap = run_with_oof(oof, cost_ror=0.0)["gated"]["net_mean_ror_after_cost"]
    dear = run_with_oof(oof, cost_ror=0.1)["gated"]["net_mean_ror_after_cost"]
    assert cheap["1x"] == pytest.approx(cheap["2x"])
    assert dear["2x"] == pytest.approx(dear["1x"] - 0.1)
    assert dear["1x"] == pytest.approx(cheap["1x"] - 0.1)


def test_the_rank_check_can_fail_alone():
    """Gated trades do well, but across the whole out-of-fold set predictions point the wrong way."""
    def ror(pred, rng):
        out = np.where(pred >= ev.GATE_CUTOFF, 0.3, -3.0 * pred)     # inverse relation below the gate
        return out + rng.normal(scale=0.01, size=len(pred))
    r = run_with_oof(crafted_oof(n=600, ror_fn=ror))
    assert not r["checks"]["ranks"], r["rank"]
    assert r["verdict"] == ev.NOT_PROVEN


def test_the_random_gate_baseline_can_fail_alone():
    """The gate beats 'take all' by a hair, but a random pick of the same size does that often."""
    rng = np.random.default_rng(5)
    n, k = 400, 60
    ror = rng.normal(loc=0.05, scale=0.5, size=n)
    chosen = rng.choice(n, size=k, replace=False)                       # chosen independently of ror
    # nudge the chosen trades' mean to 0.4 standard errors above the overall mean
    ror[chosen] += (ror.mean() + 0.4 * 0.5 / np.sqrt(k)) - ror[chosen].mean()
    pred = rng.uniform(-0.3, 0.1, n)
    pred[chosen] = 0.3
    oof = pd.DataFrame({"pred": pred, "ror": ror, "fold": np.repeat(np.arange(5), 80), "strategy": "x"})
    r = run_with_oof(oof)
    assert r["gated"]["lift_over_all_trades"] > 0                        # looks like an improvement...
    assert not r["checks"]["gate_beats_random"], r["gated"]              # ...that chance explains
    assert r["gated"]["p_vs_random_gate"] > 0.2
    assert r["verdict"] == ev.NOT_PROVEN


def test_some_but_too_few_out_of_fold_trades_is_not_proven():
    """Not the empty case: 100 excellent-looking trades are still below the sample floor."""
    r = run_with_oof(crafted_oof(n=100))
    assert ev.MIN_OOF_TRADES > 100
    assert r["verdict"] == ev.NOT_PROVEN
    assert any("only 100 out-of-fold trades" in x for x in r["reasons"])


def test_some_but_too_few_gated_trades_is_not_proven():
    """Not the empty case: a gate that passes 12 trades, all winners, is still too few to trust."""
    rng = np.random.default_rng(2)
    n = 400
    pred = rng.uniform(-0.3, 0.1, n)
    pred[:12] = 0.4
    ror = 0.5 * pred + rng.normal(scale=0.02, size=n)
    oof = pd.DataFrame({"pred": pred, "ror": ror, "fold": np.repeat(np.arange(5), 80), "strategy": "x"})
    r = run_with_oof(oof)
    assert r["gated"]["n"] == 12 and ev.MIN_GATED_TRADES > 12
    assert r["verdict"] == ev.NOT_PROVEN
    assert any("lets through only 12" in x for x in r["reasons"])


def test_fold_consistency_can_fail_alone():
    """The gate wins big in one fold and loses in the other four."""
    rng = np.random.default_rng(11)
    parts = []
    for fold, size, sign, scale in [(0, 320, +1, 1.0), (1, 30, -1, 0.3), (2, 30, -1, 0.3), (3, 30, -1, 0.3), (4, 30, -1, 0.3)]:
        pred = rng.normal(scale=0.25, size=size)
        pred[: max(8, size // 3)] = rng.uniform(0.2, 0.5, max(8, size // 3))          # enough gated trades per fold
        parts.append(pd.DataFrame({"pred": pred, "ror": sign * scale * pred + rng.normal(scale=0.01, size=size),
                                    "fold": fold, "strategy": "x"}))
    oof = pd.concat(parts, ignore_index=True)
    r = run_with_oof(oof)
    assert r["fold_consistency"]["qualifying_folds"] >= ev.MIN_QUALIFYING_FOLDS
    assert r["fold_consistency"]["folds_helped"] == 1
    assert not r["checks"]["consistent_across_folds"]
    assert r["checks"]["ranks"] and r["checks"]["pays_at_2x_cost"]      # the rest looks fine: only luck-of-one-fold is wrong
    assert r["verdict"] == ev.NOT_PROVEN


def test_fold_consistency_needs_enough_qualifying_folds():
    oof = crafted_oof(n=400, folds=2)                    # only two folds at all
    fc = ev.fold_consistency(oof, ev.GATE_CUTOFF)
    assert fc["qualifying_folds"] == 2 and not fc["passes"]


def test_a_fold_with_too_few_gated_trades_does_not_count():
    oof = crafted_oof(n=400, folds=5)
    oof.loc[oof["fold"] == 0, "pred"] = -1.0             # nothing gated in fold 0
    fc = ev.fold_consistency(oof, ev.GATE_CUTOFF)
    assert 0 not in [d["fold"] for d in fc["detail"]]


# ── data handling ────────────────────────────────────────────────────────────

def _fake_trades(n=300, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        d = pd.Timestamp("2019-01-02") + pd.Timedelta(days=int(i))
        out.append(SimpleNamespace(
            days_to_expiry=30.0, iv_rank=float(rng.uniform(0, 100)), iv_percentile=float(rng.uniform(0, 100)),
            entry_vix=float(rng.uniform(12, 30)), spy_rsi_14=float(rng.uniform(20, 80)), spy_adx_14=float(rng.uniform(10, 40)),
            spy_trend_direction=float(rng.choice([-1, 0, 1])), short_strike_delta=0.2, spread_width=5.0,
            credit_to_width_ratio=float(rng.uniform(0.1, 0.4)), spy_realized_vol_20d=float(rng.uniform(0.1, 0.3)),
            iv_minus_rv=float(rng.normal()), entry_credit=1.0, contracts=1, pnl=float(rng.normal(20, 150)),
            entry_date=d.date(), exit_date=None if i % 7 == 0 else (d + pd.Timedelta(days=5)).date(),
            strategy=str(rng.choice(STRATEGIES)),
        ))
    return out


def test_a_backtest_trade_list_becomes_a_frame_and_flags_the_constant_feature():
    df = ev.trades_to_frame(_fake_trades())
    assert list(df.columns) == ev.TRADE_COLUMNS and len(df) == 300
    r = ev.evaluate(df, **FAST)
    assert "vix_term_slope" in r["constant_features"]      # the known constant: carries no information
    assert "earnings_days_away" in r["constant_features"]


def test_a_missing_exit_date_falls_back_to_the_entry_date_without_dropping_the_trade():
    df = ev.trades_to_frame(_fake_trades())
    missing = [i for i in range(300) if i % 7 == 0]
    assert len(df) == 300 and (df["exit_date"] >= df["entry_date"]).all()
    assert (df.loc[missing, "exit_date"] == df.loc[missing, "entry_date"]).all()


def test_csv_round_trip_and_cli_end_to_end(tmp_path, capsys):
    path, out = tmp_path / "trades.csv", tmp_path / "report.json"
    make_frame(n=900, signal=0.3, noise=0.3, seed=1).to_csv(path, index=False)
    rc = ev.main(["--trades-csv", str(path), "--permutations", "300", "--report-json", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "VERDICT: PASS" in text and "PASS means the model ranks" in text
    assert json.loads(out.read_text())["verdict"] == ev.PASS


def test_the_cli_refuses_a_file_missing_columns(tmp_path, capsys):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"ror": [1.0]}).to_csv(path, index=False)
    assert ev.main(["--trades-csv", str(path)]) == 2
    assert "missing columns" in capsys.readouterr().err


def test_the_cli_refuses_a_missing_file(tmp_path, capsys):
    assert ev.main(["--trades-csv", str(tmp_path / "nope.csv")]) == 2


def test_the_report_states_what_it_cannot_show_and_that_not_proven_is_not_disproof():
    text = ev.format_report(ev.evaluate(make_frame(n=900, signal=0.0, base=0.1, seed=2), **FAST))
    assert "VERDICT: NOT_PROVEN" in text
    assert "simulated backtest trades" in text and "not proof there is none" in text
