"""The quality filter must never train on what it scores, and must be able to find skill.

An ML filter on a trading rule is the easiest place in a backtest to leak the
future: fit on all the trades, score all the trades, report a beautiful lift.
So the tests here are mostly about what the model is NOT allowed to see, plus a
power check (planted skill is found; no skill is not called a discovery),
because a filter that reports "no lift" is only informative if it could have
reported one.
"""
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))
import test_vwap_reversion_backtest as base   # noqa: E402
import vwap_quality_score as q                # noqa: E402

v = q.v
P = v.Params()


# ── features ────────────────────────────────────────────────────────────────

def test_features_use_only_data_up_to_the_signal_bar():
    df = base._bars(8, seed=1, symbols=("QQQ",))
    a = q.add_features(base._feats(df))
    cut = len(df) // 2
    mut = df.copy()
    mut.loc[cut + 1:, ["open", "high", "low", "close"]] *= 1.3
    b = q.add_features(base._feats(mut))
    cols = ["ret3", "gap", "tod_sin", "tod_cos"]
    pd.testing.assert_frame_equal(a.iloc[: cut + 1][cols].reset_index(drop=True),
                                  b.iloc[: cut + 1][cols].reset_index(drop=True))


def test_gap_is_the_open_over_the_previous_sessions_last_close():
    rows = []
    for di, (px_open, px_close) in enumerate([(100.0, 101.0), (103.0, 102.0)]):
        d = pd.bdate_range("2025-03-03", periods=2)[di]
        for k in range(78):
            ts = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30 + 5 * k)
            px = px_open if k == 0 else px_close
            rows.append(("QQQ", ts.isoformat(), px, px + 0.1, px - 0.1, px, 100_000))
    f = q.add_features(base._feats(pd.DataFrame(
        rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])))
    day2 = f[f["date"] == f["date"].max()]
    assert np.allclose(day2["gap"], 103.0 / 101.0 - 1.0)
    assert f[f["date"] == f["date"].min()]["gap"].isna().all()       # no previous session


def test_time_of_day_encoding_is_a_circle_over_the_session():
    f = q.add_features(base._feats(base._bars(3, seed=2, symbols=("QQQ",))))
    assert np.allclose(f["tod_sin"] ** 2 + f["tod_cos"] ** 2, 1.0)
    assert f["tod_sin"].nunique() > 20


def test_candidate_rows_describe_the_signal_bar_and_are_oriented_by_direction():
    f = base._feats(base._bars(40, seed=6, symbols=("QQQ", "SPY")))
    p = v.Params(z_entry=1.5)
    tab = q.candidate_table(f, p)
    assert len(tab) > 100
    # Every trade was triggered by |z| >= z_entry on the SIGNAL bar. Reading the entry bar
    # instead would break this for a good share of rows.
    assert (tab["abs_z"] >= p.z_entry - 1e-12).all()
    ff = q.add_features(f).set_index(["symbol", "ts"])
    for r in tab.head(25).itertuples():
        raw = ff.loc[(r.symbol, r.entry_ts - pd.Timedelta(minutes=5))]
        side = 1.0 if r.side == "long" else -1.0
        assert r.ret3_aligned == pytest.approx(side * raw["ret3"] * 100)
        assert r.trend_aligned == pytest.approx(side * raw["h_slope"] / raw["close"] * 1e4)
        assert r.gap_aligned == pytest.approx(side * raw["gap"] * 100)
        assert r.y == int(r.net_pct > 0)


# ── the model ───────────────────────────────────────────────────────────────

def test_logistic_recovers_planted_coefficients_and_behaves_on_degenerate_input():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(20000, 3))
    true = np.array([1.2, -0.8, 0.0])
    y = (rng.random(20000) < 1 / (1 + np.exp(-(0.3 + X @ true)))).astype(float)
    m = q.fit_logistic(X, y, l2=1e-6)
    assert np.allclose(m["w"][1:], true, atol=0.1) and abs(m["w"][0] - 0.3) < 0.1
    # a constant column and perfectly separable labels must not produce NaN/inf
    Xc = np.column_stack([X[:300, 0], np.ones(300)])
    sep = (Xc[:, 0] > 0).astype(float)
    mc = q.fit_logistic(Xc, sep)
    assert np.isfinite(mc["w"]).all()
    pr = q.predict_proba(mc, Xc)
    assert ((pr > 0) & (pr < 1)).all()


def test_auc_known_values():
    assert q.auc(np.array([.1, .2, .8, .9]), np.array([0, 0, 1, 1])) == 1.0
    assert q.auc(np.array([.9, .8, .2, .1]), np.array([0, 0, 1, 1])) == 0.0
    assert q.auc(np.array([.5, .5, .5, .5]), np.array([0, 1, 0, 1])) == 0.5   # ties
    assert np.isnan(q.auc(np.array([.1, .2]), np.array([1, 1])))


def test_permutation_p_value_behaves():
    rng = np.random.default_rng(1)
    net = rng.normal(0, 1, 400)
    assert np.isnan(q.permutation_p(net, np.ones(400, bool)))
    best = net >= np.quantile(net, 0.5)
    assert q.permutation_p(net, best) < 0.01                    # picking the top half is no accident
    rand = rng.random(400) < 0.5
    assert q.permutation_p(net, rand, seed=3) > 0.05


# ── walk-forward: no leakage ────────────────────────────────────────────────

def _synthetic_table(n=3000, skill=0.0, seed=0, months=24):
    """Candidate trades spread over `months`; P(win) depends on abs_z when skill > 0."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2023-01-03 10:30", tz=v.TZ)
    ts = start + pd.to_timedelta(np.sort(rng.integers(0, months * 28 * 24 * 60, n)), unit="m")
    X = {k: rng.normal(size=n) for k in q.FEATURES}
    logit = skill * X["abs_z"]
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    net = np.where(y == 1, 0.35, -0.30) + rng.normal(0, 0.05, n)
    t = pd.DataFrame({"symbol": "QQQ", "entry_ts": ts, "exit_ts": ts + pd.Timedelta(minutes=30),
                      "net_pct": net, "r": net / P.stop_pct, "y": y, **X})
    t["date"] = t["entry_ts"].dt.date
    t["side"] = "long"
    return t


def test_nothing_is_scored_before_enough_history_exists():
    t = _synthetic_table(skill=1.0)
    sc, _ = q.walk_forward(t, min_train=300)
    first_scored = sc["entry_ts"].min()
    assert (t["entry_ts"] < first_scored).sum() >= 300


def test_a_months_scores_do_not_depend_on_that_month_or_any_later_one():
    t = _synthetic_table(skill=1.0, seed=3)
    base_sc, _ = q.walk_forward(t)
    month = t["entry_ts"].dt.tz_localize(None).dt.to_period("M")
    target = sorted(month.unique())[14]
    t2 = t.copy()
    later = month >= target
    t2.loc[later, "y"] = 1 - t2.loc[later, "y"]                 # flip every label from that month on
    for k in ("abs_z", "vol_ratio"):
        t2.loc[later, k] = t2.loc[later, k] * 5 + 1
    new_sc, _ = q.walk_forward(t2)
    # Scores for months BEFORE the target are identical; the target month's own model is trained
    # only on earlier months, so its PROBABILITIES on the (modified) features differ only through
    # the features, not through its own labels: check a pure label flip leaves them identical.
    before = base_sc[base_sc["entry_ts"].dt.tz_localize(None).dt.to_period("M") < target]
    before2 = new_sc[new_sc["entry_ts"].dt.tz_localize(None).dt.to_period("M") < target]
    pd.testing.assert_series_equal(before["p"].reset_index(drop=True), before2["p"].reset_index(drop=True))
    t3 = t.copy()
    t3.loc[later, "y"] = 1 - t3.loc[later, "y"]                 # labels only
    lab_sc, _ = q.walk_forward(t3)
    cur = base_sc[base_sc["entry_ts"].dt.tz_localize(None).dt.to_period("M") == target]
    cur3 = lab_sc[lab_sc["entry_ts"].dt.tz_localize(None).dt.to_period("M") == target]
    pd.testing.assert_series_equal(cur["p"].reset_index(drop=True), cur3["p"].reset_index(drop=True))


def test_keep_fraction_comes_from_the_training_scores():
    t = _synthetic_table(skill=0.5, seed=4)
    sc, _ = q.walk_forward(t, keep_frac=0.5)
    assert 0.3 < sc["keep"].mean() < 0.7
    sc2, _ = q.walk_forward(t, keep_frac=0.2)
    assert sc2["keep"].mean() < sc["keep"].mean()


# ── power: planted skill is found, no skill is not a discovery ──────────────

def test_planted_skill_is_found():
    t = _synthetic_table(n=4000, skill=1.5, seed=5)
    sc, model = q.walk_forward(t)
    res = q.evaluate(sc, P)
    assert res["auc"] > 0.6 and res["lift_pct"] > 0.05 and res["perm_p"] < 0.01
    assert "beats a random subset" in q.format_report(res, model)
    assert model["w"][1 + q.FEATURES.index("abs_z")] > 0.3           # and it found the right feature


@pytest.mark.parametrize("seed", [11, 12, 13, 14])
def test_no_skill_is_not_called_a_discovery(seed):
    t = _synthetic_table(n=4000, skill=0.0, seed=seed)
    sc, model = q.walk_forward(t)
    res = q.evaluate(sc, P)
    text = q.format_report(res, model)
    assert "beats a random subset" not in text
    assert 0.4 < res["auc"] < 0.6


def _res(lift, p, kept_mean):
    s = lambda mean: {"n": 100, "win_rate": 0.5, "expectancy_pct": mean, "profit_factor": 1.0}
    row = {"mean_daily_pct": 0.0, "days_ge_0.3": 0.0, "max_drawdown_pct": -1.0}
    return {"scored": s(0.0), "kept": s(kept_mean), "dropped": s(-0.01), "lift_pct": lift,
            "perm_p": p, "auc": 0.55, "scored_days": 200,
            "by_cap": {c: {"all": row, "kept": row} for c in v.EXPOSURE_CAPS}}


def test_verdict_branches():
    r = lambda *a: q.format_report(_res(*a), None)
    assert "No lift" in r(-0.01, 0.8, -0.02)
    assert "Not distinguishable from chance" in r(0.02, 0.30, 0.01)
    assert "still lose after costs" in r(0.02, 0.01, -0.005)
    assert "beats a random subset and the kept trades are positive" in r(0.02, 0.01, 0.01)
    assert "NOT ENOUGH DATA" in q.format_report({"error": "x"}, None, 10)


# ── whole pipeline ──────────────────────────────────────────────────────────

def test_small_inputs_and_the_cli(tmp_path, capsys):
    f = base._feats(base._bars(3, symbols=("QQQ",)))
    tab = q.candidate_table(f.iloc[0:0], P)
    assert tab.empty
    sc, model = q.walk_forward(tab)
    assert sc.empty and model is None and "error" in q.evaluate(sc, P)
    csv = tmp_path / "bars.csv"
    base._bars(260, seed=8, symbols=("QQQ", "SPY")).to_csv(csv, index=False)
    assert q.main([str(csv)]) == 0
    out = capsys.readouterr().out
    assert "Candidate D+" in out and "configurations tried: 1" in out


def test_the_keep_threshold_is_not_recomputed_from_the_month_being_scored():
    """If a month's features all shift up, a threshold taken from the TRAINING scores keeps
    nearly all of that month; one taken from the month itself would still keep half."""
    t = _synthetic_table(n=4000, skill=1.5, seed=9)
    month = t["entry_ts"].dt.tz_localize(None).dt.to_period("M")
    last = sorted(month.unique())[-1]
    shifted = t.copy()
    shifted.loc[month == last, "abs_z"] += 6.0
    sc, _ = q.walk_forward(shifted)
    in_last = sc["entry_ts"].dt.tz_localize(None).dt.to_period("M") == last
    assert in_last.sum() > 20
    assert sc.loc[in_last, "keep"].mean() > 0.95
