"""
Account State — single source of truth for "what is the account worth right now."

Previously this exact fetch-with-fallback snippet was duplicated three times
(trade_desk.py's guardrail state, main.py's options sizing) with two other call
sites (main.py's equity trade-plan sizing, portfolio.py's heat %) hardcoded to
the static settings.starting_capital instead — meaning equity position sizing
and portfolio heat were computed against a number that could be far from the
real, live account value. One function, used everywhere a live account value
is needed.
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# get_account_summary() has no timeout of its own. This function is called
# from the trade-execution guardrail gate (trade_desk.py's portfolio-state
# check runs on every execution attempt), equity/options position sizing,
# and several polled UI routes — an unbounded hang here doesn't just show a
# stale number, it can silently stall every trade attempt indefinitely.
ACCOUNT_VALUE_TIMEOUT_SECONDS = 5.0


#: Floor for the desk's capital once losses have eaten the base. Positive on
#: purpose: a zero or negative figure is how a failed account read is recognised
#: elsewhere, so a desk that has lost its whole base must not look like one.
DESK_CAPITAL_FLOOR = 1.0

_warned_no_anchor = False


def capped_capital(live_value: float) -> float:
    """The capital the desk trades with.

    * TRADING_CAPITAL_BASE unset (0): the live account, exactly as before.
    * Base and TRADING_CAPITAL_ANCHOR set: ``base + (live - anchor)``, never more
      than the live account. The desk starts at the base and then moves with the
      account's own gains and losses, unrealized included, so a loss lowers the
      capital the drawdown and capital-preservation checks measure.
    * Base set, no anchor: the older behaviour, ``min(live, base)``. It sizes
      correctly but pins the capital at the base while the account is above it, so
      desk losses are invisible to drawdown and preservation. Warned about once.

    A non-positive live value is returned untouched: a failed read must stay
    recognisable as one, never become the base.
    """
    global _warned_no_anchor
    base = float(settings.trading_capital_base or 0.0)
    if base <= 0 or live_value <= 0:
        return live_value
    anchor = float(settings.trading_capital_anchor or 0.0)
    if anchor > 0:
        return max(min(live_value, base + (live_value - anchor)), DESK_CAPITAL_FLOOR)
    if not _warned_no_anchor:
        _warned_no_anchor = True
        logger.warning(
            "TRADING_CAPITAL_BASE is set without TRADING_CAPITAL_ANCHOR: the desk's capital "
            "is pinned at the base, so desk losses do not reach the drawdown or "
            "capital-preservation checks. Set TRADING_CAPITAL_ANCHOR to the account's "
            "net liquidation on the day the base took effect."
        )
    return min(live_value, base)


async def get_account_value() -> float:
    """
    Live net liquidation value from the broker, falling back to
    settings.starting_capital if the broker is unreachable.

    Never raises — callers that need fail-closed behavior on top of this
    (e.g. the guardrail gate) already layer their own DB-driven checks; this
    function's job is just "best available capital figure," never zero.
    """
    try:
        from app.broker.broker_factory import get_broker
        acct = await asyncio.wait_for(
            get_broker().get_account_summary(), timeout=ACCOUNT_VALUE_TIMEOUT_SECONDS,
        )
        return capped_capital(float(acct.net_liquidation or settings.starting_capital))
    except Exception as exc:
        logger.debug("get_account_value: broker unreachable, using starting_capital: %s", exc)
        # A stand-in, not an account reading: it must not go through the anchor
        # arithmetic (base + (starting_capital - anchor) is nonsense for a figure
        # that is not the account's net liquidation). Bounded by the base only.
        base = float(settings.trading_capital_base or 0.0)
        return min(settings.starting_capital, base) if base > 0 else settings.starting_capital
