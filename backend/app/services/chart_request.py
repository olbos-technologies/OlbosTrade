"""What to ask Yahoo for when the chart page picks an interval and a range.

The chart page has two independent controls, like TradingView: the BAR INTERVAL
(1m ... 1M) and the RANGE of history shown (1D ... All). Yahoo only keeps so much
history at fine intervals (1-minute bars: about a week, 5/15/30-minute: about 60
days, hourly: about two years), so a range wider than the interval allows is
CLAMPED to what exists and the response says so; the UI must never imply it is
showing five years of one-minute bars.

Pure functions: nothing here fetches or reads the clock, so each rule is testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

#: UI interval -> yfinance interval. 4h is not a Yahoo interval: it is built by
#: resampling hourly bars (see `resample_4h`).
INTERVALS: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "60m", "4h": "60m",
    "1d": "1d", "1w": "1wk", "1M": "1mo",
}

#: Longest history Yahoo returns per interval, as a yfinance period.
MAX_PERIOD: dict[str, str] = {
    "1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d", "4h": "730d",
}

#: UI range -> yfinance period.
RANGE_PERIOD: dict[str, str] = {
    "1d": "1d", "5d": "5d", "1m": "1mo", "3m": "3mo", "6m": "6mo",
    "ytd": "ytd", "1y": "1y", "5y": "5y", "all": "max",
}

_PERIOD_DAYS: dict[str, int] = {
    "1d": 1, "5d": 5, "7d": 7, "1mo": 30, "3mo": 90, "6mo": 180, "60d": 60,
    "1y": 365, "730d": 730, "5y": 1825, "max": 10**6,
}

#: The ranges and intervals before this existed, kept exactly so older callers
#: (the Equity and Options desk charts) get the same bars as before.
LEGACY_PERIOD: dict[str, str] = {"5m": "5d", "15m": "10d", "1h": "60d", "1d": "1y"}

#: When a new interval is asked for without a range.
DEFAULT_RANGE: dict[str, str] = {
    "1m": "1d", "30m": "1m", "4h": "6m", "1w": "5y", "1M": "all",
}

#: Picking a range also picks a sensible bar size for it, as TradingView does.
RANGE_DEFAULT_INTERVAL: dict[str, str] = {
    "1d": "5m", "5d": "15m", "1m": "1h", "3m": "1d", "6m": "1d",
    "ytd": "1d", "1y": "1d", "5y": "1w", "all": "1M",
}


@dataclass(frozen=True)
class ChartPlan:
    interval: str            # yfinance interval
    period: str              # yfinance period actually requested
    resample_4h: bool
    range: Optional[str]     # the UI range requested, if any
    clamped: bool            # the range was cut down to what this interval has


def _days(period: str, today: date) -> int:
    if period == "ytd":
        return max(1, (today - date(today.year, 1, 1)).days + 1)
    return _PERIOD_DAYS[period]


def plan_chart_request(timeframe: str, range_: Optional[str], today: date) -> ChartPlan:
    """Interval + optional range -> what to fetch. Raises KeyError on an unknown value."""
    interval = INTERVALS[timeframe]
    if range_ is None:
        period = LEGACY_PERIOD.get(timeframe) or RANGE_PERIOD[DEFAULT_RANGE[timeframe]]
        return ChartPlan(interval, period, timeframe == "4h", None, False)

    period = RANGE_PERIOD[range_]
    cap = MAX_PERIOD.get(timeframe)
    clamped = False
    if cap is not None and _days(period, today) > _days(cap, today):
        period, clamped = cap, True
    return ChartPlan(interval, period, timeframe == "4h", range_, clamped)


def resample_4h(hist):
    """Hourly bars -> 4-hour bars, anchored on the 09:30 open so the first bar of
    a session is 09:30-13:30 and the second 13:30 to the close."""
    out = hist.resample("4h", offset="90min").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    return out.dropna(subset=["Open", "Close"])
