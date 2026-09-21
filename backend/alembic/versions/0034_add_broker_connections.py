"""add broker_connections

A user's own broker API credentials, encrypted at rest.

Alpaca only in practice, though the `broker` column is not constrained to it.
Alpaca authenticates every request with a key pair and holds no session, so
one process can act for many users. IBKR cannot work that way here: the
gateway container is one logged-in session and IBKR_CLIENT_ID multiplexes
connections to the same account, so per-user IBKR needs a container per user —
infrastructure, not a schema change.

Inert on its own. Nothing reads this table until a user connects a broker, and
the routes that write it refuse to run unless BROKER_ENCRYPTION_KEY is set.

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_connections",
        # No server_default, matching 0030 and 0032: ids come from the model's
        # default=uuid.uuid4 in Python. gen_random_uuid() would need pgcrypto
        # or PG13+, a deployment assumption these migrations do not make.
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("label", sa.String(60), nullable=False, server_default=""),
        # Text, not String(n). A Fernet token grows with the plaintext and
        # Alpaca does not promise a key length; a cap here would truncate a
        # ciphertext into something that fails to decrypt much later, with
        # nothing pointing at the column that did it.
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("secret_key_enc", sa.Text(), nullable=True),
        sa.Column("key_last4", sa.String(8), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # PARTIAL: one ACTIVE connection per user, broker and environment.
    # Revoked rows are kept for history, and a plain unique index would let an
    # old revoked row block reconnecting forever.
    op.create_index(
        "idx_broker_connections_active",
        "broker_connections",
        ["user_id", "broker", "environment"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("idx_broker_connections_user", "broker_connections", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_broker_connections_user", table_name="broker_connections")
    op.drop_index("idx_broker_connections_active", table_name="broker_connections")
    op.drop_table("broker_connections")
