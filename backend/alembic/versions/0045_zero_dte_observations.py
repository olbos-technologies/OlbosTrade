"""Add zero_dte_observations — a recorder of 0DTE debit-spread quotes and 5/10-minute marks.

Revision ID: 0045
Revises: 0044
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zero_dte_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("slot", sa.String(length=5), nullable=False),
        sa.Column("symbol", sa.String(length=10), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("proposed", sa.Boolean(), nullable=False),
        sa.Column("refusal_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("option_type", sa.String(length=4), nullable=True),
        sa.Column("long_strike", sa.Float(), nullable=True),
        sa.Column("short_strike", sa.Float(), nullable=True),
        sa.Column("width", sa.Float(), nullable=True),
        sa.Column("long_bid", sa.Float(), nullable=True),
        sa.Column("long_ask", sa.Float(), nullable=True),
        sa.Column("short_bid", sa.Float(), nullable=True),
        sa.Column("short_ask", sa.Float(), nullable=True),
        sa.Column("mid_debit", sa.Float(), nullable=True),
        sa.Column("natural_debit", sa.Float(), nullable=True),
        sa.Column("marks", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("context", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("trade_date", "slot", "symbol", "direction", name="uq_zero_dte_obs_slot"),
    )
    op.create_index("idx_zero_dte_obs_trade_date", "zero_dte_observations", ["trade_date"])


def downgrade() -> None:
    op.drop_index("idx_zero_dte_obs_trade_date", table_name="zero_dte_observations")
    op.drop_table("zero_dte_observations")
