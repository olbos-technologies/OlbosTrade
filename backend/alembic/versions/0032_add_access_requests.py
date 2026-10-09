"""add access_requests

A queue of people asking for an account, and the one-time token that turns an
approval into a user.

Inert on its own. The public routes that write here 404 while AUTH_ENABLED is
false, exactly as the auth routes added in 0030 do, so an existing
single-operator install is unchanged until the flag is turned on.

The unique index is PARTIAL — one live request per address, while still
letting someone who was denied apply again later. A plain unique index on
email would make a denial permanent, which is a harsher policy than anyone
chose and an awkward one to reverse by hand.

No token is stored, only its SHA-256, for the same reason user_sessions stores
only a hash: a database dump must not hand over live grants.

Revision ID: 0032
Revises: 0031
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        # No server_default, matching 0030: the model's default=uuid.uuid4
        # generates ids in Python. gen_random_uuid() would need pgcrypto or
        # PG13+, which is a deployment assumption this file has no business
        # introducing on its own.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="pending"),
        sa.Column("setup_token_hash", sa.String(64), nullable=True),
        sa.Column("setup_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_access_requests_email_pending",
        "access_requests",
        ["email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("idx_access_requests_token", "access_requests", ["setup_token_hash"])
    op.create_index("idx_access_requests_status", "access_requests", ["status"])


def downgrade() -> None:
    op.drop_index("idx_access_requests_status", table_name="access_requests")
    op.drop_index("idx_access_requests_token", table_name="access_requests")
    op.drop_index("idx_access_requests_email_pending", table_name="access_requests")
    op.drop_table("access_requests")
