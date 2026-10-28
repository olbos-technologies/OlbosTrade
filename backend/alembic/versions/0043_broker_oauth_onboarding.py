"""Read-only broker OAuth onboarding. No execution routing.

Revision ID: 0043
Revises: 0042
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("broker_oauth_attempts",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("broker_oauth_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("access_token_enc", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "broker", "environment", "account_id", name="uq_broker_oauth_account"))
    op.create_index("ix_broker_oauth_grants_organization_id", "broker_oauth_grants", ["organization_id"])


def downgrade():
    op.drop_table("broker_oauth_grants")
    op.drop_table("broker_oauth_attempts")
