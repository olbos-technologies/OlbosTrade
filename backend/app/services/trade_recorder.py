"""
TradeRecorder — writes Trade rows and auto-creates JournalEntry records.

Called by paper_trader.py immediately after a successful fill.
This is the missing link between execution and the journal/analytics.

What it writes automatically:
  Trade row         → used by Mode Analytics, backtester, optimizer
  JournalEntry row  → used by Journal page, rule breach analysis,
                      monthly review, confidence vs outcome

The JournalEntry pre-populates all mechanical fields automatically.
The trader only needs to fill in the human fields
(pre_trade_thesis, followed_rules, post_trade_notes, tags)
via the Journal UI after each session — or never, if they only want
the performance analytics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


class TradeRecorder:
    """
    Persists a filled trade to the database and creates
    a corresponding JournalEntry stub automatically.

    Both writes happen in a single DB transaction — if either
    fails, neither is committed (no orphaned records).
    """

    async def record_fill(
        self,
        *,
        strategy:              str,
        underlying:            str,
        option_type:           str,
        short_strike:          float,
        long_strike:           float,
        expiration:            date,
        entry_credit:          float,
        quantity:              int,
        signal_score:          float,
        iv_rank:               float,
        regime:                str,
        approved_by:           str,
        dispatch_id:           str,
        # Whose position this is. REQUIRED, keyword-only and with no default:
        # a default would silently file every trade in one scope, and the one
        # value that looks like a safe default (None) is the single-operator
        # scope, so it would attribute a tenant's position to the operator.
        # Callers resolve it with services/trade_scope.py — from the request
        # for a user-initiated entry, or `autonomous_scope` for one the
        # background scanner started, which refuses rather than guessing.
        organization_id:       "uuid.UUID | None",
        net_fill_price:        Optional[float] = None,
        spread_width:          Optional[float] = None,
        target_price:          Optional[float] = None,
        status:                str = "open",
    ) -> Optional[str]:
        """
        Write Trade + JournalEntry to DB in one transaction.

        ``status`` is the entry state to persist:
          - ``"open"``    — the broker confirmed a fill; this is a live position.
          - ``"pending"`` — the order was accepted but is still working (no fill
                            yet). A pending row is NOT a live position and NOT a
                            closed trade — the fill reconciler (_poll_fills)
                            promotes it to ``open`` once the broker reports a fill,
                            or cancels it if the order terminates unfilled. This
                            prevents unfilled/ProgramCancelled limit orders from
                            ever becoming phantom positions or fabricated P&L.

        Returns the trade_id string on success, None on failure.

        Idempotent: if dispatch_id already exists in the DB the existing
        trade_id is returned without creating a duplicate row.

        CRITICAL alert is emitted when a broker fill cannot be persisted —
        the position exists at the broker but is untracked in the DB.
        """
        try:
            from sqlalchemy import select
            from app.core.database import AsyncSessionLocal
            from app.models.trade import Trade
            from app.models.journal_entry import JournalEntry
            from app.services.strategy_config_service import strategy_config_service
            from app.services.trading_mode import trading_mode_manager

            # Read the live risk-style mode directly rather than threading it
            # through every caller — trading_mode_manager is already treated
            # as ambient global state elsewhere (main.py reads
            # trading_mode_manager.config.risk_per_trade_pct the same way).
            # This is what trading_mode_at_entry was always meant to hold
            # (conservative|balanced|aggressive|scalper); approved_by is the
            # separate concept of who/what triggered this specific fill.
            risk_mode = trading_mode_manager.current.active_mode.value

            # ── Idempotency check ───────────────────────────────────────────
            async with AsyncSessionLocal() as session:
                existing = (await session.execute(
                    select(Trade).where(Trade.dispatch_id == dispatch_id)
                )).scalar_one_or_none()
            if existing is not None:
                logger.info(
                    "record_fill: dispatch_id %s already recorded as trade %s — skipping duplicate",
                    dispatch_id, existing.id,
                )
                return str(existing.id)

            trade_id = uuid.uuid4()
            snapshot_id = None

            async with AsyncSessionLocal() as session:
                active_snapshot = await strategy_config_service.get_active_snapshot(session, strategy)
                if active_snapshot is not None:
                    snapshot_id = active_snapshot.id

            async with AsyncSessionLocal() as session:
                async with session.begin():

                    # ── Trade row ──────────────────────────────────────────────
                    trade = Trade(
                        id=trade_id,
                        organization_id=organization_id,
                        strategy=strategy,
                        underlying=underlying,
                        spread_type=option_type or "equity",
                        short_strike=Decimal(str(short_strike or 0)),
                        long_strike=Decimal(str(long_strike or 0)),
                        expiration=expiration,
                        credit_received=Decimal(str(round(entry_credit, 4))),
                        target_price=(
                            Decimal(str(round(target_price, 4)))
                            if target_price else None
                        ),
                        cost_to_close=None,
                        pnl=None,
                        pnl_pct=None,
                        status=status,
                        entry_date=datetime.now(timezone.utc),
                        exit_date=None,
                        exit_reason=None,
                        signal_score=Decimal(str(round(signal_score, 4))),
                        quantity=int(quantity or 1),
                        trading_mode_at_entry=risk_mode,
                        approved_by=approved_by or "unknown",
                        regime=regime or None,
                        dispatch_id=dispatch_id,
                        strategy_snapshot_id=snapshot_id,
                        mfe_pnl=Decimal("0"),
                        mae_pnl=Decimal("0"),
                        pnl_capture_pct=None,
                    )
                    session.add(trade)

            # ── JournalEntry stub (separate transaction so FK is satisfied) ──
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    journal = JournalEntry(
                        trade_id=trade_id,
                        pre_trade_thesis="",
                        confidence_level=None,   # NULL — trader fills in via Journal UI
                        market_context=regime or "",
                        tags=[],
                        mistake_tags=[],
                    )
                    session.add(journal)

            logger.info(
                "Trade recorded (%s): %s %s %s strike=%.0f/%.0f "
                "credit=%.2f mode=%s approved_by=%s score=%.3f dispatch_id=%s trade_id=%s",
                status, strategy, underlying, option_type,
                short_strike, long_strike,
                entry_credit, risk_mode, approved_by, signal_score, dispatch_id, trade_id,
            )
            return str(trade_id)

        except Exception as exc:
            logger.critical(
                "CRITICAL: TradeRecorder.record_fill FAILED — "
                "broker fill for dispatch_id=%s is UNTRACKED in DB. "
                "Position exists at broker but not in OlbosTrade records. "
                "Manual reconciliation required. Error: %s",
                dispatch_id, exc, exc_info=True,
            )
            return None

    async def confirm_fill(self, *, trade_id: str,
                           net_fill_price: Optional[float] = None,
                           quantity: Optional[float] = None) -> bool:
        """
        Promote a ``pending`` trade to ``open`` once the broker confirms a fill.

        Called by the fill reconciler when a pending order's position appears at
        the broker (or an execution fill is found). Optionally corrects the
        recorded entry credit to the real net fill price, and — just as
        important — the recorded quantity to what the broker actually holds.
        Without this, a trade promoted from `pending` keeps whatever quantity
        was planned at signal time forever, even when the real fill (or
        residual shares from prior activity on the same ticker) came in
        different — confirmed in production via a real MU/SNDK/MRVL quantity
        drift (DB under-reporting broker holdings by 2-7x). Returns True on
        success, False if the trade is missing or not pending.
        """
        try:
            from sqlalchemy import select
            from app.core.database import AsyncSessionLocal
            from app.models.trade import Trade

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    trade = (await session.execute(
                        select(Trade).where(Trade.id == trade_id)
                    )).scalar_one_or_none()
                    if trade is None or trade.status != "pending":
                        return False
                    trade.status = "open"
                    if net_fill_price is not None:
                        trade.credit_received = Decimal(str(round(net_fill_price, 4)))
                    if quantity is not None and quantity > 0:
                        trade.quantity = int(round(quantity))
            logger.info("Pending trade %s confirmed filled → open (fill=%s, qty=%s)",
                        trade_id, net_fill_price, quantity)
            return True
        except Exception as exc:
            logger.warning("confirm_fill failed for %s: %s", trade_id, exc)
            return False

    async def cancel_pending(self, *, trade_id: str, reason: str) -> bool:
        """
        Mark a ``pending`` trade as ``cancelled`` when its order terminated
        unfilled (DAY order expired, ProgramCancel, rejection). This is what
        stops an unfilled limit order from ever becoming a phantom position or
        a fabricated round-trip. Returns True on success.
        """
        try:
            from datetime import timezone as _tz
            from sqlalchemy import select
            from app.core.database import AsyncSessionLocal
            from app.models.trade import Trade

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    trade = (await session.execute(
                        select(Trade).where(Trade.id == trade_id)
                    )).scalar_one_or_none()
                    if trade is None or trade.status != "pending":
                        return False
                    trade.status = "cancelled"
                    trade.exit_reason = reason
                    trade.exit_date = datetime.now(_tz.utc)
            logger.info("Pending trade %s cancelled (%s)", trade_id, reason)
            return True
        except Exception as exc:
            logger.warning("cancel_pending failed for %s: %s", trade_id, exc)
            return False

    async def update_excursion(self, *, trade_id: str, unrealized_pnl: float) -> bool:
        """
        Update a trade's running MFE (max favorable) / MAE (max adverse) from the
        latest unrealized P&L. MFE tracks the highest value seen, MAE the lowest.
        Called periodically by the position poller while the trade is open.
        Returns True if a high/low-water mark was advanced.
        """
        try:
            from sqlalchemy import select
            from app.core.database import AsyncSessionLocal
            from app.models.trade import Trade

            changed = False
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    trade = (await session.execute(
                        select(Trade).where(Trade.id == trade_id)
                    )).scalar_one_or_none()
                    if trade is None or trade.status != "open":
                        return False
                    pnl = Decimal(str(round(unrealized_pnl, 4)))
                    if trade.mfe is None or pnl > trade.mfe:
                        trade.mfe = pnl
                        changed = True
                    if trade.mae is None or pnl < trade.mae:
                        trade.mae = pnl
                        changed = True
            return changed
        except Exception as exc:
            logger.debug("update_excursion failed for %s: %s", trade_id, exc)
            return False

    async def record_exit(
        self,
        *,
        trade_id:    str,
        cost_to_close: float,
        exit_reason: str,
    ) -> bool:
        """
        Update an open Trade row with exit data and compute P&L.
        Called when a position is closed.

        Returns True on success.
        """
        try:
            from sqlalchemy import select
            from app.core.config import settings
            from app.core.database import AsyncSessionLocal
            from app.models.trade import Trade

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    result = await session.execute(
                        select(Trade).where(Trade.id == uuid.UUID(trade_id))
                    )
                    trade = result.scalar_one_or_none()
                    if trade is None:
                        logger.warning("record_exit: trade %s not found", trade_id)
                        return False

                    entry  = float(trade.credit_received or 0)
                    qty    = int(getattr(trade, "quantity", None) or 1)

                    # Equity has no x100 contract multiplier; options do.
                    # Direction is encoded in spread_type ("equity_long"/"equity_short").
                    spread_type = (getattr(trade, "spread_type", "") or "").lower()
                    is_equity   = (getattr(trade, "strategy", "") == "equity") or spread_type.startswith("equity")

                    if is_equity:
                        multiplier = 1
                        if spread_type == "equity_short":
                            # Short: profit when exit price is below entry.
                            pnl = (entry - cost_to_close) * qty * multiplier
                        else:
                            # Long (default): profit when exit price is above entry.
                            pnl = (cost_to_close - entry) * qty * multiplier
                    else:
                        # Credit spread: entry credit received, pay cost_to_close to exit.
                        multiplier = 100
                        pnl = (entry - cost_to_close) * qty * multiplier

                    starting_capital = float(settings.starting_capital)
                    pnl_pct = pnl / starting_capital if starting_capital > 0 else 0.0
                    mfe_pnl = float(getattr(trade, "mfe_pnl", 0) or 0)
                    pnl_capture_pct = (pnl / mfe_pnl) if mfe_pnl > 0 else None

                    trade.cost_to_close = Decimal(str(round(cost_to_close, 4)))
                    trade.pnl           = Decimal(str(round(pnl, 2)))
                    trade.pnl_pct       = Decimal(str(round(pnl_pct, 6)))
                    trade.pnl_capture_pct = (
                        Decimal(str(round(pnl_capture_pct, 6)))
                        if pnl_capture_pct is not None
                        else None
                    )
                    trade.status        = "closed"
                    trade.exit_date     = datetime.now(timezone.utc)
                    trade.exit_reason   = exit_reason

            logger.info(
                "Trade exited: %s | P&L=$%.0f | reason=%s",
                trade_id, pnl, exit_reason,
            )
            return True

        except Exception as exc:
            logger.error("record_exit failed for %s: %s", trade_id, exc)
            return False

    async def record_close_unknown(
        self,
        *,
        trade_id:    str,
        exit_reason: str,
    ) -> bool:
        """
        Close a trade whose real exit price could NOT be determined.

        Marks the trade closed with pnl = NULL (unknown) rather than fabricating a
        neutral $0 close. Analytics and guardrail P&L windows filter on
        ``pnl IS NOT NULL``, so an unknown close is excluded instead of polluting
        win-rate / loss-limit math with a fake zero. Emits CRITICAL so the trade
        can be reconciled by hand against the broker.

        Returns True on success.
        """
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.trade import Trade

            async with AsyncSessionLocal() as session:
                async with session.begin():
                    trade = await session.get(Trade, uuid.UUID(trade_id))
                    if trade is None:
                        logger.warning("record_close_unknown: trade %s not found", trade_id)
                        return False
                    trade.cost_to_close = None
                    trade.pnl           = None
                    trade.pnl_pct       = None
                    trade.status        = "closed"
                    trade.exit_date     = datetime.now(timezone.utc)
                    trade.exit_reason   = exit_reason

            logger.critical(
                "Trade %s closed with UNKNOWN P&L (reason=%s) — broker position is "
                "gone but no execution price was available. Manual reconciliation "
                "required to record realized P&L.",
                trade_id, exit_reason,
            )
            return True

        except Exception as exc:
            logger.error("record_close_unknown failed for %s: %s", trade_id, exc)
            return False


# ── Singleton ──────────────────────────────────────────────────────────────────
trade_recorder = TradeRecorder()
