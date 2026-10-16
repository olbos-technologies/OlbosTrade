"""Which owners a background worker runs for, and whose broker it uses.

Batch E2 steps 4 and 6. The request path got its scope from the caller
(`trade_scope.request_scope`); a scheduler job has no caller, so this module
is where the question "whose positions am I processing?" gets answered for the
14 jobs in `main._background_scheduler`.

Three rules, each of them a failure that already exists in the code this
replaces:

**The scope is a parameter, never resolved inside the worker.** A worker that
looks its own scope up can be called with none and still run, and what it does
then is process every row — the shared query under a new name. So
`PositionReconciler` takes `organization_id` as a required keyword and
`for_each_scope` is the only thing that supplies it.

**A scope whose broker cannot be resolved is skipped, never defaulted.** This
is the specific bug §4 of the analysis describes. `get_broker()` is a
process-wide singleton, so falling back to it for an organization that has no
connection of its own would compare tenant A's broker against tenant B's open
trades — reporting B's real positions as `phantom_in_db` and A's as
`untracked_at_broker`, either of which can drive an adopt or halt trading on
data the broker was never asked about. `BrokerUnresolved` makes that
impossible to write by accident.

**One owner's failure does not end the sweep.** `main._guarded` isolates jobs
from each other; a per-organization loop needs the same isolation *within* a
job, or one unreachable broker stops every other tenant's reconciliation.

NOT EVERY WORKER IS SCOPED. `sector_cache` and `rotation_correlation_cache`
derive *market* data — sector membership and return correlations — which
belongs to no tenant. They stay global, and
`test_global_workers_are_not_scoped` asserts that rather than leaving it to
the reader.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from sqlalchemy import select

from app.models.trade import Trade
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: The single-operator scope: `organization_id IS NULL`, matching the rows
#: migration 0041 declined to attribute. It is a real scope, so it appears in
#: the iteration like any other — it is not "no scope" and not a wildcard.
SINGLE_OPERATOR = None


class BrokerUnresolved(Exception):
    """No broker can be established for this scope, so it must be skipped.

    Raised rather than returning the process-wide singleton. See the module
    docstring: the fallback is what produces a cross-tenant comparison.
    """


@dataclass(frozen=True)
class ScopedWork:
    """One unit of per-owner work: whose rows, and which broker reports them."""

    organization_id: Optional[uuid.UUID]
    broker: object
    #: Where the broker came from, for logs and for the snapshot row. Either
    #: "process" (the operator's own configured broker) or a connection id.
    broker_source: str

    @property
    def is_single_operator(self) -> bool:
        return self.organization_id is None


async def scopes_with_open_trades(db) -> list[Optional[uuid.UUID]]:
    """Every owner that currently has an open or pending trade.

    Driven by the trades themselves rather than by the organization table, for
    two reasons. An organization with no positions has nothing to reconcile,
    so visiting it costs a broker round trip to compare two empty sets. And
    the unattributed rows must be visited even though no organization row
    corresponds to them — they are the single-operator scope, and they are
    exactly the rows a deployment that has never enabled auth holds.

    `None` in the returned list means the single-operator scope. It is
    included only when such rows actually exist, so a fully-attributed
    deployment does not sweep an empty scope every cycle.
    """
    rows = (await db.execute(
        select(Trade.organization_id)
        .where(Trade.status.in_(["open", "pending"]))
        .distinct()
    )).scalars().all()
    # `None` sorts badly against UUIDs, so order the attributed ones and put
    # the single-operator scope first — it is the one that exists today.
    attributed = sorted((r for r in rows if r is not None), key=str)
    return ([SINGLE_OPERATOR] if any(r is None for r in rows) else []) + attributed


async def broker_for_scope(db, organization_id: Optional[uuid.UUID],
                           *, environment: Optional[str] = None) -> ScopedWork:
    """The broker whose account holds this owner's positions.

    * The single-operator scope gets the process broker. That is the whole
      deployment today: one operator, one configured broker, and the
      unattributed rows are its positions.

    * An organization gets a client built from ITS OWN stored connection, via
      `connection_scoped.scoped_alpaca_for`. The base URL comes from the
      connection, so a paper-connected organization cannot be read against the
      live endpoint.

    * An organization with no usable connection raises `BrokerUnresolved`.

    IBKR has no per-organization form in this process — its gateway is a
    single logged-in session and IBKR_CLIENT_ID multiplexes onto the same
    account, so a per-organization IBKR stack is infrastructure rather than
    application code. An organization whose only connection is IBKR therefore
    also raises, rather than being read against the operator's own IBKR
    account, which is the same cross-tenant comparison by another route.
    """
    if organization_id is None:
        from app.broker.broker_factory import get_broker

        return ScopedWork(organization_id=None, broker=get_broker(),
                          broker_source="process")

    from app.broker.connection_scoped import (
        ConnectionNotExecutable, scoped_alpaca_for,
    )

    try:
        scoped = await scoped_alpaca_for(db, organization_id,
                                         environment=environment)
    except ConnectionNotExecutable as exc:
        raise BrokerUnresolved(
            f"organization {organization_id} has no reconcilable broker: {exc}"
        ) from exc

    return ScopedWork(organization_id=organization_id, broker=scoped.client,
                      broker_source=scoped.connection_id)


async def for_each_scope(
    db_factory,
    work: Callable[[ScopedWork], Awaitable[None]],
    *,
    job: str,
    environment: Optional[str] = None,
) -> dict:
    """Run `work` once per owner, isolating failures per owner.

    Returns a summary rather than raising: a sweep that processed four of five
    organizations has done useful work, and the caller — a scheduler job under
    `_guarded` — needs to log what happened, not abort.

    `skipped` counts owners whose broker could not be resolved. That is not an
    error in a deployment where a tenant has signed up but not connected a
    broker, and it is reported separately from `failed` so the two cannot be
    confused in a log.
    """
    summary = {"scopes": 0, "ok": 0, "skipped": 0, "failed": 0, "errors": []}

    async with db_factory() as db:
        scopes = await scopes_with_open_trades(db)

    summary["scopes"] = len(scopes)

    for org_id in scopes:
        label = "single-operator" if org_id is None else str(org_id)
        try:
            async with db_factory() as db:
                scoped = await broker_for_scope(db, org_id,
                                                environment=environment)
        except BrokerUnresolved as exc:
            summary["skipped"] += 1
            logger.warning("%s: skipping %s — %s", job, label, exc)
            continue
        except Exception as exc:
            # Resolving a broker should not fail for any other reason, so this
            # is loud. It is still per-owner: the next organization is tried.
            summary["failed"] += 1
            summary["errors"].append(f"{label}: broker_resolution: {exc}")
            logger.exception("%s: could not resolve a broker for %s", job, label)
            continue

        try:
            await work(scoped)
            summary["ok"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append(f"{label}: {type(exc).__name__}: {exc}")
            logger.exception("%s: failed for %s", job, label)

    return summary
