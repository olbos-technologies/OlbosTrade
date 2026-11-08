"""The chart page's interval + range controls, and what Yahoo is asked for.

Yahoo keeps limited history at fine intervals, so a wide range at a small interval
must be CLAMPED and reported, never silently pretended. And the older behaviour,
used by the Equity/Options desk charts, must not change.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import HTTPException

from app.services.chart_request import (
    DEFAULT_RANGE, INTERVALS, LEGACY_PERIOD, MAX_PERIOD, RANGE_DEFAULT_INTERVAL, RANGE_PERIOD,
    plan_chart_request, resample_4h,
)

TODAY = date(2026, 11, 8)


# ── legacy behaviour is untouched ──────────────────────────────────────────

@pytest.mark.parametrize("tf, period, interval", [
    ("5m", "5d", "5m"), ("15m", "10d", "15m"), ("1h", "60d", "60m"), ("1d", "1y", "1d"),
])
def test_without_a_range_the_old_four_intervals_ask_for_exactly_what_they_did(tf, period, interval):
    plan = plan_chart_request(tf, None, TODAY)
    assert (plan.period, plan.interval, plan.range, plan.clamped) == (period, interval, None, False)
    assert LEGACY_PERIOD[tf] == period


@pytest.mark.parametrize("tf", ["1m", "30m", "4h", "1w", "1M"])
def test_a_new_interval_without_a_range_gets_a_sensible_default(tf):
    plan = plan_chart_request(tf, None, TODAY)
    assert plan.period == RANGE_PERIOD[DEFAULT_RANGE[tf]] or plan.period == MAX_PERIOD.get(tf)
    assert plan.range is None and plan.clamped is False


# ── clamping ───────────────────────────────────────────────────────────────

def test_a_range_wider_than_the_interval_has_is_clamped_and_says_so():
    plan = plan_chart_request("1m", "1y", TODAY)
    assert (plan.period, plan.clamped, plan.range) == ("7d", True, "1y")
    plan = plan_chart_request("15m", "5y", TODAY)
    assert (plan.period, plan.clamped) == ("60d", True)
    plan = plan_chart_request("1h", "all", TODAY)
    assert (plan.period, plan.clamped) == ("730d", True)


def test_a_range_the_interval_can_cover_is_not_clamped():
    assert plan_chart_request("1m", "1d", TODAY).clamped is False
    assert plan_chart_request("1m", "5d", TODAY).clamped is False
    assert plan_chart_request("5m", "1m", TODAY).clamped is False
    assert plan_chart_request("1h", "1y", TODAY).clamped is False
    # daily and wider bars have no cap
    for tf in ("1d", "1w", "1M"):
        plan = plan_chart_request(tf, "all", TODAY)
        assert (plan.period, plan.clamped) == ("max", False)


def test_ytd_is_compared_by_its_real_length():
    # early January: YTD is a few days, so a 1-minute chart covers it
    assert plan_chart_request("1m", "ytd", date(2026, 1, 4)).clamped is False
    # November: ~312 days, far more than a week of 1-minute bars
    assert plan_chart_request("1m", "ytd", date(2026, 11, 8)).clamped is True


def test_every_range_and_interval_is_known_and_has_a_default_pairing():
    assert set(RANGE_DEFAULT_INTERVAL) == set(RANGE_PERIOD)
    for rng, tf in RANGE_DEFAULT_INTERVAL.items():
        assert tf in INTERVALS, rng
        # the interval a range picks for itself must be able to show that range
        assert plan_chart_request(tf, rng, TODAY).clamped is False, (rng, tf)
    assert len(INTERVALS) == 9 and len(RANGE_PERIOD) == 9


def test_unknown_values_are_refused_not_guessed():
    with pytest.raises(KeyError):
        plan_chart_request("2m", None, TODAY)
    with pytest.raises(KeyError):
        plan_chart_request("1d", "10y", TODAY)


def test_four_hour_bars_are_flagged_for_resampling_from_hourly():
    plan = plan_chart_request("4h", "1m", TODAY)
    assert (plan.interval, plan.resample_4h) == ("60m", True)
    assert plan_chart_request("1h", "1m", TODAY).resample_4h is False


# ── 4-hour resampling ──────────────────────────────────────────────────────

def _hourly():
    idx = pd.date_range("2026-11-02 09:30", periods=7, freq="60min", tz="America/New_York")
    return pd.DataFrame({
        "Open": [10, 11, 12, 13, 14, 15, 16], "High": [11, 12, 13, 14, 15, 16, 17],
        "Low": [9, 10, 11, 12, 13, 14, 15], "Close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5],
        "Volume": [100] * 7,
    }, index=idx)


def test_hourly_bars_become_four_hour_bars_anchored_on_the_open():
    out = resample_4h(_hourly())
    assert len(out) == 2
    first, second = out.iloc[0], out.iloc[1]
    assert out.index[0].strftime("%H:%M") == "09:30" and out.index[1].strftime("%H:%M") == "13:30"
    assert (first.Open, first.High, first.Low, first.Close, first.Volume) == (10, 14, 9, 13.5, 400)
    assert (second.Open, second.High, second.Low, second.Close, second.Volume) == (14, 17, 13, 16.5, 300)


# ── the route ──────────────────────────────────────────────────────────────

class _FakeTicker:
    calls: list = []

    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, period, interval, auto_adjust):
        _FakeTicker.calls.append((self.symbol, period, interval))
        idx = pd.date_range("2026-11-02 09:30", periods=30, freq="60min", tz="America/New_York")
        return pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10}, index=idx)


async def _route(**kw):
    from app.api.routes import equity
    args = dict(ticker="aapl", timeframe="15m", limit=96, range_=None)
    args.update(kw)
    with patch("yfinance.Ticker", _FakeTicker):
        return await equity.get_equity_chart(**args)


@pytest.mark.asyncio
async def test_the_route_without_a_range_is_unchanged_and_reports_no_clamp():
    _FakeTicker.calls = []
    out = await _route()
    assert _FakeTicker.calls == [("AAPL", "10d", "15m")]
    assert out["range"] is None and out["range_clamped"] is False
    assert out["timeframe"] == "15m" and len(out["bars"]) == 30
    assert {"summary", "bars", "ticker"} <= set(out)


@pytest.mark.asyncio
async def test_the_route_reports_a_clamped_range():
    _FakeTicker.calls = []
    out = await _route(timeframe="1m", range_="1y", limit=5000)
    assert _FakeTicker.calls == [("AAPL", "7d", "1m")]
    assert (out["range"], out["range_clamped"], out["period"]) == ("1y", True, "7d")


@pytest.mark.asyncio
async def test_the_route_builds_four_hour_bars_from_hourly():
    _FakeTicker.calls = []
    out = await _route(timeframe="4h", range_="1m")
    assert _FakeTicker.calls == [("AAPL", "1mo", "60m")]
    assert 0 < len(out["bars"]) < 30                       # fewer than the 30 hourly bars


@pytest.mark.asyncio
async def test_the_limit_still_caps_the_number_of_bars():
    out = await _route(limit=20)
    assert len(out["bars"]) == 20


@pytest.mark.asyncio
async def test_an_empty_answer_is_a_404_not_an_empty_chart():
    class Empty(_FakeTicker):
        def history(self, period, interval, auto_adjust):
            return pd.DataFrame({"Open": [], "High": [], "Low": [], "Close": [], "Volume": []})
    from app.api.routes import equity
    with patch("yfinance.Ticker", Empty), pytest.raises(HTTPException) as err:
        await equity.get_equity_chart(ticker="zzz", timeframe="1d", limit=96, range_=None)
    assert err.value.status_code == 404


def test_the_route_validates_its_parameters():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    bad = client.get("/api/equity/chart/AAPL?timeframe=2m")
    assert bad.status_code in (401, 403, 422)
    r2 = client.get("/api/equity/chart/AAPL?timeframe=1d&range=10y")
    assert r2.status_code in (401, 403, 422)
