"""Create account-scoped broker execution fill ledger.

Revision ID: 0042
Revises: 0041

Schema only: ingestion is not enabled and no existing trading data is
backfilled or guessed to be an authenticated broker execution.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_execution_fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("broker_account_id", sa.String(128), nullable=False),
        sa.Column("broker_execution_id", sa.String(160), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_order_id", sa.String(160), nullable=True),
        sa.Column("contract_id", sa.String(160), nullable=False),
        sa.Column("underlying", sa.String(24), nullable=False),
        sa.Column("expiration", sa.String(10), nullable=False),
        sa.Column("strike", sa.Numeric(14, 4), nullable=False),
        sa.Column("option_type", sa.String(8), nullable=False),
        sa.Column("signed_filled_quantity", sa.Integer(), nullable=False),
        sa.Column("fill_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("broker", "broker_account_id", "broker_execution_id",
                            name="uq_broker_execution_fills_identity"),
    )
    op.create_index("ix_broker_execution_fills_organization_id",
                    "broker_execution_fills", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_broker_execution_fills_organization_id",
                  table_name="broker_execution_fills")
    op.drop_table("broker_execution_fills")
