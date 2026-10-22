"""R2-I paper-account-only boundary for IBKR execution snapshots.

This receives an already-authenticated session supplied by the caller. It
neither connects to IBKR nor enables trading, registers callbacks, or places
orders. Only a confirmed, single DU paper account is eligible.
"""
from __future__ import annotations

import uuid

from app.services.ibkr_ledger_adapter import ingest_ibkr_option_fills


async def ingest_paper_execution_snapshot(
    session, *, expected_account_id: str, organization_id: uuid.UUID | None,
):
    """Reject disconnected, ambiguous, or non-paper IBKR sessions before IO."""
    account = expected_account_id.strip()
    if not account.startswith("DU") or not account[2:].isdigit():
        raise ValueError("ibkr_paper_account_required")
    if not session.isConnected():
        raise ValueError("broker_session_disconnected")
    accounts = tuple(session.managedAccounts())
    if len(accounts) != 1 or accounts[0] != account:
        raise ValueError("broker_session_account_mismatch")
    # IBKR fills() is a local session cache, not a verified complete history.
    # No history completeness claim is ever made from this snapshot.
    fills = tuple(session.fills())
    return await ingest_ibkr_option_fills(
        fills, expected_account_id=account, organization_id=organization_id
    )
