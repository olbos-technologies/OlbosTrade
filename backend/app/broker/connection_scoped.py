"""
Building a broker client for ONE organization's stored connection.

MASTER_ARCHITECTURE §21 Phase 2's first action. §6.3 draws the line this module
sits on: the connection control plane "does not place orders. It produces an
execution-ready connection reference for broker workers." So this resolves and
constructs; it never submits.

NOT WIRED. Nothing calls this yet. Routing the live order path through it is
Phase 2's SECOND action, and `get_broker()`'s 52 call sites still serve every
caller unchanged. Keeping construction separate from routing means the thing
that decides WHOSE credentials to use can be reviewed and tested before the
thing that decides WHETHER to send an order changes at all.

WHY ALPACA ONLY. Alpaca authenticates per request and holds no session, so one
process can act for many organizations by varying headers. IBKR's gateway is a
single logged-in session and IBKR_CLIENT_ID multiplexes onto the SAME account;
per-organization IBKR needs a container per organization, which is
infrastructure rather than application code. Asking for an IBKR connection here
raises instead of quietly returning the operator's own account — which is the
failure that would matter, because it would route one tenant's order into
another's account.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.broker.alpaca_client import AlpacaClient
from app.models.broker_connection import (
    BROKER_ALPACA, BROKER_IBKR, STATUS_ACTIVE,
)
from app.services import broker_connection_service as connections

logger = logging.getLogger(__name__)


class ConnectionNotExecutable(Exception):
    """This organization has no connection that may be executed against.

    Raised rather than returning None so a caller cannot treat "no connection"
    as a falsy value and fall through to the process-wide singleton, which
    would route one organization's order through the operator's own account.
    """


@dataclass(frozen=True)
class ScopedBroker:
    """A client plus the identity of the connection it was built from.

    The version travels with the client because §8 requires the worker to
    revalidate it immediately before submitting: a connection rotated or
    revoked between evaluation and dispatch must stop the order, and that
    check is impossible if the client does not know what it was built from.
    """

    client: AlpacaClient
    connection_id: str
    connection_version: int
    organization_id: str
    environment: str
    broker: str = BROKER_ALPACA

    def __repr__(self) -> str:
        # Mirrors AlpacaClient.__repr__: no credential may reach a log through
        # an interpolated object.
        return (f"ScopedBroker(org={self.organization_id}, "
                f"connection={self.connection_id}, v={self.connection_version}, "
                f"env={self.environment})")

    __str__ = __repr__


async def scoped_alpaca_for(
    db,
    organization_id,
    *,
    environment: Optional[str] = None,
) -> ScopedBroker:
    """An execution-ready Alpaca client for this organization's connection.

    `environment` selects paper or live explicitly. Left None, the stored
    preference applies — credentials_for() resolves live over paper when an
    organization holds both, because someone who connected a live account
    means it.

    The base URL comes from the CONNECTION, never from settings.alpaca_base_url.
    A per-organization client that took its host from the operator's own
    environment variable could send a paper-connected organization's order to
    the live endpoint, which is the precise mistake this layer exists to make
    impossible.
    """
    found = await connections.credentials_for(
        db, organization_id, broker=BROKER_ALPACA, environment=environment,
    )
    if found is None:
        raise ConnectionNotExecutable(
            f"organization {organization_id} has no active Alpaca connection"
        )

    api_key, secret_key, base_url, row = found

    # Defence in depth. credentials_for() already filters on status, but this
    # is the last point before a credential becomes a client that can trade,
    # and a revoked connection reaching a broker is not a bug worth being
    # subtle about.
    if getattr(row, "api_key_enc", None) is None:
        raise ConnectionNotExecutable(
            f"connection {row.id} has no credentials; it is revoked"
        )

    client = AlpacaClient(
        api_key=api_key,
        secret_key=secret_key,
        base_url=base_url,
        connection_id=str(row.id),
        connection_version=_version_of(row),
    )
    scoped = ScopedBroker(
        client=client,
        connection_id=str(row.id),
        connection_version=_version_of(row),
        organization_id=str(organization_id),
        environment=row.environment,
    )
    # Identity and environment only. Never the label (user text), never the
    # key, never any part of it.
    logger.info("Scoped broker built: org=%s connection=%s env=%s",
                organization_id, row.id, row.environment)
    return scoped


def _version_of(row) -> int:
    """§7.2's connection version, as stored. Migration 0038 added the column;
    the `or 1` covers a row object built in a test without one."""
    return int(getattr(row, "version", 1) or 1)


async def assert_still_executable(db, scoped: ScopedBroker) -> None:
    """§8's pre-submit revalidation: has this connection changed under us?

    The worker builds a client, queues work, and submits some time later.
    Between those points the credential may have been revoked or rotated. This
    is the check that stops the order, and it runs immediately before
    submission rather than at build time — a check at build time proves only
    that the connection was fine when nobody was about to trade on it.

    Raises rather than returning False for the same reason the builder does: a
    caller must not be able to treat "changed" as a falsy value and carry on.

    Three ways to fail, all of them the same answer to the caller:
      * the row is gone;
      * it is no longer active;
      * its version moved.
    """
    row = await connections.get_connection(db, scoped.connection_id)

    if row is None:
        raise ConnectionNotExecutable(
            f"connection {scoped.connection_id} no longer exists"
        )
    if row.status != STATUS_ACTIVE:
        raise ConnectionNotExecutable(
            f"connection {scoped.connection_id} is {row.status}; it was active "
            "when this order was prepared"
        )
    current = _version_of(row)
    if current != scoped.connection_version:
        raise ConnectionNotExecutable(
            f"connection {scoped.connection_id} moved from version "
            f"{scoped.connection_version} to {current} since this order was "
            "prepared; it may no longer be the account that was evaluated"
        )


def scoped_broker_unsupported(broker: str) -> ConnectionNotExecutable:
    """IBKR has no per-organization form here. See the module docstring."""
    if broker == BROKER_IBKR:
        return ConnectionNotExecutable(
            "IBKR cannot be connection-scoped in this process: its gateway is a "
            "single logged-in session, so per-organization IBKR needs a stack "
            "per organization"
        )
    return ConnectionNotExecutable(f"unsupported broker {broker!r}")
