"""Point-in-time health of read-only OAuth connections.

Revision ID: 0044
Revises: 0043
"""
from alembic import op
import sqlalchemy as sa

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("broker_oauth_grants", sa.Column("health", sa.String(32), nullable=False, server_default="unverified"))
    op.add_column("broker_oauth_grants", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("broker_oauth_grants", "last_checked_at")
    op.drop_column("broker_oauth_grants", "health")
