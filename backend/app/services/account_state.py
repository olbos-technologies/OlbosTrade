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


def capped_capital(live_value: float) -> float:
    """The capital the desk trades with: the live account, bounded by TRADING_CAPITAL_BASE.

    Unset (0) is the old behaviour exactly. A non-positive live value is returned
    untouched — a failed read must stay recognisable as one, never become the base.
    """
    base = float(settings.trading_capital_base or 0.0)
    if base > 0 and live_value > base:
        return base
    return live_value


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
        return capped_capital(settings.starting_capital)
