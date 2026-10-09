"""
Position rotation — free slots when at max concurrent opens.

When a new entry (equity or options) is blocked by max_positions and
``settings.position_rotation_on_max`` is True, close N open positions so
the new trade can proceed. Symmetric on both ends: the incoming entry may
be equity or options, and the candidate pool being closed may be equity
OR options — whichever open position genuinely ranks worst. Winner
Protection is a hard floor, not a ranking preference:
a position with unrealized P&L above
``settings.position_rotation_winner_pnl_floor`` (default: any profit at
all) is never a rotation target, full stop — it is excluded before any
ranking happens, and if too few non-winners remain to free the requested
slots, rotation is skipped entirely rather than touching a winner.

Among the eligible (non-winning) positions, closure order is:

  1. Lowest Position Quality Score (``compute_equity_hold_score`` for
     equity, ``compute_options_hold_score`` for options — both
     alpha_edge_engine.py's hold-score formulas reused, not new ones)
  2. Positions in a flagged correlation cluster close before non-clustered
     ties — a tiebreaker only, never overrides a real quality_score
     difference. Neutral/no-op when the correlation cache
     (rotation_correlation_cache.py) is stale or doesn't cover the ticker
     — always true for options tickers, since that cache is equity-only.
  3. Lowest entry ``signal_score`` among ties / missing quality score
  4. Oldest ``entry_date`` among ties / missing both

Never closes the incoming ticker's underlying.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Execution boundary for rotation-sourced closes ───────────────────────────
ROTATION_CLOSED_BY = "position_rotation"


class RotationApprovalRequired(RuntimeError):
    """Raised when a rotation-sourced close is attempted without approval."""


def _assert_rotation_approved(closed_by: str, rotation_approval: Optional[str]) -> None:
    """Refuse any rotation-sourced close that carries no approval token.

    This guard lives at the chokepoint — inside the close functions — rather
    than at the Stage 2b call site, deliberately. Removing the call from
    Stage 2b stops *that* path; it does not stop the next one. Any caller,
    present or future, that reaches a close labelled `position_rotation`
    without an approval token is refused here, so the boundary holds even if
    someone later re-wires rotation without remembering this requirement.

    No issuer for this token exists yet — the approval queue that mints it is
    a later increment — so today every rotation-sourced close raises. That is
    the intended state: rotation may recommend, never execute. Manual closes
    pass closed_by="manual" and are unaffected; so are stop/target exits,
    which never route through here at all.
    """
    if closed_by != ROTATION_CLOSED_BY:
        return
    if not rotation_approval:
        raise RotationApprovalRequired(
            "Rotation-sourced close refused: no approval token. Capital "
            "Rotation may produce a ROTATION_REVIEW for approval, but must "
            "never close a position on its own."
        )


@dataclass(frozen=True)
class RotationCandidate:
    trade_id: str
    underlying: str
    # None means "we could not establish this position's P&L" — a quote
    # failure, a missing entry price, or no broker legs found. It is NOT
    # zero: Winner Protection reads this, and a fabricated 0.0 passes a
    # <= 0.0 floor, which would turn every protected winner into a
    # rotation candidate exactly when the data is least trustworthy.
    unrealized_pnl: Optional[float]
    confidence: Optional[float]
    entry_date: Optional[datetime]
    spread_type: str
    quality_score: Optional[float] = None
    in_flagged_cluster: Optional[bool] = None


def _rank_key(c: RotationCandidate):
    """Ascending sort key: quality_score (None-last) -> cluster membership
    (flagged sorts before not-flagged/unknown) -> confidence (None-last) ->
    oldest entry_date. Tuple-of-tuples so a missing field always sorts
    after every real value at that tier, never interpolated as if it were
    a real score.

    Cluster membership is a tiebreaker only: it only changes the outcome
    when two candidates already share the same quality_score tier.
    `in_flagged_cluster` is None whenever the correlation cache is stale/
    missing or doesn't cover this ticker — that must read identically to
    "not flagged" (0 vs 1, not a 3-way split), so a dead cache degrades
    this tier to a full no-op rather than biasing ranking in either
    direction."""
    quality = (0, c.quality_score) if c.quality_score is not None else (1, 0.0)
    clustered = 0 if c.in_flagged_cluster else 1   # True -> 0 (closed first); False/None -> 1 (tie)
    confidence = (0, c.confidence) if c.confidence is not None else (1, 0.0)
    entry = c.entry_date or datetime.min.replace(tzinfo=timezone.utc)
    return (quality, clustered, confidence, entry)


def select_rotation_targets(
    candidates: Sequence[RotationCandidate],
    *,
    incoming_ticker: str,
    count: int = 2,
) -> list[RotationCandidate]:
    """
    Pick up to ``count`` equity positions to close for a new entry.

    Returns [] if fewer than ``count`` eligible candidates remain after
    excluding the incoming underlying and applying the Winner Protection
    floor (caller should keep blocking rather than touch a winner).
    """
    if count <= 0:
        return []
    incoming = (incoming_ticker or "").upper()
    pool = [
        c for c in candidates
        if (c.underlying or "").upper() != incoming
        and (c.spread_type or "").lower() in ("equity_long", "equity_short", "put", "call")
    ]

    winner_floor = float(getattr(settings, "position_rotation_winner_pnl_floor", 0.0) or 0.0)
    # `is not None` first: an unknown P&L is excluded outright, never compared
    # against the floor. Closing a position is irreversible and immediate,
    # while declining to rotate only leaves a signal blocked — so the unknown
    # case has to fall toward not acting. A fabricated 0.0 here used to pass
    # the <= 0.0 floor, meaning a quote outage promoted every protected
    # winner into a rotation candidate.
    unknown = [c for c in pool if c.unrealized_pnl is None]
    if unknown:
        logger.warning(
            "rotation: %d candidate(s) excluded — P&L unavailable: %s",
            len(unknown), ", ".join(sorted(c.underlying for c in unknown)),
        )
    eligible = [
        c for c in pool
        if c.unrealized_pnl is not None and c.unrealized_pnl <= winner_floor
    ]
    if len(eligible) < count:
        return []

    return sorted(eligible, key=_rank_key)[:count]


def _equity_entry_price(trade: Any) -> float:
    return float(getattr(trade, "credit_received", None) or 0)


def _equity_unrealized(trade: Any, mid: float) -> Optional[float]:
    """None when P&L cannot be established — never 0.0. See
    RotationCandidate.unrealized_pnl for why the distinction is load-bearing."""
    entry = _equity_entry_price(trade)
    qty = int(getattr(trade, "quantity", None) or 1)
    st = (getattr(trade, "spread_type", None) or "").lower()
    if entry <= 0 or mid <= 0:
        return None
    if st == "equity_short":
        return (entry - mid) * qty
    return (mid - entry) * qty


async def _mid_price(broker: Any, ticker: str) -> Optional[float]:
    try:
        from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
        quote = await ibkr_coordinator.submit(
            Priority.P1,
            lambda: broker.get_latest_quote(ticker),
            req_type="QUOTE",
            symbol=ticker,
            timeout=30.0,
        )
        bid = float(getattr(quote, "bid_price", 0) or 0)
        ask = float(getattr(quote, "ask_price", 0) or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        # No last-price fallback: Quote carries only bid/ask/sizes/timestamp,
        # so the getattr(quote, "last_price") that used to sit here always
        # read 0 and never fired. P&L no longer comes from this function at
        # all — see _unrealized_by_position.
        return None
    except Exception as exc:
        logger.warning("rotation quote failed for %s: %s", ticker, exc)
        return None


async def _unrealized_by_position(broker: Any) -> dict[tuple[str, str], float]:
    """IBKR's own unrealized P&L per (SYMBOL, asset_class), from ONE call.

    Single source of truth for both asset classes. Equity P&L used to be
    reconstructed as (mid - entry) x qty from a fetched quote while options
    took the broker's figure — two derivations that could disagree, and only
    one of which survived outside regular hours.

    The equity path was broken in a way nothing caught: Quote carries only
    bid_price/ask_price/sizes/timestamp, and _mid_price's fallback read
    `last_price`, a field that has never existed on that model. So whenever
    bid/ask were absent — every weekend, every overnight, any thin quote —
    mid came back None, every position read as unknown P&L, Winner Protection
    excluded everything, and rotation could not act. Confirmed live
    2026-08-29: MRVL quoted bid=None ask=None while the broker reported
    -$11,167.34 and the positions endpoint rendered it without trouble.

    Also materially faster. The old path issued one QUOTE per candidate,
    sequentially, at ~2.8s each through the coordinator; this is a single
    ib.portfolio() cache read with no network round-trip.

    A symbol absent from the result yields no entry, so the caller sees None
    (unknown) rather than zero — an empty portfolio cache after a reconnect
    must never read as "every position is flat".
    """
    totals: dict[tuple[str, str], float] = {}
    try:
        positions = await broker.get_positions()
    except Exception as exc:
        logger.warning("rotation position fetch failed: %s", exc)
        return totals
    for pos in positions:
        pnl = getattr(pos, "unrealized_pnl", None)
        if pnl is None:
            continue
        key = (getattr(pos, "underlying", "") or "").upper()
        if not key:
            continue
        cls = "equity" if getattr(pos, "asset_type", None) == "equity" else "options"
        totals[(key, cls)] = totals.get((key, cls), 0.0) + float(pnl)
    return totals


async def close_equity_trade(
    trade: Any,
    *,
    broker: Any,
    closed_by: str = "position_rotation",
    order_type: str = "market",
    limit_price: Optional[float] = None,
    rotation_approval: Optional[str] = None,
) -> dict:
    """
    Submit an equity close for an open Trade row. Shared by manual close and rotation.
    Does not check kill switch (closing reduces risk).

    Side and quantity come from the broker's OWN live position (via
    get_equity_positions() — a local ib.portfolio() cache read, not a
    network round-trip, same convention already used above by
    _unrealized_by_position), never from trade.spread_type/
    trade.quantity. Root-caused in production on 2026-08-26: the DB's
    recorded quantity can drift from the broker's real holding (a partial
    fill never fully reconciled, an earlier close that itself misfired,
    etc.) — trading on that stale number is exactly what produced several
    real orphaned positions, discovered when a "closed" Trade row's ticker
    still showed a live broker position, in three cases flipped to the
    *opposite* side entirely (a close order sized larger than the real
    holding oversold straight through zero). Sourcing the live quantity
    here makes every close self-correcting regardless of any prior drift,
    rather than compounding it.

    A rotation-sourced close (closed_by="position_rotation") requires an
    approval token — see _assert_rotation_approved. Note the default for
    closed_by is "position_rotation", so a caller that forgets the kwarg now
    fails closed rather than silently submitting an unreviewed close.
    """
    _assert_rotation_approved(closed_by, rotation_approval)

    from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
    from app.services.trade_recorder import trade_recorder

    spread_type = (getattr(trade, "spread_type", None) or "").lower()
    if spread_type not in ("equity_long", "equity_short"):
        raise ValueError(f"equity close only; got spread_type={spread_type!r}")

    ticker = trade.underlying
    trade_id = str(trade.id)

    live_positions = await broker.get_equity_positions()
    live_qty = next(
        (int(p.quantity) for p in live_positions if p.symbol == ticker), 0
    )
    if live_qty == 0:
        raise RuntimeError(
            f"{ticker} is already flat at the broker — nothing to close "
            f"(trade_id={trade_id})"
        )

    close_side = "SELL" if live_qty > 0 else "BUY"
    qty = abs(live_qty)

    cancelled = await broker.cancel_open_orders(ticker)
    result = await ibkr_coordinator.submit(
        Priority.P0,
        lambda: broker.place_equity_order(
            ticker=ticker, qty=qty, side=close_side,
            order_type=order_type, limit_price=limit_price,
        ),
        req_type="PLACE_ORDER", symbol=ticker,
    )
    if result.status in ("cancelled", "rejected"):
        raise RuntimeError(f"broker rejected close for {ticker}: {result.status}")

    if result.status == "filled" and result.fill_price is not None:
        await trade_recorder.record_exit(
            trade_id=trade_id,
            cost_to_close=float(result.fill_price),
            exit_reason="position_rotation" if closed_by == "position_rotation" else "manual",
        )

    return {
        "trade_id": trade_id,
        "ticker": ticker,
        "action": close_side,
        "quantity": qty,
        "order_id": result.order_id,
        "status": result.status,
        "cancelled_open_orders": cancelled,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "closed_by": closed_by,
    }


async def close_options_trade(
    trade: Any,
    *,
    broker: Any,
    closed_by: str = "manual",
    rotation_approval: Optional[str] = None,
) -> dict:
    """
    Submit a closing order for an open 2-leg options spread Trade row: buy
    back the short leg, sell the long leg, as a single BAG combo order via
    broker.place_order(). Shared entry point for manual close (rotation
    does not use this yet — options rotation is a separate, deferred
    increment).

    Always submits MKT (order_type="MKT", limit_price=Decimal("0")) — the
    LMT combo net-credit/net-debit sign convention documented on
    place_order() has never been exercised for a closing (action-flipped)
    combo anywhere in this codebase, so this function does not accept
    order_type/limit_price overrides. That is a deliberate, stated
    limitation, not a silent gap.

    Does not check kill switch (closing reduces risk). No
    asyncio.TimeoutError handling — same convention as close_equity_trade;
    timeouts propagate to the caller.
    """
    from decimal import Decimal

    from app.broker.broker_interface import SpreadLeg, SpreadOrder
    _assert_rotation_approved(closed_by, rotation_approval)

    from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
    from app.services.trade_recorder import trade_recorder

    spread_type = (getattr(trade, "spread_type", None) or "").lower()
    if spread_type not in ("put", "call"):
        raise ValueError(f"options close only; got spread_type={spread_type!r}")

    short_strike = getattr(trade, "short_strike", None)
    long_strike = getattr(trade, "long_strike", None)
    expiration = getattr(trade, "expiration", None)
    if short_strike is None or long_strike is None:
        raise ValueError(f"trade {trade.id} missing short_strike/long_strike — cannot close")
    if expiration is None:
        raise ValueError(f"trade {trade.id} missing expiration — cannot close")

    ticker = trade.underlying
    qty = int(trade.quantity or 1)
    trade_id = str(trade.id)
    strategy = trade.strategy or "options_close"

    # Mirror of the entry construction: short_strike was SELL-to-open, now
    # BUY-to-close; long_strike was BUY-to-open, now SELL-to-close.
    order = SpreadOrder(
        strategy=strategy,
        underlying=ticker,
        legs=[
            SpreadLeg(symbol=ticker, expiration=expiration, strike=Decimal(str(short_strike)),
                      option_type=spread_type, action="BUY", quantity=qty),
            SpreadLeg(symbol=ticker, expiration=expiration, strike=Decimal(str(long_strike)),
                      option_type=spread_type, action="SELL", quantity=qty),
        ],
        limit_price=Decimal("0"),
        order_type="MKT",
        time_in_force="DAY",
    )

    # Size this close from the BROKER's live legs, never from the DB row.
    #
    # Two separate hazards, and the first fix only covered one of them:
    #
    #   1. The position may not exist at all. paper_trade.py emits DB-only rows
    #      (source="db_only", tracked=True) for open Trade rows with no matching
    #      broker position, carrying a valid id and valid strikes. A closing
    #      combo for a position nobody holds is an OPENING trade.
    #
    #   2. The SIZE may be wrong. `trade.quantity` is the DB's belief. A partial
    #      fill or drift can leave one contract live while the row still says
    #      two — and a 2-lot mirrored combo against a 1-lot position closes one
    #      and opens one in the opposite direction.
    #
    # close_equity_trade() has guarded both since 2026-08-26, after exactly this
    # drift was root-caused in production: it takes side and size from
    # get_equity_positions() and refuses a flat symbol. The first pass at this
    # guard cited that precedent and then only checked existence, which is the
    # half of the lesson that was never the point. Caught in review on PR #64.
    #
    # Both legs must be live. A combo needs both; if one has expired, been
    # assigned or been closed separately, the mirrored order is not a close of
    # anything and refusing is the safe direction — the operator can still
    # flatten at the broker.
    def _same(a, b) -> bool:
        """Tolerant equality for strikes and expirations across adapters."""
        if a is None or b is None:
            return False
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return str(a)[:10] == str(b)[:10]

    live = await broker.get_positions()

    def _leg_qty(strike) -> int:
        for p in live:
            if (getattr(p, "asset_type", "option") == "option"
                    and p.underlying == ticker
                    and (getattr(p, "option_type", None) or "").lower() == spread_type
                    and _same(getattr(p, "strike", None), strike)
                    and _same(getattr(p, "expiration", None), expiration)):
                return abs(int(p.quantity or 0))
        return 0

    live_short = _leg_qty(short_strike)
    live_long = _leg_qty(long_strike)

    if live_short == 0 or live_long == 0:
        raise RuntimeError(
            f"{ticker} {spread_type} {short_strike}/{long_strike} exp {expiration} "
            f"is not fully live at the broker (short={live_short}, long={live_long}) "
            f"— refusing to submit a closing combo for it (trade_id={trade_id}). "
            f"The DB row is open; the broker is not holding both legs. Submitting "
            f"this would OPEN exposure, not close it. Flatten at the broker."
        )

    live_qty = min(live_short, live_long)
    if live_qty != qty:
        # Close what is actually held. Going smaller leaves the operator with
        # less than they asked to close, which is recoverable; going larger
        # reverses into a new position, which is not.
        logger.warning(
            "options close %s: DB says %d contract(s), broker holds %d "
            "(short=%d long=%d) — closing the live size",
            ticker, qty, live_qty, live_short, live_long,
        )
        qty = live_qty
        order.legs[0].quantity = qty
        order.legs[1].quantity = qty

    cancelled = await broker.cancel_open_orders(ticker)
    result = await ibkr_coordinator.submit(
        Priority.P0,
        lambda: broker.place_order(order),
        req_type="PLACE_ORDER", symbol=ticker,
    )
    if result.status in ("cancelled", "rejected"):
        raise RuntimeError(f"broker rejected options close for {ticker}: {result.status}")

    if result.status == "filled" and result.fill_price is not None:
        await trade_recorder.record_exit(
            trade_id=trade_id,
            cost_to_close=float(result.fill_price),
            exit_reason="position_rotation" if closed_by == "position_rotation" else "manual",
        )

    return {
        "trade_id": trade_id,
        "ticker": ticker,
        "asset_type": "options",
        "strategy": strategy,
        "option_type": spread_type,
        "short_strike": float(short_strike),
        "long_strike": float(long_strike),
        "quantity": qty,
        "order_id": result.order_id,
        "status": result.status,
        "cancelled_open_orders": cancelled,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "closed_by": closed_by,
    }


async def build_rotation_candidates(broker: Any) -> list[RotationCandidate]:
    """Read-only. Gather ranking facts for every open rotation-eligible
    position. Closes nothing, submits nothing, and is safe to call from a
    review path — the reason it exists is so building a ROTATION_REVIEW never
    has to go anywhere near rotate_for_blocked_entry()'s closing loop.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        open_trades = (
            await session.execute(select(Trade).where(Trade.status == "open"))
        ).scalars().all()

    rotation_opens = [
        t for t in open_trades
        if (t.spread_type or "").lower() in ("equity_long", "equity_short", "put", "call")
    ]
    if not rotation_opens:
        return []

    from app.services.alpha_edge_engine import compute_equity_hold_score, compute_options_hold_score
    from app.services.rotation_correlation_cache import in_flagged_cluster

    cluster_membership = {t.underlying: in_flagged_cluster(t.underlying) for t in rotation_opens}
    # One broker call covering every candidate of either asset class.
    pnl_by_position = await _unrealized_by_position(broker)

    candidates: list[RotationCandidate] = []
    for t in rotation_opens:
        st = (t.spread_type or "").lower()
        conf = float(t.signal_score) if t.signal_score is not None else None
        is_equity = st in ("equity_long", "equity_short")
        # Absent → None ("unknown"), never 0.0. Winner Protection excludes
        # unknowns outright rather than treating them as break-even.
        upnl = pnl_by_position.get(
            ((t.underlying or "").upper(), "equity" if is_equity else "options"))
        if is_equity:
            direction = "BUY" if st == "equity_long" else "SELL"
            try:
                quality = await compute_equity_hold_score(t.underlying, direction)
            except Exception as exc:
                logger.warning("rotation quality score failed for %s: %s", t.underlying, exc)
                quality = None
        else:
            try:
                quality = compute_options_hold_score(t)
            except Exception as exc:
                logger.warning("rotation options quality score failed for %s: %s", t.underlying, exc)
                quality = None
        candidates.append(
            RotationCandidate(
                trade_id=str(t.id),
                underlying=t.underlying,
                unrealized_pnl=upnl,
                confidence=conf,
                entry_date=t.entry_date,
                spread_type=t.spread_type or "",
                quality_score=quality,
                in_flagged_cluster=cluster_membership.get(t.underlying),
            )
        )
    return candidates


async def propose_rotation_incumbent(
    *, incoming_ticker: str, broker: Any
) -> Optional[RotationCandidate]:
    """The single worst-ranked eligible position, or None. Read-only.

    Deliberately one, not position_rotation_closes. Freeing one slot needs one
    close; the old auto path closed two to free one, which over-rotated every
    time it fired. The approval path fixes that by construction — an operator
    approves one replacement, so exactly one incumbent is proposed.
    """
    candidates = await build_rotation_candidates(broker)
    targets = select_rotation_targets(
        candidates, incoming_ticker=incoming_ticker, count=1,
    )
    return targets[0] if targets else None


async def rotate_for_blocked_entry(
    *,
    incoming_ticker: str,
    broker: Any,
    log_execution=None,
) -> list[dict]:
    """
    Close configured number of open positions to free a slot for a new
    entry (equity or options) blocked by max_positions. Asset-class-
    agnostic on both ends: the incoming entry may be equity or options,
    and the candidate pool being closed may be equity OR options —
    whichever open position genuinely ranks worst.

    Returns list of close receipts (may be empty if rotation not possible).
    """
    if not getattr(settings, "position_rotation_on_max", False):
        return []

    n = max(int(getattr(settings, "position_rotation_closes", 2) or 2), 1)
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        open_trades = (
            await session.execute(select(Trade).where(Trade.status == "open"))
        ).scalars().all()

    rotation_opens = [
        t for t in open_trades
        if (t.spread_type or "").lower() in ("equity_long", "equity_short", "put", "call")
    ]
    if len(rotation_opens) < n:
        logger.info(
            "rotation skipped — need %d open positions, have %d",
            n, len(rotation_opens),
        )
        return []

    from app.services.alpha_edge_engine import compute_equity_hold_score, compute_options_hold_score
    from app.services.rotation_correlation_cache import in_flagged_cluster

    # Cache-only, synchronous, no I/O — safe to resolve once for every
    # candidate up front rather than inside the per-ticker await loop below.
    # Options tickers are never covered by this cache (equity-only) and
    # naturally resolve to None — a full no-op tiebreaker, not a bias.
    cluster_membership = {t.underlying: in_flagged_cluster(t.underlying) for t in rotation_opens}
    # One broker call up front for every options candidate, not one per candidate.
    # One broker call covering every candidate of either asset class.
    pnl_by_position = await _unrealized_by_position(broker)

    candidates: list[RotationCandidate] = []
    for t in rotation_opens:
        st = (t.spread_type or "").lower()
        conf = float(t.signal_score) if t.signal_score is not None else None
        is_equity = st in ("equity_long", "equity_short")
        # Absent → None ("unknown"), never 0.0. Winner Protection excludes
        # unknowns outright rather than treating them as break-even.
        upnl = pnl_by_position.get(
            ((t.underlying or "").upper(), "equity" if is_equity else "options"))
        if is_equity:
            direction = "BUY" if st == "equity_long" else "SELL"
            try:
                quality = await compute_equity_hold_score(t.underlying, direction)
            except Exception as exc:
                logger.warning("rotation quality score failed for %s: %s", t.underlying, exc)
                quality = None
        else:
            try:
                quality = compute_options_hold_score(t)
            except Exception as exc:
                logger.warning("rotation options quality score failed for %s: %s", t.underlying, exc)
                quality = None
        candidates.append(
            RotationCandidate(
                trade_id=str(t.id),
                underlying=t.underlying,
                unrealized_pnl=upnl,
                confidence=conf,
                entry_date=t.entry_date,
                spread_type=t.spread_type or "",
                quality_score=quality,
                in_flagged_cluster=cluster_membership.get(t.underlying),
            )
        )

    targets = select_rotation_targets(
        candidates, incoming_ticker=incoming_ticker, count=n,
    )
    if len(targets) < n:
        logger.info(
            "rotation skipped — fewer than %d eligible targets (incoming=%s)",
            n, incoming_ticker,
        )
        return []

    by_id = {str(t.id): t for t in rotation_opens}
    receipts: list[dict] = []
    for target in targets:
        trade = by_id.get(target.trade_id)
        if trade is None:
            continue
        try:
            close_fn = (
                close_options_trade
                if (trade.spread_type or "").lower() in ("put", "call")
                else close_equity_trade
            )
            receipt = await close_fn(
                trade, broker=broker, closed_by="position_rotation",
            )
            # Decision-time ranking context — not on close_equity_trade()'s own
            # return shape (its other caller, close_position()'s manual close,
            # has no RotationCandidate) so it's added here, at the rotation call
            # site only. Distinct key from realized fill-based P&L, which lands
            # separately on Trade.pnl once trade_recorder.record_exit() runs.
            receipt = {
                **receipt,
                "quality_score": target.quality_score,
                "in_flagged_cluster": target.in_flagged_cluster,
                "confidence": target.confidence,
                "unrealized_pnl_at_decision": target.unrealized_pnl,
            }
            receipts.append(receipt)
            if log_execution is not None:
                await log_execution(receipt)
            logger.info(
                # %s not %.2f: this line runs after the close has already gone
                # through, and a format error here would surface as
                # "rotation close failed" for a position that really closed.
                "rotation closed %s trade_id=%s unrealized≈%s quality=%s cluster=%s confidence=%s",
                target.underlying, target.trade_id,
                target.unrealized_pnl, target.quality_score,
                target.in_flagged_cluster, target.confidence,
            )
        except Exception as exc:
            logger.error(
                "rotation close failed for %s (%s): %s",
                target.underlying, target.trade_id, exc,
            )
            # Partial closes still help free slots; continue
            continue

    return receipts
