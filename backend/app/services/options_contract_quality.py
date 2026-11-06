"""Experimental spread-quality evidence, never probability or order authority.

Liquidity and strategy delta fit only. Gamma/theta and a universal low-IV bonus
are intentionally excluded: they are not comparable across strategy/DTE/price.
"""
import math
from datetime import datetime, timezone

STRATEGIES = {"bull_put_spread", "bear_call_spread", "bull_call_debit_spread", "bear_put_spread"}


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def score_spread_quality(short, long, strategy, *, now=None, max_age_seconds=60, max_spread_pct=.10):
    result = {"version":"spread-quality-v1", "experimental":True, "execution_authorized":False,
              "state":"unavailable", "score":None, "reason":None, "breakdown":None}
    def fail(reason, state="unavailable"):
        return {**result, "state":state, "reason":reason}
    if strategy not in STRATEGIES:
        return fail("unsupported_strategy")
    if (finite(max_age_seconds) is None or max_age_seconds <= 0
            or finite(max_spread_pct) is None or not 0 < max_spread_pct <= 1):
        raise ValueError("Invalid quality limits")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Quality clock must be timezone-aware")
    if (short.symbol == long.symbol or short.underlying != long.underlying
            or short.expiration != long.expiration or short.option_type != long.option_type):
        return fail("incompatible_legs", "rejected")
    expected_type = "put" if strategy in ("bull_put_spread", "bear_put_spread") else "call"
    if short.option_type != expected_type:
        return fail("strategy_leg_type_mismatch", "rejected")
    short_strike, long_strike = finite(short.strike), finite(long.strike)
    if short_strike is None or long_strike is None or min(short_strike,long_strike) <= 0:
        return fail("invalid_strikes", "rejected")
    below = strategy in ("bull_put_spread", "bull_call_debit_spread")
    if (long_strike < short_strike) != below or long_strike == short_strike:
        return fail("strategy_strike_order_mismatch", "rejected")
    if short.expiration < now.date():
        return fail("expired_contract", "rejected")
    widths = []
    mids = []
    for leg in (short,long):
        if getattr(leg, "quote_feed", None) not in ("opra", "realtime"):
            return fail("indicative_or_unverified_feed")
        stamp = getattr(leg, "quote_timestamp", None)
        if stamp is None or stamp.tzinfo is None or stamp.utcoffset() is None:
            return fail("quote_time_unknown")
        age = (now - stamp).total_seconds()
        if age > max_age_seconds or age < -5:
            return fail("stale_or_future_quote")
        bid, ask = finite(leg.bid), finite(leg.ask)
        if bid is None or ask is None or bid <= 0 or ask < bid:
            return fail("invalid_quote", "rejected")
        mid = (bid + ask) / 2
        width = (ask - bid) / mid
        if width > max_spread_pct:
            return fail("spread_too_wide", "rejected")
        widths.append(width); mids.append(mid)
    is_debit = strategy in ("bull_call_debit_spread", "bear_put_spread")
    amount = (mids[1] - mids[0]) if is_debit else (mids[0] - mids[1])
    spread_width = finite(abs(long_strike - short_strike))
    if spread_width is None or not 0 < amount < spread_width:
        return fail("invalid_spread_economics", "rejected")
    selected = long if is_debit else short
    delta = finite(selected.greeks.delta) if selected.greeks else None
    if delta is None or not 0 < abs(delta) <= 1 or (delta > 0) != (expected_type == "call"):
        return fail("invalid_or_missing_delta")
    target = .55 if is_debit else .20
    liquidity = 60 * (1 - max(widths) / max_spread_pct)
    delta_fit = 40 * max(0., 1 - abs(abs(delta) - target) / .30)
    return {**result, "state":"scored", "score":round(liquidity + delta_fit, 2),
            "reason":None, "target_delta":target, "delta_leg":"long" if is_debit else "short",
            "breakdown":{"liquidity":round(liquidity,2), "delta_fit":round(delta_fit,2)},
            "worst_leg_spread_pct":round(max(widths)*100,4),
            "max_loss_per_contract":round((amount if is_debit else spread_width-amount)*100,2)}
