"""
Connection version and the pre-submit revalidation (§7.2, §8).

The version exists so §8's worker can ask one question immediately before
submitting: is this still the connection the order was evaluated against? The
answer has to be wrong in only one direction -- refusing an order that would
have been fine costs a retry, submitting one against a credential that has
been pulled costs an order in an account nobody authorised.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

import app.models.user  # noqa: F401
from app.broker import connection_scoped as cs
from app.broker.connection_scoped import ConnectionNotExecutable, ScopedBroker
from app.models.broker_connection import (
    STATUS_ACTIVE, STATUS_REVOKED, BrokerConnection,
)
from app.services import broker_connection_service as svc

pytestmark = pytest.mark.asyncio

ORG = uuid.UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")


def _row(*, status=STATUS_ACTIVE, version=1):
    return BrokerConnection(
        id=uuid.uuid4(), organization_id=ORG, broker="alpaca",
        environment="paper", label="", status=status, version=version,
        api_key_enc="cipher", secret_key_enc="cipher", key_last4="0000",
        created_at=datetime.now(timezone.utc),
    )


def _scoped(row, *, version=None):
    return ScopedBroker(
        client=object(), connection_id=str(row.id),
        connection_version=version if version is not None else row.version,
        organization_id=str(ORG), environment=row.environment,
    )


# ── The increment rule ───────────────────────────────────────────────────────

def test_revoking_bumps_the_version():
    row = _row(version=1)
    svc._revoke_in_place(row, datetime.now(timezone.utc))
    assert row.status == STATUS_REVOKED
    assert row.version == 2, (
        "revocation is a security-relevant change; an in-flight order holding "
        "version 1 must be stopped by it"
    )


def test_revoking_still_destroys_the_secrets():
    """The version must not distract from what revoke is actually for."""
    row = _row(version=1)
    svc._revoke_in_place(row, datetime.now(timezone.utc))
    assert row.api_key_enc is None and row.secret_key_enc is None


def test_a_row_with_no_version_yet_still_increments():
    """Defensive: a row loaded before migration 0038 ran, or built in a test."""
    row = _row(version=1)
    row.version = None
    svc._revoke_in_place(row, datetime.now(timezone.utc))
    assert row.version == 2


async def test_recording_a_verification_does_NOT_bump_the_version():
    """The asymmetry that makes the version usable.

    Verification records that a credential was checked, not that it changed.
    If it bumped, the periodic health check would invalidate every in-flight
    order for that connection and the check designed to catch a pulled
    credential would instead fire constantly on healthy ones -- which is how a
    safety check gets switched off.
    """
    row = _row(version=5)

    class _DB:
        async def execute(self, stmt):
            class _R:
                def scalar_one_or_none(self_inner):
                    return row
            return _R()
        async def commit(self):
            pass

    await svc.touch_verified(_DB(), row.id)

    assert row.last_verified_at is not None, "the verification was not recorded"
    assert row.version == 5, "verifying a credential invalidated in-flight orders"


def test_the_column_defaults_to_one():
    assert BrokerConnection.__table__.c.version.default.arg == 1
    assert BrokerConnection.__table__.c.version.nullable is False


# ── Pre-submit revalidation ──────────────────────────────────────────────────

async def _patch_get(monkeypatch, row):
    async def _fake(db, connection_id):
        return row
    monkeypatch.setattr(cs.connections, "get_connection", _fake)


async def test_an_unchanged_connection_passes(monkeypatch):
    row = _row(version=3)
    await _patch_get(monkeypatch, row)
    await cs.assert_still_executable(object(), _scoped(row))  # does not raise


async def test_a_bumped_version_stops_the_order(monkeypatch):
    """The case the column exists for: revoked between preparation and submit."""
    row = _row(version=4)
    await _patch_get(monkeypatch, row)

    with pytest.raises(ConnectionNotExecutable) as exc:
        await cs.assert_still_executable(object(), _scoped(row, version=3))

    assert "version 3 to 4" in str(exc.value)
    assert "may no longer be the account that was evaluated" in str(exc.value)


async def test_a_revoked_connection_stops_the_order(monkeypatch):
    row = _row(status=STATUS_REVOKED, version=1)
    await _patch_get(monkeypatch, row)

    with pytest.raises(ConnectionNotExecutable) as exc:
        await cs.assert_still_executable(object(), _scoped(row))
    assert "revoked" in str(exc.value)


async def test_a_deleted_connection_stops_the_order(monkeypatch):
    row = _row()
    await _patch_get(monkeypatch, None)

    with pytest.raises(ConnectionNotExecutable) as exc:
        await cs.assert_still_executable(object(), _scoped(row))
    assert "no longer exists" in str(exc.value)


async def test_revocation_and_revalidation_compose(monkeypatch):
    """End to end on the rule: prepare, revoke, then try to submit."""
    row = _row(version=1)
    scoped = _scoped(row)
    await _patch_get(monkeypatch, row)
    await cs.assert_still_executable(object(), scoped)      # fine before

    svc._revoke_in_place(row, datetime.now(timezone.utc))   # credential pulled

    with pytest.raises(ConnectionNotExecutable):
        await cs.assert_still_executable(object(), scoped)  # stopped after


async def test_the_refusal_names_no_credential(monkeypatch):
    row = _row(version=2)
    row.api_key_enc = "ciphertext-that-must-not-appear"
    await _patch_get(monkeypatch, row)
    try:
        await cs.assert_still_executable(object(), _scoped(row, version=1))
    except ConnectionNotExecutable as exc:
        assert "ciphertext-that-must-not-appear" not in str(exc)


async def test_the_check_would_pass_vacuously_without_the_column(monkeypatch):
    """The mutation this whole change exists to fix.

    Before migration 0038 the version was a constant 1 on both sides, so the
    comparison could never fail. This asserts the fix is real: with the column
    gone from the row entirely, a revoked connection is still caught -- but a
    version bump alone would not be, which is why the status check is a
    separate condition rather than folded into the version comparison.
    """
    row = _row(version=1)
    del row.version
    await _patch_get(monkeypatch, row)
    # No version to compare, so this passes on version -- and that is exactly
    # why status is checked independently.
    await cs.assert_still_executable(object(), _scoped(row, version=1))

    row.status = STATUS_REVOKED
    with pytest.raises(ConnectionNotExecutable):
        await cs.assert_still_executable(object(), _scoped(row, version=1))
