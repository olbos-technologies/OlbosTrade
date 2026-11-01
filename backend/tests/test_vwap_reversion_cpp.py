"""The C++ simulator must give EXACTLY the Python reference's trades.

Python's `simulate` is the reference, covered by test_vwap_reversion_backtest.py.
The C++ twin exists only to be faster, so the only property worth testing is
that it is not a different strategy: same rows, same floats, bit for bit, over
a wide sweep of parameters and awkward data. Anything less and a speedup could
quietly change a research result.

Skipped, loudly, where no C++ compiler exists — CI installs one, and the
production backend image is not expected to have one.
"""
import importlib.util
import itertools
import pathlib
import shutil
import sys
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"

COMPILER = shutil.which("g++") or shutil.which("c++") or shutil.which("clang++")
pytestmark = pytest.mark.skipif(
    COMPILER is None,
    reason="no C++ compiler on PATH: the C++/Python differential test did NOT run",
)

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))
import test_vwap_reversion_backtest as base      # noqa: E402  (reuses its bar generator)
import vwap_reversion_cpp as cpp                 # noqa: E402

bt = base.bt
P = bt.Params()


@pytest.fixture(scope="module")
def lib():
    cpp.build()
    return cpp._load()


def _same(a: pd.DataFrame, b: pd.DataFrame):
    pd.testing.assert_frame_equal(
        a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True)


def _both(feats, p):
    return bt.simulate(feats, p), cpp.simulate(feats, p)


def _short_days(df, seed):
    """Some sessions end early (half days, bad exports): exercises 'ran off the end'."""
    rng = np.random.default_rng(seed)
    ts = pd.to_datetime(df["ts"])
    days = ts.dt.date
    drop = np.zeros(len(df), bool)
    for d in days.unique():
        if rng.random() < 0.3:
            idx = np.flatnonzero((days == d).to_numpy())
            drop[idx[int(len(idx) * rng.uniform(0.3, 0.9)):]] = True
    return df[~drop].reset_index(drop=True)


DATASETS = {
    "calm": lambda: base._bars(25, seed=1, symbols=("QQQ", "SPY")),
    "drift_up": lambda: base._bars(25, seed=2, symbols=("QQQ",), drift=0.0002),
    "drift_down": lambda: base._bars(25, seed=3, symbols=("QQQ",), drift=-0.0002),
    "short_sessions": lambda: _short_days(base._bars(25, seed=4, symbols=("QQQ", "SPY")), 4),
}

PARAM_SPACE = {
    "z_entry": (0.8, 1.2, 1.7, 2.1),
    "stop_pct": (0.1, 0.22, 0.6),
    "target_pct": (0.1, 0.4, 0.9),
    "time_stop_min": (15, 90, 600),
    "trend_filter": ("ema_slope", "none"),
    "be_trigger_pct": (0.05, 0.22, 0.5),
    "cost_mult": (0.0, 1.0, 2.0),
}
WINDOWS = (bt.DEFAULT_WINDOWS, ((585, 930),), ((570, 700), (720, 800), (820, 930)))
SIDES = (("long", "short"), ("long",), ("short",))


@pytest.mark.parametrize("name", list(DATASETS))
def test_identical_trades_across_a_sweep_of_parameters(lib, name):
    feats = base._feats(DATASETS[name]())
    rng = np.random.default_rng(100 + list(DATASETS).index(name))   # fixed: str hash() is salted per process
    keys = list(PARAM_SPACE)
    n_trades_seen = 0
    for _ in range(40):
        kw = {k: PARAM_SPACE[k][rng.integers(len(PARAM_SPACE[k]))] for k in keys}
        kw["windows"] = WINDOWS[rng.integers(len(WINDOWS))]
        kw["sides"] = SIDES[rng.integers(len(SIDES))]
        p = replace(P, **kw)
        a, b = _both(feats, p)
        _same(a, b)
        n_trades_seen += len(a)
    # A sweep that never trades would pass vacuously.
    assert n_trades_seen > 500, n_trades_seen


def test_every_exit_reason_is_exercised_and_matches(lib):
    seen = set()
    for name, make in DATASETS.items():
        feats = base._feats(make())
        for kw in ({"z_entry": 0.8, "stop_pct": 0.1, "target_pct": 0.1, "time_stop_min": 15,
                    "trend_filter": "none", "windows": ((585, 930),)},
                   {"z_entry": 1.0, "stop_pct": 0.6, "target_pct": 0.9, "time_stop_min": 600,
                    "trend_filter": "none", "windows": ((585, 930),)}):
            a, b = _both(feats, replace(P, **kw))
            _same(a, b)
            seen |= set(a["reason"])
    assert {"stop", "target", "time", "flatten"} <= seen, seen


def test_empty_input_and_one_session(lib):
    feats = base._feats(base._bars(1))
    a, b = _both(feats.iloc[0:0], P)
    assert a.empty and b.empty and list(a.columns) == list(b.columns)
    _same(*_both(feats, replace(P, z_entry=0.5, trend_filter="none", windows=((585, 930),))))


def test_non_contiguous_partitions_match(lib):
    """run_grid hands over date-filtered slices whose index has gaps."""
    feats = base._feats(base._bars(30, seed=9, symbols=("QQQ", "SPY")))
    dates = sorted(feats["date"].unique())
    late = feats[feats["date"] >= dates[18]]
    assert not late.index.equals(pd.RangeIndex(len(late)))
    p = replace(P, z_entry=1.0, trend_filter="none", windows=((585, 930),))
    a, b = _both(late, p)
    assert len(a) > 10
    _same(a, b)


def test_the_full_grid_report_is_identical_in_both_engines(lib):
    feats = base._feats(base._bars(30, seed=5, symbols=("QQQ", "SPY")))
    small = {"z_entry": (1.0, 1.5), "stop_pct": (0.22, 0.35), "target_pct": (0.3, 0.5),
             "time_stop_min": (60, 90), "trend_filter": ("none", "ema_slope")}
    py = bt.format_report(bt.run_grid(feats, small, min_trades=1, engine="python"))
    cx = bt.format_report(bt.run_grid(feats, small, min_trades=1, engine="cpp"))
    assert py == cx


def test_unknown_engine_is_rejected():
    with pytest.raises(ValueError, match="unknown engine"):
        bt.get_engine("fortran")


def test_a_broken_cpp_build_is_caught_by_this_comparison(lib, tmp_path):
    """Prove the comparison has teeth: a plausible bug must produce a diff.

    The mutant checks the target before the stop. If this ever passed as
    'identical' the whole file would be giving false assurance.
    """
    src = (SCRIPTS / "cpp" / "vwap_sim.cpp").read_text()
    stop = "if (l[g] <= stop) { exit_px = stop - slip; reason = STOP; found = true; break; }\n"
    targ = "                    if (h[g] >= target + p.tick) { exit_px = target; reason = TARGET; found = true; break; }\n"
    assert src.count(stop) == 1 and src.count(targ) == 1
    mutant = src.replace(stop, "").replace(targ, targ + "                    " + stop)
    assert mutant != src
    m = tmp_path / "mutant.cpp"
    m.write_text(mutant)
    broken = cpp._load(cpp.build(src=m, force=True))
    feats = base._feats(base._bars(25, seed=1, symbols=("QQQ", "SPY")))
    p = replace(P, z_entry=0.8, stop_pct=0.1, target_pct=0.1, trend_filter="none",
                windows=((585, 930),))
    ref = bt.simulate(feats, p)
    got = cpp.simulate(feats, p, lib=broken)
    assert len(ref) > 50
    assert not ref.reset_index(drop=True).equals(got.reset_index(drop=True))


def test_the_source_and_the_python_agree_on_the_flatten_time():
    assert f"FLATTEN_MIN = {bt.FLATTEN_MIN // 60} * 60 + {bt.FLATTEN_MIN % 60}" in (
        SCRIPTS / "cpp" / "vwap_sim.cpp").read_text()
