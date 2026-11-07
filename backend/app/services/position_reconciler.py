"""
Position Reconciler — ensures OlbosTrade's DB state matches broker truth.

FIX #10: On every startup and before every signal cycle, compare broker
positions against DB open trades. Any discrepancy halts trading until
manually resolved to prevent position doubling on restart.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.broker_interface import BrokerInterface, Position
from app.core.database import AsyncSessionLocal
from app.models.trade import Trade
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ReconciliationError(Exception):
    """Raised when broker positions don't match DB state — halts trading."""

    def __init__(self, message: str, result: Optional["ReconciliationResult"] = None):
        super().__init__(message)
        self.result = result


@dataclass
class ReconciliationResult:
    """Result of a position reconciliation check."""
    clean: bool
    broker_position_count: int
    db_open_trade_count: int
    untracked_at_broker: list[str]   # in broker, not in DB — ghost positions
    phantom_in_db: list[str]         # in DB as open, not at broker — orphaned records
    quantity_mismatches: list[str] = field(default_factory=list)
    # Bare tickers with a quantity disagreement, populated only by check()
    # (reconcile()'s own quantity_mismatches field holds full warning
    # strings instead) — main.py's auto-correct step needs plain tickers,
    # not prose, to look the position back up.
    quantity_mismatch_tickers: list[str] = field(default_factory=list)
    # Equity positions the broker holds in the OPPOSITE direction to what the
    # database records (broker short, row long, or the reverse). Kept apart
    # from the quantity lists on purpose: quantity_mismatch_tickers feeds an
    # auto-correct that rewrites a row's size to the broker's, and a flipped
    # position must never be "fixed" by being resized. Report, never repair.
    direction_mismatch_tickers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SignedFinding:
    """One disagreement found by the signed equity comparison."""
    symbol: str
    kind: str          # "direction" | "quantity" | "unverified"
    broker_signed: int
    db_signed: int

    def describe(self) -> str:
        if self.kind == "direction":
            return (
                f"DIRECTION MISMATCH {self.symbol}: broker={_side(self.broker_signed)} "
                f"{abs(self.broker_signed)}, db={_side(self.db_signed)} {abs(self.db_signed)}"
            )
        if self.kind == "quantity":
            return (
                f"QUANTITY MISMATCH {self.symbol}: broker={abs(self.broker_signed)} "
                f"db={abs(self.db_signed)}"
            )
        return (
            f"DIRECTION UNVERIFIED {self.symbol}: the open trade row has no "
            f"recognised long/short direction, so only its size was compared"
        )


def _side(signed: int) -> str:
    return "long" if signed > 0 else "short" if signed < 0 else "flat"


def _text(value) -> str:
    """A trade attribute as lower-case text, or "" if it is anything else.

    Deliberately strict: a missing column, None or a test double must read as
    "unknown", never as a truthy something that happens to answer .lower().
    """
    return value.strip().lower() if isinstance(value, str) else ""


def _trade_is_equity(trade) -> bool:
    return _text(getattr(trade, "spread_type", None)).startswith("equity") \
        or _text(getattr(trade, "strategy", None)) == "equity"


def _trade_direction(trade) -> int:
    """+1 long, -1 short, 0 when the row does not say."""
    spread = _text(getattr(trade, "spread_type", None))
    return 1 if spread == "equity_long" else -1 if spread == "equity_short" else 0


def signed_equity_findings(
    broker_positions, db_open_trades,
) -> tuple[list[SignedFinding], set[str]]:
    """Compare equity holdings WITH direction, underlying by underlying.

    The legacy comparison summed abs(quantity) on both sides, so long 10 and
    short 10 looked identical. That hides exactly the failures worth catching:
    an exit that fires on top of a manual close and flips the position, a
    stop that triggers after the position was already flat, an entry sent in
    the wrong direction. The position is then the same SIZE and the opposite
    SIDE, and every check that compared sizes called it clean.

    Scope is deliberately narrow. It covers an underlying only when it is
    equity on BOTH sides and has no option exposure on either: an option
    spread is several legs whose signs cancel or add by structure, and no
    verified leg ledger exists for legacy rows, so a signed comparison there
    would be invented evidence. Those underlyings stay on the legacy size
    check. Returns (findings, handled): `handled` is the set of underlyings
    this function fully compared, which the caller must not compare again.

    A row whose direction is not recognised cannot be compared by sign. It is
    reported as "unverified" and left to the legacy size check; it is never
    guessed to be long.
    """
    broker_equity: dict[str, int] = {}
    broker_option: set[str] = set()
    for p in broker_positions:
        qty = int(getattr(p, "quantity", 0) or 0)
        if not qty:
            continue
        sym = str(p.underlying).upper()
        if getattr(p, "asset_type", "option") == "equity":
            broker_equity[sym] = broker_equity.get(sym, 0) + qty
        else:
            broker_option.add(sym)

    db_equity: dict[str, int] = {}
    db_unknown: set[str] = set()
    db_option: set[str] = set()
    for t in db_open_trades:
        sym = str(t.underlying).upper()
        if not _trade_is_equity(t):
            db_option.add(sym)
            continue
        direction = _trade_direction(t)
        if direction == 0:
            db_unknown.add(sym)
        db_equity[sym] = db_equity.get(sym, 0) + direction * int(t.quantity or 1)

    findings: list[SignedFinding] = []
    handled: set[str] = set()
    for sym in sorted(set(broker_equity) & set(db_equity)):
        if sym in broker_option or sym in db_option:
            continue                       # mixed exposure: legacy size check
        b, d = broker_equity[sym], db_equity[sym]
        if sym in db_unknown:
            findings.append(SignedFinding(sym, "unverified", b, d))
            continue                       # not handled: legacy size check runs
        handled.add(sym)
        if b == d:
            continue
        if b * d < 0:
            findings.append(SignedFinding(sym, "direction", b, d))
        else:
            findings.append(SignedFinding(sym, "quantity", b, d))
    return findings, handled


class PositionReconciler:
    """
    FIX #10: Reconciles broker positions against DB on startup and before cycles.

    Design principle: the broker is always the source of truth.
    If they disagree, halt trading and require manual resolution.
    """

    def __init__(self, broker: BrokerInterface, *,
                 organization_id: "Optional[uuid.UUID]") -> None:
        """A broker paired with the owner whose trades it is authoritative for.

        `organization_id` is a REQUIRED keyword, which is the whole point of
        Batch E2 step 6. This class used to take one broker and compare it
        against every open trade in the database. With two tenants that is
        wrong in both directions: tenant A's broker knows nothing about tenant
        B's positions, so B's real trades come back as `phantom_in_db` and A's
        as `untracked_at_broker` — and either can drive an adopt or halt
        trading on data the broker was never asked about. Pairing them is the
        only way the two outputs mean anything.

        `None` is the single-operator scope: the process broker against the
        rows migration 0041 left unattributed. It must be passed explicitly.
        There is deliberately no default, so `PositionReconciler(broker)` no
        longer constructs — an unscoped reconciliation cannot be written by
        accident, only chosen in words.

        Resolving the pair is `worker_scope.broker_for_scope`'s job, not this
        class's: a class that looked up its own broker could be constructed
        for one owner and silently read another's.
        """
        self.broker = broker
        self.organization_id = organization_id

    @property
    def _owned(self):
        """This reconciler's ownership filter, so the three reads cannot drift."""
        from app.services.trade_scope import owned_by_operator_aware

        return owned_by_operator_aware(self.organization_id)

    @property
    def scope_label(self) -> str:
        """For logs and snapshot rows — a reconciliation result is meaningless
        without saying whose book it describes."""
        return "single-operator" if self.organization_id is None else str(self.organization_id)

    async def check(self) -> ReconciliationResult:
        """
        Non-raising reconciliation status for dashboards/monitoring.

        Same comparison as :meth:`reconcile` but never raises — untracked broker
        positions and DB phantoms are returned in the result for the UI to
        surface, rather than halting. ``clean`` is False when either side
        disagrees. On broker/DB error, returns a non-clean result with the error
        captured in ``warnings`` (fail-loud, but non-fatal).
        """
        from collections import defaultdict

        try:
            broker_positions = await self.broker.get_positions()
        except Exception as exc:
            return ReconciliationResult(
                clean=False, broker_position_count=0, db_open_trade_count=0,
                untracked_at_broker=[], phantom_in_db=[],
                warnings=[f"broker_unavailable: {exc}"],
            )

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Trade).where(self._owned, Trade.status == "open")
                )
                db_open_trades = result.scalars().all()
        except Exception as exc:
            return ReconciliationResult(
                clean=False, broker_position_count=len(broker_positions),
                db_open_trade_count=0, untracked_at_broker=[], phantom_in_db=[],
                warnings=[f"db_unavailable: {exc}"],
            )

        broker_qty: dict[str, int] = defaultdict(int)
        for p in broker_positions:
            broker_qty[p.underlying] += abs(p.quantity)
        db_qty: dict[str, int] = defaultdict(int)
        for t in db_open_trades:
            db_qty[str(t.underlying)] += int(t.quantity or 1)

        untracked = sorted(set(broker_qty) - set(db_qty))
        phantom   = sorted(set(db_qty) - set(broker_qty))

        warnings = []
        mismatch_tickers = []
        direction_tickers = []
        signed, handled = signed_equity_findings(broker_positions, db_open_trades)
        for sym in sorted(set(broker_qty) & set(db_qty)):
            if sym in handled:
                continue            # compared with direction below
            if broker_qty[sym] != db_qty[sym]:
                warnings.append(
                    f"QUANTITY MISMATCH {sym}: broker={broker_qty[sym]} db={db_qty[sym]}"
                )
                mismatch_tickers.append(sym)
        for finding in signed:
            warnings.append(finding.describe())
            if finding.kind == "quantity":
                mismatch_tickers.append(finding.symbol)
            elif finding.kind == "direction":
                direction_tickers.append(finding.symbol)

        return ReconciliationResult(
            clean=not untracked and not phantom and not warnings,
            broker_position_count=len(broker_positions),
            db_open_trade_count=len(db_open_trades),
            untracked_at_broker=untracked,
            phantom_in_db=phantom,
            quantity_mismatch_tickers=mismatch_tickers,
            direction_mismatch_tickers=direction_tickers,
            warnings=warnings,
        )

    async def check_contracts(self):
        """R2-A read-only signed contract check; never authorize entries from this.

        Legacy trades encode strategy-level spreads, not a verified execution-leg
        ledger. For now, nonempty DB trades are *unverified*, never inferred
        clean from matching ticker totals. This method deliberately does not
        change legacy reconcile()/check() or the live order path.
        """
        from app.services.contract_reconciliation import (
            ContractLeg, compare_contract_legs,
        )

        try:
            positions = await self.broker.get_positions()
        except Exception:
            return compare_contract_legs([], None)

        try:
            async with AsyncSessionLocal() as session:
                query = await session.execute(
                    select(Trade).where(self._owned, Trade.status == "open")
                )
                trades = query.scalars().all()
        except Exception:
            return compare_contract_legs([], None)

        # No safe row-by-row signed leg mapping exists yet for legacy spread
        # records; matching on underlying + quantity would be false evidence.
        if trades:
            return compare_contract_legs([], None)

        broker_legs = [
            ContractLeg(
                underlying=p.underlying, expiration=p.expiration,
                strike=p.strike, option_type=p.option_type, quantity=p.quantity,
            )
            for p in positions
            if p.quantity
        ]
        # An empty book on both sides is the only currently verified state.
        # Any broker holdings against no DB leg records are reported as drift.
        return compare_contract_legs(broker_legs, [])

    async def reconcile(self) -> ReconciliationResult:
        """
        Compare broker positions against DB open trades.

        Returns ReconciliationResult.
        Raises ReconciliationError if discrepancies are found — caller must
        handle this by halting the signal cycle.
        """
        # Fetch broker positions
        try:
            broker_positions: list[Position] = await self.broker.get_positions()
        except Exception as exc:
            raise ReconciliationError(
                f"Could not fetch broker positions for reconciliation: {exc}"
            ) from exc

        # Fetch DB open trades
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(self._owned, Trade.status == "open")
            )
            db_open_trades: list[Trade] = result.scalars().all()

        # ── Quantity-aware matching ────────────────────────────────────────────
        # Group broker positions by underlying; sum absolute quantities.
        # A doubled position (qty=2 at broker, qty=1 in DB) is now detectable.
        from collections import defaultdict
        broker_qty: dict[str, int] = defaultdict(int)
        for p in broker_positions:
            broker_qty[p.underlying] += abs(p.quantity)

        db_qty: dict[str, int] = defaultdict(int)
        for t in db_open_trades:
            db_qty[str(t.underlying)] += int(t.quantity or 1)

        broker_underlyings = set(broker_qty.keys())
        db_underlyings     = set(db_qty.keys())

        untracked = broker_underlyings - db_underlyings
        phantom   = db_underlyings - broker_underlyings

        warnings: list[str] = []
        quantity_mismatches: list[str] = []

        # Quantity mismatches on known underlyings (e.g. doubled position)
        signed, handled = signed_equity_findings(broker_positions, db_open_trades)
        direction_mismatches: list[str] = []
        direction_tickers: list[str] = []
        common = broker_underlyings & db_underlyings
        for sym in sorted(common):
            if sym in handled:
                continue            # compared with direction below
            if broker_qty[sym] != db_qty[sym]:
                warn = (
                    f"QUANTITY MISMATCH for {sym}: broker={broker_qty[sym]} "
                    f"contracts, DB={db_qty[sym]}. Possible double-fill or partial close."
                )
                logger.critical(warn)
                warnings.append(warn)
                quantity_mismatches.append(warn)
        for finding in signed:
            text = finding.describe()
            if finding.kind == "direction":
                text += (
                    ". The position is the opposite SIDE of what is recorded "
                    "(a flipped or wrong-way position); it is not auto-corrected."
                )
                direction_mismatches.append(text)
                direction_tickers.append(finding.symbol)
            elif finding.kind == "quantity":
                text += ". Possible double-fill or partial close."
                quantity_mismatches.append(text)
            logger.critical(text)
            warnings.append(text)

        result = ReconciliationResult(
            # DB-only trades are drift too; warning-only does not mean clean.
            clean=(not untracked and not phantom and not quantity_mismatches
                   and not direction_mismatches),
            broker_position_count=len(broker_positions),
            db_open_trade_count=len(db_open_trades),
            untracked_at_broker=list(untracked),
            phantom_in_db=list(phantom),
            quantity_mismatches=quantity_mismatches,
            direction_mismatch_tickers=direction_tickers,
            warnings=warnings,
        )

        # Untracked broker positions — halt trading
        if untracked:
            msg = (
                f"RECONCILIATION FAILURE: {len(untracked)} positions at broker "
                f"have no matching DB record: {untracked}. "
                f"These are UNTRACKED positions — OlbosTrade does not know about them. "
                f"Trading halted until manual review."
            )
            logger.critical(msg)
            raise ReconciliationError(msg, result=result)

        # A position on the wrong side is the more serious of the two
        if direction_mismatches:
            raise ReconciliationError(
                f"Position direction mismatch detected — trading halted: {direction_mismatches}",
                result=result,
            )

        # Quantity mismatches are also a hard halt
        if quantity_mismatches:
            raise ReconciliationError(
                f"Position quantity mismatch detected — trading halted: {quantity_mismatches}",
                result=result,
            )

        if phantom:
            warning = (
                f"WARNING: {len(phantom)} DB open trades have no matching broker position: "
                f"{phantom}. These may be stale records. Review and close if needed."
            )
            logger.warning(warning)
            warnings.append(warning)

        if result.clean:
            logger.info(
                "Position reconciliation clean — %d broker positions, %d DB open trades",
                result.broker_position_count, result.db_open_trade_count,
            )
        else:
            logger.warning(
                "Position reconciliation warnings: %s", warnings
            )

        return result

    async def reconcile_and_record(self, source: str = "system") -> ReconciliationResult:
        """
        Run reconciliation and persist the latest broker-vs-DB state.

        This provides a canonical, queryable status record for the operator
        terminal without changing the existing fail-closed behavior.
        """
        try:
            result = await self.reconcile()
        except ReconciliationError as exc:
            await self._persist_snapshot(
                result=exc.result,
                source=source,
                status="error",
                error_message=str(exc),
            )
            raise

        status = "warning" if result.warnings else "clean"
        await self._persist_snapshot(result=result, source=source, status=status)
        return result

    async def _persist_snapshot(
        self,
        *,
        result: Optional[ReconciliationResult],
        source: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Best-effort persistence of reconciliation state.

        Operational status should be visible in the terminal even when no one is
        tailing logs, but persistence failure must never mask the original drift.
        """
        try:
            from app.core.config import settings
            from app.models.reconciliation_snapshot import ReconciliationSnapshot

            checked_at = result.checked_at if result is not None else datetime.now(timezone.utc)

            snapshot = ReconciliationSnapshot(
                status=status,
                clean=bool(result.clean) if result is not None else False,
                source=source,
                broker_name=settings.broker,
                broker_position_count=result.broker_position_count if result is not None else 0,
                db_open_trade_count=result.db_open_trade_count if result is not None else 0,
                untracked_at_broker=list(result.untracked_at_broker) if result is not None else [],
                phantom_in_db=list(result.phantom_in_db) if result is not None else [],
                quantity_mismatches=list(result.quantity_mismatches) if result is not None else [],
                warnings=list(result.warnings) if result is not None else [],
                error_message=error_message,
                checked_at=checked_at,
            )

            async with AsyncSessionLocal() as session:
                session.add(snapshot)
                await session.commit()
        except Exception as exc:
            logger.error("Could not persist reconciliation snapshot: %s", exc)

    async def load_guardrail_counters_from_db(self) -> dict:
        """
        Load the guardrail COUNTERS from the most recent portfolio snapshot,
        so consecutive_losses, trades_today and an active cooling-off period do
        not reset to zero when the process restarts.

        COUNTERS ONLY, and that restriction is the point. This previously also
        returned `current_value` read off the snapshot — a stored number
        standing in for a live measurement. That is the exact shape of the
        failure that took the desk down on 2026-10-31: the account-summary
        route substituted STARTING_CAPITAL for a broker read it could not make,
        the guardrail engine compared the constant against the all-time peak,
        and a fabricated "81.82% drawdown" suspended trading for 30 days. A
        stale snapshot value would do the same thing with a different number.

        Portfolio value, P&L and anything derived from them must be measured
        every time. What legitimately survives a restart is bookkeeping: how
        many losses in a row, how many trades today, and whether a cooling-off
        the engine already imposed is still running.
        """
        from app.core.config import settings
        from app.models.risk_state import PortfolioSnapshot

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(PortfolioSnapshot)
                .order_by(PortfolioSnapshot.timestamp.desc())
                .limit(1)
            )
            snap = result.scalar_one_or_none()

        from decimal import Decimal
        if snap is None:
            logger.info("No portfolio snapshot found — guardrail counters start at zero")
            return {
                "consecutive_losses": 0,
                "trades_today": 0,
                "cooling_off_until": None,
            }

        logger.info(
            "Restored guardrail state from DB — "
            "consecutive_losses=%d trades_today=%d trading_mode=%s cooling_off_until=%s",
            snap.consecutive_losses, snap.trades_today,
            snap.trading_mode, snap.cooling_off_until,
        )

        return {
            "consecutive_losses": snap.consecutive_losses,
            "trades_today": snap.trades_today,
            "cooling_off_until": snap.cooling_off_until,
        }

    async def save_portfolio_snapshot(
        self,
        portfolio_value: float,
        daily_pnl: float,
        weekly_pnl: float,
        monthly_pnl: float,
        consecutive_losses: int,
        trades_today: int,
        trading_mode: str,
        cooling_off_until=None,
        account_value_known: bool = True,
    ) -> None:
        """
        Persist the guardrail counters after a trade cycle, so they survive a
        restart.

        Refuses to write when `account_value_known` is False, and that refusal
        is load-bearing. On 2026-10-31 a disconnected IBKR gateway made the
        account-summary route substitute STARTING_CAPITAL for a reading it
        could not take; compared against the all-time peak that produced a
        fabricated 81.82% drawdown and a 30-DAY TRADING SUSPENSION. The only
        reason that did not outlive the disconnect is that nothing was writing
        this table. Wiring it up without this guard would have made the
        phantom suspension permanent — a worse bug than the one it fixes.

        A cycle whose inputs were not measured has nothing worth remembering.
        Skipping the write leaves the previous good snapshot in place, which is
        the conservative outcome: counters continue from the last cycle that
        actually knew what the book was worth.
        """
        if not account_value_known:
            logger.warning(
                "Skipping portfolio snapshot — account value was not readable this "
                "cycle. Persisting guardrail state derived from a substituted "
                "value would make a fabricated limit breach outlive the outage "
                "that caused it."
            )
            return
        from app.models.risk_state import PortfolioSnapshot
        from app.core.config import settings

        from app.broker.ibkr_coordinator import Priority, ibkr_coordinator

        positions = await self.broker.get_positions()
        account = await ibkr_coordinator.submit(
            Priority.P0, self.broker.get_account_summary, key="account_summary", req_type="ACCOUNT_SUMMARY",
        )

        async with AsyncSessionLocal() as session:
            snap = PortfolioSnapshot(
                timestamp=datetime.now(timezone.utc),
                total_value=portfolio_value,
                cash=float(account.cash_balance),
                open_position_count=len(positions),
                daily_pnl=daily_pnl,
                weekly_pnl=weekly_pnl,
                monthly_pnl=monthly_pnl,
                trading_mode=trading_mode,
                consecutive_losses=consecutive_losses,
                trades_today=trades_today,
                cooling_off_until=cooling_off_until,
            )
            session.add(snap)
            await session.commit()

        logger.debug(
            "Portfolio snapshot saved — value=%.2f mode=%s consecutive_losses=%d",
            portfolio_value, trading_mode, consecutive_losses,
        )
