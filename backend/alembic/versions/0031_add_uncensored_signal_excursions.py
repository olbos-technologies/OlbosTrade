"""add uncensored MFE/MAE to signal_outcomes

`max_favorable_pct` is censored at resolution. The outcome tracker returned the
moment the target was touched, so a target_hit row's MFE is capped at its own
target — every winner reads as exactly the target and never a basis point more,
because the measurement stopped at the instant it succeeded.

That makes one question unanswerable: would a trailing stop or a partial profit
target have captured more than the fixed target does? Answering it requires
knowing how far winners actually ran, and the column that should say so stops
looking at precisely the wrong moment.

It matters more than it sounds, because the hit rate cannot substitute. For
barriers at +a/-b the breakeven hit rate and the random-walk hit rate are both
b/(a+b) — identical for every ratio — so the hit rate alone carries no
information about edge. The excursion distribution is where that information
lives, and it was being truncated.

These columns are ADDITIVE. `max_favorable_pct` keeps its censored meaning so
rows written before and after this migration stay comparable on it, and every
pre-existing row gets NULL in the new columns rather than a backfilled guess.
That NULL is the marker separating measurable rows from unmeasurable ones:
backfilling it from `max_favorable_pct` would launder a censored number into a
column whose entire purpose is to be uncensored.

`full_window_days` records how many bars the uncensored measurement actually
covered. A signal resolved yesterday has only a day or two of history, so its
"full" excursion is itself censored — by data availability rather than by the
target. Consumers should require full_window_days == max_hold_days before
aggregating; without that, this fix would only move the censoring somewhere
harder to see.

No backfill is possible here: recovering true excursions for already-resolved
rows needs price history re-fetched per ticker, which is a job, not a
migration. The NULLs make the gap visible instead of pretending otherwise.

Revision ID: 0031
Revises: 0030
"""
from alembic import op
import sqlalchemy as sa

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signal_outcomes",
        sa.Column("mfe_full_pct", sa.Numeric(8, 4), nullable=True),
    )
    op.add_column(
        "signal_outcomes",
        sa.Column("mae_full_pct", sa.Numeric(8, 4), nullable=True),
    )
    op.add_column(
        "signal_outcomes",
        sa.Column("full_window_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signal_outcomes", "full_window_days")
    op.drop_column("signal_outcomes", "mae_full_pct")
    op.drop_column("signal_outcomes", "mfe_full_pct")
