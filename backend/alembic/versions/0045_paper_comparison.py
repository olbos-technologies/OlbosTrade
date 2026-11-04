"""Saved read-only System / Hybrid paper account pairing.

Revision ID: 0045
Revises: 0044
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("paper_comparisons",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("system_grant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("broker_oauth_grants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hybrid_grant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("broker_oauth_grants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("system_grant_id <> hybrid_grant_id", name="ck_paper_comparison_distinct"))


def downgrade():
    op.drop_table("paper_comparisons")
