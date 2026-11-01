"""The bar exporter is read-only, resumable, and writes what the backtest reads.

It runs once, on the production box, against the paper gateway, for about an
hour. A failure part-way must lose nothing already fetched, and the one thing
it must never be able to do is trade.
"""
import importlib.util
import pathlib
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


fx = _load("intraday_bars_fetch")
bt = _load("vwap_reversion_backtest")


def _bar(h, m, px=400.0, day=3):
    return SimpleNamespace(date=datetime(2025, 3, day, h, m, tzinfo=timezone.utc),
                           open=px, high=px + 0.2, low=px - 0.2, close=px + 0.1, volume=150_000.0)


def _bar_on(d):
    """A bar dated on `d`, as the gateway returns for the week ending `d`."""
    return SimpleNamespace(date=datetime(d.year, d.month, d.day, 14, 30, tzinfo=timezone.utc),
                           open=400.0, high=400.2, low=399.8, close=400.1, volume=150_000.0)


def _end(end_dt):
    return datetime.strptime(end_dt[:8], "%Y%m%d").date()


def test_the_exporter_can_not_place_orders_and_asks_for_a_read_only_session():
    src = (SCRIPTS / "intraday_bars_fetch.py").read_text()
    assert "readonly=True" in src
    for banned in ("placeOrder", "cancelOrder", "modifyOrder", "MarketOrder", "LimitOrder"):
        assert banned not in src, banned


def test_week_ends_cover_the_range_newest_first():
    ends = fx.week_ends(date(2025, 1, 1), date(2025, 1, 29))
    assert ends[0] == date(2025, 1, 29) and ends[-1] >= date(2025, 1, 1)
    assert ends == sorted(ends, reverse=True) and len(ends) == 5
    assert fx.week_ends(date(2025, 2, 1), date(2025, 1, 1)) == []


def test_rows_carry_the_fields_the_backtest_needs(tmp_path):
    p = tmp_path / "b.csv"
    fx.append_rows(p, fx.bars_to_rows("QQQ", [_bar(14, 30)]), set())
    df = pd.read_csv(p)
    assert list(df.columns) == fx.FIELDS
    loaded = bt.load_bars(df)                       # UTC 14:30 == 09:30 ET (EST, 3 Mar 2025)
    assert len(loaded) == 1 and loaded["end_min"].iloc[0] == 9 * 60 + 35


def test_a_failed_week_does_not_lose_the_rest_and_a_rerun_retries_only_that_week(tmp_path):
    p = tmp_path / "b.csv"
    calls = {"n": 0}

    def flaky(symbol, end_dt):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("pacing violation")
        return [_bar_on(_end(end_dt))]

    logs = []
    n1 = fx.fetch_symbol(flaky, "QQQ", date(2025, 3, 1), date(2025, 3, 22), p, sleep=0, log=logs.append)
    assert n1 == 3 and any("FAILED TimeoutError" in l for l in logs)

    asked = []

    def healthy(symbol, end_dt):
        asked.append(_end(end_dt))
        return [_bar_on(_end(end_dt))]

    again = fx.fetch_symbol(healthy, "QQQ", date(2025, 3, 1), date(2025, 3, 22), p, sleep=0,
                            log=lambda _l: None)
    assert again == 1 and asked == [date(2025, 3, 15)]      # only the week that failed
    assert len(pd.read_csv(p)) == 4


def test_empty_response_is_not_an_error(tmp_path):
    n = fx.fetch_symbol(lambda s, e: [], "SPY", date(2025, 3, 1), date(2025, 3, 8),
                        tmp_path / "b.csv", sleep=0, log=lambda _l: None)
    assert n == 0


def test_weeks_already_saved_are_not_requested_again(tmp_path):
    p = tmp_path / "b.csv"
    calls = []

    def request(symbol, end_dt):
        calls.append((symbol, end_dt))
        return [_bar_on(_end(end_dt))]              # one bar dated inside the requested week

    logs = []
    fx.fetch_symbol(request, "QQQ", date(2025, 3, 1), date(2025, 3, 22), p, sleep=0, log=logs.append)
    first = len(calls)
    assert first >= 3
    fx.fetch_symbol(request, "QQQ", date(2025, 3, 1), date(2025, 3, 22), p, sleep=0, log=logs.append)
    assert len(calls) == first                      # the second run made no requests at all
    assert any("already saved, skipped" in l for l in logs)
    # another symbol is not mistaken for the saved one
    fx.fetch_symbol(request, "SPY", date(2025, 3, 15), date(2025, 3, 22), p, sleep=0, log=lambda _l: None)
    assert any(c[0] == "SPY" for c in calls)


def test_have_week_window_is_the_seven_days_ending_on_the_request_date():
    seen = {("QQQ", "2025-03-16T14:30:00+00:00")}
    assert fx.have_week(seen, "QQQ", date(2025, 3, 22))          # 16th is the first day of 16..22
    assert not fx.have_week(seen, "QQQ", date(2025, 3, 29))      # next week
    assert not fx.have_week(seen, "QQQ", date(2025, 3, 15))      # previous week ends the day before
    assert not fx.have_week(seen, "SPY", date(2025, 3, 22))


def test_progress_is_flushed_for_log_files(capsys):
    fx._say("hello")
    assert "hello" in capsys.readouterr().out
    assert "flush=True" in (SCRIPTS / "intraday_bars_fetch.py").read_text()
