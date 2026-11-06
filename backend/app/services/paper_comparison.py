"""Tenant-owned read-only paper comparison setup. No order adapter or singleton.

Roles describe the user's intended experiment; they do NOT start automation.
Balance snapshots are observations, not returns: cash flows and execution/exit
attribution are not yet available for these read-only grants.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert
from app.models.broker_oauth import BrokerOAuthGrant
from app.models.paper_comparison import PaperComparison
from app.services import broker_paper_snapshot, broker_connection_health as health


class InvalidPair(Exception):
    pass


class ComparisonChanged(Exception):
    pass


def view(row):
    if row is None:
        return None
    return {"revision": str(row.revision), "system_grant_id": str(row.system_grant_id),
            "hybrid_grant_id": str(row.hybrid_grant_id), "updated_at": row.updated_at.isoformat()}


async def get(db, org):
    return (await db.execute(select(PaperComparison).where(
        PaperComparison.organization_id == org).execution_options(populate_existing=True))).scalar_one_or_none()


async def save(db, org, system_id, hybrid_id, expected_revision):
    # Lock grants through the write so disconnect cannot race validation/commit.
    ids = sorted({system_id, hybrid_id}, key=str)
    if len(ids) != 2:
        raise InvalidPair()
    grants = (await db.execute(select(BrokerOAuthGrant).where(
        BrokerOAuthGrant.id.in_(ids), BrokerOAuthGrant.organization_id == org,
        BrokerOAuthGrant.broker == "alpaca", BrokerOAuthGrant.environment == "paper",
        BrokerOAuthGrant.status == "active", BrokerOAuthGrant.access_token_enc.is_not(None)
    ).order_by(BrokerOAuthGrant.id).with_for_update())).scalars().all()
    if len(grants) != 2 or grants[0].account_id == grants[1].account_id:
        raise InvalidPair()
    values = dict(system_grant_id=system_id, hybrid_grant_id=hybrid_id,
                  revision=uuid.uuid4(), updated_at=func.now())
    if expected_revision is None:
        stmt = insert(PaperComparison).values(organization_id=org, **values).on_conflict_do_nothing(
            index_elements=[PaperComparison.organization_id])
    else:
        stmt = update(PaperComparison).where(PaperComparison.organization_id == org,
            PaperComparison.revision == expected_revision).values(**values)
    changed = (await db.execute(stmt.returning(PaperComparison.revision))).scalar_one_or_none()
    if changed is None:
        await db.rollback()
        raise ComparisonChanged()
    await db.commit()
    return view(await get(db, org))


async def observe(db, org, expected_revision):
    row = await get(db, org)
    if row is None or row.revision != expected_revision:
        raise ComparisonChanged()
    pair = view(row)
    result = {"comparison": pair, "execution_enabled": False, "snapshot_atomic": False,
              "collection_started_at": datetime.now(timezone.utc).isoformat(), "accounts": {}}
    # Sequential: AsyncSession cannot run concurrent transactions. Each underlying
    # snapshot rechecks stored identity/token and conditionally records health.
    for role in ("system", "hybrid"):
        gid = pair[role + "_grant_id"]
        try:
            snapshot = await broker_paper_snapshot.snapshot(db, org, gid)
            result["accounts"][role] = {"state": "observed", "snapshot": snapshot}
        except health.ConnectionMissing:
            result["accounts"][role] = {"state": "unavailable", "snapshot": None}
        except health.ConnectionChanged:
            result["accounts"][role] = {"state": "changed", "snapshot": None}
    # Do not return observations against a pairing replaced in another session.
    current = await get(db, org)
    if current is None or current.revision != expected_revision:
        raise ComparisonChanged()
    # First account may have been revoked/reauthorized while reading the second.
    # Check its current stored identity via the snapshot health check timestamp.
    for role, observed in result["accounts"].items():
        snap = observed["snapshot"]
        if snap is None:
            continue
        grant = (await db.execute(select(BrokerOAuthGrant).where(
            BrokerOAuthGrant.id == uuid.UUID(pair[role + "_grant_id"]),
            BrokerOAuthGrant.organization_id == org).execution_options(populate_existing=True))).scalar_one_or_none()
        if (grant is None or grant.status != snap["status"] or grant.last_checked_at is None
                or grant.last_checked_at.isoformat() != snap["last_checked_at"]):
            result["accounts"][role] = {"state": "changed", "snapshot": None}
    result["collection_completed_at"] = datetime.now(timezone.utc).isoformat()
    return result
