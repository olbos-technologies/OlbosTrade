"""
Market-hours gating tests.

  - is_market_open boundaries (open/close edges, weekend, holiday, pre/after).
  - _execute_signal blocks order submission when the market is closed, regardless
    of execution path.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.utils.market_hours import is_market_open, market_status, minutes_to_close

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("dt,expected,reason", [
    (datetime(2026, 6, 24, 11, 0, tzinfo=ET),  True,  "open"),        # Wed midday
    (datetime(2026, 6, 24, 9, 30, tzinfo=ET),  True,  "open"),        # exactly open
    (datetime(2026, 6, 24, 15, 59, tzinfo=ET), True,  "open"),        # just before close
    (datetime(2026, 6, 24, 16, 0, tzinfo=ET),  False, "after_hours"), # exactly close
    (datetime(2026, 6, 24, 9, 0, tzinfo=ET),   False, "pre_market"),
    (datetime(2026, 6, 27, 11, 0, tzinfo=ET),  False, "weekend"),     # Saturday
    (datetime(2026, 7, 3, 11, 0, tzinfo=ET),   False, "holiday"),     # Independence Day (obs)
])
def test_is_market_open_boundaries(dt, expected, reason):
    assert is_market_open(dt) is expected
    assert market_status(dt)["reason"] == reason


@pytest.mark.parametrize("dt,expected", [
    (datetime(2026, 6, 24, 9, 30, tzinfo=ET),  390.0),  # exactly open
    (datetime(2026, 6, 24, 15, 45, tzinfo=ET), 15.0),   # 15 min left
    (datetime(2026, 6, 24, 15, 59, tzinfo=ET), 1.0),    # just before close
    (datetime(2026, 6, 24, 16, 0, tzinfo=ET),  None),   # exactly close — market closed
    (datetime(2026, 6, 24, 9, 0, tzinfo=ET),   None),   # pre-market
    (datetime(2026, 6, 27, 11, 0, tzinfo=ET),  None),   # weekend
])
def test_minutes_to_close_boundaries(dt, expected):
    result = minutes_to_close(dt)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.asyncio
async def test_execute_signal_blocked_when_market_closed():
    """No order is submitted outside RTH; result is blocked with market_closed."""
    from app.api.routes import trade_desk
    from app.api.routes.trade_desk import _execute_signal

    signal = {
        "id": "sig-mh", "ticker": "AAPL", "action": "BUY", "asset_type": "equity",
        "trade_plan": {"shares": 10, "entry_price": 150.0},
    }
    # Ensure kill switch is off so we're testing the market gate specifically.
    trade_desk._kill_switch.clear()
    with patch("app.utils.market_hours.is_market_open", return_value=False):
        result = await _execute_signal(signal, approved_by="autopilot")

    assert result["result"] == "blocked"
    assert "market_closed" in result["reason"]


# ── market_day_bounds: the Eastern calendar day, in UTC ──────────────────────

from datetime import timezone  # noqa: E402

from app.utils.market_hours import market_day_bounds  # noqa: E402

UTC = timezone.utc


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_the_evening_after_the_utc_rollover_is_still_the_same_market_day():
    """The reported case: 22:21 ET on 2026-11-11 is 03:21 UTC on 2026-11-12, but still market day 11-11."""
    start, end = market_day_bounds(_utc(2026, 11, 12, 3, 21))
    assert start == _utc(2026, 11, 11, 5, 0) and end == _utc(2026, 11, 12, 5, 0)     # EST: ET midnight is 05:00 UTC


def test_just_after_eastern_midnight_starts_the_next_market_day():
    start, end = market_day_bounds(_utc(2026, 11, 12, 5, 0))       # exactly 00:00 ET on 11-12
    assert start == _utc(2026, 11, 12, 5, 0) and end == _utc(2026, 11, 13, 5, 0)
    start, _ = market_day_bounds(_utc(2026, 11, 12, 4, 59))        # one minute earlier is still 11-11
    assert start == _utc(2026, 11, 11, 5, 0)


def test_during_daylight_time_eastern_midnight_is_04_utc():
    start, end = market_day_bounds(_utc(2026, 7, 15, 3, 0))        # 23:00 ET on 07-14
    assert start == _utc(2026, 7, 14, 4, 0) and end == _utc(2026, 7, 15, 4, 0)


@pytest.mark.parametrize("now,hours", [
    (_utc(2026, 3, 8, 12, 0), 23),     # spring forward: the Eastern day is 23 hours long
    (_utc(2026, 11, 1, 12, 0), 25),    # fall back: 25 hours
    (_utc(2026, 6, 10, 12, 0), 24),
    (_utc(2026, 12, 10, 12, 0), 24),
])
def test_a_day_is_24_hours_except_on_the_two_daylight_saving_changes(now, hours):
    start, end = market_day_bounds(now)
    assert (end - start).total_seconds() / 3600 == hours
    assert start <= now < end


def test_a_naive_time_is_read_as_eastern_like_the_rest_of_the_module():
    start, end = market_day_bounds(datetime(2026, 11, 11, 22, 21))     # 22:21 ET, no tzinfo
    assert start == _utc(2026, 11, 11, 5, 0) and end == _utc(2026, 11, 12, 5, 0)


def test_the_bounds_are_timezone_aware_utc():
    start, end = market_day_bounds(_utc(2026, 11, 11, 15, 0))
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start.utcoffset().total_seconds() == 0 and end.utcoffset().total_seconds() == 0
