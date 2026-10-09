"""
The AccessRequest model and migration 0032 must describe the same schema.

Same motivation as test_auth_model_schema.py: model/migration drift is
invisible until an autogenerate run proposes something alarming, or until the
constraint you thought you had turns out not to exist.

One check here is doing more work than it looks. The partial unique index is
the ONLY thing making "one live request per address" true — the route's read
before write is a convenience, not a guarantee, because two requests can
interleave between the SELECT and the INSERT. And that index is defined by a
raw SQL predicate, `status = 'pending'`, which no Python reference reaches. So
if STATUS_PENDING were ever renamed, the model would keep compiling, every
route test would keep passing, and the index would quietly cover zero rows.
test_the_partial_index_predicate_matches_the_constant is what fails instead.
"""

from __future__ import annotations

import pathlib
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.models.access_request import STATUS_PENDING, STATUSES, AccessRequest

MIGRATION = (pathlib.Path(__file__).parent.parent
             / "alembic/versions/0032_add_access_requests.py")


def _index(name):
    return next(ix for ix in AccessRequest.__table__.indexes if ix.name == name)


def _rendered(name) -> str:
    return str(CreateIndex(_index(name)).compile(dialect=postgresql.dialect()))


def test_the_email_index_is_partial_not_global():
    """A plain unique index on email would make every denial permanent.

    Asserted on the COMPILED DDL rather than on the postgresql_where kwarg,
    because a kwarg that a dialect silently ignores is indistinguishable from
    one that works when you only inspect the Python.
    """
    ddl = " ".join(_rendered("idx_access_requests_email_pending").split())
    assert "UNIQUE INDEX" in ddl
    assert "WHERE status = 'pending'" in ddl, (
        f"the email index is not partial — it renders as: {ddl}"
    )


def test_the_partial_index_predicate_matches_the_constant():
    """The index's predicate is a raw string no refactor will follow.

    Rename STATUS_PENDING and this file is the only thing that notices; the
    index would keep existing, keep being unique, and keep matching nothing.
    """
    ddl = _rendered("idx_access_requests_email_pending")
    assert f"= '{STATUS_PENDING}'" in ddl, (
        f"the index filters on a status literal that is not STATUS_PENDING "
        f"({STATUS_PENDING!r}): {ddl}"
    )


def test_the_migration_creates_the_same_partial_index():
    """The model describes the table; the migration is what Postgres runs."""
    sql = MIGRATION.read_text()
    assert f"status = '{STATUS_PENDING}'" in sql, (
        "migration 0032 does not create the index partial on STATUS_PENDING — "
        "the model's guarantee would not exist in a real database"
    )


def test_model_indexes_match_the_migration():
    sql = MIGRATION.read_text()
    declared = {ix.name for ix in AccessRequest.__table__.indexes}
    created = set(re.findall(r'op\.create_index\(\s*"([^"]+)"', sql))
    assert declared == created, (
        f"model indexes {sorted(declared)} != migration indexes {sorted(created)}"
    )


def test_every_created_index_is_dropped_again():
    """A downgrade that leaves indexes behind fails on the next upgrade."""
    sql = MIGRATION.read_text()
    created = set(re.findall(r'op\.create_index\(\s*"([^"]+)"', sql))
    dropped = set(re.findall(r'op\.drop_index\(\s*"([^"]+)"', sql))
    assert created == dropped, f"created {sorted(created)}, dropped {sorted(dropped)}"


def test_model_columns_match_the_migration():
    sql = MIGRATION.read_text()
    declared = {c.name for c in AccessRequest.__table__.columns}
    created = set(re.findall(r'sa\.Column\(\s*"([^"]+)"', sql))
    assert declared == created, (
        f"model columns {sorted(declared)} != migration columns {sorted(created)}"
    )


def test_uniqueness_is_declared_exactly_once():
    """One mechanism per column. Both is not twice as unique, just ambiguous —
    and it is what made metadata-based creation diverge from 0030."""
    constraints = {
        c.name or "<unnamed>"
        for c in AccessRequest.__table__.constraints
        if isinstance(c, sa.UniqueConstraint)
    }
    assert constraints == set(), (
        f"AccessRequest declares UNIQUE constraints {sorted(constraints)} as "
        "well as a unique index; migration 0032 creates only the index"
    )


def test_the_token_column_holds_a_sha256_digest_not_a_token():
    """64 hex characters. A column wide enough for the plaintext token is the
    first sign someone stored one."""
    col = AccessRequest.__table__.c.setup_token_hash
    assert col.type.length == 64
    assert col.nullable, "the hash exists only between approval and redemption"


def test_the_statuses_a_request_can_hold_fit_the_column():
    col = AccessRequest.__table__.c.status
    assert all(len(s) <= col.type.length for s in STATUSES)


def test_the_migration_chains_from_the_current_head():
    """Two migrations sharing a down_revision give alembic two heads, and
    `alembic upgrade head` then refuses to run at all."""
    versions = MIGRATION.parent
    downs = []
    for f in versions.glob("*.py"):
        m = re.search(r'^down_revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)',
                      f.read_text(), re.M)
        if m:
            downs.append((m.group(1), f.name))
    duplicated = {r for r, _ in downs if sum(1 for d, _ in downs if d == r) > 1}
    assert not duplicated, f"branched revision chain at {sorted(duplicated)}"


#: Model modules alembic's environment cannot see TODAY. Thirteen tables,
#: including signal_outcomes and watchlists.
#:
#: This is a pre-existing gap, not something this change introduced, and
#: fixing it means touching the migration environment for eleven unrelated
#: models — its own change, with its own review. It is recorded here rather
#: than left implicit so the test below can fail on a NEW omission without
#: either blocking on these or quietly passing over them.
#:
#: This set should only ever shrink. Removing a name from it is the fix.
KNOWN_UNIMPORTED = {
    "alert",
    "daily_signal_snapshot",
    "execution_event",
    "options_scan_rejection",
    "options_signal_history",
    "reconciliation_snapshot",
    "signal_outcome",
    "strategy_preset",
    "strategy_profile",
    "strategy_snapshot",
    "watchlist",
}


def _unimported_model_modules() -> set:
    import re

    env = (MIGRATION.parent.parent / "env.py").read_text()
    block = re.search(r"from app\.models import \((.*?)\)", env, re.S)
    assert block, "env.py no longer imports app.models by name — check this test"
    imported = set(re.findall(r"[\w]+", block.group(1)))

    models_dir = MIGRATION.parent.parent.parent / "app" / "models"
    on_disk = {
        f.stem for f in models_dir.glob("*.py")
        if f.stem != "__init__" and "Base" in f.read_text()
    }
    return on_disk - imported


def test_this_table_is_visible_to_the_migration_environment():
    """alembic/env.py imports each model module by name, then hands
    Base.metadata to alembic. A module missing from that list is a table
    alembic cannot see — so `alembic revision --autogenerate` omits changes to
    it and, worse, can propose DROPPING a live table because the metadata says
    it should not exist. access_request was missing exactly this way: the
    migration created the table and the environment could not see it."""
    assert "access_request" not in _unimported_model_modules()


def test_no_new_model_module_is_left_out_of_the_migration_environment():
    """The baseline above may shrink, never grow.

    Written against the whole directory rather than one name, because the next
    model added will have this problem and a test naming access_request would
    not notice.
    """
    missing = _unimported_model_modules()

    newly_missing = sorted(missing - KNOWN_UNIMPORTED)
    assert not newly_missing, (
        f"these model modules define tables alembic cannot see: "
        f"{newly_missing}. Autogenerate will ignore them, or propose dropping "
        "their tables. Add them to the import list in alembic/env.py."
    )

    fixed = sorted(KNOWN_UNIMPORTED - missing)
    assert not fixed, (
        f"{fixed} are imported now — delete them from KNOWN_UNIMPORTED so the "
        "baseline keeps shrinking instead of hiding the next regression."
    )
