"""
Daily screener metrics for one symbol, from daily OHLCV bars.

Pure functions, no I/O, so every definition is pinned by tests. All figures are
for the LAST COMPLETED SESSION: a bar for today is dropped until the regular
session has closed, because a half-formed day would make volume, range and
relative volume look small (see `completed_sessions`).

Definitions (the page shows the same wording):
  last          close of the session
  change_pct    close vs the previous close, percent
  gap_pct       open vs the previous close, percent
  gap_atr       (open - previous close) / ATR14 as it stood BEFORE the session,
                so the gap is measured against what was known at the open
  atr14_pct     ATR14 including the session, as a percent of the close
  adv20         average daily volume over the 20 sessions ending with this one
  dollar_vol20  average of close x volume over those 20 sessions
  rvol          session volume / average volume of the 20 sessions BEFORE it
  range_pos     where the close sits in the session's high-low range, 0 to 1
                (null when the range is zero)
ATR here is the simple 14-session mean of true range, not Wilder's smoothing.
These describe what traded. They are not signals.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
# Daily bars are final some minutes after the 16:00 close; wait a little past it.
SESSION_FINAL_ET = time(16, 15)
MIN_SESSIONS = 21  # 20 earlier sessions for the volume baseline, plus the session itself
ATR_LEN = 14
VOL_LEN = 20


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def clean_bars(bars: list[dict]) -> list[dict]:
    """Bars with usable prices, sorted by date, one per date (the last one wins)."""
    by_date: dict[str, dict] = {}
    for b in bars or []:
        d = b.get("date")
        o, h, l, c = (_num(b.get(k)) for k in ("open", "high", "low", "close"))
        if not isinstance(d, str) or None in (o, h, l, c):
            continue
        if min(o, h, l, c) <= 0 or h < l:
            continue
        v = _num(b.get("volume"))
        by_date[d] = {"date": d, "open": o, "high": h, "low": l, "close": c,
                      "volume": v if v is not None and v >= 0 else 0.0}
    return [by_date[k] for k in sorted(by_date)]


def completed_sessions(bars: list[dict], now: datetime) -> list[dict]:
    """Drop today's bar while today's regular session is not yet final (ET)."""
    if not bars:
        return bars
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_et = now.astimezone(ET)
    if bars[-1]["date"] == now_et.date().isoformat() and now_et.time() < SESSION_FINAL_ET:
        return bars[:-1]
    return bars


def _true_ranges(bars: list[dict]) -> list[float]:
    """True range for bars[1:], each against the bar before it."""
    out = []
    for prev, b in zip(bars, bars[1:]):
        out.append(max(b["high"] - b["low"], abs(b["high"] - prev["close"]), abs(b["low"] - prev["close"])))
    return out


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def session_metrics(bars: list[dict]) -> Optional[dict]:
    """Metrics for the last bar of `bars` (already cleaned and completed). None if too short."""
    if len(bars) < MIN_SESSIONS:
        return None
    s, p = bars[-1], bars[-2]
    tr = _true_ranges(bars)                 # tr[i] belongs to bars[i + 1]
    atr_now = _mean(tr[-ATR_LEN:])          # includes the session
    atr_prior = _mean(tr[-ATR_LEN - 1:-1])  # ends with the previous session
    last20 = bars[-VOL_LEN:]
    prior20 = bars[-VOL_LEN - 1:-1]
    adv20 = _mean([b["volume"] for b in last20])
    base_vol = _mean([b["volume"] for b in prior20])
    dollar20 = _mean([b["close"] * b["volume"] for b in last20])
    rng = s["high"] - s["low"]

    def r(x, n=4):
        return None if x is None else round(x, n)

    return {
        "session_date": s["date"],
        "last": r(s["close"]),
        "open": r(s["open"]),
        "high": r(s["high"]),
        "low": r(s["low"]),
        "prev_close": r(p["close"]),
        "volume": int(s["volume"]),
        "change_pct": r((s["close"] / p["close"] - 1) * 100),
        "gap_pct": r((s["open"] / p["close"] - 1) * 100),
        "gap_atr": r((s["open"] - p["close"]) / atr_prior) if atr_prior else None,
        "atr14": r(atr_now),
        "atr14_pct": r(atr_now / s["close"] * 100) if atr_now is not None else None,
        "adv20": r(adv20, 0),
        "dollar_vol20": r(dollar20, 0),
        "rvol": r(s["volume"] / base_vol) if base_vol else None,
        "range_pos": r((s["close"] - s["low"]) / rng) if rng > 0 else None,
    }
