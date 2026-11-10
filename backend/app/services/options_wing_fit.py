"""Size-aware wing width for credit spreads.

THE PROBLEM THIS SOLVES
-----------------------
The options strategies place the protective leg a fixed $10 from the short strike
(`short_strike -/+ 10`). Aggressive risks about 2% of the options pool per trade, so on
a small account one contract of a $10 wing can lose more than the whole per-trade risk
budget (measured 2026-11-10: budget about $428, typical spread max loss about $768). The
position sizer then rounds to ZERO contracts and the candidate is thrown away: 1,479 of
2,082 scan rejections that day were "sized to 0 contracts".

WHAT IT DOES
------------
When, and only when, the usual wing would size to zero, look for a NARROWER wing on the
SAME short strike, from the option chain the scan has already fetched (no extra broker
request), and take the widest one whose max loss fits the budget. It changes how much is
risked per contract, never how much may be risked per trade: the budget is the same cap
the sizer already enforced, and every later gate (AI scorer, equity-signal conflict,
frequency controller, guardrails, position cap, market hours) is untouched.

WHAT IT IS NOT
--------------
Not a loosening of any limit, and not a promise of trades: a narrower wing pays a smaller
credit, so its probability of profit is a little lower and its reward-to-risk is low, and
the controller's own floors still apply to it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

#: Wing widths tried, widest first, only those narrower than the width already quoted.
CANDIDATE_WIDTHS: tuple[float, ...] = (10.0, 5.0, 2.5, 1.0)

#: Credit per share must be at least this to count as a real quote (the scan's own
#: "no usable spread" threshold is dollars-per-contract > 0.05; this is the same floor).
MIN_CREDIT_PS = 0.0005

#: Sanity floor on credit / width for a narrowed wing. Not a policy gate: it only stops a
#: stale or crossed quote from passing as a spread. Narrow wings naturally pay about the
#: short strike's delta (~0.15-0.20) of their width, so this sits well below that.
MIN_CREDIT_TO_WIDTH = 0.10

_EPS = 1e-6


def mid_price(contract: Any) -> Optional[float]:
    """Bid/ask mid, else last, else None. Same rule the live-quote path uses."""
    bid = float(getattr(contract, "bid", 0) or 0)
    ask = float(getattr(contract, "ask", 0) or 0)
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2.0
    last = float(getattr(contract, "last", 0) or 0)
    return last if last > 0 else None


@dataclass(frozen=True)
class WingChoice:
    long_leg: Any            # the chain contract chosen for the protective leg
    width: float
    net_credit_ps: float     # per share
    max_loss: float          # dollars per contract


def fits_budget(max_loss: float, budget: float) -> bool:
    """True when at least one contract fits. Mirrors RiskManager.calculate_position_size:
    int(budget / max(max_loss, 1.0)) >= 1."""
    return int(budget / max(max_loss, 1.0)) >= 1


def choose_wing(
    legs: Sequence[Any],
    short_strike: float,
    opt_type: str,
    risk_budget: float,
    *,
    default_width: float,
    short_mid: float,
    widths: Sequence[float] = CANDIDATE_WIDTHS,
    min_credit_to_width: float = MIN_CREDIT_TO_WIDTH,
) -> Optional[WingChoice]:
    """The widest narrower wing on `short_strike` whose max loss fits `risk_budget`, or None.

    `legs` is the chain's list of contracts for this option type; `short_mid` the short leg's
    mid; `default_width` the width already quoted (only strictly narrower wings are tried).
    A candidate must be an exact listed strike, have a usable mid, pay a real credit that is
    at least `min_credit_to_width` of the width, and leave a max loss inside the budget.
    Puts take the long leg below the short strike; calls, above it."""
    if risk_budget <= 0 or short_mid <= 0:
        return None
    sign = -1.0 if opt_type == "put" else 1.0
    by_strike = {round(float(c.strike), 4): c for c in legs}
    for width in sorted((w for w in widths if w < default_width - _EPS), reverse=True):
        leg = by_strike.get(round(short_strike + sign * width, 4))
        if leg is None:
            continue
        long_mid = mid_price(leg)
        if long_mid is None:
            continue
        credit_ps = short_mid - long_mid
        if credit_ps <= MIN_CREDIT_PS or credit_ps / width < min_credit_to_width:
            continue
        max_loss = round((width - credit_ps) * 100, 2)
        if fits_budget(max_loss, risk_budget):
            return WingChoice(long_leg=leg, width=width, net_credit_ps=credit_ps, max_loss=max_loss)
    return None
