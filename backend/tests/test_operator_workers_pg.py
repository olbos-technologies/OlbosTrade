"""The designated operator organization is served by the process broker, as ONE scope.

2026-11: OPERATOR_ORGANIZATION_ID (#218) made new autopilot entries attribute to
the operator's organization. The background workers were not told: an
org-attributed trade made `scopes_with_open_trades` yield that org, and
`broker_for_scope` refused it (no Alpaca connection; IBKR has no per-org form),
so the fill poller and reconciler skipped it with a reconciliation error, while
126 legacy NULL rows were swept under the process broker as a second, partial
ledger of the SAME broker account.

What must hold: the operator's org gets the process broker and one merged
ledger (NULL + operator); every OTHER org still needs its own connection and is
never read against the operator's account; with no designation, nothing changes.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from test_worker_scope_pg import (  # noqa: F401  (db fixture + helpers)
    ORG_A, ORG_B, _broker, _position, _seed, db, pytestmark,
)

OP = ORG_A


def _designate(org=OP):
    return patch("app.core.config.settings.operator_organization_id", str(org))


def _valid_designation():
    """Resolve without the users-table check; the designation's validation has its own suite."""
    return patch("app.services.trade_scope.designated_operator_id", return_value=OP)


# ── which broker ────────────────────────────────────────────────────────────

async def test_the_operator_org_resolves_to_the_process_broker_without_a_connection(db):
    from app.services.worker_scope import broker_for_scope

    sentinel = _broker()
    with _valid_designation(), patch("app.broker.broker_factory.get_broker", return_value=sentinel):
        async with db() as s:
            work = await broker_for_scope(s, OP)
    assert work.broker is sentinel and work.organization_id == OP
    assert work.uses_process_broker and work.broker_source == "process:operator"


async def test_another_org_with_no_connection_is_still_unresolved(db):
    from app.services.worker_scope import BrokerUnresolved, broker_for_scope

    with _valid_designation(), patch("app.broker.broker_factory.get_broker", return_value=_broker()) as gb:
        async with db() as s:
            with pytest.raises(BrokerUnresolved):
                await broker_for_scope(s, ORG_B)
    gb.assert_not_called()          # a customer is never read against the operator's account


async def test_without_a_designation_an_org_is_unresolved_as_before(db):
    from app.services.worker_scope import BrokerUnresolved, broker_for_scope

    with patch("app.services.trade_scope.designated_operator_id", return_value=None), \
         patch("app.broker.broker_factory.get_broker", return_value=_broker()) as gb:
        async with db() as s:
            with pytest.raises(BrokerUnresolved):
                await broker_for_scope(s, OP)
    gb.assert_not_called()


async def test_the_unattributed_scope_still_uses_the_process_broker(db):
    from app.services.worker_scope import broker_for_scope

    with _valid_designation(), patch("app.broker.broker_factory.get_broker", return_value=_broker()):
        async with db() as s:
            work = await broker_for_scope(s, None)
    assert work.is_single_operator and work.uses_process_broker


# ── which scopes are visited ────────────────────────────────────────────────

async def test_null_and_operator_rows_collapse_into_one_visit(db):
    from app.services.worker_scope import scopes_with_open_trades

    await _seed(db, None, "LEGACY")
    await _seed(db, OP, "NEW")
    with _valid_designation():
        async with db() as s:
            assert await scopes_with_open_trades(s) == [OP]


async def test_only_null_rows_are_visited_as_the_operator_when_designated(db):
    from app.services.worker_scope import scopes_with_open_trades

    await _seed(db, None, "LEGACY")
    with _valid_designation():
        async with db() as s:
            assert await scopes_with_open_trades(s) == [OP]


async def test_other_orgs_are_still_visited_separately(db):
    from app.services.worker_scope import scopes_with_open_trades

    await _seed(db, None, "LEGACY")
    await _seed(db, ORG_B, "BBB")
    with _valid_designation():
        async with db() as s:
            assert await scopes_with_open_trades(s) == [OP, ORG_B]


async def test_without_a_designation_null_and_org_are_separate_scopes(db):
    from app.services.worker_scope import scopes_with_open_trades

    await _seed(db, None, "LEGACY")
    await _seed(db, OP, "NEW")
    with patch("app.services.trade_scope.designated_operator_id", return_value=None):
        async with db() as s:
            assert await scopes_with_open_trades(s) == [None, OP]


# ── the reconciler sees one merged ledger, and never another org's ──────────

async def _reconcile(db, org, broker_positions):
    from app.services.position_reconciler import PositionReconciler

    rec = PositionReconciler(_broker(broker_positions), organization_id=org)
    return await rec.check()


async def test_the_operator_reconciler_matches_legacy_and_attributed_rows_against_one_broker(db):
    await _seed(db, None, "LEGACY")
    await _seed(db, OP, "NEW")
    with _valid_designation():
        r = await _reconcile(db, OP, [_position("LEGACY"), _position("NEW")])
    assert r.clean and r.untracked_at_broker == [] and r.phantom_in_db == [], r
    assert r.db_open_trade_count == 2


async def test_a_position_only_at_the_broker_is_untracked_not_hidden_by_the_merge(db):
    await _seed(db, OP, "NEW")
    with _valid_designation():
        r = await _reconcile(db, OP, [_position("NEW"), _position("STRAY")])
    assert not r.clean and r.untracked_at_broker == ["STRAY"] and r.phantom_in_db == []


async def test_a_row_the_broker_lacks_is_phantom_whether_legacy_or_attributed(db):
    await _seed(db, None, "LEGACY")
    await _seed(db, OP, "NEW")
    with _valid_designation():
        r = await _reconcile(db, OP, [])
    assert not r.clean and r.phantom_in_db == ["LEGACY", "NEW"]


async def test_another_orgs_trade_is_not_phantom_for_the_operator_reconciler(db):
    await _seed(db, OP, "NEW")
    await _seed(db, ORG_B, "THEIRS")
    with _valid_designation():
        r = await _reconcile(db, OP, [_position("NEW")])
    assert r.clean and r.phantom_in_db == [] and r.db_open_trade_count == 1


async def test_an_org_reconciler_does_not_widen_to_unattributed_rows(db):
    await _seed(db, None, "LEGACY")
    await _seed(db, ORG_B, "THEIRS")
    with _valid_designation():
        r = await _reconcile(db, ORG_B, [_position("THEIRS")])
    assert r.clean and r.db_open_trade_count == 1


async def test_without_a_designation_the_operator_org_reconciler_sees_only_its_own_rows(db):
    await _seed(db, None, "LEGACY")
    await _seed(db, OP, "NEW")
    with patch("app.services.trade_scope.designated_operator_id", return_value=None):
        r = await _reconcile(db, OP, [_position("NEW")])
    assert r.clean and r.db_open_trade_count == 1
