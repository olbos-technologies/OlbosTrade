"""trades are owned by an organization

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-09

`trades` had no ownership column — not organization_id, not even user_id — and
was read by 22 modules, every one of them a shared query. This is Batch E2,
the half Batch E (migration 0039, journal entries) deliberately deferred
because `trades` is read far more widely.

THIS MIGRATION DOES NOT GUESS, for the same reason 0039 did not. A
pre-ownership trade's owner is not recoverable from the row: `approved_by`
holds a role label ("user", "manual", "autopilot", "reconciler_adopt"), never
an identity; `strategy_snapshots` has no organization_id; and nothing links a
trade to the broker_connections row that executed it. So organization_id is
nullable and stays that way — NOT NULL would force this migration to invent an
answer for every historical position.

The backfill assigns only the unambiguous case: exactly one personal
organization means exactly one person could have opened them. Zero (an
operator with auth disabled) or several leaves them unattributed and raises a
NOTICE with the count, because attributing one customer's position history to
another is worse than leaving old rows unowned.

NULL IS A SCOPE, NOT A WILDCARD. Unattributed rows match only the
single-operator scope used when auth is disabled, so they are invisible to
every organization-scoped query and fail closed.

ONE DIFFERENCE FROM 0039 WORTH KNOWING. A journal entry nobody owns is inert.
An *open* trade nobody owns is a live position: the fills poller still polls
it, the excursion tracker still tracks it, stop backfill still writes stops to
it, and the reconciler still compares it against a broker. Those background
paths are NOT scoped in this change (Batch E2 steps 4 and 6, deliberately held
back), so they continue to process every row as they do today. That is the
current single-operator behaviour preserved exactly; it is not multi-tenant
safe, and the modules concerned are listed in
docs/batch-e2-trades-isolation-analysis.md §3-4.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_trades_organization", "trades",
        "organizations", ["organization_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("idx_trades_organization", "trades", ["organization_id"])
    # The duplicate guard reads (organization_id, underlying, status) on every
    # entry, which is the hottest scoped query in the system.
    op.create_index(
        "idx_trades_org_underlying_status",
        "trades",
        ["organization_id", "underlying", "status"],
    )

    # Assign only when there is exactly one personal organization, which is the
    # only case where the owner is not a guess.
    op.execute("""
        DO $$
        DECLARE
            org_count int;
            only_org uuid;
            orphaned int;
            open_orphaned int;
        BEGIN
            SELECT count(*) INTO org_count
            FROM organizations WHERE personal_for_user_id IS NOT NULL;

            IF org_count = 1 THEN
                SELECT id INTO only_org
                FROM organizations WHERE personal_for_user_id IS NOT NULL;
                UPDATE trades
                SET organization_id = only_org
                WHERE organization_id IS NULL;
                RAISE NOTICE
                  'migration 0041: existing trades assigned to the only '
                  'personal organization %', only_org;
            ELSE
                SELECT count(*) INTO orphaned
                FROM trades WHERE organization_id IS NULL;
                SELECT count(*) INTO open_orphaned
                FROM trades
                WHERE organization_id IS NULL AND status IN ('open', 'pending');
                RAISE NOTICE
                  'migration 0041: % personal organizations found, so % trades '
                  '(% of them OPEN or PENDING) are left unattributed rather '
                  'than guessed. They are visible only to the single-operator '
                  'scope. Open positions still participate in background fill '
                  'polling and reconciliation, which are not organization '
                  'scoped in this change.',
                  org_count, orphaned, open_orphaned;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_index("idx_trades_org_underlying_status", table_name="trades")
    op.drop_index("idx_trades_organization", table_name="trades")
    op.drop_constraint("fk_trades_organization", "trades", type_="foreignkey")
    op.drop_column("trades", "organization_id")
