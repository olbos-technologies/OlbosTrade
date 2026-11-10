"""The options signal timing report must describe timing, not assume a cause.

These pin the mechanics of scripts/options_signal_timing_report.py on made-up
signals and events: bands, in/outside regular hours (using the repo's own market
hours rule), the ET hour spread, matching a signal to an execution attempt, and the
verdict sentence. They say nothing about why options have not traded.
"""
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "options_signal_timing_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("options_signal_timing_report", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


r = _load()
UTC = timezone.utc

# 2026-11-10 is a Tuesday. 15:00 UTC = 10:00 ET (open); 23:00 UTC = 18:00 ET (closed);
# 2026-11-14 is a Saturday; 2026-11-26 is Thanksgiving (a listed holiday).
OPEN = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
AFTER_HOURS = datetime(2026, 11, 10, 23, 0, tzinfo=UTC)
PRE_MARKET = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)         # 07:00 ET
SATURDAY = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
THANKSGIVING = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)


def test_the_script_file_was_actually_read():
    assert SCRIPT.exists() and callable(r.summarize_timing) and r.MATCH_MINUTES == 30


def test_bands_use_the_aggressive_floor_as_the_top_edge():
    assert r.band_of(0.619) == 0 and r.band_of(0.62) == 1 and r.band_of(0.70) == 2 and r.band_of(1.0) == 2
    assert [r.band_label(a, b) for a, b in zip(r.EDGES, r.EDGES[1:])] == ["< 0.62", "0.62 - 0.70", ">= 0.70"]


def test_the_repos_market_hours_rule_is_what_decides_in_or_out():
    s = r.summarize_timing([(0.8, OPEN), (0.8, AFTER_HOURS), (0.8, PRE_MARKET), (0.8, SATURDAY), (0.8, THANKSGIVING)])
    top = s["bands"][2]
    assert top["signals"] == 5 and top["in_hours"] == 1 and top["out_of_hours"] == 4
    assert top["share_in_hours"] == 0.2


def test_signals_are_counted_per_band_and_hours_are_in_eastern_time():
    s = r.summarize_timing([(0.5, OPEN), (0.65, OPEN), (0.8, AFTER_HOURS), (0.8, AFTER_HOURS), (0.9, PRE_MARKET)])
    assert [b["signals"] for b in s["bands"]] == [1, 1, 3]
    assert s["bands"][2]["hours_et"] == {7: 1, 18: 2}          # 12:00 UTC = 07 ET, 23:00 UTC = 18 ET


def test_an_empty_band_has_no_share_not_zero():
    b = r.summarize_timing([(0.8, OPEN)])["bands"][0]
    assert b["signals"] == 0 and b["share_in_hours"] is None and b["ci_low"] is None


def _many(score, n_in, n_out):
    return [(score, OPEN)] * n_in + [(score, AFTER_HOURS)] * n_out


def test_too_few_signals_refuses_to_compare():
    s = r.summarize_timing(_many(0.8, 5, 5) + _many(0.5, 100, 100))
    assert r.timing_verdict(s).startswith("INSUFFICIENT DATA")
    assert r.timing_verdict(r.summarize_timing([])).startswith("INSUFFICIENT DATA")


def test_a_clearly_lower_in_hours_share_says_it_fits_the_closed_market_explanation():
    s = r.summarize_timing(_many(0.8, 5, 295) + _many(0.5, 150, 150))
    v = r.timing_verdict(s)
    assert "LESS often" in v and "closed-market explanation" in v


def test_a_clearly_higher_in_hours_share_says_it_argues_against_the_explanation():
    s = r.summarize_timing(_many(0.8, 280, 20) + _many(0.5, 60, 240))
    assert "MORE often" in r.timing_verdict(s)


def test_overlapping_shares_say_timing_alone_does_not_explain_it():
    s = r.summarize_timing(_many(0.8, 40, 60) + _many(0.5, 200, 300))
    assert "no reliable timing difference" in r.timing_verdict(s)


def test_a_signal_matches_the_first_attempt_for_the_same_ticker_inside_the_window():
    t0 = AFTER_HOURS
    signals = [("AAPL", t0), ("MSFT", t0), ("NVDA", t0), ("TSLA", t0)]
    events = [
        ("AAPL", t0 + timedelta(minutes=2), "blocked", "market_closed: after_hours"),
        ("AAPL", t0 + timedelta(minutes=3), "blocked", "frequency_controller: below_min_confidence: 0.6 < 0.70 (aggressive)"),
        ("MSFT", t0 + timedelta(minutes=45), "blocked", "market_closed: after_hours"),    # outside the 30-minute window
        ("NVDA", t0 - timedelta(minutes=1), "blocked", "market_closed: after_hours"),    # before the signal
    ]
    got = r.match_attempts(signals, events)
    assert got["blocked: market_closed: after_hours"] == 1       # AAPL's FIRST attempt only
    assert got["no execution attempt recorded"] == 3             # MSFT late, NVDA early, TSLA none
    assert sum(got.values()) == 4


def test_a_submitted_attempt_is_reported_as_submitted_and_reasons_collapse_to_buckets():
    t0 = OPEN
    events = [("A", t0, "submitted", None), ("B", t0, "blocked", "frequency_controller: below_min_confidence: 0.61 < 0.70 (aggressive)"),
              ("C", t0, "blocked", "frequency_controller: below_min_confidence: 0.64 < 0.70 (aggressive)")]
    got = r.match_attempts([("A", t0), ("B", t0), ("C", t0)], events)
    assert got["submitted"] == 1
    assert got["blocked: frequency_controller: below_min_confidence"] == 2     # numbers do not split the reason


def test_the_script_only_reads():
    src = SCRIPT.read_text()
    for forbidden in ("session.add", ".commit(", "session.delete", "import insert", "import update",
                      "import delete", "update(", "delete(", "place_order", "submit_order"):
        assert forbidden not in src, forbidden
