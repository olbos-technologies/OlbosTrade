"""Pure, side-effect-free limit-price preparation for equity entries.

Preparing a price never authorizes submission or consults a broker.
The caller supplies a separately obtained, potentially missing quote.
"""
from math import isfinite


def reprice_equity_limit(*, action: str, fallback_price, quote, buffer: float):
    """Use a finite, positive live quote or preserve the scan price.

    NaN and infinity must never become a limit price simply because a
    comparison against zero is inconclusive or true.
    """
    if quote is None:
        return fallback_price
    try:
        adjustment = float(buffer)
        if not isfinite(adjustment) or not 0 <= adjustment < 1:
            return fallback_price
        side = quote.ask_price if action == "BUY" else (
            quote.bid_price if action == "SELL" else None
        )
        if side is None:
            return fallback_price
        price = float(side)
        if not isfinite(price) or price <= 0:
            return fallback_price
        adjusted = price * (1 + adjustment if action == "BUY" else 1 - adjustment)
        return adjusted if isfinite(adjusted) and adjusted > 0 else fallback_price
    except (AttributeError, TypeError, ValueError, OverflowError):
        return fallback_price
