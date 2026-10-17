"""Which trades describe a strategy's performance, and which merely happened.

Not every row in `trades` is a decision the desk made. The reconciler's adopt
path (`main._adopt_untracked_positions`) writes a Trade for every live broker
position that has no database record, so the guardrails can see it — without
that, an untracked position is invisible to every DB-derived risk check, which
is the incident the adopt path exists to prevent.

Those rows are real positions with real money at risk. What they are NOT is
evidence about a strategy, for a specific and disqualifying reason: **their
cost basis is invented**. The adopt path sets `entry_date`, the strikes and
`credit_received` from whatever the broker reported at the moment of
discovery, and says so in its own log line — "entry_date/strikes are
discovery-time placeholders, not real historical entry data". P&L computed
against a placeholder entry is the distance the position travelled since
somebody noticed it, not the result of a decision.

Measured on production, 2026-10-17, over three months of paper trading:

    strategy            n    total P&L    win %
    equity             43    +3,163.51     53.5
    adopted_untracked  25    -4,263.07     48.0
    ------------------------------------------
    combined           68    -1,099.56     51.5

The desk read "we are losing" from that combined line. The strategy was
positive; 37% of the rows were orphans carrying a fabricated basis, and they
dominated the total. That is the failure this module exists to prevent — not
a rounding error but a sign flip, on the number the whole operation is
steered by.

TWO RULES, AND THE SECOND IS WHY THIS IS NOT JUST A FILTER.

**Adopted positions stay in risk.** Exposure, portfolio heat, concurrent
position counts, reconciliation and the kill switch must keep counting them:
the capital is genuinely committed and the broker genuinely holds it. Nothing
here is used by those paths, deliberately.

**Nothing is dropped silently.** The counts travel with the numbers, the same
way `unmeasured_trades` travels with `compute_performance` and
`heat_overstated` travels with portfolio heat. A caller that excluded 25 of 68
rows and said nothing would be replacing one misleading number with another.
"""

from __future__ import annotations

from typing import Any, Iterable

#: `Trade.strategy` written by `main._adopt_untracked_positions`. A literal
#: rather than an enum because it is also written as a literal there, and the
#: two must be greppable together.
ADOPTED_STRATEGY = "adopted_untracked"


def is_adopted(trade: Any) -> bool:
    """True for a reconciler-adopted position, whose cost basis is synthetic.

    Accepts a mapping or anything with a `.strategy` attribute, so route code
    and ORM rows can both ask without converting first.
    """
    if isinstance(trade, dict):
        strategy = trade.get("strategy")
    else:
        strategy = getattr(trade, "strategy", None)
    return (strategy or "") == ADOPTED_STRATEGY


def partition(trades: Iterable[Any]) -> tuple[list, list]:
    """Split into (strategy-attributable, adopted).

    Returns both halves rather than filtering, because the caller is expected
    to report the second — see the module docstring's second rule.
    """
    attributable, adopted = [], []
    for t in trades:
        (adopted if is_adopted(t) else attributable).append(t)
    return attributable, adopted
