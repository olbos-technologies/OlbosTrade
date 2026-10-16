"""Resolving a caller's trade scope inside a route, and failing closed.

Nine route modules read `trades`. Each of them needs the same three things —
the caller's scope, a 401 when auth is on and there is no identity, and the
`owned_by` filter — and nine hand-rolled copies is how one of them ends up
subtly different. `app.services.trade_scope` owns the *decision*; this module
owns the HTTP shape of it.

`PermissionError` becoming a 401 rather than a default scope is the point.
`owner_scope` raises it when auth is enabled and the connection carries no
identity. The tempting fallback — treat that as the single-operator scope —
would answer an unauthenticated caller with the unattributed rows, which in a
single-operator deployment is *every open position*.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from starlette.requests import HTTPConnection

from app.services import trade_scope


async def scope_or_401(db, conn: HTTPConnection) -> Optional[uuid.UUID]:
    """The caller's trade scope, or a 401.

    Returns `None` for the single-operator scope — which is a scope, matching
    the rows migration 0041 left unattributed, and not "no filter". Pass the
    result to `trade_scope.owned_by`; never test it for truthiness.
    """
    # Reached through the module rather than a `from ... import request_scope`
    # binding, deliberately. A bound name would make this a SECOND patch point:
    # a test that pins the scope at `trade_scope.request_scope` — which is how
    # every other scoping test in this suite does it — would silently miss the
    # routes that come through here, and the route would answer with the
    # single-operator scope while the test believed it had pinned a tenant.
    # That is a test which passes with the filter removed, so there is exactly
    # one place to patch.
    try:
        return await trade_scope.request_scope(db, conn)
    except PermissionError:
        raise HTTPException(401, "Not authenticated")
