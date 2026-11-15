"""The recorder report must measure after real costs and must not read meaning into too little data."""
import importlib.util
import pathlib
from datetime import date

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "zero_dte_recorder_report.py"
spec = importlib.util.spec_from_file_location("zero_dte_recorder_report", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def row(direction="bullish", slot="10:30", nat_debit=1.10, mid_debit=1.06, marks=None,
        proposed=True, day=date(2026, 11, 11), symbol="SPY", reasons=None):
    return {"trade_date": day, "slot": slot, "symbol": symbol, "direction": direction, "proposed": proposed,
            "refusal_reasons": reasons, "mid_debit": mid_debit, "natural_debit": nat_debit, "marks": marks or []}


def mark(minutes=5, natural=0.96, mid=1.00, ok=True):
    return {"minutes": minutes, "quote_ok": ok, "natural_credit": natural if ok else None,
            "mid_credit": mid if ok else None}


def test_the_script_file_was_actually_read():
    assert SCRIPT.exists() and r.MIN_N == 30 and callable(r.summarize)


def test_net_result_is_the_natural_exit_less_the_natural_entry():
    assert r.net_per_share(row(), mark(natural=0.96)) == 0.96 - 1.10


def test_cost_is_what_crossing_the_spread_twice_took():
    # in: paid 1.10 vs mid 1.06 (4c); out: could get 0.96 vs mid 1.00 (4c)
    assert abs(r.cost_per_share(row(), mark(natural=0.96, mid=1.00)) - 0.08) < 1e-9


def test_an_unreadable_mark_is_not_counted_as_a_result():
    assert r.net_per_share(row(), mark(ok=False)) is None and r.cost_per_share(row(), mark(ok=False)) is None
    s = r.summarize([row(marks=[mark(ok=False)])])
    assert s["marks"][5] == {"expected": 1, "recorded": 1, "readable": 0} and s["net"] == {}


def test_missing_marks_are_counted_as_missing():
    s = r.summarize([row(), row(marks=[mark(5)])])
    assert s["marks"][5] == {"expected": 2, "recorded": 1, "readable": 1}
    assert s["marks"][10] == {"expected": 2, "recorded": 0, "readable": 0}


def test_refusals_are_counted_and_the_top_reasons_listed():
    s = r.summarize([row(proposed=False, reasons=["long leg bid/ask is too wide (1 / 2)"]),
                     row(proposed=False, reasons=["long leg bid/ask is too wide (1 / 2)"]),
                     row(proposed=False, reasons=["chain unavailable: TimeoutError: x"]), row()])
    assert s["observations"] == 4 and s["proposed"] == 1
    assert s["top_refusals"][0] == ("long leg bid/ask is too wide (1 / 2)", 2)
    assert ("chain unavailable", 1) in s["top_refusals"]


def test_too_few_marks_gives_no_mean():
    s = r.summarize([row(marks=[mark()]) for _ in range(r.MIN_N - 1)])
    v = s["net"][(5, "all", "bullish")]
    assert v["verdict"].startswith("INSUFFICIENT DATA") and "mean" not in v


def test_enough_marks_gives_a_mean_and_a_hit_rate_but_no_verdict():
    s = r.summarize([row(marks=[mark(natural=1.20 if i % 2 else 0.80)]) for i in range(r.MIN_N)])
    v = s["net"][(5, "all", "bullish")]
    assert v["n"] == r.MIN_N and abs(v["mean"] - (1.00 - 1.10)) < 1e-9 and v["hit"] == 0.5
    assert "verdict" in v and v["verdict"] is None
    assert "not a verdict" in r.render(s, 30)


def test_baseline_pairs_both_directions_at_the_same_moment():
    rows = []
    for i in range(r.MIN_N):
        day = date(2026, 11, 1 + i % 28)
        rows.append(row("bullish", marks=[mark(natural=1.30)], day=day, symbol="SPY" if i < 15 else "QQQ"))
        rows.append(row("bearish", marks=[mark(natural=0.70)], day=day, symbol="SPY" if i < 15 else "QQQ"))
    s = r.summarize(rows)
    assert s["net"][(5, "all", "bullish")]["mean"] > 0 > s["net"][(5, "all", "bearish")]["mean"]
    base = s["baseline"][(5, "all", "both directions")]
    assert abs(base["mean"] - ((1.30 - 1.10) + (0.70 - 1.10)) / 2) < 1e-9


def test_a_lone_direction_is_not_part_of_the_baseline():
    s = r.summarize([row("bullish", marks=[mark()])])
    assert s["baseline"][(5, "all", "both directions")]["n"] == 0
