"""R2-Q: create inert, durable entry authorization record store.

Revision ID: 0043
Revises: 0042

Schema only. This migration does not issue permits or enable entry orders.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entry_authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("broker_account_id", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("asset_class", sa.String(16), nullable=False),
        sa.Column("position_identity", sa.String(160), nullable=False),
        sa.Column("intent_digest", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("evidence_id", sa.String(160), nullable=False),
        sa.Column("evidence_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("risk_revision", sa.String(160), nullable=False),
        sa.Column("kill_switch_revision", sa.String(160), nullable=False),
        sa.Column("position_claim_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('issued', 'consumed', 'expired', 'revoked')",
            name="ck_entry_authorizations_state",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="ck_entry_authorizations_expiry"),
        sa.CheckConstraint(
            "(state = 'consumed' AND consumed_at IS NOT NULL) OR "
            "(state <> 'consumed' AND consumed_at IS NULL)",
            name="ck_entry_authorizations_consumed_at",
        ),
        sa.UniqueConstraint("token_hash", name="uq_entry_authorizations_token_hash"),
    )
    op.create_index(
        "ix_entry_authorizations_scope", "entry_authorizations",
        ["organization_id", "broker_account_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_entry_authorizations_scope", table_name="entry_authorizations")
    op.drop_table("entry_authorizations")
