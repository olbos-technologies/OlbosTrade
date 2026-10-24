"""R2-Q centralized broker entry submission boundary.

No authenticated complete-history proof provider exists yet. Entry authorization
cannot be issued; no order should cross this boundary. Close flows remain
separate and must retain their position-bound checks.
"""
from __future__ import annotations

from app.services.entry_authority import EntryAuthorization


class EntrySubmissionDenied(PermissionError):
    pass


def _require_authorization(authorization: EntryAuthorization | None) -> None:
    # No authorization may be consumed until a durable, single-use proof
    # registry and broker/account ownership validation have been implemented.
    raise EntrySubmissionDenied("entry_history_proof_unavailable")


async def submit_equity_entry(*, authorization: EntryAuthorization | None, broker, **kwargs):
    _require_authorization(authorization)
    return await broker.place_equity_order(**kwargs)


async def submit_options_entry(*, authorization: EntryAuthorization | None, broker, order):
    _require_authorization(authorization)
    return await broker.place_order(order)
