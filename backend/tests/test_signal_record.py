"""
Signal record (audit phase 1): duplicates collapsed with the dedupe script's
rule, results in R after costs, intervals and the publication verdict.
"""
import importlib.util
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.signal_record import (
    clustered_mean_ci, confidence_bucket, dedupe, keep_rank, meets_standard, r_result, summarize, verdict, wilson,
)

T0 = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)


def row(status="target_hit", action="BUY", entry=100.0, stop=98.0, target=104.0, exit_=None,
        t=T0, ticker="AAA", asset="equity", conf=0.7, regime="trend"):
    return {"ticker": ticker, "asset_type": asset, "action": action, "confidence": conf,
            "entry_price": entry, "stop_price": stop, "target_price": target, "exit_price": exit_,
            "status": status, "regime": regime, "signal_engine_version": "1.0.0", "generated_at": t}


def test_r_result_long_short_and_costs():
    assert r_result(row(), 0)["gross_r"] == pytest.approx(2.0)          # target 4 above, risk 2
    assert r_result(row(status="stop_hit"), 0)["gross_r"] == pytest.approx(-1.0)
    short = row(action="SELL", stop=102.0, target=96.0)
    assert r_result(short, 0)["gross_r"] == pytest.approx(2.0)
    # 10 bps of a $100 entry is $0.10, which is 0.05 R on a $2 risk
    res = r_result(row(), 10)
    assert res["cost_r"] == pytest.approx(0.05) and res["net_r"] == pytest.approx(1.95)


def test_r_result_prefers_the_recorded_exit():
    assert r_result(row(exit_=103.0), 0)["gross_r"] == pytest.approx(1.5)


def test_expired_needs_an_exit_and_bad_rows_are_unmeasurable():
    assert r_result(row(status="expired"), 0) is None
    assert r_result(row(status="expired", exit_=101.0), 0)["gross_r"] == pytest.approx(0.5)
    assert r_result(row(stop=100.0), 0) is None           # zero risk
    assert r_result(row(action="HOLD"), 0) is None
    assert r_result(row(status="pending"), 0) is None
    assert r_result(row(entry=None), 0) is None


def test_dedupe_matches_the_dedupe_script_rule():
    early_pending = row(status="pending", t=T0)
    later_resolved = row(status="stop_hit", t=T0 + timedelta(hours=2))
    other_day = row(status="pending", t=T0 + timedelta(days=1))
    kept, dropped = dedupe([early_pending, later_resolved, other_day])
    assert dropped == 1
    assert later_resolved in kept and early_pending not in kept    # a resolved twin wins
    a, b = row(status="target_hit", t=T0), row(status="stop_hit", t=T0 + timedelta(hours=1))
    kept, _ = dedupe([b, a])
    assert kept == [a]                                             # same rank: earliest wins


def test_keep_rank_is_the_scripts_rule():
    path = Path(__file__).resolve().parents[1] / "scripts" / "dedupe_signal_outcomes.py"
    src = path.read_text()
    assert 'STATUS_RANK = {"target_hit": 3, "stop_hit": 3, "expired": 2, "pending": 1}' in src
    assert keep_rank(row(status="expired")) < keep_rank(row(status="stop_hit"))


def test_wilson_matches_known_values():
    lo, hi = wilson(75, 100)
    assert (round(lo, 3), round(hi, 3)) == (0.657, 0.825)
    assert wilson(0, 0) is None


def test_clustered_interval_needs_two_days():
    assert clustered_mean_ci([1.0, 2.0], ["d1", "d1"]) is None
    ci = clustered_mean_ci([1.0, -1.0, 1.0, -1.0], ["d1", "d1", "d2", "d2"], draws=200)
    assert ci[0] <= 0 <= ci[1]


def test_verdicts():
    assert verdict(10, (0.1, 0.2), min_n=300) == "insufficient sample"
    assert verdict(400, (0.01, 0.2)) == "positive"
    assert verdict(400, (-0.3, -0.01)) == "negative"
    assert verdict(400, (-0.1, 0.1)) == "unproven"


def _cohort(n_days, per_day, win_share, asset="equity", seed=1):
    rng = random.Random(seed)
    out = []
    for d in range(n_days):
        for i in range(per_day):
            st = "target_hit" if rng.random() < win_share else "stop_hit"
            out.append(row(status=st, t=T0 + timedelta(days=d, minutes=i), ticker=f"T{i}", asset=asset))
    return out


def test_summary_of_a_clearly_positive_cohort_passes_the_gate():
    rows = _cohort(40, 10, 0.6)      # +2R / -1R at 60% wins: expectancy about +0.8R before costs
    s = summarize(rows, lambda r: (r["asset_type"],), min_n=300, draws=300)[0]
    assert s["resolved"] == 400 and s["days"] == 40
    assert s["expectancy_r"] > 0.5 and s["verdict"] == "positive"
    assert meets_standard(s)
    assert not meets_standard(s, min_win_rate_lb=0.75)   # 60% does not clear a 75% lower bound


def test_a_high_target_rate_can_still_lose_money():
    # target 0.5 R away, stop 1 R away (risk 2, target 1 above): 60% "accurate", negative expectancy
    rng = random.Random(3)
    rows = [row(status="target_hit" if rng.random() < 0.6 else "stop_hit", target=101.0,
                t=T0 + timedelta(days=d, minutes=i), ticker=f"T{i}") for d in range(40) for i in range(10)]
    s = summarize(rows, lambda r: (r["asset_type"],), min_n=300, draws=300)[0]
    assert s["target_hit_rate"] > 0.55
    assert s["expectancy_r"] < 0 and s["verdict"] in ("negative", "unproven")
    assert not meets_standard(s)


def test_summary_counts_pending_and_unmeasurable_and_groups_by_style():
    rows = _cohort(5, 2, 0.5) + [row(status="pending", asset="crypto"),
                                 row(status="expired", asset="crypto", t=T0 + timedelta(days=9))]
    out = {tuple(s["group"]): s for s in summarize(rows, lambda r: (r["asset_type"],), draws=50)}
    assert out[("crypto",)]["pending"] == 1 and out[("crypto",)]["unmeasurable"] == 1
    assert out[("crypto",)]["resolved"] == 0 and out[("crypto",)]["verdict"] == "insufficient sample"
    assert out[("equity",)]["resolved"] == 10


def test_crypto_costs_more_than_equity_by_default():
    eq = summarize([row()], lambda r: (1,), draws=10)[0]["avg_cost_r"]
    cr = summarize([row(asset="crypto")], lambda r: (1,), draws=10)[0]["avg_cost_r"]
    assert cr > eq


def test_confidence_buckets():
    assert confidence_bucket(0.95) == ">= 0.9"
    assert confidence_bucket(0.55) == ">= 0.5"
    assert confidence_bucket(0.2) == "< 0.5"
    assert confidence_bucket(None) == "none"


def test_report_script_imports_and_parses_costs():
    path = Path(__file__).resolve().parents[1] / "scripts" / "signal_record_report.py"
    spec = importlib.util.spec_from_file_location("signal_record_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.parse_costs(["equity=5", "crypto = 40"]) == {"equity": 5.0, "crypto": 40.0}
    assert set(mod.VIEWS) >= {"style", "style x confidence"}


def test_win_means_above_zero_after_costs():
    small_loss = row(status="expired", exit_=99.9, t=T0)                       # -0.05 R before costs
    eaten_by_costs = row(status="target_hit", target=100.05, t=T0 + timedelta(days=1), ticker="BBB")
    clear_win = row(status="target_hit", t=T0 + timedelta(days=2), ticker="CCC")
    s = summarize([small_loss, eaten_by_costs, clear_win], lambda r: (1,), draws=20)[0]
    assert s["resolved"] == 3
    assert s["win_rate"] == pytest.approx(1 / 3)
    assert s["target_hit_rate"] == pytest.approx(2 / 3)
