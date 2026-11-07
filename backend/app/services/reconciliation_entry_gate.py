"""Reconciliation gate for NEW IBKR entries: off, observe, or enforce.

Before an entry order is sent, ask whether the desk's picture of the account
agrees with the broker's. This is an ENTRY veto only. It is not a kill switch
and it repairs nothing; closes and emergency flattening carry no entry permit
and are never evaluated, in any mode.

Modes (``RECONCILIATION_ENTRY_GATE_MODE``, default ``observe``):

  off      never evaluated.
  observe  evaluated before each entry send; what WOULD have been vetoed, and
           why, is recorded; nothing is blocked. The evaluation is bounded by
           ``GATE_TIMEOUT_SECONDS`` and can never raise into the entry.
  enforce  the entry is vetoed when reconciliation is unresolved, or cannot be
           established (an unreadable broker or database, a timeout, an error).
           "Could not check" is never "clean".

Why observe exists. Whether enforcing is safe depends on how often a real
desk's reconciliation is unclean for reasons that should not stop an entry, and
that cannot be known from a desk that was never measured. Observe answers it
from real entries: every would-veto and every unreadable check is written to
``execution_events`` (kind ``entry_gate``) with its scope, ticker, categorised
reasons, and what was explained away, so it survives a redeploy.

Scope. IBKR is the single-operator account in this process (worker_scope.py).
Its holdings are compared with the single-operator rows only (organization
``None``); an organization's separate broker or trades are never part of this
comparison, and every record says so.

What is explained, and what is not. The comparison reads only ``open`` trade
rows. A working order's row is ``pending``, so a position that has just filled
but not yet been promoted by the fill poll (up to ~30 s) would read as an
untracked position and, enforced, would veto every entry meanwhile. Untracked
and size findings on a ticker that has a pending row are therefore reported as
EXPLAINED rather than as findings. A pending row never explains a flipped
position or a phantom row: those are never expected of a pending entry.

Known limits, stated so they are not rediscovered as surprises:
  * IBKR positions come from ``ib.portfolio()``, a local cache. Right after a
    reconnect it can be empty without raising, which can read as clean when
    nothing is tracked in the database either. The broker's connection is
    required to send at all, but this does not prove the cache is warm.
  * An entry whose order was just sent but whose row is not yet written looks
    untracked for the instant between the two. Observe will show how often.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services.execution_interlock import EntryHalted, check_entry_permit
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: Upper bound on one evaluation (reconcile + pending-row lookup). An entry must
#: not wait on this longer, in any mode; past it the check is "unavailable".
GATE_TIMEOUT_SECONDS = 3.0

SCOPE = "single-operator"

#: Findings a pending row can explain. Direction mismatches and phantom rows
#: are deliberately absent: a pending entry accounts for neither.
_EXPLAINABLE_BY_PENDING = frozenset({"untracked_at_broker", "quantity_mismatch"})


class ReconciliationBlocked(EntryHalted):
    """No entry sent: reconciliation is unresolved or could not be established.

    ``str(exc)`` is the stable reason the route reports: ``reconciliation_unresolved``
    or ``reconciliation_unavailable``. The detail is in the ``entry_gate`` event.
    """


@dataclass(frozen=True)
class GateAssessment:
    available: bool
    unavailable_cause: Optional[str] = None
    #: ("untracked_at_broker", "NVDA"), ("direction_mismatch", "AAPL"), ...
    findings: tuple = ()
    #: ticker -> why it was not counted, e.g. "untracked_at_broker: pending_trade"
    explained: dict = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def clean(self) -> bool:
        return self.available and not self.findings

    @property
    def reason(self) -> Optional[str]:
        if not self.available:
            return "reconciliation_unavailable"
        if self.findings:
            return "reconciliation_unresolved"
        return None

    def reasons(self) -> list[str]:
        return [f"{cat}:{ticker}" if ticker else cat for cat, ticker in self.findings]


def _mode() -> str:
    from app.core.config import settings
    return getattr(settings, "reconciliation_entry_gate_mode", "observe")


async def _pending_underlyings() -> set[str]:
    """Underlyings with a pending row in the single-operator book."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.trade_scope import owned_by_operator_aware

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Trade.underlying).where(
                owned_by_operator_aware(None), Trade.status == "pending",
            )
        )).scalars().all()
    return {str(u).upper() for u in rows if u}


def _unavailable_cause(warnings) -> Optional[str]:
    for w in warnings or ():
        text = str(w)
        if text.startswith("broker_unavailable"):
            return "broker_unavailable"
        if text.startswith("db_unavailable"):
            return "db_unavailable"
    return None


def _raw_findings(result) -> list[tuple]:
    found: list[tuple] = []
    for t in result.untracked_at_broker:
        found.append(("untracked_at_broker", str(t).upper()))
    for t in result.phantom_in_db:
        found.append(("phantom_in_db", str(t).upper()))
    for t in result.quantity_mismatch_tickers:
        found.append(("quantity_mismatch", str(t).upper()))
    for t in result.direction_mismatch_tickers:
        found.append(("direction_mismatch", str(t).upper()))
    for w in result.warnings:
        text = str(w)
        if text.startswith("DIRECTION UNVERIFIED"):
            parts = text.split()
            found.append(("direction_unverified", parts[2].rstrip(":").upper() if len(parts) > 2 else None))
    return found


async def _assess(broker) -> GateAssessment:
    from app.services.position_reconciler import PositionReconciler

    result = await PositionReconciler(broker, organization_id=None).check()

    cause = _unavailable_cause(result.warnings)
    if cause:
        return GateAssessment(available=False, unavailable_cause=cause)

    raw = _raw_findings(result)
    if result.clean is not True and not raw:
        # Unclean, and nothing here can say why. Never read that as clean.
        raw = [("unclassified", None)]
    if not raw:
        return GateAssessment(available=True)

    explained: dict[str, str] = {}
    findings = raw
    if any(cat in _EXPLAINABLE_BY_PENDING for cat, _ in raw):
        try:
            pending = await _pending_underlyings()
        except Exception as exc:  # cannot tell what is explained -> cannot tell
            logger.warning("entry gate: could not read pending trades (%s)", exc)
            return GateAssessment(available=False, unavailable_cause="db_unavailable")
        findings = []
        for cat, ticker in raw:
            if cat in _EXPLAINABLE_BY_PENDING and ticker in pending:
                explained[ticker] = f"{cat}: pending_trade"
            else:
                findings.append((cat, ticker))
    return GateAssessment(available=True, findings=tuple(findings), explained=explained)


async def evaluate_entry_reconciliation(broker) -> GateAssessment:
    """Assess reconciliation for a new entry. Never raises; bounded in time."""
    started = time.monotonic()
    try:
        assessment = await asyncio.wait_for(_assess(broker), timeout=GATE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        assessment = GateAssessment(available=False, unavailable_cause="timeout")
    except Exception as exc:  # noqa: BLE001 — an error is "unavailable", not "clean"
        logger.warning("entry gate: reconciliation errored (%s: %s)", type(exc).__name__, exc)
        assessment = GateAssessment(available=False, unavailable_cause=f"error:{type(exc).__name__}")
    return GateAssessment(
        available=assessment.available, unavailable_cause=assessment.unavailable_cause,
        findings=assessment.findings, explained=assessment.explained,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


async def _record(mode: str, status: str, assessment: GateAssessment,
                  ticker: Optional[str], asset_type: Optional[str]) -> None:
    """Persist one gate decision. Best effort: a failed write must never change
    what the entry does, so it is logged and swallowed."""
    payload = {
        "mode": mode,
        "scope": SCOPE,
        "ticker": ticker,
        "asset_type": asset_type,
        "available": assessment.available,
        "unavailable_cause": assessment.unavailable_cause,
        "reasons": assessment.reasons(),
        "explained": dict(assessment.explained),
        "elapsed_ms": assessment.elapsed_ms,
    }
    logger.warning(
        "ENTRY_GATE mode=%s status=%s scope=%s ticker=%s asset=%s unavailable=%s "
        "reasons=%s explained=%s elapsed_ms=%d",
        mode, status, SCOPE, ticker, asset_type, assessment.unavailable_cause,
        assessment.reasons(), dict(assessment.explained), assessment.elapsed_ms,
    )
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.execution_event import ExecutionEvent

        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(ExecutionEvent(
                    kind="entry_gate", ticker=(ticker or "")[:16] or None,
                    asset_type=asset_type, status=status, payload=payload,
                ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("entry gate: could not persist the %s record (%s)", status, exc)


async def require_reconciled_entry(broker, permit, *, ticker: Optional[str] = None,
                                   asset_type: Optional[str] = None) -> None:
    """Call immediately before each entry send, including replacement attempts."""
    if permit is None:          # reviewed risk-reducing paths, incl. emergency exits
        return
    check_entry_permit(permit)

    mode = _mode()
    if mode == "off":
        return

    from app.services.observability import observability

    assessment = await evaluate_entry_reconciliation(broker)
    # A halt during the awaits above must still invalidate this send.
    check_entry_permit(permit)

    if assessment.clean:
        observability.incr(f"entry_gate.{mode}.clean")
        return

    if mode == "observe":
        status = "unavailable" if not assessment.available else "would_veto"
        observability.incr(f"entry_gate.observe.{status}")
        await _record("observe", status, assessment, ticker, asset_type)
        return

    # enforce
    observability.incr("entry_gate.enforce.vetoed")
    await _record("enforce", "vetoed", assessment, ticker, asset_type)
    raise ReconciliationBlocked(assessment.reason)
