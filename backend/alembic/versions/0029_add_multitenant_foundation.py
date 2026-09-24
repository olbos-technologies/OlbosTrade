"""Add multi-tenant identity, entitlement, credential vault and audit schema.

Revision ID: 0029
Revises: 0028
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("organizations", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("clerk_org_id", sa.String(128), unique=True), sa.Column("name", sa.String(160), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_table("users", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("clerk_user_id", sa.String(128), unique=True, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_table("organization_memberships", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("role", sa.String(24), nullable=False, server_default="member"), sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_member"))
    op.create_table("subscriptions", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("stripe_customer_id", sa.String(128), unique=True), sa.Column("stripe_subscription_id", sa.String(128), unique=True), sa.Column("tier", sa.String(24), nullable=False, server_default="free"), sa.Column("status", sa.String(32), nullable=False, server_default="inactive"), sa.Column("current_period_end", sa.DateTime(timezone=True)))
    op.create_table("broker_credentials", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False), sa.Column("broker", sa.String(24), nullable=False), sa.Column("environment", sa.String(12), nullable=False, server_default="paper"), sa.Column("encrypted_payload", sa.Text(), nullable=False), sa.Column("key_version", sa.String(64), nullable=False), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_table("audit_events", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL")), sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")), sa.Column("action", sa.String(96), nullable=False), sa.Column("resource_type", sa.String(64), nullable=False), sa.Column("resource_id", sa.String(128)), sa.Column("metadata_json", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))

def downgrade():
    for table in ("audit_events", "broker_credentials", "subscriptions", "organization_memberships", "users", "organizations"): op.drop_table(table)
