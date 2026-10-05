"""
Personal organizations — the owner of a broker connection since ADR-0001.

The interesting behaviour is not "it makes a row". It is that resolving an
organization happens on the READ path, concurrently, for a user who may not
have one yet, and that losing that race must be a hit rather than a 500.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

import app.models.user  # noqa: F401  (resolves the users FK target)
from app.models.organization import (
    KIND_PERSONAL, ROLE_OWNER, Organization, OrganizationMember,
)
from app.services import organization_service as svc

pytestmark = pytest.mark.asyncio

USER = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return [(r,) for r in self._rows]


class _FakeDB:
    """Enough SQLAlchemy to exercise the get-or-create.

    `fail_flush_once` stands in for the unique index firing: the point of the
    retry is that the database, not the service, is what guarantees one
    personal organization per user.
    """

    def __init__(self, orgs=None, members=None, *, fail_flush_once=False,
                 winner_appears=True):
        self.orgs = list(orgs or [])
        self.members = list(members or [])
        self.staged: list = []
        self.fail_flush_once = fail_flush_once
        self.winner_appears = winner_appears
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        sql = str(stmt)
        params = stmt.compile().params
        if "organization_members" in sql:
            uid = params.get("user_id_1")
            return _Result([m.organization_id for m in self.members
                            if m.user_id == uid
                            and (params.get("organization_id_1") is None
                                 or m.organization_id == params["organization_id_1"])])
        uid = params.get("personal_for_user_id_1")
        return _Result([o for o in self.orgs if o.personal_for_user_id == uid])

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.staged.append(obj)

    async def flush(self):
        if self.fail_flush_once:
            self.fail_flush_once = False
            if self.winner_appears:
                # Another request committed first — its row is now visible.
                self.orgs.append(Organization(
                    id=uuid.uuid4(), name="", kind=KIND_PERSONAL,
                    personal_for_user_id=USER,
                ))
            raise IntegrityError("insert", {}, Exception("unique violation"))

    async def commit(self):
        for obj in self.staged:
            (self.orgs if isinstance(obj, Organization) else self.members).append(obj)
        self.staged.clear()
        self.commits += 1

    async def rollback(self):
        self.staged.clear()
        self.rollbacks += 1


async def test_creates_a_personal_organization_on_first_use():
    db = _FakeDB()
    org = await svc.personal_org_for(db, USER)

    assert org.personal_for_user_id == USER
    assert org.kind == KIND_PERSONAL
    assert db.commits == 1
    member = next(m for m in db.members if isinstance(m, OrganizationMember))
    assert (member.user_id, member.role) == (USER, ROLE_OWNER), (
        "the creator must be a member, or they cannot act for their own org"
    )


async def test_is_idempotent_for_a_user_who_already_has_one():
    existing = Organization(id=uuid.uuid4(), name="", kind=KIND_PERSONAL,
                            personal_for_user_id=USER)
    db = _FakeDB(orgs=[existing])

    assert (await svc.personal_org_for(db, USER)).id == existing.id
    assert db.commits == 0, "a second call wrote a row; this runs on every read"


async def test_accepts_a_string_user_id():
    """Routes hand this whatever the session carries. asyncpg rejects a str
    bound to a UUID column, so coercion happens here or it fails at the wire."""
    db = _FakeDB()
    org = await svc.personal_org_for(db, str(USER))
    assert org.personal_for_user_id == USER


async def test_losing_the_create_race_returns_the_winners_row():
    """A unique violation here is a dedup HIT, not a failure.

    Two concurrent first requests for the same new user both miss the SELECT
    and both insert. The index rejects one. That caller must get the same
    organization as the winner, because the alternative is a 500 on an
    ordinary first page load.
    """
    db = _FakeDB(fail_flush_once=True)

    org = await svc.personal_org_for(db, USER)

    assert org.personal_for_user_id == USER
    assert db.rollbacks == 1, "the failed transaction must be rolled back"
    assert len([o for o in db.orgs if o.personal_for_user_id == USER]) == 1, (
        "the race produced two personal organizations for one user"
    )


async def test_an_integrity_error_with_no_visible_winner_is_raised():
    """The honest failure.

    If the insert is rejected but no row appears, the constraint that fired
    was not the one assumed. Swallowing that would hand back an organization
    that does not exist, and the caller would route an order with it.
    """
    db = _FakeDB(fail_flush_once=True, winner_appears=False)

    with pytest.raises(IntegrityError):
        await svc.personal_org_for(db, USER)


async def test_org_ids_for_user_is_a_list():
    org = uuid.uuid4()
    db = _FakeDB(members=[OrganizationMember(organization_id=org, user_id=USER,
                                             role=ROLE_OWNER)])
    assert await svc.org_ids_for_user(db, USER) == [org]


async def test_a_user_may_act_for_their_own_organization():
    org = uuid.uuid4()
    db = _FakeDB(members=[OrganizationMember(organization_id=org, user_id=USER,
                                             role=ROLE_OWNER)])
    assert await svc.user_may_act_for(db, USER, org) is True


async def test_a_user_may_not_act_for_someone_elses_organization():
    """The negative case, which is the one that matters: a leaked or guessed
    organization id must not become authorization."""
    org = uuid.uuid4()
    db = _FakeDB(members=[OrganizationMember(organization_id=org, user_id=OTHER,
                                             role=ROLE_OWNER)])
    assert await svc.user_may_act_for(db, USER, org) is False


async def test_membership_in_one_organization_is_not_membership_in_another():
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    db = _FakeDB(members=[OrganizationMember(organization_id=mine, user_id=USER,
                                             role=ROLE_OWNER)])
    assert await svc.user_may_act_for(db, USER, mine) is True
    assert await svc.user_may_act_for(db, USER, theirs) is False
