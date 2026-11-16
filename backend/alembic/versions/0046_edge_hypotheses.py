"""Add edge_hypotheses — the Edge Lab hypothesis ledger.

Revision ID: 0046
Revises: 0045
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "edge_hypotheses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("family", sa.String(length=80), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("universe", postgresql.JSONB(), nullable=False),
        sa.Column("horizon", sa.String(length=60), nullable=False),
        sa.Column("criteria", postgresql.JSONB(), nullable=False),
        sa.Column("data_plan", sa.Text(), nullable=False),
        sa.Column("retroactive", sa.Boolean(), nullable=False),
        sa.Column("registered_by", sa.String(length=80), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="registered"),
        sa.Column("results", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("verdict_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("retired_reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_edge_hypotheses_family", "edge_hypotheses", ["family"])
    op.create_index("idx_edge_hypotheses_status", "edge_hypotheses", ["status"])


def downgrade() -> None:
    op.drop_index("idx_edge_hypotheses_status", table_name="edge_hypotheses")
    op.drop_index("idx_edge_hypotheses_family", table_name="edge_hypotheses")
    op.drop_table("edge_hypotheses")
