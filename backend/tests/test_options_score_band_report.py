"""The options score-band report must not read meaning into too little data.

These pin the mechanics of scripts/options_score_band_report.py on made-up
trades. They say nothing about whether any score threshold helps; only that the
report bands scores correctly, leaves empty bands empty, refuses to compare
bands with too few trades, and keeps its strategy list in step with the funnel.
"""
import importlib.util
import math
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "options_score_band_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("options_score_band_report", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


r = _load()


def test_the_script_file_was_actually_read():
    assert SCRIPT.exists() and callable(r.summarize_bands) and r.MIN_N == 30


def test_scores_fall_into_the_right_band_and_edges_belong_to_the_upper_band():
    assert r.band_of(0.10) == 0
    assert r.band_of(0.55) == 1           # an edge belongs to the band it opens
    assert r.band_of(0.619) == 1
    assert r.band_of(0.62) == 2
    assert r.band_of(0.70) == 3
    assert r.band_of(1.0) == 3
    assert r.band_of(1.5) is None and r.band_of(-0.1) is None


def test_band_labels_name_the_aggressive_thresholds():
    labels = [r.band_label(lo, hi) for lo, hi in zip(r.EDGES, r.EDGES[1:])]
    assert labels == ["< 0.55", "0.55 - 0.62", "0.62 - 0.70", ">= 0.70"]


def test_summary_counts_wins_and_pnl_per_band_and_leaves_empty_bands_empty():
    rows = [(0.65, 100.0), (0.66, -50.0), (0.68, 30.0), (0.80, 10.0)]
    b = r.summarize_bands(rows)
    assert [x["n"] for x in b] == [0, 0, 3, 1]
    assert b[2]["wins"] == 2 and b[2]["total_pnl"] == 80.0 and round(b[2]["avg_pnl"], 4) == round(80 / 3, 4)
    assert b[0]["win_rate"] is None and b[0]["avg_pnl"] is None and b[0]["ci_low"] is None
    assert b[0]["total_pnl"] == 0.0


def test_a_breakeven_trade_is_not_a_win():
    assert r.summarize_bands([(0.8, 0.0)])[3]["wins"] == 0


def test_wilson_interval_brackets_the_rate_and_is_wide_for_few_trades():
    lo, hi = r.wilson(5, 10)
    assert lo < 0.5 < hi and (hi - lo) > 0.5            # ten trades say very little
    lo2, hi2 = r.wilson(500, 1000)
    assert (hi2 - lo2) < 0.07
    assert all(math.isnan(x) for x in r.wilson(0, 0))


def _bands(mid_n, mid_wins, top_n, top_wins):
    rows = [(0.65, 1.0 if i < mid_wins else -1.0) for i in range(mid_n)]
    rows += [(0.80, 1.0 if i < top_wins else -1.0) for i in range(top_n)]
    return r.summarize_bands(rows)


def test_too_few_trades_in_either_band_refuses_to_compare():
    v = r.comparison_verdict(_bands(29, 25, 100, 50))
    assert v.startswith("INSUFFICIENT DATA") and "29" in v and "Nothing here supports" in v
    assert r.comparison_verdict(_bands(100, 50, 5, 5)).startswith("INSUFFICIENT DATA")
    assert r.comparison_verdict(r.summarize_bands([])).startswith("INSUFFICIENT DATA")


def test_enough_trades_with_overlapping_intervals_shows_no_reliable_difference():
    v = r.comparison_verdict(_bands(40, 22, 40, 24))
    assert "no reliable difference" in v


def test_enough_trades_with_separated_intervals_names_the_worse_band():
    worse_mid = r.comparison_verdict(_bands(300, 90, 300, 200))
    assert "lower band did worse" in worse_mid
    worse_top = r.comparison_verdict(_bands(300, 200, 300, 90))
    assert "higher band did worse" in worse_top


def test_strategy_list_matches_the_options_funnel():
    from app.services.options_funnel import KNOWN_STRATEGIES
    assert tuple(r.OPTIONS_STRATEGIES) == tuple(KNOWN_STRATEGIES)


def test_the_script_only_reads():
    src = SCRIPT.read_text()
    # Database writes and order calls (`sys.path.insert` is a path tweak, not a write).
    for forbidden in ("session.add", ".commit(", "session.delete", "import insert", "import update",
                      "import delete", "update(Trade", "delete(Trade", "place_order", "submit_order"):
        assert forbidden not in src, forbidden
