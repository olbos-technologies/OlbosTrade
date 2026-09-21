"""
Options signal history — persists every qualifying options spread signal.

record_options_signal() is called at signal-generation time (inside the
options scanner, main.py::_run_options_scan) right after the signal is
appended to the in-memory _recent_options_signals store, so it survives a
backend restart the way the in-memory list never does. Mirrors
signal_outcome_tracker.record_signal()'s equity precedent, but options
spreads have no entry/stop/target shape to track a forward outcome
against — this is a signal log only, no resolution logic.

One signal per (ticker, strategy, day)
--------------------------------------
The options scan ticks every 30 minutes across the watchlist, so a spread that
keeps qualifying was re-recorded on every pass — the same structural bug
`record_signal()` had on the equity side (measured there at ~45x; this table's
slower cadence makes it ~13x per trading day). The first row of the day is kept
and its id returned for later re-emissions.

Strikes and expiration are deliberately excluded from the identity. They drift
with spot between ticks, so including them would make nearly every re-emission
look like a new signal and dedupe almost nothing — the re-emission is the same
setup re-priced, not a new decision.

As on the equity side there is no UNIQUE constraint yet: historical duplicates
still exist, and adding one would make `alembic upgrade head` — and therefore
deploy/hetzner/update.sh — fail. Run scripts/dedupe_options_signal_history.py
first.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _dec_or_none(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(round(float(value), 4)))
    except (TypeError, ValueError):
        return None


async def record_options_signal(signal: dict) -> Optional[str]:
    """
    Persist a qualifying options spread signal (BUY_SPREAD | SELL_SPREAD)
    for the History view.

    No-ops (returns None) for rejection entries (action other than
    BUY_SPREAD/SELL_SPREAD, or no spread data) — those come from
    _record_options_rejection(), a separate code path with just a reason
    string, nothing to persist. Never raises: a tracking failure must not
    break the scan that produced the signal.
    """
    action = signal.get("action")
    if action not in ("BUY_SPREAD", "SELL_SPREAD"):
        return None

    spread = signal.get("spread") or {}
    if not spread:
        return None

    try:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.options_signal_history import OptionsSignalHistory

        generated_at_raw = signal.get("generated_at")
        try:
            generated_at = (
                datetime.fromisoformat(generated_at_raw)
                if generated_at_raw else datetime.now(timezone.utc)
            )
        except ValueError:
            generated_at = datetime.now(timezone.utc)

        # Normalize to UTC before deriving the day boundary — an offset-less
        # `generated_at` parses naive, and treating that as local time would
        # put the dedup window on the wrong day.
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        else:
            generated_at = generated_at.astimezone(timezone.utc)

        expiration_raw = spread.get("expiration")
        expiration = date.fromisoformat(expiration_raw) if expiration_raw else generated_at.date()

        ticker = signal.get("ticker", "")
        strategy = signal.get("strategy", "")
        row_id = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            # One row per (ticker, strategy, action, UTC day) — see the module
            # docstring. Strikes and expiration are deliberately NOT part of the
            # identity: they drift with spot between ticks, so including them
            # would make almost every re-emission look like a new signal and
            # dedupe nothing.
            day_start = generated_at.replace(hour=0, minute=0, second=0, microsecond=0)
            existing_id = (await session.execute(
                select(OptionsSignalHistory.id)
                .where(
                    OptionsSignalHistory.ticker == ticker,
                    OptionsSignalHistory.strategy == strategy,
                    OptionsSignalHistory.action == action,
                    OptionsSignalHistory.generated_at >= day_start,
                    OptionsSignalHistory.generated_at < day_start + timedelta(days=1),
                )
                .limit(1)
            )).scalar_one_or_none()
            if existing_id is not None:
                logger.debug(
                    "record_options_signal: %s %s %s already recorded for %s — keeping the first",
                    ticker, strategy, action, day_start.date(),
                )
                return str(existing_id)

            # NO `async with session.begin()` HERE — the dedup SELECT above has
            # already autobegun this session's transaction, and a second begin
            # raises InvalidRequestError. Identical to the bug fixed in
            # signal_outcome_tracker.record_signal in this commit, and it cost
            # the same thing: every options signal failed to record, silently,
            # because the failure is swallowed into a logger.warning.
            #
            # Found by scanning for the pattern rather than by noticing it —
            # nothing in the options history logs stood out, and its tests
            # passed throughout for the same reason the tracker's did.
            session.add(OptionsSignalHistory(
                id=row_id,
                signal_id=signal.get("id"),
                ticker=ticker,
                strategy=strategy,
                action=action,
                confidence=Decimal(str(round(signal.get("confidence", 0.0), 4))),
                pop=_dec_or_none(signal.get("pop")),
                kelly_fraction=_dec_or_none(signal.get("kelly_fraction")),
                signal_score=Decimal(str(round(signal.get("signal_score", 0.0), 4))),
                quantity=int(signal.get("quantity", 0)),
                iv_rank=Decimal(str(round(signal.get("iv_rank", 0.0), 2))),
                regime=signal.get("regime", "unknown"),
                option_type=spread.get("option_type", ""),
                short_strike=Decimal(str(spread.get("short_strike", 0))),
                long_strike=Decimal(str(spread.get("long_strike", 0))),
                expiration=expiration,
                dte=int(spread.get("dte", 0)),
                net_credit=Decimal(str(spread.get("net_credit", 0))),
                max_loss=Decimal(str(spread.get("max_loss", 0))),
                breakeven=Decimal(str(spread.get("breakeven", 0))),
                sigma=Decimal(str(round(signal.get("sigma", 0.0), 4))),
                vix_used=Decimal(str(round(signal.get("vix_used", 0.0), 2))),
                credit_source=signal.get("credit_source", "unknown"),
                evidence=signal.get("evidence"),
                intelligence=signal.get("intelligence"),
                generated_at=generated_at,
            ))
            await session.commit()
        return str(row_id)
    except Exception as exc:
        logger.warning("record_options_signal failed for %s: %s", signal.get("ticker"), exc)
        return None
