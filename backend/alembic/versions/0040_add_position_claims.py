"""position claims — close the duplicate guard's race window

Stage 3 of _execute_signal checks the trades table for an open or pending row
on (underlying, asset class) and skips if it finds one. That row is written
only after the broker accepts, so two signals arriving inside that round trip
both read zero rows, both pass, and both submit.

This table gives the second attempt something to collide with before any trade
row exists. The composite primary key is the mechanism: INSERT ... ON CONFLICT
DO NOTHING lets exactly one of two concurrent inserters report success.

`state` is what makes a lease safe. A claim that merely expired would hand the
position to a second caller at the 120-second mark while the first order's
fate was still unknown, and a lease timeout is not evidence that no order
exists. Only `pending` claims — nothing sent yet — are reclaimable by the
lease; `submitted` and `unknown` need a real outcome. See the model docstring.

ADDITIVE AND EMPTY. No backfill: a claim describes an entry in flight right
now, and there are none at migration time. Nothing reads this table except the
duplicate guard, so applying it changes no existing behaviour until the guard
starts writing to it. That is what makes it safe to apply BEFORE deploying the
code that uses it, which is the required order — see docs/runbook.md.

Revision ID: 0040
Revises: 0039
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_claims",
        sa.Column("scope", sa.String(64), primary_key=True,
                  server_default="global"),
        sa.Column("underlying", sa.String(16), primary_key=True),
        sa.Column("asset_class", sa.String(10), primary_key=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("dispatch_id", sa.String(64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unresolved_reason", sa.String(200), nullable=True),
    )
    # Every state change is keyed on the token, so it has to identify one row.
    op.create_unique_constraint(
        "uq_position_claims_token", "position_claims", ["claim_token"]
    )
    # The reaper deletes expired PENDING claims on the hot path of every entry.
    op.create_index(
        "idx_position_claims_lease", "position_claims", ["state", "lease_expires_at"]
    )
    # Reconciliation sweeps unresolved claims by state.
    op.create_index(
        "idx_position_claims_unresolved", "position_claims", ["state"],
        postgresql_where=sa.text("state IN ('submitted', 'unknown')"),
    )


def downgrade() -> None:
    op.drop_index("idx_position_claims_unresolved", table_name="position_claims")
    op.drop_index("idx_position_claims_lease", table_name="position_claims")
    op.drop_constraint("uq_position_claims_token", "position_claims", type_="unique")
    op.drop_table("position_claims")
