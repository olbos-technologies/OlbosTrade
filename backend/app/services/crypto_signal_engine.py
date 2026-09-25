"""
Crypto signal engine — phase 1 (read-only).

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
Phase 1 generates crypto signals, records their forward outcomes, and stops.
There is no order path here, and none is reachable from here: run_crypto_scan()
(crypto_scan.py) never calls handle_signal(). The point is to build an honest
forward-outcome record with a real denominator before any capital is at risk,
using the same resolver that already labels equity signals.

The scoring math is NOT re-derived for crypto. compute_indicators() and
score_equity_signal() are pure functions over an OHLCV frame — nothing in them
is equity-specific — so crypto reuses them rather than forking a second copy of
the same indicator code that would then drift. What IS crypto-specific lives
here: symbol shape, the checks below, and the fact that two equity inputs have
no crypto analogue:

  * earnings_gate()  — there are no earnings. Skipped, not stubbed.
  * orderflow_score  — the IBKR level-2 derived score has no crypto feed, so
                       the neutral 0.0 is passed. Note this is the function's
                       own default, so crypto signals score exactly as an
                       equity signal would with orderflow unavailable, rather
                       than being penalised for a feed that does not exist.

Because of that second point, a crypto signal's confidence is NOT directly
comparable to an equity signal's: the equity one had up to
orderflow_multiplier points of extra evidence available to it. Keep the two
populations apart when measuring — which is why asset_type is now threaded
through record_signal() and why /api/signal-research defaults to equity only.

PRICE PRECISION IS A REAL CONSTRAINT
------------------------------------
signal_outcomes stores prices as Numeric(12, 4): eight integer digits and four
decimals. That is comfortable for BTC at ~$110k, and it is a trap at the other
end — a sub-cent coin's entry, stop and target all round to the same value (or
to 0.0000), producing rows that look like data and encode nothing. Hence
prices_representable(): it is cheaper to drop such a symbol than to explain a
100%-stop-hit cohort later. The ticker column is String(10), which the
DASH-form symbols below fit with room to spare.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

# Bumped when the crypto-specific layer changes — the symbol handling, the
# guards, or which inputs are passed through. It is a separate lineage from
# EQUITY_SCORING_VERSION on purpose: the shared indicator/scoring code can be
# retuned for equities without that implying a crypto change, and vice versa.
# Rows carry whichever version generated them, so a cohort stays interpretable.
CRYPTO_SCORING_VERSION = "1.0.0"

# Numeric(12, 4) bounds. The floor is not arbitrary: below ~$0.05 a 4-decimal
# column no longer separates an entry from a stop placed a fraction of an ATR
# away, so the row would record a geometry it does not actually have.
MIN_REPRESENTABLE_PRICE = 0.05
MAX_REPRESENTABLE_PRICE = 99_999_999.0

# Minimum daily bars before indicators are trusted. Same floor the equity scan
# uses; crypto reaches it faster in calendar terms because it prints 7 bars a
# week rather than 5.
MIN_BARS = 30


def normalize_crypto_symbol(symbol: str) -> str:
    """
    Canonical internal form: ``BASE-QUOTE`` upper-case, e.g. ``BTC-USD``.

    Accepts the three shapes this symbol arrives in — ``BTC/USD`` (Alpaca),
    ``BTCUSD`` (compact), ``BTC-USD`` (yfinance) — because the internal form is
    also the yfinance ticker AND the value stored in signal_outcomes.ticker, so
    a mixed-shape watchlist would silently split one instrument into three
    populations and break the (ticker, action, day) dedup between them.
    """
    s = (symbol or "").strip().upper().replace("/", "-")
    if "-" in s:
        base, _, quote = s.partition("-")
        return f"{base}-{quote}" if base and quote else s
    # Compact form: split off a known quote suffix. Longest first, so USDT is
    # not mis-read as USD with a stray T left on the base.
    for quote in ("USDT", "USDC", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[: -len(quote)]}-{quote}"
    return s


def to_alpaca_symbol(symbol: str) -> str:
    """
    Internal form → Alpaca's crypto shape (``BTC-USD`` → ``BTC/USD``).

    Unused by phase 1, which places no orders and fetches no Alpaca data. It
    lives here because the conversion belongs with normalize_crypto_symbol()
    rather than being reinvented at the first call site that needs it.
    """
    return normalize_crypto_symbol(symbol).replace("-", "/", 1)


def is_crypto_symbol(symbol: str) -> bool:
    """True for a symbol in the internal crypto form with a fiat/stable quote."""
    s = normalize_crypto_symbol(symbol)
    return "-" in s and s.rpartition("-")[2] in {"USD", "USDT", "USDC"}


def prices_representable(*prices: Optional[float]) -> bool:
    """
    True when every price survives a round-trip through Numeric(12, 4) as a
    distinct, positive value.

    Rejects three failure modes in one check: a price outside the column's
    range, a price so small that 4 decimals quantise it to zero, and the
    subtler one — an entry/stop/target triple that is distinct in float and
    collapses to identical values once stored, which would record a
    zero-risk or zero-reward trade that never existed.
    """
    seen: set[Decimal] = set()
    for price in prices:
        if price is None:
            return False
        try:
            value = float(price)
        except (TypeError, ValueError):
            return False
        if not MIN_REPRESENTABLE_PRICE <= value <= MAX_REPRESENTABLE_PRICE:
            return False
        quantised = Decimal(str(round(value, 4)))
        if quantised <= 0 or quantised in seen:
            return False
        seen.add(quantised)
    return bool(seen)
