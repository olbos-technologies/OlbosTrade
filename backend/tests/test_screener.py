"""
S&P 500 daily screener: metric definitions, the closed-session rule, Yahoo
frame parsing, missing-symbol disclosure, and the constituents parser.
"""
import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.services.screener.metrics import clean_bars, completed_sessions, session_metrics
from app.services.screener.snapshot import Snapshot, build_rows, frame_to_bars
from app.services.screener.universe import yahoo_symbol


def _bars(n=25, close=100.0, vol=1_000_000, start=date(2026, 1, 1)):
    out = []
    for i in range(n):
        out.append({"date": (start + timedelta(days=i)).isoformat(),
                    "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": vol})
    return out


def test_flat_series_definitions():
    bars = _bars()
    bars[-1] = {**bars[-1], "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 3_000_000}
    m = session_metrics(clean_bars(bars))
    assert m["last"] == 103.0
    assert m["change_pct"] == 3.0
    assert m["gap_pct"] == 2.0
    # prior ATR: each flat bar's true range is 2 (high-low), so the gap of 2 is 1 ATR
    assert m["gap_atr"] == 1.0
    assert m["rvol"] == 3.0                    # 3M vs the 1M average of the 20 sessions before
    assert m["adv20"] == round((19 * 1e6 + 3e6) / 20)
    assert m["range_pos"] == round((103 - 101) / 3, 4)
    assert m["session_date"] == bars[-1]["date"]


def test_dollar_volume_is_mean_of_close_times_volume():
    m = session_metrics(clean_bars(_bars(close=50.0, vol=2_000_000)))
    assert m["dollar_vol20"] == 100_000_000


def test_too_short_history_gives_none():
    assert session_metrics(clean_bars(_bars(n=20))) is None


def test_zero_range_session_has_no_range_position():
    bars = _bars()
    bars[-1] = {**bars[-1], "high": 100.0, "low": 100.0}
    assert session_metrics(clean_bars(bars))["range_pos"] is None


def test_clean_bars_drops_bad_rows_and_sorts():
    bars = [
        {"date": "2026-01-03", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 5},
        {"date": "2026-01-02", "open": 1, "high": 2, "low": 1, "close": 1, "volume": None},
        {"date": "2026-01-04", "open": float("nan"), "high": 2, "low": 1, "close": 2, "volume": 1},
        {"date": "2026-01-05", "open": 1, "high": 1, "low": 2, "close": 1, "volume": 1},   # high < low
        {"date": "2026-01-06", "open": 0, "high": 1, "low": 0, "close": 1, "volume": 1},   # zero price
    ]
    out = clean_bars(bars)
    assert [b["date"] for b in out] == ["2026-01-02", "2026-01-03"]
    assert out[0]["volume"] == 0.0


@pytest.mark.parametrize("et_hour,et_min,kept", [(15, 59, False), (16, 14, False), (16, 15, True), (19, 0, True)])
def test_todays_bar_is_dropped_until_the_session_is_final(et_hour, et_min, kept):
    # 2026-03-10 is during US daylight time: ET = UTC-4
    bars = [{"date": "2026-03-09"}, {"date": "2026-03-10"}]
    now = datetime(2026, 3, 10, et_hour + 4, et_min, tzinfo=timezone.utc)
    assert (completed_sessions(bars, now)[-1]["date"] == "2026-03-10") is kept


def test_an_earlier_days_bar_is_always_kept():
    bars = [{"date": "2026-03-09"}]
    assert completed_sessions(bars, datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)) == bars


def test_yahoo_symbol_uses_a_dash_for_share_classes():
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert yahoo_symbol("AAPL") == "AAPL"


def _frame(symbols, n=25):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    cols = pd.MultiIndex.from_product([symbols, ["Open", "High", "Low", "Close", "Volume"]])
    data = []
    for _ in range(n):
        row = []
        for _s in symbols:
            row += [100.0, 101.0, 99.0, 100.0, 1_000_000]
        data.append(row)
    return pd.DataFrame(data, index=idx, columns=cols)


def test_frame_to_bars_reads_multi_and_single_ticker_frames():
    multi = _frame(["AAA", "BRK-B"])
    assert len(frame_to_bars(multi, "BRK-B")) == 25
    assert frame_to_bars(multi, "ZZZ") == []
    single = multi["AAA"]
    assert frame_to_bars(single, "AAA")[0]["date"] == "2026-01-01"
    assert frame_to_bars(None, "AAA") == []


def test_build_rows_lists_missing_symbols_and_survives_a_failed_batch():
    uni = [{"symbol": "AAA", "name": "A", "sector": "X"}, {"symbol": "BRK.B", "name": "B", "sector": "Y"},
           {"symbol": "NOPE", "name": "N", "sector": "Z"}]
    late = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rows, missing = build_rows(uni, lambda syms: _frame(["AAA", "BRK-B"]), now=late)
    assert [r["symbol"] for r in rows] == ["AAA", "BRK.B"]
    assert rows[1]["sector"] == "Y"
    assert missing == ["NOPE"]

    def boom(_):
        raise RuntimeError("network")
    rows, missing = build_rows(uni, boom, now=late)
    assert rows == [] and missing == ["AAA", "BRK.B", "NOPE"]


def test_view_states():
    s = Snapshot()
    empty = {"source": None, "retrieved": None, "constituents": []}
    uni = {"source": "x", "retrieved": "2026-11-19", "constituents": [{"symbol": "AAA"}]}
    assert s.view(empty)["state"] == "no_universe"
    assert s.view(uni)["state"] == "warming"
    s.error = "The market-data refresh failed."
    assert s.view(uni)["state"] == "error"
    s.computed_at = "2026-11-19T00:00:00+00:00"
    v = s.view(uni)
    assert v["state"] == "ready" and v["universe"]["count"] == 1


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh_sp500.py"
    spec = importlib.util.spec_from_file_location("refresh_sp500", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HTML = """<html><body>
<table id="other"><tr><th>Symbol</th></tr><tr><td>NOT</td></tr></table>
<table class="wikitable" id="constituents">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th></tr>
<tr><td><a href="#">MMM</a></td><td><a>3M</a></td><td>Industrials</td><td>x</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>y</td></tr>
<tr><td>AOS</td><td>A. O. Smith <sup>[1]</sup></td><td>Industrials</td><td><table><tr><td>nested</td></tr></table></td></tr>
</table></body></html>"""


def test_constituents_parser_reads_only_the_constituents_table():
    mod = _load_script()
    rows = mod.parse_constituents(HTML)
    assert [r["symbol"] for r in rows] == ["MMM", "BRK.B", "AOS"]
    assert rows[0]["name"] == "3M" and rows[1]["sector"] == "Financials"


def test_constituents_validation_rejects_a_short_or_malformed_list():
    mod = _load_script()
    problems = mod.validate(mod.parse_constituents(HTML))
    assert any("rows, expected" in p for p in problems)
    good = [{"symbol": f"A{chr(65 + i // 26)}{chr(65 + i % 26)}", "name": "n", "sector": "s"} for i in range(500)]
    assert mod.validate(good) == []
    assert any("malformed" in p for p in mod.validate(good[:-1] + [{"symbol": "bad sym", "name": "n", "sector": "s"}]))
    assert any("duplicate" in p for p in mod.validate(good[:-1] + [good[0]]))
