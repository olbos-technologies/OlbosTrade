"""Market breadth: how many stocks are taking part in the market, not just the index.

An index can rise on a few big names while most stocks fall; breadth shows that. This module is
pure arithmetic over a table of daily closes. It fetches nothing, trades nothing and feeds no
execution or risk path: it DESCRIBES the market. Whether any breadth reading improves our trades
is a question for the Edge Lab ledger, not something to assume here.

DEFINITIONS (fixed here so a number always means the same thing)
  breadth   share of the universe whose close is above its own 50-day simple average that day
            (the usual "percent above the 50-day"). A name counts only once it has 50 closes.
  fast      8-day average of breadth: the quick, noisy line.
  slow      200-day average of breadth: the slow background line.
  washout   fast below 0.40: most stocks are below their averages at once. Historically
            stretches like this sit near market lows, but "near" can be weeks early.

HONESTY RULES
  * Turning points (peaks and troughs) are found in HINDSIGHT: a point is only called once
    `window` more bars exist after it, so the newest `window` bars never carry one. They are for
    reading history, never a signal that a top or bottom is happening now.
  * Thin or broken data returns state "unknown", never a healthy-looking number. If too few
    names have a close on the latest day the reading is refused with the reason.
  * The universe is whatever table is passed in. A 60-stock list is a proxy for the market, not
    the S&P 500, and the report says how many names were used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

MA_WINDOW = 50
FAST_WINDOW = 8
SLOW_WINDOW = 200
WASHOUT_BELOW = 0.40
WEAK_BELOW = 0.50
EXTENDED_FROM = 0.70
#: Fewer valid names than this on the latest day and the reading is refused.
MIN_NAMES = 30
#: ...or fewer than this share of the universe having a usable value (a feed that lost half its names).
MIN_COVERAGE = 0.80

WASHOUT, WEAK, HEALTHY, EXTENDED, UNKNOWN = "washout", "weak", "healthy", "extended", "unknown"


def breadth_table(closes: pd.DataFrame, ma_window: int = MA_WINDOW) -> pd.DataFrame:
    """Per-day breadth from a table of closes (rows are dates, columns are symbols).

    Returns columns: `pct_above` (0 to 1, NaN on a day with no valid names), `n_valid` (names
    with a close and a full average that day), `n_universe` (columns passed in).
    A name with no close on a day, or fewer than `ma_window` closes so far, is not counted
    either way: missing data is not "below".
    """
    if not isinstance(closes, pd.DataFrame) or closes.empty:
        return pd.DataFrame(columns=["pct_above", "n_valid", "n_universe"])
    c = closes.sort_index().apply(pd.to_numeric, errors="coerce")
    c = c.where(c > 0)                                   # a zero or negative close is bad data
    ma = c.rolling(ma_window, min_periods=ma_window).mean()
    valid = c.notna() & ma.notna()
    above = (c > ma) & valid
    n_valid = valid.sum(axis=1)
    pct = above.sum(axis=1) / n_valid.where(n_valid > 0)
    return pd.DataFrame({"pct_above": pct, "n_valid": n_valid.astype(int), "n_universe": closes.shape[1]})


def add_lines(table: pd.DataFrame, fast: int = FAST_WINDOW, slow: int = SLOW_WINDOW) -> pd.DataFrame:
    """Adds the `fast` and `slow` averages of breadth. Each is NaN until it has a full window."""
    out = table.copy()
    out["fast"] = out["pct_above"].rolling(fast, min_periods=fast).mean()
    out["slow"] = out["pct_above"].rolling(slow, min_periods=slow).mean()
    return out


@dataclass(frozen=True)
class TurningPoint:
    date: Any
    kind: str          # "peak" or "trough"
    value: float


def turning_points(series: pd.Series, window: int = 20, prominence: float = 0.10) -> list[TurningPoint]:
    """Peaks and troughs of `series`, found in hindsight.

    A point is a peak if it is the highest value within `window` bars either side AND stands at
    least `prominence` above the lower of the two sides' lowest values (so a wiggle on a plateau is
    not a peak). Troughs are the mirror. The last `window` bars are never examined, because the
    bars that would confirm them do not exist yet. A flat tie is reported once, at its first bar.
    """
    s = series.dropna()
    n = len(s)
    out: list[TurningPoint] = []
    if window < 1 or n < 2 * window + 1:
        return out
    vals = s.to_numpy(dtype=float)
    for i in range(window, n - window):
        lo, hi = i - window, i + window + 1
        seg = vals[lo:hi]
        v = vals[i]
        left, right = vals[lo:i], vals[i + 1:hi]
        if v >= seg.max() and v > left.max() and v >= right.max():
            if v - max(left.min(), right.min()) >= prominence:
                out.append(TurningPoint(s.index[i], "peak", float(v)))
        elif v <= seg.min() and v < left.min() and v <= right.min():
            if min(left.max(), right.max()) - v >= prominence:
                out.append(TurningPoint(s.index[i], "trough", float(v)))
    return out


@dataclass(frozen=True)
class BreadthReading:
    state: str
    reasons: list[str] = field(default_factory=list)
    as_of: Any = None
    pct_above: Optional[float] = None
    fast: Optional[float] = None
    slow: Optional[float] = None
    n_valid: int = 0
    n_universe: int = 0


def classify(table: pd.DataFrame) -> BreadthReading:
    """The latest reading in words, or `unknown` with the reason. Description, not a signal."""
    if table is None or table.empty or "fast" not in table:
        return BreadthReading(UNKNOWN, ["no breadth data"])
    last = table.iloc[-1]
    as_of = table.index[-1]
    n_valid, n_uni = int(last["n_valid"]), int(last["n_universe"])
    base = dict(as_of=as_of, n_valid=n_valid, n_universe=n_uni)
    if n_valid < MIN_NAMES:
        return BreadthReading(UNKNOWN, [f"only {n_valid} names have data on {as_of} (need {MIN_NAMES})"], **base)
    if n_uni and n_valid / n_uni < MIN_COVERAGE:
        return BreadthReading(UNKNOWN, [f"only {n_valid} of {n_uni} names have data on {as_of} "
                                        f"(need {MIN_COVERAGE:.0%}): part of the feed is missing"], **base)
    fast = last["fast"]
    pct = last["pct_above"]
    slow = last.get("slow")
    if pd.isna(fast) or pd.isna(pct):
        return BreadthReading(UNKNOWN, ["not enough history for the 8-day line"], **base)
    fast = float(fast)
    nums = dict(pct_above=float(pct), fast=fast, slow=None if slow is None or pd.isna(slow) else float(slow))
    if fast < WASHOUT_BELOW:
        state, why = WASHOUT, f"8-day breadth {fast:.0%} is below {WASHOUT_BELOW:.0%}: most stocks are under their 50-day averages"
    elif fast < WEAK_BELOW:
        state, why = WEAK, f"8-day breadth {fast:.0%} is below {WEAK_BELOW:.0%}: fewer than half of stocks are above their 50-day averages"
    elif fast >= EXTENDED_FROM:
        state, why = EXTENDED, f"8-day breadth {fast:.0%} is at or above {EXTENDED_FROM:.0%}: most stocks are already stretched above their averages"
    else:
        state, why = HEALTHY, f"8-day breadth {fast:.0%}: between {WEAK_BELOW:.0%} and {EXTENDED_FROM:.0%}"
    reasons = [why]
    if nums["slow"] is None:
        reasons.append("200-day line not shown: fewer than 200 days of breadth history")
    return BreadthReading(state, reasons, **base, **nums)


def summarise(closes: pd.DataFrame) -> tuple[pd.DataFrame, BreadthReading]:
    """Everything in one call: the table with both lines, and the latest reading."""
    t = add_lines(breadth_table(closes))
    return t, classify(t)
