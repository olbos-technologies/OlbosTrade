"""Which trades describe a strategy's decision, and which were merely inherited.

Not every row in `trades` is a decision the desk made. The reconciler's adopt
path (`main._adopt_untracked_positions`) writes a Trade for every live broker
position that has no database record, so the guardrails can see it — without
that, an untracked position is invisible to every DB-derived risk check, which
is the incident the adopt path exists to prevent.

WHAT IS AND IS NOT A PLACEHOLDER ON THOSE ROWS.

An earlier version of this module claimed their cost basis was invented and
excluded their P&L from every total on that basis. **That was wrong, and the
correction is the reason this module is shaped the way it is.** The adopt path
sets `price = Decimal(str(pos.avg_cost))` (`main.py`) and writes it to
`short_strike`, `long_strike` and `credit_received`. `avg_cost` is the
broker's real average cost basis — what was actually paid. Equity P&L is
computed from `credit_received` (`position_rotation._equity_entry_price`), so
**an adopted equity position's P&L is correct arithmetic on real money.**

What IS a placeholder, per the adopt path's own docstring, is `entry_date`
(discovery time, not the real entry) and the two strike fields (meaningless
for equity in any case). So:

  - **Dollar P&L is real.** It belongs in every account-level total. A -4,263
    adopted loss is 4,263 dollars gone, not an accounting artifact.
  - **Attribution is not.** No strategy chose these positions, so no strategy
    can be graded on them.
  - **Duration and timing are not.** Anything derived from `entry_date` —
    hold days, time-to-target — is measured from when somebody noticed the
    position, not when it was opened.

Measured on production, 2026-10-17, over three months of paper trading:

    strategy            n    total P&L    win %
    equity             43    +3,163.51     53.5
    adopted_untracked  25    -4,263.07     48.0
    ------------------------------------------
    combined           68    -1,099.56     51.5

Both lines are true and both matter. The desk's own decisions were positive;
the account still lost money, because 37% of the closed rows were positions
it inherited rather than chose. Reporting only the combined line hides that
the strategy and the account disagree. Reporting only the strategy line hides
a real loss — which is the mistake this module made first time round, and the
worse of the two: it would have read as "we are fine" while capital drained.

TWO RULES.

**Adopted positions stay in risk, and stay in the money.** Exposure, portfolio
heat, concurrent position counts, reconciliation, the kill switch and every
account-level P&L total must keep counting them: the capital is genuinely
committed, the broker genuinely holds it, and the loss is genuinely realised.
This module is used only to break totals down, never to shrink them.

**Nothing is dropped silently.** The split travels with the numbers, the same
way `unmeasured_trades` travels with `compute_performance` and
`heat_overstated` travels with portfolio heat.
"""

from __future__ import annotations

from typing import Any, Iterable

#: `Trade.strategy` written by `main._adopt_untracked_positions`. A literal
#: rather than an enum because it is also written as a literal there, and the
#: two must be greppable together.
ADOPTED_STRATEGY = "adopted_untracked"


def is_adopted(trade: Any) -> bool:
    """True for a position the reconciler inherited from the broker.

    Not a judgement about data quality: the cost basis on these rows is real
    (see the module docstring). It says only that no strategy chose the trade.

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

    Returns both halves rather than filtering, because a caller is expected to
    report both and to keep both in any account-level total — see the module
    docstring's rules. Filtering here would invite the sign flip the first
    version of this module shipped.
    """
    attributable, adopted = [], []
    for t in trades:
        (adopted if is_adopted(t) else attributable).append(t)
    return attributable, adopted
