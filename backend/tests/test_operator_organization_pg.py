"""OPERATOR_ORGANIZATION_ID: a stated owner for autonomous entries.

Why this exists. Production has three personal organizations — all created by
the one migration that gave every pre-tenancy user an organization — and only
one is the operator: the others are a deactivated account and a sign-up nobody
used. `autonomous_scope` refused to guess between three, so every autopilot
entry was blocked `owner_ambiguous` (1,476 times in ten days). The refusal was
correct; the fix is for the operator to *say* who they are.

Two properties, and the second is the one that makes the first safe:

1. **The designation resolves ownership, and never guesses.** A value that is
   unset keeps the old rule; one that is malformed, unknown, not a personal
   organization, or belongs to a deactivated user REFUSES — it must not fall
   back to counting organizations, because that would turn a typo into a guess.

2. **Designating an owner must not blind the duplicate guard.** All of the
   operator's history is unattributed (NULL; migration 0041 would not guess).
   Entries are now stamped with the operator's organization, and the duplicate
   guard and cooldown filter by owner — so without the widening in
   `owned_by_for_entry_guards` the desk would re-buy a name it already holds
   and skip the cooldown after a stop-out. Only the *designated* organization
   inherits those rows; every other organization keeps the strict rule.

Real PostgreSQL, for the reason test_trade_scope_entry_pg.py gives.
"""
from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text

# Same harness as the ownership tests: same stub broker, same neutralised
# upstream stages, so these assert on the ownership stage and nothing else.
from test_trade_scope_entry_pg import (  # noqa: F401  (db is a fixture)
    ORG_A, ORG_B, TEST_DB_URL, _as_org, _broker, _market_open, _patches,
    _seed_open_trade, _signal, db,
)

from app.api.routes.trade_desk import _execute_signal
from app.models.trade import Trade
from app.services import trade_scope
from app.services.trade_scope import AmbiguousOwner

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]

ORG_C = uuid.UUID("cccccccc-0000-4000-8000-00000000000c")


@pytest_asyncio.fixture
async def orgs(db):
    """Three personal organizations with active users, like production.

    `db` seeds A and B with random user ids but no users table; the designation
    check joins to users to refuse a deactivated one, so build that here.
    """
    async with db() as s, s.begin():
        await s.execute(text("DROP TABLE IF EXISTS users"))
        await s.execute(text("CREATE TABLE users (id uuid PRIMARY KEY, is_active boolean NOT NULL)"))
        await s.execute(text(
            "INSERT INTO organizations (id, personal_for_user_id) VALUES (:i, :u)"
        ), {"i": ORG_C, "u": uuid.uuid4()})
        await s.execute(text(
            "INSERT INTO users (id, is_active) "
            "SELECT personal_for_user_id, true FROM organizations "
            "WHERE personal_for_user_id IS NOT NULL"
        ))
    yield db
    async with db() as s, s.begin():
        await s.execute(text("DROP TABLE IF EXISTS users"))


def _designate(org):
    return patch("app.core.config.settings.operator_organization_id", str(org))


def _auth_on():
    return patch("app.core.config.settings.auth_enabled", True)


async def _scope(factory):
    async with factory() as s:
        return await trade_scope.autonomous_scope(s)


async def _seed_closed_trade(factory, org_id, ticker="SPY"):
    async with factory() as s, s.begin():
        s.add(Trade(
            id=uuid.uuid4(), organization_id=org_id, strategy="bull_put_spread",
            underlying=ticker, spread_type="put", short_strike=450, long_strike=445,
            expiration=dt.date(2026, 12, 19),
            entry_date=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3),
            exit_date=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20),
            status="closed",
        ))


# ── 1. the designation resolves ownership, and never guesses ────────────────

async def test_a_designated_operator_resolves_among_several_organizations(orgs):
    """Three personal organizations is `owner_ambiguous` without a designation."""
    with _auth_on():
        with pytest.raises(AmbiguousOwner):
            await _scope(orgs)
        with _designate(ORG_B):
            assert await _scope(orgs) == ORG_B


async def test_an_unset_designation_keeps_the_original_rule(orgs):
    with _auth_on(), patch("app.core.config.settings.operator_organization_id", ""), \
         pytest.raises(AmbiguousOwner):
        await _scope(orgs)


async def test_whitespace_only_counts_as_unset(orgs):
    with _auth_on(), patch("app.core.config.settings.operator_organization_id", "   "), \
         pytest.raises(AmbiguousOwner):
        await _scope(orgs)


@pytest.mark.parametrize("bad", ["not-a-uuid", "0f3da72a", "0f3da72a-03ff-470a"])
async def test_a_malformed_designation_refuses_even_when_one_organization_exists(orgs, bad):
    """Must not fall back to "the only one" — that turns a typo into a guess."""
    async with orgs() as s, s.begin():
        await s.execute(text("DELETE FROM organizations WHERE id IN (:b, :c)"),
                        {"b": ORG_B, "c": ORG_C})
    with _auth_on(), patch("app.core.config.settings.operator_organization_id", bad):
        with pytest.raises(AmbiguousOwner, match="not a UUID"):
            await _scope(orgs)


async def test_an_unknown_organization_refuses_even_when_one_organization_exists(orgs):
    async with orgs() as s, s.begin():
        await s.execute(text("DELETE FROM organizations WHERE id IN (:b, :c)"),
                        {"b": ORG_B, "c": ORG_C})
    with _auth_on(), _designate(uuid.uuid4()):
        with pytest.raises(AmbiguousOwner, match="not a personal organization"):
            await _scope(orgs)


async def test_a_non_personal_organization_is_refused(orgs):
    async with orgs() as s, s.begin():
        await s.execute(text(
            "UPDATE organizations SET personal_for_user_id = NULL WHERE id = :i"
        ), {"i": ORG_B})
    with _auth_on(), _designate(ORG_B):
        with pytest.raises(AmbiguousOwner, match="not a personal organization"):
            await _scope(orgs)


async def test_a_deactivated_users_organization_is_refused(orgs):
    async with orgs() as s, s.begin():
        await s.execute(text(
            "UPDATE users SET is_active = false WHERE id = "
            "(SELECT personal_for_user_id FROM organizations WHERE id = :i)"
        ), {"i": ORG_B})
    with _auth_on(), _designate(ORG_B):
        with pytest.raises(AmbiguousOwner, match="deactivated"):
            await _scope(orgs)
    # and the still-active one resolves, so the refusal above is about B.
    with _auth_on(), _designate(ORG_A):
        assert await _scope(orgs) == ORG_A


async def test_auth_disabled_ignores_the_designation(orgs):
    """Single-operator mode has one scope; a stale setting must not change it."""
    with patch("app.core.config.settings.auth_enabled", False), _designate(ORG_B):
        assert await _scope(orgs) is None


async def test_the_refusal_reaches_the_entry_as_owner_ambiguous(orgs):
    """End to end: a bad designation blocks the order, with the reason named."""
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _auth_on(), _designate(uuid.uuid4()):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")
    assert res["result"] == "blocked"
    assert "owner_ambiguous" in res["reason"]
    broker.place_order.assert_not_awaited()


async def test_a_designated_autopilot_entry_proceeds(orgs):
    """The point of the change: three organizations, and the desk enters."""
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _auth_on(), _designate(ORG_B):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")
    assert res["result"] not in ("blocked", "skipped"), res
    broker.place_order.assert_awaited_once()


# ── 2. designating an owner must not blind the duplicate guard ──────────────

async def test_the_operator_still_sees_its_legacy_open_position(orgs):
    """The hazard. A pre-tenancy position (NULL owner) is still the operator's
    for the duplicate check, so the desk does not buy SPY twice."""
    await _seed_open_trade(orgs, None, "SPY")
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _auth_on(), _designate(ORG_B):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")
    assert res["result"] == "skipped"
    assert res["reason"] == "already_open"
    broker.place_order.assert_not_awaited()


async def test_the_operator_still_sees_its_own_attributed_position(orgs):
    await _seed_open_trade(orgs, ORG_B, "SPY")
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _auth_on(), _designate(ORG_B):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")
    assert res["reason"] == "already_open"
    broker.place_order.assert_not_awaited()


async def test_the_operator_is_not_blocked_by_another_tenants_position(orgs):
    """Widening adds the NULL rows only — not other organizations' rows."""
    await _seed_open_trade(orgs, ORG_A, "SPY")
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _auth_on(), _designate(ORG_B):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")
    assert res["result"] not in ("blocked", "skipped"), res
    broker.place_order.assert_awaited_once()


async def test_another_organization_still_does_not_inherit_legacy_rows(orgs):
    """The strict rule survives for everyone who is not the designated owner:
    for a tenant, an unattributed position is somebody else's."""
    await _seed_open_trade(orgs, None, "SPY")
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _as_org(ORG_A), _designate(ORG_B):
        res = await _execute_signal(_signal("SPY"), approved_by="user", conn=object())
    assert res["result"] not in ("skipped", "blocked"), res
    broker.place_order.assert_awaited_once()


async def test_the_cooldown_after_a_legacy_close_still_applies_to_the_operator(orgs):
    """A stop-out on an unattributed trade must still hold off re-entry."""
    await _seed_closed_trade(orgs, None, "SPY")
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _auth_on(), _designate(ORG_B), \
         patch("app.core.config.settings.position_cooldown_hours", 2):
        res = await _execute_signal(_signal("SPY"), approved_by="autopilot")
    assert res["result"] == "skipped"
    assert res["reason"] == "cooldown_active"
    broker.place_order.assert_not_awaited()


async def test_another_organization_has_no_cooldown_from_a_legacy_close(orgs):
    await _seed_closed_trade(orgs, None, "SPY")
    broker = _broker()
    p = _patches(broker)
    with p[0], p[1], p[2], p[3], p[4], _as_org(ORG_A), _designate(ORG_B), \
         patch("app.core.config.settings.position_cooldown_hours", 2):
        res = await _execute_signal(_signal("SPY"), approved_by="user", conn=object())
    assert res["result"] not in ("skipped", "blocked"), res


# ── 3. the filter itself ────────────────────────────────────────────────────

def _sql(clause):
    from sqlalchemy.dialects import postgresql
    return str(clause.compile(dialect=postgresql.dialect()))


async def test_with_no_designation_the_filter_is_exactly_owned_by():
    with patch("app.core.config.settings.operator_organization_id", ""):
        assert _sql(trade_scope.owned_by_for_entry_guards(ORG_A)) == _sql(trade_scope.owned_by(ORG_A))
        assert _sql(trade_scope.owned_by_for_entry_guards(None)) == _sql(trade_scope.owned_by(None))


async def test_a_malformed_designation_widens_nothing():
    with patch("app.core.config.settings.operator_organization_id", "garbage"):
        assert trade_scope.designated_operator_id() is None
        assert _sql(trade_scope.owned_by_for_entry_guards(ORG_A)) == _sql(trade_scope.owned_by(ORG_A))


async def test_only_the_designated_organization_is_widened():
    with _designate(ORG_B):
        widened = _sql(trade_scope.owned_by_for_entry_guards(ORG_B))
        assert "IS NULL" in widened and " OR " in widened
        other = _sql(trade_scope.owned_by_for_entry_guards(ORG_A))
        assert other == _sql(trade_scope.owned_by(ORG_A))
        assert "IS NULL" not in other


async def test_the_single_operator_scope_is_never_widened():
    """None already means "the NULL rows"; widening it would mean "every row"."""
    with _designate(ORG_B):
        assert _sql(trade_scope.owned_by_for_entry_guards(None)) == _sql(trade_scope.owned_by(None))
