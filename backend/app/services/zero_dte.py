"""0DTE debit-spread proposals for the human-in-the-loop 0DTE desk (paper only).

WHAT THIS IS
------------
A pure, broker-free function that turns ONE quoted option chain, a direction the
operator chose, and a risk budget into either a concrete defined-risk debit-spread
proposal (strikes, limit price, contracts, max loss/profit, an expiry for the proposal)
or a list of reasons it refused. It does not fetch quotes, send orders, or decide the
direction. Nothing here can place a trade.

WHY IT IS STRICT
----------------
A 0DTE option loses most of its value within hours, quotes go stale in seconds, and a
bad quote becomes a bad fill. So every check fails CLOSED: a missing, crossed, wide,
stale or implausible quote is a refusal, never a guess.

WHAT IT DOES NOT CLAIM
----------------------
No edge. The existing signal is built from daily bars and says nothing about the next
5-10 minutes; the direction here comes from the operator. A debit spread's worst case is
the premium paid, which is why it is the structure used, not because it is likely to win.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional, Sequence

from app.utils.market_hours import _MARKET_HOLIDAYS, now_et

#: Underlyings with daily expiries and the tightest 0DTE markets. Nothing else is proposed.
ALLOWED_SYMBOLS: tuple[str, ...] = ("SPY", "QQQ")

#: No new entries before this (the open is too wide and noisy) or after this (too little
#: time left for a 5-10 minute trade plus a manual exit before the close).
ENTRY_OPEN = time(10, 0)
ENTRY_CLOSE = time(14, 30)

#: Full-day 13:00 ET early closes. The market-hours helper does not model early closes, and a
#: 0DTE position on such a day would expire three hours sooner than the desk assumes, so these
#: days are refused outright. Fails closed for any day not listed here: extend each year.
EARLY_CLOSE_DAYS: frozenset[str] = frozenset({"2026-11-27", "2026-12-24", "2027-11-26"})

#: How long a proposal may be acted on. Approving it after this must re-quote, not reuse it.
PROPOSAL_TTL_S = 120

#: A chain older than this at the moment of proposing is not a live quote.
MAX_CHAIN_AGE_S = 20.0

#: Spread widths tried, in order of preference, in dollars of strike distance.
WIDTHS: tuple[float, ...] = (2.0, 1.0, 3.0, 5.0)

#: A leg whose ask-bid exceeds max(MAX_LEG_SPREAD_ABS, MAX_LEG_SPREAD_REL * mid) is too wide to trade.
MAX_LEG_SPREAD_ABS = 0.05
MAX_LEG_SPREAD_REL = 0.10

#: Net debit as a share of the strike width. Near-the-money debit spreads cost about half the
#: width; far outside this band the quote is stale/crossed (too cheap) or there is no reward left.
MIN_DEBIT_TO_WIDTH = 0.15
MAX_DEBIT_TO_WIDTH = 0.80

#: A long leg priced further below intrinsic value than this is an inconsistent (stale) quote.
INTRINSIC_TOLERANCE = 0.05

#: The nearest listed strike must be this close to spot (fraction) or the chain window is wrong.
MAX_STRIKE_DISTANCE_PCT = 0.01

#: Minutes that must remain before the close to open a position.
MIN_MINUTES_TO_CLOSE = 90.0

MAX_CONTRACTS = 10
CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class ZeroDteProposal:
    symbol: str
    direction: str                 # "bullish" | "bearish"
    option_type: str               # "call" | "put"
    expiration: date
    long_strike: float             # bought
    short_strike: float            # sold
    width: float
    limit_debit: float             # per share, the mid, rounded to a cent: what the order should rest at
    natural_debit: float           # per share, long ask - short bid: the worst plausible fill
    contracts: int
    max_loss: float                # dollars, at the natural (worst) debit, all contracts
    max_profit: float              # dollars, at the limit debit, all contracts
    breakeven: float               # underlying price at expiry, at the limit debit
    reward_to_risk: float          # at the limit debit
    spot: float
    quoted_at: datetime
    expires_at: datetime
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ZeroDteResult:
    proposal: Optional[ZeroDteProposal]
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.proposal is not None


def _f(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def entry_window_problem(now: Optional[datetime] = None) -> Optional[str]:
    """None when a 0DTE entry may be proposed now, else why not (US Eastern, regular hours only)."""
    et = now_et(now)
    day = et.strftime("%Y-%m-%d")
    if et.weekday() >= 5:
        return "weekend"
    if day in _MARKET_HOLIDAYS:
        return "market holiday"
    if day in EARLY_CLOSE_DAYS:
        return "early-close day: 0DTE entries are not proposed"
    if et.time() < ENTRY_OPEN:
        return f"before {ENTRY_OPEN:%H:%M} ET"
    if et.time() >= ENTRY_CLOSE:
        return f"after {ENTRY_CLOSE:%H:%M} ET"
    close = et.replace(hour=16, minute=0, second=0, microsecond=0)
    if (close - et).total_seconds() / 60.0 < MIN_MINUTES_TO_CLOSE:
        return "too close to the close"
    return None


def zero_dte_expiry(now: Optional[datetime] = None) -> date:
    """Today's date in US Eastern: the expiry a 0DTE proposal must use."""
    return now_et(now).date()


def _leg_problems(label: str, c: Any) -> list[str]:
    bid, ask = _f(getattr(c, "bid", 0)), _f(getattr(c, "ask", 0))
    if bid <= 0 or ask <= 0:
        return [f"{label} leg has no two-sided quote (bid {bid:g}, ask {ask:g})"]
    if ask < bid:
        return [f"{label} leg quote is crossed (bid {bid:g} > ask {ask:g})"]
    mid = (bid + ask) / 2.0
    if ask - bid > max(MAX_LEG_SPREAD_ABS, MAX_LEG_SPREAD_REL * mid) + 1e-9:
        return [f"{label} leg bid/ask is too wide ({bid:g} / {ask:g})"]
    return []


def _by_strike(contracts: Sequence[Any]) -> dict[float, Any]:
    return {round(_f(getattr(c, "strike", 0)), 4): c for c in contracts}


def propose_debit_spread(
    chain: Any,
    *,
    symbol: str,
    direction: str,
    risk_budget: float,
    now: Optional[datetime] = None,
    widths: Sequence[float] = WIDTHS,
) -> ZeroDteResult:
    """Build a 0DTE vertical debit spread from `chain`, or say why not.

    bullish -> buy a call near the money, sell the call `width` above (bull call spread).
    bearish -> buy a put near the money, sell the put `width` below (bear put spread).

    `risk_budget` is the most the operator's risk rules allow on this trade, in dollars; the
    contract count is sized on the worst plausible fill so the budget holds even if the order
    is filled at the natural price instead of the mid.
    """
    sym = (symbol or "").upper()
    reasons: list[str] = []
    if sym not in ALLOWED_SYMBOLS:
        return ZeroDteResult(None, (f"{sym or '(none)'} is not enabled for 0DTE (only {', '.join(ALLOWED_SYMBOLS)})",))
    if direction not in ("bullish", "bearish"):
        return ZeroDteResult(None, ("direction must be 'bullish' or 'bearish'",))
    if not risk_budget or risk_budget <= 0:
        return ZeroDteResult(None, ("no risk budget available",))

    window = entry_window_problem(now)
    if window:
        return ZeroDteResult(None, (f"outside the 0DTE entry window: {window}",))

    now_utc = (now or datetime.now(timezone.utc))
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    today = zero_dte_expiry(now_utc)

    if getattr(chain, "expiration", None) != today:
        return ZeroDteResult(None, (f"chain expiry {getattr(chain, 'expiration', None)} is not today ({today})",))
    fetched = getattr(chain, "fetched_at", None)
    if fetched is None:
        return ZeroDteResult(None, ("chain has no quote time",))
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = (now_utc - fetched).total_seconds()
    if age > MAX_CHAIN_AGE_S:
        return ZeroDteResult(None, (f"quotes are {age:.0f}s old (max {MAX_CHAIN_AGE_S:.0f}s)",))
    if age < -5:
        return ZeroDteResult(None, ("quote time is in the future",))

    spot = _f(getattr(chain, "underlying_price", 0))
    if spot <= 0:
        return ZeroDteResult(None, ("no underlying price",))

    option_type = "call" if direction == "bullish" else "put"
    side = _by_strike(chain.calls if option_type == "call" else chain.puts)
    if not side:
        return ZeroDteResult(None, (f"chain has no {option_type}s",))

    long_strike = min(side, key=lambda k: abs(k - spot))
    if abs(long_strike - spot) > MAX_STRIKE_DISTANCE_PCT * spot:
        return ZeroDteResult(None, (f"no strike within {MAX_STRIKE_DISTANCE_PCT:.0%} of spot {spot:g}",))

    long_leg = side[long_strike]
    long_problems = _leg_problems("long", long_leg)
    if long_problems:
        return ZeroDteResult(None, tuple(long_problems))
    long_bid, long_ask = _f(long_leg.bid), _f(long_leg.ask)
    long_mid = (long_bid + long_ask) / 2.0
    intrinsic = max(spot - long_strike, 0.0) if option_type == "call" else max(long_strike - spot, 0.0)
    if long_mid < intrinsic - INTRINSIC_TOLERANCE:
        return ZeroDteResult(None, (
            f"long leg mid {long_mid:g} is below intrinsic value {intrinsic:g}: stale or inconsistent quote",))

    for width in widths:
        short_strike = round(long_strike + width, 4) if option_type == "call" else round(long_strike - width, 4)
        short_leg = side.get(short_strike)
        tag = f"${width:g} wide"
        if short_leg is None:
            reasons.append(f"{tag}: no {short_strike:g} {option_type} in the chain")
            continue
        problems = _leg_problems("short", short_leg)
        if problems:
            reasons.extend(f"{tag}: {p}" for p in problems)
            continue
        short_bid, short_ask = _f(short_leg.bid), _f(short_leg.ask)
        short_mid = (short_bid + short_ask) / 2.0

        mid_debit = long_mid - short_mid
        natural_debit = long_ask - short_bid
        if mid_debit <= 0:
            reasons.append(f"{tag}: short leg is priced at or above the long leg")
            continue
        ratio = mid_debit / width
        if ratio < MIN_DEBIT_TO_WIDTH:
            reasons.append(f"{tag}: debit {mid_debit:.2f} is only {ratio:.0%} of the width: implausibly cheap, likely a stale quote")
            continue
        if ratio > MAX_DEBIT_TO_WIDTH or natural_debit >= width:
            reasons.append(f"{tag}: debit {mid_debit:.2f} is {ratio:.0%} of the width: too little reward left")
            continue

        limit_debit = round(mid_debit, 2)
        if limit_debit <= 0:
            reasons.append(f"{tag}: limit price rounds to zero")
            continue
        per_contract_worst = natural_debit * CONTRACT_MULTIPLIER
        contracts = min(int(risk_budget // per_contract_worst), MAX_CONTRACTS)
        if contracts < 1:
            reasons.append(f"{tag}: one contract risks ${per_contract_worst:.0f}, over the ${risk_budget:.0f} budget")
            continue

        max_loss = round(natural_debit * CONTRACT_MULTIPLIER * contracts, 2)
        max_profit = round((width - limit_debit) * CONTRACT_MULTIPLIER * contracts, 2)
        breakeven = long_strike + limit_debit if option_type == "call" else long_strike - limit_debit
        warnings: list[str] = []
        if natural_debit - limit_debit > 0.05:
            warnings.append("natural price is more than 5c above the limit: a resting limit order may not fill")
        return ZeroDteResult(ZeroDteProposal(
            symbol=sym, direction=direction, option_type=option_type, expiration=today,
            long_strike=long_strike, short_strike=short_strike, width=float(width),
            limit_debit=limit_debit, natural_debit=round(natural_debit, 4), contracts=contracts,
            max_loss=max_loss, max_profit=max_profit, breakeven=round(breakeven, 4),
            reward_to_risk=round((width - limit_debit) / limit_debit, 3),
            spot=spot, quoted_at=fetched, expires_at=now_utc + timedelta(seconds=PROPOSAL_TTL_S),
            warnings=tuple(warnings),
        ))

    return ZeroDteResult(None, tuple(reasons) or ("no usable spread in the chain",))
