"""Pure, side-effect-free limit-price preparation for equity entries.

Preparing a price never authorizes submission or consults a broker.
The caller supplies a separately obtained, potentially missing quote.
"""


def reprice_equity_limit(*, action: str, fallback_price, quote, buffer: float):
    """Preserve the scan price when the relevant quote side is unavailable."""
    if quote is None:
        return fallback_price
    try:
        if action == "BUY" and quote.ask_price > 0:
            return float(quote.ask_price) * (1 + buffer)
        if action == "SELL" and quote.bid_price > 0:
            return float(quote.bid_price) * (1 - buffer)
    except (AttributeError, TypeError, ValueError):
        return fallback_price
    return fallback_price
