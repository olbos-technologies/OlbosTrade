"""
What each tier actually grants, and the one place that decides it.

THE NUMBERS HERE ARE A CONTRACT WITH THE LANDING PAGE. frontend/src/pages/
Landing.tsx publishes them on three pricing cards; until now they were
labelled "(planned)" and nothing enforced them, which is why the label was
honest. This module is what makes them true, so the two must not drift —
test_tier_limits_match_the_landing_page reads the TSX and compares.

WHY A TABLE AND NOT `if tier == "free"` AT EACH SITE. There are ~45 routes
this could touch. Scattering the comparison means the day a tier is added or
a limit moves, the change is found by whichever site someone remembers. A
table has one row per tier and every site reads it.

DISABLED AUTH MEANS NO LIMITS. `settings.auth_enabled` is False on an
existing single-operator install: there are no accounts, so there is no tier,
and the operator is the only caller. Those installs must be completely
unaffected, so limits_for(None) returns UNLIMITED rather than defaulting to
Free — defaulting to the most restrictive row would silently take features
away from the one person who already had them all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.models.user import TIER_ELITE, TIER_FREE, TIER_PRO

#: Sentinel for "no cap". None rather than a large int, so a forgotten
#: comparison fails loudly instead of silently permitting 999999.
UNCAPPED = None

#: Signal freshness. "live" is as the engine produces them; "eod" means a
#: caller sees only what was settled at the last close.
DELAY_LIVE = "live"
DELAY_EOD = "eod"


@dataclass(frozen=True)
class TierLimits:
    """One row of the pricing table, as the API enforces it."""

    #: Symbols a caller may see across watchlists. None = the full watchlist.
    watchlist_symbols: Optional[int]
    #: DELAY_LIVE or DELAY_EOD.
    signal_delay: str
    #: How far back history and backtests may reach. None = no limit, and
    #: that is not a tier — it is the auth-disabled install below. Using
    #: Elite's number for "unlimited" looked harmless and was not: an install
    #: with no accounts would have been clamped to five years of history it
    #: already had unrestricted, which is the exact regression UNLIMITED
    #: exists to prevent. Caught by sanity-checking the clamp's output.
    history_years: Optional[int]
    #: Whether the caller may reach broker-connected routes at all.
    #:
    #: NOT a count, and the landing page's "1 (planned)" cannot become one
    #: here. config.py describes a hybrid tenancy model: this shared instance
    #: serves signals and research for many accounts, and execution_enabled
    #: makes the whole DEPLOYMENT capable or incapable of placing an order,
    #: with execution living in a separate per-tenant stack. There is one
    #: instance-wide broker, so "how many brokers has this user connected" is
    #: not a question the schema can answer. What is enforceable, and what
    #: the pricing card actually means to a buyer, is whether they may reach
    #: execution at all.
    broker_access: bool

    @property
    def sees_live_signals(self) -> bool:
        return self.signal_delay == DELAY_LIVE


FREE = TierLimits(
    watchlist_symbols=1,
    signal_delay=DELAY_EOD,
    history_years=1,
    broker_access=False,
)
PRO = TierLimits(
    watchlist_symbols=UNCAPPED,
    signal_delay=DELAY_LIVE,
    history_years=5,
    broker_access=False,
)
ELITE = TierLimits(
    watchlist_symbols=UNCAPPED,
    signal_delay=DELAY_LIVE,
    history_years=5,
    broker_access=True,
)

#: The deployment with no accounts. See the module docstring: this is what an
#: install running on Basic Auth alone gets, and it must be everything.
UNLIMITED = TierLimits(
    watchlist_symbols=UNCAPPED,
    signal_delay=DELAY_LIVE,
    history_years=UNCAPPED,
    broker_access=True,
)

BY_TIER = {
    TIER_FREE: FREE,
    TIER_PRO: PRO,
    TIER_ELITE: ELITE,
}


def limits_for(tier: Optional[str]) -> TierLimits:
    """The limits for a tier label, falling back to the MOST restrictive row.

    Two different fallbacks, and the difference is deliberate:

      * None — nobody is signed in, because auth is off. UNLIMITED.
      * an unrecognised string — a tier this build does not know, which means
        either a typo in the database or a row written by a newer version.
        FREE, because guessing generously on an unknown label is how a
        mangled tier column becomes free Elite access.
    """
    if tier is None:
        return UNLIMITED
    return BY_TIER.get(tier, FREE)
