"""
Migration 0033 elevates pre-tier accounts — but only when intent is clear.

The failure it exists to prevent is specific and was live. Accounts created
before tier enforcement carry the column default `free`, and Free has no
broker access. So the deploy that turns enforcement on locks every existing
operator out of approve, reject, manual-trade and close-position on a running
trading system, with no route back through the UI. Both production accounts
were `free`; it was caught by inspection minutes before the deploy.

The risk in fixing it is the opposite one — a blanket `UPDATE users SET tier =
'elite'` hands full capability to accounts somebody deliberately limited. So
the migration only acts when NO user has ever been assigned a tier.

These tests run the migration's OWN SQL, lifted out of the file, against
sqlite — not a retyped copy, which would let the file drift from what is
tested. sqlite is in the standard library, so this adds no dependency (see
test_auth_flag_is_explicit.py for why that matters here).
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

MIGRATION = (pathlib.Path(__file__).parent.parent
             / "alembic/versions/0033_backfill_pre_tier_users.py")


def _upgrade_sql() -> str:
    """The SQL inside op.execute(), as the migration actually runs it."""
    text = MIGRATION.read_text()
    body = text[text.index("def upgrade()"):text.index("def downgrade()")]
    match = re.search(r'op\.execute\(\s*"""(.*?)"""', body, re.S)
    assert match, "could not find the op.execute SQL in 0033"
    return match.group(1)


def _db(tiers: list) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (email TEXT, tier TEXT)")
    conn.executemany("INSERT INTO users VALUES (?, ?)",
                     [(f"u{i}@example.com", t) for i, t in enumerate(tiers)])
    return conn


def _tiers_after(tiers: list) -> list:
    conn = _db(tiers)
    conn.executescript(_upgrade_sql())
    return [r[0] for r in conn.execute("SELECT tier FROM users").fetchall()]


def test_the_sql_was_actually_extracted():
    """Vacuity guard: every test below runs whatever this returns, and an
    empty string would execute nothing and pass everything."""
    sql = _upgrade_sql().strip()
    assert "UPDATE users" in sql
    assert "elite" in sql


def test_all_free_accounts_are_elevated():
    """Nobody has ever assigned a tier, so every row is the column default and
    every row predates enforcement. This is the production case."""
    assert _tiers_after(["free", "free"]) == ["elite", "elite"]


def test_nothing_changes_when_tiers_are_already_managed():
    """Someone set a tier deliberately, so a blanket elevation would hand
    broker access to an account that was meant to be limited."""
    assert _tiers_after(["free", "pro"]) == ["free", "pro"]
    assert _tiers_after(["free", "elite"]) == ["free", "elite"]
    assert _tiers_after(["free", "free", "elite"]) == ["free", "free", "elite"]


def test_an_already_fixed_install_is_untouched():
    """The production box was repaired by hand before this migration existed.
    Running it there must be a no-op, not a second elevation of something."""
    assert _tiers_after(["elite", "elite"]) == ["elite", "elite"]


def test_an_empty_users_table_is_fine():
    assert _tiers_after([]) == []


def test_the_table_is_locked_before_the_update():
    """One statement is NOT enough on READ COMMITTED.

    PostgreSQL's default isolation evaluates the UPDATE's NOT EXISTS against
    the snapshot taken when the statement began, so a `pro` or `elite` user
    committed by another connection an instant later is invisible — and every
    `free` row is elevated regardless, which is exactly what the guard is for.

    The window is not theoretical: up.sh and update.sh start the backend
    BEFORE running alembic, so the API is live and creating users while this
    migration runs. Raised in review on #75; my own reasoning had stopped at
    "it is a single statement, so it cannot race".
    """
    body = MIGRATION.read_text()
    upgrade = body[body.index("def upgrade()"):body.index("def downgrade()")]

    assert "LOCK TABLE users" in upgrade, (
        "no table lock — the NOT EXISTS guard races any concurrent user write")
    assert upgrade.index("LOCK TABLE users") < upgrade.index("UPDATE users"), (
        "the lock must be taken BEFORE the update, or it guards nothing")
    assert "EXCLUSIVE" in upgrade, (
        "the lock mode must block concurrent writes")


def test_the_condition_is_in_the_sql_not_in_python():
    """The guard belongs in the UPDATE, not in a Python branch.

    Reading the tiers, deciding in Python, then writing would race in a second
    way that the table lock alone would not obviously cover. Checked
    structurally rather than by string matching, because my first version of
    this test was unreadable regex guesswork that failed against correct code.
    """
    import ast

    tree = ast.parse(MIGRATION.read_text())
    upgrade = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "upgrade")

    branches = [n for n in ast.walk(upgrade)
                if isinstance(n, (ast.If, ast.For, ast.While))]
    assert not branches, (
        "upgrade() branches in Python — the decision belongs in the SQL")

    assert "NOT EXISTS" in _upgrade_sql().upper()


def test_the_downgrade_does_not_revoke_access():
    """A literal reverse is `UPDATE users SET tier = 'free'`, which locks the
    operator out again AND catches anyone legitimately promoted afterwards,
    because nothing records which is which."""
    body = MIGRATION.read_text()
    downgrade = body[body.index("def downgrade()"):]

    assert "op.execute" not in downgrade, (
        "0033's downgrade runs SQL — a reverse of this migration silently "
        "removes access and cannot tell a backfilled row from a deliberate one")


@pytest.mark.parametrize("bad", ["ELITE", "Elite", " elite"])
def test_the_migration_writes_the_canonical_lowercase_tier(bad):
    """tier matching is case-sensitive: limits_for('ELITE') falls back to FREE.
    A migration writing the wrong case would look correct in psql and leave
    everyone locked out anyway."""
    assert f"'{bad}'" not in _upgrade_sql()
