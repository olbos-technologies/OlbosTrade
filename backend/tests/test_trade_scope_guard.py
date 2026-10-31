"""`trade_scope.owned_by` is the only place the ownership filter is written.

A static scan, and the test `app/services/trade_scope.py` names in its own
docstring. It exists because the filter is one line and the wrong version of
it is shorter:

    Trade.organization_id == org_id          # looks right, leaks every
                                             # unattributed row when org_id
                                             # is the single-operator None

`owned_by(None)` deliberately means `organization_id IS NULL` — the rows
migration 0041 declined to guess an owner for — and NOT "every row". A
hand-rolled equality comparison gets the None case wrong silently: the query
returns nothing, somebody notices the empty screen, and the obvious fix is to
drop the filter when `org_id` is falsy. That is the shared query Batch E2
removes, reintroduced by a plausible bugfix.

So the decision lives in one function and this asserts nobody wrote their own.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"
ROUTES = _APP / "api" / "routes"
SERVICES = _APP / "services"
#: main.py too. It holds route handlers that query `trades` directly —
#: /api/dashboard/summary summed every organization's closed trades with no
#: ownership filter at all, and this scan did not see it because main.py was
#: outside both roots above. A guard that covers the tidy half of the
#: codebase reports clean while the untidy half is where the leak lives.
MAIN = _APP / "main.py"

#: A comparison or IS NULL test written directly against the ownership column.
_HAND_ROLLED = re.compile(
    r"Trade\.organization_id\s*(==|!=|\.is_\(|\.isnot\(|\.in_\()"
)

#: The two files allowed to write it: the function that owns the decision, and
#: the migration-shaped backfill helper beside it.
_ALLOWED = {"trade_scope.py"}


def _python_files():
    for root in (ROUTES, SERVICES):
        for p in sorted(root.rglob("*.py")):
            if "__pycache__" not in str(p):
                yield p
    if MAIN.exists():
        yield MAIN


def test_the_scan_actually_reads_something():
    """A guard that silently matches nothing is worse than no guard.

    Asserts the corpus is non-trivial AND that the pattern matches where it
    is supposed to — in `trade_scope.py` itself. If `owned_by` is ever
    rewritten so this pattern no longer appears there, the scan below has
    stopped testing anything and this fails first.
    """
    files = list(_python_files())
    assert len(files) > 30, f"only {len(files)} files scanned — wrong root?"

    owner = (SERVICES / "trade_scope.py").read_text()
    assert _HAND_ROLLED.search(owner), (
        "the pattern no longer matches trade_scope.owned_by itself, so the "
        "scan below would pass against any code at all"
    )


def test_no_module_hand_rolls_the_ownership_filter():
    offenders = []
    for p in _python_files():
        if p.name in _ALLOWED:
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if _HAND_ROLLED.search(stripped):
                offenders.append(f"{p.name}:{i}: {stripped}")

    assert not offenders, (
        "these write the ownership filter by hand instead of calling "
        "trade_scope.owned_by, which is the only place the single-operator "
        "None case is handled correctly:\n  " + "\n  ".join(offenders)
    )


def test_owned_by_none_is_a_null_test_not_a_wildcard():
    """The one behaviour everything above protects, asserted directly."""
    from sqlalchemy import select

    from app.models.trade import Trade
    from app.services.trade_scope import owned_by

    sql = str(select(Trade.id).where(owned_by(None)))
    assert "IS NULL" in sql, f"owned_by(None) compiled to: {sql}"

    import uuid
    sql = str(select(Trade.id).where(owned_by(uuid.uuid4())))
    assert "organization_id =" in sql, f"owned_by(<uuid>) compiled to: {sql}"
