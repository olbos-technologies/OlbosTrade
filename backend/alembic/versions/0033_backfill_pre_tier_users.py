"""backfill pre-tier accounts to elite

Accounts created before tier enforcement existed all carry the column default,
`free` — and Free has no broker access. So the deploy that turns enforcement on
locks every existing operator out of approve, reject, manual-trade,
close-position and the rest, on a live trading system, with no warning and no
route back through the UI.

That happened. Both accounts on the production instance were `free`, caught by
inspection minutes before the deploy, and fixed with a hand-written UPDATE.
This is that fix, made repeatable for anyone else upgrading through here.

ONLY WHEN THE INTENT IS UNAMBIGUOUS. If every user is `free`, nobody has ever
assigned a tier: the column has only ever held its default, so every row
predates enforcement and every row is an operator account. If ANY user is
already `pro` or `elite`, somebody is managing tiers deliberately and a blanket
elevation would hand paid capability to accounts that were meant to be limited.
In that case this does nothing.

The check and the update run in one statement for a reason — reading the
tiers, deciding, and then writing would be a race against any account created
in between.

Revision ID: 0033
Revises: 0032
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users
           SET tier = 'elite'
         WHERE tier = 'free'
           AND NOT EXISTS (
               SELECT 1 FROM users WHERE tier <> 'free'
           )
        """
    )


def downgrade() -> None:
    """Deliberately a no-op.

    The previous value was `free` for every row this touched, so a literal
    reverse is `UPDATE users SET tier = 'free'` — which would lock the
    operator out again, and would also catch any account legitimately promoted
    to elite afterwards, because nothing here records which is which. A
    downgrade that silently removes someone's access is worse than one that
    leaves tiers alone.
    """
