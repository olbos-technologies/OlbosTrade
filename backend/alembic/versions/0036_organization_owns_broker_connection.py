"""organizations own broker connections

Implements docs/adr/0001-broker-connection-ownership.md (Accepted 2026-10-05):
an organization owns a broker connection, and every user gets a personal
organization. Today that is one user, one organization, one connection —
identical behaviour, different key.

ONE MIGRATION ON PURPOSE. The ADR requires it. A window in which some
connections are keyed by user and some by organization is a routing ambiguity
on the money path: "which connection executes for this caller" would have two
answers, and the wrong one sends an order with someone else's credentials.
Tables, backfill, re-key and index rebuild therefore land together or not at
all.

NO CIPHERTEXT MOVES. The backfill changes an owning foreign key and nothing
else. api_key_enc and secret_key_enc are never read, rewritten or re-encrypted
here, so this migration cannot corrupt a credential and does not need
BROKER_ENCRYPTION_KEY to run.

THE INDEX IS REBUILT, NOT EDITED. Dropping and recreating inside the same
transaction keeps "one active connection per scope" true at every point —
Postgres DDL is transactional, so there is no instant where a second active
connection could be inserted for the same scope.

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("kind", sa.String(20), nullable=False, server_default="personal"),
        # Nullable + UNIQUE: set for a personal org, NULL for a team one.
        # Postgres does not collide NULLs, so this enforces "one personal
        # organization per user" without constraining team organizations.
        sa.Column("personal_for_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_organizations_personal_user", "organizations",
                    ["personal_for_user_id"], unique=True)

    op.create_table(
        "organization_members",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_organization_members_user", "organization_members", ["user_id"])

    # One personal organization per existing user, named from the local part of
    # their address purely so the row is recognisable in a console.
    op.execute("""
        INSERT INTO organizations (id, name, kind, personal_for_user_id, created_at)
        SELECT gen_random_uuid(), split_part(u.email, '@', 1), 'personal', u.id, now()
        FROM users u
    """)
    op.execute("""
        INSERT INTO organization_members (organization_id, user_id, role, created_at)
        SELECT o.id, o.personal_for_user_id, 'owner', now()
        FROM organizations o
        WHERE o.personal_for_user_id IS NOT NULL
    """)

    # Nullable first so the backfill has somewhere to write.
    op.add_column("broker_connections",
                  sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute("""
        UPDATE broker_connections bc
        SET organization_id = o.id
        FROM organizations o
        WHERE o.personal_for_user_id = bc.user_id
    """)

    # Fails loudly rather than silently orphaning a connection. A row that did
    # not get an organization means a connection whose user no longer exists,
    # and guessing an owner for a live trading credential is not a recovery.
    op.execute("""
        DO $$
        DECLARE orphaned int;
        BEGIN
            SELECT count(*) INTO orphaned
            FROM broker_connections WHERE organization_id IS NULL;
            IF orphaned > 0 THEN
                RAISE EXCEPTION
                  'migration 0036: % broker_connections rows have no owning organization; '
                  'refusing to guess an owner for a trading credential', orphaned;
            END IF;
        END $$;
    """)
    op.alter_column("broker_connections", "organization_id", nullable=False)
    op.create_foreign_key("fk_broker_connections_organization", "broker_connections",
                          "organizations", ["organization_id"], ["id"], ondelete="CASCADE")

    # user_id stops being the owner and becomes provenance: WHO connected it.
    # Worth keeping -- MASTER_ARCHITECTURE §7.2 wants an actor on connection
    # lifecycle events -- but it must no longer drag the connection into the
    # grave with the user. CASCADE becomes SET NULL, which is also what keeps
    # the model's revoke-not-delete intent true: deleting a user must not
    # destroy the record of when an organization's connection existed.
    op.drop_index("idx_broker_connections_active", table_name="broker_connections")
    op.drop_index("idx_broker_connections_user", table_name="broker_connections")
    op.drop_constraint("broker_connections_user_id_fkey", "broker_connections",
                       type_="foreignkey")
    op.alter_column("broker_connections", "user_id", new_column_name="created_by_user_id",
                    nullable=True)
    op.create_foreign_key("fk_broker_connections_created_by", "broker_connections",
                          "users", ["created_by_user_id"], ["id"], ondelete="SET NULL")

    # Same partial-unique shape as before, re-scoped. Partial so that revoking
    # and reconnecting works: a plain unique index would let the old revoked
    # row block the new one forever.
    op.create_index(
        "idx_broker_connections_active", "broker_connections",
        ["organization_id", "broker", "environment"], unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("idx_broker_connections_org", "broker_connections", ["organization_id"])


def downgrade() -> None:
    # Reversible only while every organization is personal. A team-owned
    # connection has no user to fall back to, so this refuses rather than
    # inventing one.
    op.execute("""
        DO $$
        DECLARE shared int;
        BEGIN
            SELECT count(*) INTO shared
            FROM broker_connections bc
            JOIN organizations o ON o.id = bc.organization_id
            WHERE o.personal_for_user_id IS NULL;
            IF shared > 0 THEN
                RAISE EXCEPTION
                  'migration 0036 downgrade: % connections are owned by a non-personal '
                  'organization and have no single user to revert to', shared;
            END IF;
        END $$;
    """)
    op.execute("""
        UPDATE broker_connections bc
        SET created_by_user_id = o.personal_for_user_id
        FROM organizations o
        WHERE o.id = bc.organization_id AND bc.created_by_user_id IS NULL
    """)

    op.drop_index("idx_broker_connections_org", table_name="broker_connections")
    op.drop_index("idx_broker_connections_active", table_name="broker_connections")
    op.drop_constraint("fk_broker_connections_created_by", "broker_connections",
                       type_="foreignkey")
    op.alter_column("broker_connections", "created_by_user_id", new_column_name="user_id",
                    nullable=False)
    op.create_foreign_key("broker_connections_user_id_fkey", "broker_connections",
                          "users", ["user_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint("fk_broker_connections_organization", "broker_connections",
                       type_="foreignkey")
    op.drop_column("broker_connections", "organization_id")
    op.create_index("idx_broker_connections_active", "broker_connections",
                    ["user_id", "broker", "environment"], unique=True,
                    postgresql_where=sa.text("status = 'active'"))
    op.create_index("idx_broker_connections_user", "broker_connections", ["user_id"])

    op.drop_table("organization_members")
    op.drop_index("idx_organizations_personal_user", table_name="organizations")
    op.drop_table("organizations")
