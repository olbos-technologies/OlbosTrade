"""Record what 0DTE debit spreads on SPY/QQQ looked like, and what they were worth 5 and 10
minutes later. READ-ONLY MARKET DATA: this module never sends, queues or approves an order.

WHY IT EXISTS
-------------
Whether a short-horizon 0DTE idea works after real costs cannot be answered from free
historical data (there are no historical 0DTE option quotes), and a model-priced backtest is
exactly where 0DTE modelling is weakest. So we collect our own: at fixed times of day, ask the
0DTE proposal logic (services/zero_dte.py) what it WOULD have proposed in each direction, store
the real quotes, then re-quote the same two legs after 5 and 10 minutes. The mark is the natural
closing price (long bid less short ask), so every observation carries its real round-trip cost.

HONESTY RULES
-------------
* A refusal is recorded (it says how often the market was too wide or stale to trade).
* A mark whose quotes cannot be read is recorded as unreadable, never filled in. A mark whose
  chain could not be fetched at all is NOT recorded and is retried on the next tick until its
  grace period ends; after that it is simply missing, which the report counts.
* Marks only ever append. An observation is never rewritten.
* Idempotent under restarts: the unique key (date, slot, symbol, direction) stops a second row.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.services.zero_dte import ALLOWED_SYMBOLS, propose_debit_spread, zero_dte_expiry
from app.utils.logger import get_logger
from app.utils.market_hours import is_market_open, now_et

logger = get_logger(__name__)

#: ET times at which an observation is taken. All inside the desk's entry window.
SLOTS: tuple[str, ...] = ("10:00", "10:30", "11:00", "11:30", "12:00", "13:00", "14:00")
#: A slot may be taken up to this many minutes late (the scheduler tick drifts, a restart happens).
SLOT_GRACE_MIN = 6
#: Minutes after the observation at which each mark is taken, and how late a mark may be.
MARK_MINUTES: tuple[int, ...] = (5, 10)
MARK_GRACE_S = 240
#: Strikes either side of the money requested at observation; extra strikes for a mark's legs.
OBSERVE_WINDOW = 8
MARK_EXTRA_STRIKES = 2
#: Sizing is irrelevant to a record; this only keeps the budget check from refusing a quote.
NOMINAL_BUDGET = 10_000.0

DIRECTIONS = ("bullish", "bearish")

FetchChain = Callable[..., Awaitable[Any]]


def due_slot(now: Optional[datetime] = None) -> Optional[str]:
    """The slot an observation is due for right now (ET), else None."""
    et = now_et(now)
    for slot in SLOTS:
        hh, mm = (int(x) for x in slot.split(":"))
        start = et.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if 0 <= (et - start).total_seconds() < SLOT_GRACE_MIN * 60:
            return slot
    return None


def _f(x: Any) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _leg(contracts: Any, strike: float) -> Optional[Any]:
    for c in contracts or []:
        if abs(_f(getattr(c, "strike", 0)) - strike) < 1e-6:
            return c
    return None


def build_mark(chain: Any, *, option_type: str, long_strike: float, short_strike: float,
               minutes: int, observed_at: datetime, now: datetime) -> dict:
    """The mark for one observation from a freshly fetched chain. Unreadable quotes are flagged."""
    side = chain.calls if option_type == "call" else chain.puts
    lg, sh = _leg(side, long_strike), _leg(side, short_strike)
    mark: dict = {
        "minutes": minutes, "at": now.isoformat(),
        "elapsed_s": round((now - observed_at).total_seconds(), 1),
        "spot": _f(getattr(chain, "underlying_price", 0)) or None,
        "quote_ok": False,
        "long_bid": None, "long_ask": None, "short_bid": None, "short_ask": None,
        "natural_credit": None, "mid_credit": None,
    }
    if lg is None or sh is None:
        mark["problem"] = "leg missing from chain"
        return mark
    lb, la, sb, sa = _f(lg.bid), _f(lg.ask), _f(sh.bid), _f(sh.ask)
    mark.update(long_bid=lb, long_ask=la, short_bid=sb, short_ask=sa)
    # A closing quote only needs asks to exist: a leg that has collapsed to a zero bid is a real
    # (and important) outcome, not an unreadable quote.
    if la <= 0 or sa <= 0 or la < lb or sa < sb or lb < 0 or sb < 0:
        mark["problem"] = "quote not usable"
        return mark
    mark["quote_ok"] = True
    mark["natural_credit"] = round(lb - sa, 4)
    mark["mid_credit"] = round((lb + la) / 2.0 - (sb + sa) / 2.0, 4)
    return mark


def _observation_row(model: Any, *, trade_date, slot, symbol, direction, now, chain, err):
    spot = _f(getattr(chain, "underlying_price", 0)) or None if chain is not None else None
    base = dict(trade_date=trade_date, slot=slot, symbol=symbol, direction=direction,
                observed_at=now, spot=spot, marks=[])
    if chain is None:
        return model(**base, proposed=False, refusal_reasons=[f"chain unavailable: {err}"])
    res = propose_debit_spread(chain, symbol=symbol, direction=direction,
                               risk_budget=NOMINAL_BUDGET, now=now)
    if not res.ok:
        return model(**base, proposed=False, refusal_reasons=list(res.reasons))
    p = res.proposal
    return model(
        **base, proposed=True, option_type=p.option_type,
        long_strike=p.long_strike, short_strike=p.short_strike, width=p.width,
        long_bid=p.long_bid, long_ask=p.long_ask, short_bid=p.short_bid, short_ask=p.short_ask,
        mid_debit=p.limit_debit, natural_debit=p.natural_debit,
        context={"quoted_at": p.quoted_at.isoformat(), "warnings": list(p.warnings)},
    )


async def _observe(fetch_chain: FetchChain, factory: Any, slot: str, now: datetime) -> int:
    from app.models.zero_dte_observation import ZeroDteObservation as Obs

    trade_date = now_et(now).date()
    expiry = zero_dte_expiry(now).isoformat()
    written = 0
    for symbol in ALLOWED_SYMBOLS:
        async with factory() as session:
            have = (await session.execute(select(Obs.direction).where(
                Obs.trade_date == trade_date, Obs.slot == slot, Obs.symbol == symbol))).scalars().all()
            if set(have) >= set(DIRECTIONS):
                continue
        chain, err = None, ""
        try:
            chain = await fetch_chain(symbol, expiry, window=OBSERVE_WINDOW,
                                      option_types=("call", "put"))
        except Exception as exc:                       # recorded as a refusal, never skipped silently
            err = f"{type(exc).__name__}: {str(exc)[:150]}"
            logger.warning("0DTE recorder: chain fetch failed for %s %s: %s", symbol, slot, err)
        async with factory() as session:
            for direction in DIRECTIONS:
                if direction in have:
                    continue
                session.add(_observation_row(Obs, trade_date=trade_date, slot=slot, symbol=symbol,
                                             direction=direction, now=now, chain=chain, err=err))
            try:
                await session.commit()
                written += len(DIRECTIONS) - len(have)
            except IntegrityError:
                await session.rollback()               # another writer got there first: fine
    return written


def _due_minutes(row: Any, now: datetime) -> list[int]:
    done = {m.get("minutes") for m in (row.marks or [])}
    elapsed = (now - row.observed_at).total_seconds()
    return [m for m in MARK_MINUTES
            if m not in done and m * 60 <= elapsed < m * 60 + MARK_GRACE_S]


async def _mark_due(fetch_chain: FetchChain, factory: Any, now: datetime) -> int:
    from app.models.zero_dte_observation import ZeroDteObservation as Obs

    trade_date = now_et(now).date()
    expiry = zero_dte_expiry(now).isoformat()
    async with factory() as session:
        rows = (await session.execute(select(Obs).where(
            Obs.trade_date == trade_date, Obs.proposed.is_(True)))).scalars().all()
        due = [(r, _due_minutes(r, now)) for r in rows]
        due = [(r, ms) for r, ms in due if ms]
        if not due:
            return 0
        # One small fetch per (symbol, side, long strike): the two legs and a little around them.
        groups: dict[tuple, list] = {}
        for r, ms in due:
            groups.setdefault((r.symbol, r.option_type, r.long_strike), []).append((r, ms))
        recorded = 0
        for (symbol, option_type, long_strike), members in groups.items():
            width = max(r.width or 1.0 for r, _ in members)
            try:
                chain = await fetch_chain(
                    symbol, expiry, window=int(math.ceil(width)) + MARK_EXTRA_STRIKES,
                    center=long_strike, option_types=(option_type,))
            except Exception as exc:
                logger.warning("0DTE recorder: mark fetch failed for %s %s: %s", symbol, long_strike, exc)
                continue                                # retried next tick until the grace ends
            for r, ms in members:
                marks = list(r.marks or [])
                for minutes in ms:
                    marks.append(build_mark(chain, option_type=option_type, long_strike=r.long_strike,
                                            short_strike=r.short_strike, minutes=minutes,
                                            observed_at=r.observed_at, now=now))
                    recorded += 1
                r.marks = marks                         # a new list, so the change is detected
            await session.commit()
        return recorded


async def record_tick(fetch_chain: FetchChain, *, now: Optional[datetime] = None,
                      session_factory: Any = None) -> dict:
    """One scheduler tick: take any observation that is due, then any marks that are due."""
    now = now or datetime.now(timezone.utc)
    if not is_market_open(now):
        return {"status": "market_closed", "observed": 0, "marks": 0}
    if session_factory is None:
        from app.core.database import AsyncSessionLocal as session_factory
    slot = due_slot(now)
    observed = await _observe(fetch_chain, session_factory, slot, now) if slot else 0
    marks = await _mark_due(fetch_chain, session_factory, now)
    return {"status": "ok", "slot": slot, "observed": observed, "marks": marks}
