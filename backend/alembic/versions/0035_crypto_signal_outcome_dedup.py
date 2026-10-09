"""Database-level dedup for crypto signal outcomes.

Revision ID: 0035
Revises: 0034

WHY THIS IS SCOPED TO CRYPTO, AND WHY THAT IS NOT A HALF MEASURE
----------------------------------------------------------------
record_signal() dedups with a SELECT followed by an INSERT. That is not atomic,
so two overlapping scans can both find no row and both insert — and phase 1
added a second way to overlap: POST /api/crypto/scan can now run while the
background scheduler is mid-scan. A duplicated row corrupts the exact thing the
crypto phase exists to produce, which is a denominator.

signal_outcome_tracker's module docstring explains why no UNIQUE constraint
backs this today: roughly 69k historical duplicate EQUITY rows still exist, so a
table-wide constraint would make the next `alembic upgrade head` fail, taking
deploy/hetzner/update.sh with it. That reasoning is about history, and crypto
has none — asset_type='crypto' is new in this release with zero rows — so the
constraint can be enforced there now, on a clean population, without waiting for
scripts/dedupe_signal_outcomes.py to be run against the equity backlog.

A partial index is the mechanism (same pattern as
idx_broker_connections_active in 0034). When the equity history is cleaned, the
predicate can be widened or dropped; nothing here has to be undone first.

The key is (ticker, action, UTC day), matching record_signal's own lookup.
`(generated_at AT TIME ZONE 'UTC')::date` is immutable — timestamptz AT TIME
ZONE yields a plain timestamp, and the cast to date is immutable — so it is
usable in an index expression.
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_signal_outcomes_crypto_daily"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
        ON signal_outcomes (
            ticker,
            action,
            ((generated_at AT TIME ZONE 'UTC')::date)
        )
        WHERE asset_type = 'crypto'
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
