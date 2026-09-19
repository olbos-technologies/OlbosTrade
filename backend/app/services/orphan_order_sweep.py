"""
Cancel resting orders that no longer protect anything.

An orphan is a resting order on a symbol the broker holds no position in. It is
NOT inert. Both legs of a short's bracket are BUYs, so with no position to
close, a triggered stop does not exit — it OPENS a new position, in the
opposite direction, unsignalled, at whatever size the dead position used.

Found in production twice.

  2026-08-28: 20 orphaned orders across ASML/EXC/INTU/MU, every one traced to
  an exit_reason of position_closed_at_broker. EXC's was a BUY STP for 906
  shares. main.py's reconciliation loop gained a canceller for exactly that
  path.

  2026-09-19: EXC, INTU and MU again — 18 orders, the same tickers, ~$176k of
  potential unintended exposure on a book carrying $100k gross. Three weeks
  after the canceller landed.

The second time is the point of this module. That canceller only sweeps symbols
it closed DURING THAT PASS: it iterates `closed_symbols`, a set the same
function just built. It prevents new orphans arriving by one path. It cannot
see an orphan that already exists, whatever created it — a manual close in TWS,
a close booked while the process was down, a path that predates the fix, or the
fix's own `except` swallowing a failed cancel ("the orphan survives to the next
pass, which is the status quo, not worse" — except no later pass ever looks at
it again).

Detection already existed too: rotation_preflight._no_orphans() computes this
exactly. But it only runs when someone calls the rotation route, and it reports
rather than repairs.

So the gap was never detection or repair. It was that both were triggered by an
event instead of by state. This module is the state-based sweep: read the book,
compare against held positions, cancel what matches nothing.

WHAT IT WILL NOT DO
-------------------
Cancelling a resting stop is the one action here that can increase risk rather
than reduce it, so every rule fails toward leaving the order alone:

  * A symbol the broker still holds is never touched, at any size or side.
  * A symbol with an open or pending Trade row is never touched, even when the
    broker reports no position — that disagreement is reconciliation's to
    resolve, and acting on it here would strip a bracket the DB still expects.
  * A cached order book is never acted on. `source != "refreshed"` aborts the
    whole sweep: an empty or partial list read from cache looks exactly like
    "this symbol has no orders", and the difference decides whether a cancel is
    safe.
  * A failure to read positions aborts the sweep. An empty held-set would make
    every order in the book an orphan.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func as sa_func, select

from app.core.database import AsyncSessionLocal
from app.models.trade import Trade
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: Cancel at most this many orders in one pass. A sweep that suddenly wants to
#: cancel dozens is more likely to be looking at a broken read than at a
#: genuinely filthy book — the 2026-08-28 incident was 20 orders and the
#: 2026-09-19 one was 18, so a limit above those is generous for the real cases
#: while still refusing to act on a book that reports everything as an orphan.
MAX_CANCELS_PER_PASS = 40


async def _held_symbols(broker: Any) -> set[str]:
    """Every upper-cased name under which the broker holds a position.

    BOTH `symbol` and `underlying`, and the difference is not cosmetic.
    IBKRClient builds an OPTION position with `symbol=c.localSymbol` (e.g.
    "AAPL  260116C00150000") and `underlying=c.symbol` ("AAPL"), while
    get_open_orders() reports that same contract's order as `symbol=c.symbol`
    ("AAPL"). Reading only `p.symbol` therefore means a live option position
    never puts "AAPL" into this set — so its resting bracket looks orphaned and
    gets cancelled, stripping a real position of its protection.

    Equities are unaffected (both fields are c.symbol), which is exactly why
    this would have survived testing on the equity book it was written for.
    Caught by Copilot on #69.

    Collecting both over-matches rather than under-matches: at worst an order
    is spared that could have been swept, which is the direction this module
    fails in by design.
    """
    held: set[str] = set()
    for p in await broker.get_positions():
        for attr in ("symbol", "underlying"):
            sym = (getattr(p, attr, "") or "").strip().upper()
            if sym:
                held.add(sym)
    return held


async def _symbols_with_live_trade_rows(symbols: set[str]) -> set[str]:
    """Of `symbols`, those with an open or pending Trade row.

    `sa_func.upper`, not a bare ==, and the same reasoning as main.py's
    canceller: a case mismatch here returns no rows, which would let the sweep
    cancel orders the DB still considers live. This comparison's failure mode
    has to be "skip the cancel", never "cancel anyway".
    """
    if not symbols:
        return set()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Trade.underlying).where(
                sa_func.upper(Trade.underlying).in_({s.upper() for s in symbols}),
                Trade.status.in_(["open", "pending"]),
            )
        )).scalars().all()
    return {(r or "").upper() for r in rows if r}


async def sweep_orphaned_orders(broker: Any, dry_run: bool = False) -> dict:
    """Cancel resting orders whose symbol has no position and no live row.

    Returns a report rather than raising. The caller runs on the scheduler
    behind reconciliation, where an exception would take the tick with it.
    """
    if not hasattr(broker, "get_open_orders") or not hasattr(broker, "cancel_orders_by_id"):
        return {"status": "skipped", "reason": f"{type(broker).__name__} cannot cancel by id"}

    try:
        book = await broker.get_open_orders(refresh=True)
    except Exception as exc:
        return {"status": "skipped", "reason": f"could not read the order book: {exc}"}

    # A cache fall-back cannot prove an order is unprotected. Abort the whole
    # sweep rather than acting on the part that happens to be present.
    source = book.get("source")
    if source != "refreshed":
        return {"status": "skipped",
                "reason": f"order book came from '{source}' — not a live read"}

    # `is not None`, not a truthiness test: order_id 0 is falsy and would be
    # silently dropped from the candidate list. IBKR ids start at 1 so this is
    # latent rather than live, but a safety sweep must not discard an order it
    # cannot see a reason to keep — the direction of that error matters.
    orders = [o for o in (book.get("orders") or []) if o.get("order_id") is not None]
    if not orders:
        return {"status": "ok", "nothing_to_do": True, "reason": "no resting orders"}

    try:
        held = await _held_symbols(broker)
    except Exception as exc:
        # Without positions every order looks orphaned. Never guess this one.
        return {"status": "skipped", "reason": f"could not read positions: {exc}"}

    # An order with no symbol cannot be proven unprotected: "" is never in
    # `held`, so a truthiness-free comparison would classify it as an orphan
    # and cancel it on the strength of a field that is missing. That is failing
    # toward the cancel, which is the one direction this module must never
    # fail in. Caught by Sourcery on #69.
    #
    # Surfaced rather than silently dropped — an order book returning entries
    # without a symbol means something is wrong upstream, and a sweep that
    # quietly ignores them hides it.
    unidentifiable = [o for o in orders if not (o.get("symbol") or "").strip()]
    if unidentifiable:
        logger.warning(
            "orphan sweep ignoring %d order(s) with no symbol — cannot prove "
            "they are unprotected: ids %s",
            len(unidentifiable),
            [o.get("order_id") for o in unidentifiable],
        )

    identifiable = [o for o in orders if (o.get("symbol") or "").strip()]
    candidates = [o for o in identifiable
                  if (o.get("symbol") or "").upper() not in held]
    if not candidates:
        return {"status": "ok", "nothing_to_do": True,
                "reason": f"all {len(identifiable)} identifiable order(s) map "
                          f"to a held position",
                "ignored_no_symbol": len(unidentifiable)}

    candidate_symbols = {(o.get("symbol") or "").upper() for o in candidates}
    try:
        still_live_in_db = await _symbols_with_live_trade_rows(candidate_symbols)
    except Exception as exc:
        return {"status": "skipped", "reason": f"could not read trade rows: {exc}"}

    orphans = [
        o for o in candidates
        if (o.get("symbol") or "").upper() not in still_live_in_db
    ]
    deferred = sorted(candidate_symbols & still_live_in_db)
    if deferred:
        logger.info(
            "orphan sweep deferring %s — broker holds no position but an "
            "open/pending trade row remains; reconciliation owns that disagreement",
            ", ".join(deferred),
        )

    if not orphans:
        return {"status": "ok", "nothing_to_do": True,
                "reason": "every candidate still has an open/pending trade row",
                "deferred_symbols": deferred}

    by_symbol: dict[str, int] = {}
    for o in orphans:
        sym = (o.get("symbol") or "").upper()
        by_symbol[sym] = by_symbol.get(sym, 0) + 1

    if len(orphans) > MAX_CANCELS_PER_PASS:
        # More likely a broken read than a filthy book. Report, cancel nothing.
        return {"status": "skipped",
                "reason": (f"{len(orphans)} orphans exceeds the {MAX_CANCELS_PER_PASS} "
                           f"per-pass limit — refusing to act on a book this far "
                           f"from expectations"),
                "by_symbol": by_symbol}

    if dry_run:
        return {"status": "ok", "dry_run": True,
                "would_cancel": [int(o["order_id"]) for o in orphans],
                "by_symbol": by_symbol, "deferred_symbols": deferred,
                "ignored_no_symbol": len(unidentifiable)}

    # RE-READ POSITIONS IMMEDIATELY BEFORE CANCELLING.
    #
    # `held` above is a snapshot taken before the DB query. A fill landing in
    # that window opens a position whose bracket is, by then, already on the
    # orphan list — and cancelling it strips a live position of its stop, the
    # worst outcome this module can produce. Caught by Sourcery on #69.
    #
    # This narrows the window from "a DB round-trip plus processing" to "one
    # broker call", it does not close it: without broker-side atomicity a fill
    # can still land between this read and the cancel. That residual is stated
    # rather than papered over — the honest claim is a much smaller window, not
    # a safe one. A failed re-read aborts, because an unverifiable position
    # list is exactly when not to act.
    try:
        held_now = await _held_symbols(broker)
    except Exception as exc:
        return {"status": "skipped",
                "reason": f"could not re-read positions before cancelling: {exc}",
                "by_symbol": by_symbol}

    raced = sorted({(o.get("symbol") or "").upper() for o in orphans
                    if (o.get("symbol") or "").upper() in held_now})
    if raced:
        logger.warning(
            "orphan sweep dropping %s — a position appeared between the two "
            "position reads; its bracket is live protection, not an orphan",
            ", ".join(raced),
        )
        orphans = [o for o in orphans
                   if (o.get("symbol") or "").upper() not in held_now]
        by_symbol = {s: c for s, c in by_symbol.items() if s not in set(raced)}

    if not orphans:
        return {"status": "ok", "nothing_to_do": True,
                "reason": "every candidate gained a position before the cancel",
                "raced_symbols": raced}

    order_ids = [int(o["order_id"]) for o in orphans]

    try:
        results = await broker.cancel_orders_by_id(order_ids)
    except Exception as exc:
        return {"status": "error", "reason": f"cancel failed: {exc}",
                "by_symbol": by_symbol}

    # WHAT THE BROKER SAID PER ORDER. cancel_orders_by_id returns "not_found"
    # when the order is no longer in openTrades() — which happens when someone
    # else cancelled it AND when it FILLED. A fill is the unintended position
    # this module exists to prevent, so counting not_found as a confirmed
    # cancel would report the exact failure as a success. Caught by Copilot
    # on #69.
    by_result: dict[int, str] = {}
    for r in (results or []):
        oid = r.get("order_id")
        if oid is not None:
            by_result[int(oid)] = (r.get("result") or "").lower()

    not_found = sorted(oid for oid, res in by_result.items() if res == "not_found")
    errored = sorted(oid for oid, res in by_result.items() if res == "error")

    # Verify against a fresh read rather than trusting the send — the
    # 2026-08-29 attempt in TWS reported nothing and changed nothing.
    #
    # The verification read's `source` is checked exactly as the first read's
    # is. get_open_orders(refresh=True) falls back to
    # "cache_after_refresh_failed", and an empty or partial cache would show
    # every order as gone — marking them all confirmed and suppressing the
    # error while they are still resting.
    verified = False
    still_open: list[int] = []
    try:
        after = await broker.get_open_orders(refresh=True)
        if after.get("source") == "refreshed":
            verified = True
            present = {int(o["order_id"]) for o in (after.get("orders") or [])
                       if o.get("order_id") is not None}
            still_open = sorted(oid for oid in order_ids if oid in present)
    except Exception:
        pass

    confirmed = sorted(
        oid for oid in order_ids
        if verified and by_result.get(oid) == "cancel_sent" and oid not in still_open
    )

    # A not_found order may have filled. If its symbol now has a position, that
    # is the unintended position opening — the outcome this sweep exists to
    # prevent, and it must be loud rather than counted as a win.
    possible_fills: list[str] = []
    if not_found:
        sym_of = {int(o["order_id"]): (o.get("symbol") or "").upper() for o in orphans}
        try:
            held_after = await _held_symbols(broker)
        except Exception:
            held_after = set()
        possible_fills = sorted({
            sym_of[oid] for oid in not_found
            if sym_of.get(oid) and sym_of[oid] in held_after
        })

    return {
        "status": "ok",
        "requested": len(order_ids),
        "confirmed_cancelled": confirmed,
        "not_found": not_found,
        "errored": errored,
        "still_open": still_open,
        "verified": verified,
        "possible_fills": possible_fills,
        "by_symbol": by_symbol,
        "deferred_symbols": deferred,
        "raced_symbols": raced,
        "ignored_no_symbol": len(unidentifiable),
        "results": results,
    }
