"""
The broker_connections schema, asserted against metadata rather than trusted.

test_broker_connections.py runs against an in-memory stand-in, so nothing
there can prove what the DATABASE enforces. Two things matter enough to pin
here, and both are invisible to a route test:

  * the unique index is PARTIAL. If it were not, revoking and reconnecting
    would be impossible: the revoked row would occupy the slot forever, and
    the only way out would be deleting the history the revoke exists to keep.
  * the foreign key CASCADES. A deleted user must not leave encrypted broker
    credentials behind in a table nothing references any more.

Same approach as test_access_request_schema.py, which guards the equivalent
partial index on access_requests.
"""

from __future__ import annotations

from sqlalchemy.schema import CreateIndex

# Imported for its side effect: the FK target must be registered on the shared
# metadata before the foreign key can resolve, and nothing else in this file
# references the users table.
import app.models.user  # noqa: F401
from app.models.broker_connection import (
    STATUS_ACTIVE, STATUS_REVOKED, BrokerConnection,
)


def _index(name: str):
    for ix in BrokerConnection.__table__.indexes:
        if ix.name == name:
            return ix
    raise AssertionError(
        f"no index named {name}; found "
        f"{sorted(i.name for i in BrokerConnection.__table__.indexes)}"
    )


def _compiled(name: str) -> str:
    from sqlalchemy.dialects import postgresql
    return str(CreateIndex(_index(name)).compile(dialect=postgresql.dialect()))


def test_one_active_connection_per_user_broker_and_environment():
    ix = _index("idx_broker_connections_active")
    assert ix.unique is True
    assert [c.name for c in ix.columns] == ["user_id", "broker", "environment"]


def test_the_unique_index_is_partial_on_active_rows():
    """Compiled for PostgreSQL, because this is a postgresql_where dialect
    option: reading the Python attribute would pass on a definition the
    database never receives."""
    sql = _compiled("idx_broker_connections_active")
    assert "WHERE" in sql, (
        "the unique index is not partial — a revoked row would block "
        "reconnecting forever"
    )
    assert f"status = '{STATUS_ACTIVE}'" in sql, sql


def test_the_partial_predicate_would_be_missed_by_a_looser_check():
    """The mutation: a non-partial index still contains the column names, so
    an assertion that only looked for those would pass on the bug."""
    sql = _compiled("idx_broker_connections_active")
    without_where = sql.split("WHERE")[0]
    assert "user_id" in without_where and "broker" in without_where
    assert f"status = '{STATUS_ACTIVE}'" not in without_where


def test_revoked_rows_do_not_occupy_the_slot():
    """States the consequence of the predicate in the terms that matter."""
    assert STATUS_ACTIVE != STATUS_REVOKED
    sql = _compiled("idx_broker_connections_active")
    assert STATUS_REVOKED not in sql, (
        "the index covers revoked rows too, which is the bug it exists to avoid"
    )


def test_deleting_a_user_removes_their_stored_credentials():
    fks = list(BrokerConnection.__table__.c.user_id.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "users"
    assert fk.ondelete == "CASCADE", (
        "a deleted user would leave encrypted broker credentials behind"
    )


def test_the_credential_columns_are_unbounded_text():
    """A Fernet token grows with its plaintext and Alpaca promises no key
    length. A String(n) here would truncate a ciphertext into something that
    fails to decrypt much later, with nothing pointing at the column."""
    for column in ("api_key_enc", "secret_key_enc"):
        col_type = BrokerConnection.__table__.c[column].type
        assert getattr(col_type, "length", None) is None, (
            f"{column} is bounded ({col_type}) — a longer key would be "
            f"silently truncated into an undecryptable value"
        )


def test_a_revoked_row_can_hold_no_credentials():
    """Revoke clears the ciphertext, so the columns must be nullable. NOT NULL
    would force a revoked row to keep a value — either the real key, or a
    placeholder indistinguishable from one."""
    for column in ("api_key_enc", "secret_key_enc"):
        assert BrokerConnection.__table__.c[column].nullable is True
