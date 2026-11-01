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


def test_rerunning_adds_nothing_and_a_failed_week_does_not_lose_the_rest(tmp_path):
    p = tmp_path / "b.csv"
    calls = {"n": 0}

    def request(symbol, end_dt):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("pacing violation")
        return [_bar(14, 30 + 5 * calls["n"], day=3 + calls["n"])]

    logs = []
    n1 = fx.fetch_symbol(request, "QQQ", date(2025, 3, 1), date(2025, 3, 22), p,
                         sleep=0, log=logs.append)
    assert n1 == 3 and any("FAILED TimeoutError" in l for l in logs)

    calls["n"] = 99                                  # now every call succeeds with the same bars
    again = fx.fetch_symbol(lambda s, e: [_bar(14, 35, day=4)], "QQQ",
                            date(2025, 3, 1), date(2025, 3, 22), p, sleep=0, log=lambda _l: None)
    assert again == 0                                # duplicates are skipped, not appended
    assert len(pd.read_csv(p)) == 3


def test_empty_response_is_not_an_error(tmp_path):
    n = fx.fetch_symbol(lambda s, e: [], "SPY", date(2025, 3, 1), date(2025, 3, 8),
                        tmp_path / "b.csv", sleep=0, log=lambda _l: None)
    assert n == 0
