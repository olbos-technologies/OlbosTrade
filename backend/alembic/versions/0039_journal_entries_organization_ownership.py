"""journal entries are owned by an organization

Implements the customer-isolation half of ADR-0001 for journal_entries, which
had NO ownership column at all — not organization_id, not even user_id. Every
journal route was a shared query: listing returned everyone's entries, and
fetching or updating by id worked on any row regardless of who asked.

NULLABLE, DELIBERATELY, AND IT STAYS THAT WAY. The obvious next step would be
to backfill and then set NOT NULL, and it is the wrong one here. A journal
entry's owner is not recoverable from the row: these pre-date any notion of
ownership, and nothing in the schema records who wrote them. Making the column
NOT NULL would force this migration to invent an answer for every historical
row, and the only answer available is a guess.

THE BACKFILL ASSIGNS ONLY THE UNAMBIGUOUS CASE. Exactly one personal
organization means exactly one person could have written these, so they are
assigned to it. Zero organizations (an operator running with auth disabled) or
more than one (ambiguous) leaves them NULL and raises a NOTICE with the count,
because handing one customer's trading journal to another is a worse outcome
than leaving old rows unattributed.

NULL IS NOT A WILDCARD. Rows with no owner match only the unowned scope used
when auth is disabled; they are invisible to every organization-scoped query.
An unattributed row therefore fails closed rather than leaking to whoever asks
first.

Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_journal_entries_organization", "journal_entries",
        "organizations", ["organization_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index(
        "idx_journal_entries_organization", "journal_entries", ["organization_id"]
    )

    # Assign only when there is exactly one personal organization, which is the
    # single-operator history this column was added for. Anything else is left
    # unattributed on purpose.
    op.execute("""
        DO $$
        DECLARE
            org_count int;
            only_org  uuid;
            orphaned  int;
        BEGIN
            SELECT count(*) INTO org_count
            FROM organizations WHERE personal_for_user_id IS NOT NULL;

            IF org_count = 1 THEN
                SELECT id INTO only_org
                FROM organizations WHERE personal_for_user_id IS NOT NULL;
                UPDATE journal_entries
                SET organization_id = only_org
                WHERE organization_id IS NULL;
                RAISE NOTICE
                  'migration 0039: journal entries assigned to the single '
                  'personal organization %', only_org;
            ELSE
                SELECT count(*) INTO orphaned
                FROM journal_entries WHERE organization_id IS NULL;
                RAISE NOTICE
                  'migration 0039: % personal organizations found, so % journal '
                  'entry/entries were LEFT UNATTRIBUTED rather than guessed. They '
                  'are visible only in single-operator (auth disabled) mode.',
                  org_count, orphaned;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_index("idx_journal_entries_organization", table_name="journal_entries")
    op.drop_constraint(
        "fk_journal_entries_organization", "journal_entries", type_="foreignkey"
    )
    op.drop_column("journal_entries", "organization_id")
