"""broker connection version

MASTER_ARCHITECTURE §7.2: "Connection version increments on security- or
routing-relevant change." §8 has the execution worker revalidate it
immediately before submitting, so an order evaluated against one set of
credentials cannot execute against another.

Without this column that revalidation was vacuous: connection_scoped.py read a
constant 1, compared it to a constant 1, and passed every time. The check
existed and protected nothing.

WHAT THE NUMBER IS FOR. Today the only in-place security-relevant change is
revocation, so this is close to a second way of reading `status`. It is not
redundant: the worker's check becomes a single integer comparison rather than a
list of conditions that has to grow every time a new kind of change is added
(scope and capability negotiation in §11.4, vault reference rotation in §15.2).
A rotation does NOT bump a version, because connect() revokes the old row and
inserts a new one with a new id -- an in-flight order holding the old id finds
it revoked, which is the same stop by a different route.

DEFAULT 1, NOT NULL, backfilled for every existing row. Nothing in flight can
hold a version from before this migration, because nothing reads the column
until the worker does.

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "broker_connections",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("broker_connections", "version")
