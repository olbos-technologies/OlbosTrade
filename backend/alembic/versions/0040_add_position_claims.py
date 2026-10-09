"""position claims — close the duplicate guard's race window

Stage 3 of _execute_signal checks the trades table for an open or pending row
on (underlying, asset class) and skips if it finds one. The row it looks for
is only written after the broker accepts the order, so two signals for the
same name arriving inside that window both read zero rows, both pass, and both
submit.

This table gives the second attempt something to collide with before any trade
row exists. The composite primary key is the whole mechanism: INSERT ... ON
CONFLICT DO NOTHING lets exactly one of two concurrent inserters report
success.

ADDITIVE AND EMPTY. No backfill: a claim describes an entry in flight right
now, and there are none at migration time. Nothing reads this table except the
duplicate guard, so applying it changes no existing behaviour until the guard
starts writing to it.

Revision ID: 0040
Revises: 0039
"""
from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_claims",
        sa.Column("underlying", sa.String(16), primary_key=True),
        sa.Column("asset_class", sa.String(10), primary_key=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_id", sa.String(64), nullable=True),
    )
    # The reaper deletes by expiry on every attempt; without this it is a
    # sequential scan on the hot path of every entry.
    op.create_index("idx_position_claims_expires", "position_claims", ["expires_at"])


def downgrade() -> None:
    op.drop_index("idx_position_claims_expires", table_name="position_claims")
    op.drop_table("position_claims")
