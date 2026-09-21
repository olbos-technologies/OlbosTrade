"""
Signal outcome tracker — persists every routable equity signal and checks
real forward price action against it.

record_signal() is called at signal-generation time (inside the equity
scanner's per-ticker loop, main.py::_run_equity_scan) so every BUY/SELL
signal that clears the routing threshold is captured — not just the ~15
that ever became actual trades. Trades alone are too small a sample and
selection-biased toward whatever already passed the confidence filter;
tracking every signal's real outcome is what lets hit rate and
days-to-target actually be measured, and eventually gives an ML pass
honest labels instead of backtest replay.

check_pending_outcomes() runs on a schedule (main.py's background loop)
and walks forward daily bars for each still-pending signal to see whether
price reached its target before its stop, or neither within the hold
window.

One signal per (ticker, action, day)
------------------------------------
The scanner re-evaluates the whole watchlist every tick, so without a guard
the *same* setup is recorded again on each pass. Measured in the 2026-08-28
assessment: 70,798 rows representing roughly 1,568 real (ticker, action, day)
signals — about 45x duplication. That is not merely wasted rows:

  - it made the dataset look ~45x larger than it is, which is how a
    ~1,568-signal sample reads as 70k;
  - it blew the resolver's time budget (~65k rows against a 120s scheduler
    limit), so check_pending_outcomes() was killed mid-pass every run and
    left a labelled subset selected by DB iteration order — which, as that
    investigation noted, "reads exactly like real data."

record_signal() therefore keeps the FIRST signal per (ticker, action, day)
and returns that row's id for later re-emissions. First is the right one to
keep: it is the moment the setup actually became actionable, before the day's
price action moved the entry/stop/target under it.

There is deliberately no UNIQUE constraint backing this yet — ~69k historical
duplicates still exist, so adding one would make the next `alembic upgrade
head` (and therefore deploy/hetzner/update.sh) fail. Run
scripts/dedupe_signal_outcomes.py first; the index can follow once history is
clean.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ~1 trading month — roughly matches the horizon a 4xATR target (the
# equity trade plan's target distance) is expected to resolve within.
# How long a signal gets to reach target or stop before it is closed as
# "expired".
#
# DELIBERATELY A CONSTANT, unlike the ATR multipliers next door in config.py,
# and the asymmetry is the whole reason. A multiplier is baked into each row
# at generation: record_signal persists stop_price and target_price, and
# _resolve_one reads those columns, so retuning a multiplier cannot touch a
# signal that already exists. This value is read at RESOLUTION time and
# applied to every pending row, so making it a setting would let a deploy
# retroactively expire 10–19-day-old signals under a policy they were never
# generated under — mixing two labelling rules inside one cohort, and
# rewriting the very sample the retune is meant to measure.
#
# Raised in review on #61 and the right call. Making this tunable needs the
# horizon persisted per outcome (or the pending cohort migrated) so each row
# resolves under its own policy; that is a migration, and it belongs in its
# own change rather than riding along here.
DEFAULT_MAX_HOLD_DAYS = 20

# Rows per UPDATE statement, and how many accumulate before a flush. Chunking
# keeps a single pass from building one enormous statement or holding the whole
# backlog in memory, while staying far away from the per-row transaction cost
# that stalled this job.
_WRITE_CHUNK = 1000
_FLUSH_EVERY = 5000


def _dec_or_none(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(round(float(value), 4)))
    except (TypeError, ValueError):
        return None


async def record_signal(signal: dict) -> Optional[str]:
    """
    Persist a routable equity BUY/SELL signal for forward-outcome tracking.

    No-ops (returns None) for HOLD signals or signals without a usable
    trade_plan — there's nothing to track a target/stop against. Never
    raises: a tracking failure must not break the scan that produced the
    signal.
    """
    action = signal.get("action")
    if action not in ("BUY", "SELL"):
        return None

    trade_plan = signal.get("trade_plan") or {}
    entry = trade_plan.get("entry_price")
    stop = trade_plan.get("stop_price")
    target = trade_plan.get("target_price")
    if not entry or not stop or not target:
        return None

    try:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.signal_outcome import SignalOutcome
        from app.services.equity_signal_engine import EQUITY_SCORING_VERSION

        indicators = signal.get("indicators") or {}

        # The scan loop sets signal["opportunity_score"] before calling here
        # (main.py, immediately after building the signal dict), so this is a
        # read of something already computed — no extra work, no extra I/O.
        #
        # Only the composite and its liquidity/regime components are kept. The
        # other three weights are confidence-determined AT THE SHIPPED 2:1
        # GEOMETRY, and so are the Alpha Edge entry score and risk score — at
        # 2:1 all five reduce to transforms of `confidence`, so storing them
        # would re-express a column already two lines below this one.
        #
        # That reduction is a property of the geometry, not of equity signals,
        # and the ATR multipliers are settings now (see config.py). Off 2:1,
        # EV (p*rr - (1-p)) and reward_risk vary with rr and risk_score's
        # sub-1:1 penalty can fire, so these stop being redundant. Nothing is
        # lost in that case either: rr is recoverable per row as
        # |target_price - entry_price| / |entry_price - stop_price|. Any
        # analysis spanning a retune has to derive it rather than assume 2:1.
        oppty = signal.get("opportunity_score")
        oppty_score = None
        oppty_components: dict = {}
        if isinstance(oppty, dict):
            raw_score = oppty.get("score")
            if isinstance(raw_score, (int, float)):
                oppty_score = int(round(raw_score))
            oppty_components = oppty.get("components") or {}

        generated_at_raw = signal.get("generated_at")
        try:
            generated_at = (
                datetime.fromisoformat(generated_at_raw)
                if generated_at_raw else datetime.now(timezone.utc)
            )
        except ValueError:
            generated_at = datetime.now(timezone.utc)

        # Normalize to UTC before deriving the day boundary — a `generated_at`
        # parsed from an offset-less string comes back naive, and treating that
        # as local time would put the window on the wrong day.
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        else:
            generated_at = generated_at.astimezone(timezone.utc)

        ticker = signal.get("ticker", "")
        row_id = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            # One row per (ticker, action, UTC day) — see the module docstring.
            # Expressed as a half-open range rather than date(generated_at) so
            # it uses idx_signal_outcomes_generated_at and carries no dependence
            # on the session's timezone setting.
            day_start = generated_at.replace(hour=0, minute=0, second=0, microsecond=0)
            existing_id = (await session.execute(
                select(SignalOutcome.id)
                .where(
                    SignalOutcome.ticker == ticker,
                    SignalOutcome.asset_type == "equity",
                    SignalOutcome.action == action,
                    SignalOutcome.generated_at >= day_start,
                    SignalOutcome.generated_at < day_start + timedelta(days=1),
                )
                .limit(1)
            )).scalar_one_or_none()
            if existing_id is not None:
                logger.debug(
                    "record_signal: %s %s already recorded for %s — keeping the first",
                    ticker, action, day_start.date(),
                )
                return str(existing_id)

            # NO `async with session.begin()` HERE, and that absence is the fix.
            #
            # The dedup SELECT above has already begun this session's
            # transaction — SQLAlchemy autobegins on the first operation — so
            # an explicit begin() is the SECOND one and raises
            # InvalidRequestError: "A transaction is already begun on this
            # Session." That is not an edge case: it fired on EVERY routable
            # BUY/SELL signal, so nothing has been written to signal_outcomes
            # since the dedup lookup landed in #45.
            #
            # It stayed invisible because record_signal swallows everything
            # into a logger.warning — correct, since a tracking failure must
            # not break the scan that produced the signal — and because the
            # test fake's begin() was an unconditional MagicMock that never
            # raised. The fake is now faithful; see _mock_session.
            #
            # The SELECT and the INSERT still share one transaction, which is
            # what the dedup check needs: autobegin opened it, commit closes
            # it.
            session.add(SignalOutcome(
                id=row_id,
                signal_id=signal.get("id"),
                ticker=ticker,
                asset_type="equity",
                action=action,
                confidence=Decimal(str(round(signal.get("confidence", 0.0), 4))),
                entry_price=Decimal(str(round(float(entry), 4))),
                stop_price=Decimal(str(round(float(stop), 4))),
                target_price=Decimal(str(round(float(target), 4))),
                target_move_pct=_dec_or_none(trade_plan.get("target_move_pct")),
                generated_at=generated_at,
                status="pending",
                rsi=_dec_or_none(indicators.get("rsi")),
                macd=_dec_or_none(indicators.get("macd")),
                bb_pct_b=_dec_or_none(indicators.get("bb_pct_b")),
                volume_ratio=_dec_or_none(indicators.get("volume_ratio")),
                atr=_dec_or_none(indicators.get("atr")),
                regime=signal.get("regime"),
                signal_engine_version=EQUITY_SCORING_VERSION,
                opportunity_score=oppty_score,
                oppty_liquidity=_dec_or_none(oppty_components.get("liquidity")),
                oppty_regime=_dec_or_none(oppty_components.get("regime")),
            ))
            await session.commit()
        return str(row_id)
    except Exception as exc:
        logger.warning("record_signal failed for %s: %s", signal.get("ticker"), exc)
        return None


async def _fetch_daily_bars(ticker: str, start: datetime):
    """Fetch daily OHLC bars for ticker from `start` through today."""
    import asyncio
    import yfinance as yf

    loop = asyncio.get_running_loop()

    def _fetch():
        period_days = max((datetime.now(timezone.utc) - start).days + 5, 10)
        return yf.Ticker(ticker).history(period=f"{period_days}d", auto_adjust=True)

    return await loop.run_in_executor(None, _fetch)


def _to_utc(ts) -> datetime:
    dt = ts.to_pydatetime()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_one(row, hist, max_hold_days: int):
    """
    Walk bars strictly after the signal's own entry day, checking each
    day's high/low against target and stop.

    When both target and stop are crossed on the same bar, the stop wins —
    a conservative tie-break rather than assuming the best case for a day
    we only have a high/low/close for, not an intraday path.

    Returns (status, exit_price, resolved_at, days_elapsed, mfe_pct, mae_pct,
    mfe_full_pct, mae_full_pct, full_window_days) or None if still unresolved
    within the bars available.

    RESOLUTION IS UNCHANGED by the uncensored excursions. status, exit_price,
    resolved_at and days_elapsed are exactly what they were — they define the
    trade's result under the rules as they stand, and redefining them would
    silently reinterpret every historical row.

    full_window_days is how many bars the uncensored measurement actually
    covered. A signal resolved yesterday has a day or two of history, so its
    "uncensored" excursion is itself censored — by data availability rather
    than by the target. Callers must require a complete window before
    aggregating, or this just moves the censoring somewhere less visible.
    """
    entry_date = row.generated_at.date()
    action = row.action
    entry = float(row.entry_price)
    stop = float(row.stop_price)
    target = float(row.target_price)

    # Censored at resolution — meaning deliberately unchanged, so rows written
    # before and after this change stay comparable on max_favorable_pct.
    mfe_pct = 0.0
    mae_pct = 0.0
    # Measured across the whole window regardless of when resolution happened.
    # This is what _censoring_ceiling_r() exists to work around: the walk used
    # to RETURN at the target touch, so a target_hit row recorded that the
    # signal reached 2.0R and never whether it would have gone on to 3.0R, and
    # every counterfactual above the nearest hit target became unanswerable.
    mfe_full_pct = 0.0
    mae_full_pct = 0.0

    resolution = None
    days_elapsed = 0

    for ts, bar in hist.iterrows():
        bar_date = ts.date() if hasattr(ts, "date") else ts
        if bar_date <= entry_date:
            continue
        days_elapsed += 1
        high, low, close = float(bar["High"]), float(bar["Low"]), float(bar["Close"])

        if action == "BUY":
            fav, adv = (high - entry) / entry, (low - entry) / entry
            stop_hit, target_hit = low <= stop, high >= target
        else:
            fav, adv = (entry - low) / entry, (entry - high) / entry
            stop_hit, target_hit = high >= stop, low <= target

        mfe_full_pct = max(mfe_full_pct, fav)
        mae_full_pct = min(mae_full_pct, adv)

        if resolution is None:
            mfe_pct = max(mfe_pct, fav)
            mae_pct = min(mae_pct, adv)
            # Same order, same stop-wins tie-break as before.
            if stop_hit:
                resolution = ("stop_hit", stop, _to_utc(ts), days_elapsed,
                              mfe_pct, mae_pct)
            elif target_hit:
                resolution = ("target_hit", target, _to_utc(ts), days_elapsed,
                              mfe_pct, mae_pct)
            elif days_elapsed >= max_hold_days:
                resolution = ("expired", close, _to_utc(ts), days_elapsed,
                              mfe_pct, mae_pct)

        # A FIXED window for every row. "To the end of available history"
        # would give an old signal a longer look than a recent one and make
        # the two incomparable.
        if days_elapsed >= max_hold_days:
            break

    if resolution is None:
        return None
    return (*resolution, mfe_full_pct, mae_full_pct, days_elapsed)


def _resolved_payload(row_id, resolution) -> dict:
    """Map a _resolve_one result onto the columns it is written to.

    Extracted so the MAPPING is testable. It is the part that fails silently:
    the censored and uncensored excursions are floats of the same shape, and
    swapping them would launder a truncated number into the column whose
    entire purpose is to be untruncated, with nothing to catch it. Mutation
    testing found exactly that hole while this was inline.
    """
    (status, exit_price, resolved_at, days_elapsed, mfe_pct, mae_pct,
     mfe_full_pct, mae_full_pct, full_window_days) = resolution
    return {
        "id": row_id,
        "status": status,
        "exit_price": Decimal(str(round(exit_price, 4))),
        "resolved_at": resolved_at,
        "days_to_resolve": days_elapsed,
        # CENSORED at resolution. Unchanged meaning.
        "max_favorable_pct": Decimal(str(round(mfe_pct, 4))),
        "max_adverse_pct": Decimal(str(round(mae_pct, 4))),
        # UNCENSORED, over a fixed max_hold_days window. NULL on every row
        # resolved before this shipped; that NULL separates measurable rows
        # from unmeasurable ones and must never be backfilled from the
        # censored value.
        "mfe_full_pct": Decimal(str(round(mfe_full_pct, 4))),
        "mae_full_pct": Decimal(str(round(mae_full_pct, 4))),
        "full_window_days": full_window_days,
    }


async def check_pending_outcomes(
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
    deadline_seconds: Optional[float] = None,
) -> dict:
    """
    Resolve every pending signal against fresh daily bars, or expire it if
    max_hold_days trading bars pass with neither target nor stop hit.

    Returns a summary: {checked, target_hit, stop_hit, expired, still_pending,
    tickers_covered, tickers_total, truncated, oldest_pending_age_days,
    elapsed_s}.

    On coverage, and why this function is shaped the way it is
    ---------------------------------------------------------
    These rows are the training labels for anything that later learns from
    signal outcomes, so *which* signals get resolved has to be a property of
    the signals, never of how far the job happened to get. The previous
    implementation opened a fresh session and transaction per row and wrote
    `checked_at` on every pending row each pass — so a backlog of ~65k rows
    meant ~65k transactions per run against a 120s scheduler budget, and the
    job was killed mid-pass every time (confirmed in production 2026-08-27/28:
    two consecutive `timed out after 120s` errors, and only 32 of 102 tickers
    had ever received a single label — the other 70 had none at all).

    That silent truncation is worse than a slow job: it produced a labelled
    subset selected by DB iteration order and the position of the timeout,
    which reads exactly like real data and is not. Three changes address it:

    * **Batched writes.** Same-value `checked_at` stamps collapse into one
      UPDATE per chunk, and resolutions go out as a single executemany —
      turning ~65k transactions into a handful.
    * **Oldest-first ticker order.** Tickers are processed by their oldest
      pending signal, so a run that cannot finish still drains the longest
      backlog instead of re-walking whichever tickers sort first.
    * **An owned deadline.** The caller passes a budget and the job stops at a
      ticker boundary, flushes, and reports `truncated=True`. Being cancelled
      externally mid-write is what made the shortfall invisible before.
    """
    import time as _time

    from sqlalchemy import select, update
    from app.core.database import AsyncSessionLocal
    from app.models.signal_outcome import SignalOutcome

    started = _time.monotonic()
    summary = {
        "checked": 0, "target_hit": 0, "stop_hit": 0, "expired": 0,
        "still_pending": 0, "tickers_covered": 0, "tickers_total": 0,
        "truncated": False, "oldest_pending_age_days": None, "elapsed_s": 0.0,
    }

    async with AsyncSessionLocal() as session:
        pending = (await session.execute(
            select(SignalOutcome).where(SignalOutcome.status == "pending")
        )).scalars().all()

    if not pending:
        return summary

    by_ticker: dict[str, list] = {}
    for row in pending:
        by_ticker.setdefault(row.ticker, []).append(row)

    now = datetime.now(timezone.utc)
    summary["tickers_total"] = len(by_ticker)
    oldest = min(r.generated_at for r in pending)
    summary["oldest_pending_age_days"] = (now - oldest).days

    # Oldest backlog first — see docstring. Ticker name breaks ties so a run
    # is reproducible rather than depending on dict/query ordering.
    ordered = sorted(by_ticker.items(),
                     key=lambda kv: (min(r.generated_at for r in kv[1]), kv[0]))

    resolved_payloads: list[dict] = []
    checked_ids: list = []

    async def _flush() -> None:
        """Write everything accumulated so far. Safe to call repeatedly."""
        if not resolved_payloads and not checked_ids:
            return
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # One statement per chunk: every row gets the same timestamp,
                # so there is nothing per-row to bind.
                for i in range(0, len(checked_ids), _WRITE_CHUNK):
                    await session.execute(
                        update(SignalOutcome)
                        .where(SignalOutcome.id.in_(checked_ids[i:i + _WRITE_CHUNK]))
                        .values(checked_at=now)
                    )
                # Resolutions differ per row, so this is an executemany keyed
                # on the primary key rather than one statement per row.
                for i in range(0, len(resolved_payloads), _WRITE_CHUNK):
                    await session.execute(
                        update(SignalOutcome), resolved_payloads[i:i + _WRITE_CHUNK]
                    )
        resolved_payloads.clear()
        checked_ids.clear()

    for ticker, rows in ordered:
        if deadline_seconds is not None and _time.monotonic() - started >= deadline_seconds:
            summary["truncated"] = True
            break

        try:
            earliest = min(r.generated_at for r in rows)
            hist = await _fetch_daily_bars(ticker, earliest)
        except Exception as exc:
            logger.warning("check_pending_outcomes: bars fetch failed for %s: %s", ticker, exc)
            continue
        if hist is None or hist.empty:
            continue

        summary["tickers_covered"] += 1

        for row in rows:
            summary["checked"] += 1
            checked_ids.append(row.id)
            resolution = _resolve_one(row, hist, max_hold_days)
            if resolution is None:
                summary["still_pending"] += 1
                continue
            summary[resolution[0]] += 1
            resolved_payloads.append(_resolved_payload(row.id, resolution))

        if len(checked_ids) >= _FLUSH_EVERY:
            await _flush()

    await _flush()
    summary["elapsed_s"] = round(_time.monotonic() - started, 1)

    # Partial coverage must be loud. A truncated pass leaves a biased label
    # set behind, and the whole point of this rewrite is that such a pass can
    # never again look identical to a complete one.
    if summary["truncated"] or summary["tickers_covered"] < summary["tickers_total"]:
        logger.warning(
            "check_pending_outcomes covered %d/%d tickers in %.1fs (truncated=%s) — "
            "labels are INCOMPLETE; oldest pending signal is %s days old",
            summary["tickers_covered"], summary["tickers_total"],
            summary["elapsed_s"], summary["truncated"],
            summary["oldest_pending_age_days"],
        )

    return summary


# ── Stats aggregation (pure — no DB access) ─────────────────────────────────

CONFIDENCE_BUCKETS = [
    ("0.00-0.65", 0.0, 0.65),
    ("0.65-0.70", 0.65, 0.70),
    ("0.70-0.75", 0.70, 0.75),
    ("0.75-0.80", 0.75, 0.80),
    ("0.80-1.00", 0.80, 1.01),
]


def _confidence_buckets(outcomes: list[dict]) -> dict:
    """Factored out so both the top-level result and the by_regime
    breakdown reuse the exact same bucketing, not a second copy of it."""
    by_confidence = {}
    for label, lo, hi in CONFIDENCE_BUCKETS:
        bucket = [o for o in outcomes if lo <= float(o["confidence"]) < hi]
        b_target = [o for o in bucket if o["status"] == "target_hit"]
        b_stop = [o for o in bucket if o["status"] == "stop_hit"]
        b_decisive = len(b_target) + len(b_stop)
        by_confidence[label] = {
            "count": len(bucket),
            "hit_rate": round(len(b_target) / b_decisive, 3) if b_decisive > 0 else None,
        }
    return by_confidence


# ── R-based outcome maths ────────────────────────────────────────────────
#
# R is one unit of risk: the entry-to-stop distance. Everything below is in
# R rather than ATR or percent, so it stays comparable across tickers AND
# across changes to the multipliers themselves — a 4×ATR target over a 2×ATR
# stop is 2.0R, and it is still 2.0R after both multipliers are halved.
#
# Why this exists at all: `hit_rate` cannot answer "is the target in the
# right place". It excludes expiries, and expiry is not neutral — the stop is
# nearer than the target, so slow winners expire while losers resolve, and
# the excluded pile is disproportionately made of signals that would have
# won. These functions measure the signals themselves instead of the
# subset that happened to resolve decisively.

async def enrich_incomplete_excursions(
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS,
    budget_s: float = 60.0,
) -> dict:
    """Fill in the full-window excursions of rows that resolved too early to have one.

    WHY THIS EXISTS. check_pending_outcomes() selects `status == "pending"` and
    writes the resolved row the moment a barrier is touched — correctly, because
    the operator needs the outcome now, not in three weeks. But a signal that
    resolves on day 2 is measured over the two bars that existed at that moment,
    so it lands with full_window_days == 2 and _is_uncensored() rejects it
    forever. It is never selected again.

    Without this pass the uncensoring is decorative: ordinary early winners —
    which is most winners — would never feed _mfe_r or lift the censoring
    ceiling, and the ceiling would sit where it always was. Raised in review on
    PR #65.

    RESOLUTION IS NEVER TOUCHED. Only mfe_full_pct, mae_full_pct and
    full_window_days are written. status, exit_price, resolved_at and
    days_to_resolve keep whatever the original pass decided; re-deciding them
    later against more data would silently rewrite history, and the first
    barrier touch does not change just because more bars arrived.

    This is also where pre-existing rows get their excursions. Migration 0031
    deliberately backfilled nothing — recovering a true excursion needs price
    history re-fetched per ticker, which is a job rather than a migration. This
    is that job, and it runs incrementally instead of as one enormous pass.

    TERMINATION. A row is eligible while more bars might still change the
    answer:

      * full_window_days IS NULL — never measured (pre-0031, or written before
        this pass existed). Tried once.
      * full_window_days < max_hold_days AND the signal is younger than the
        margin — more bars are still arriving.

    Past the margin, whatever window the data supports is the final answer, so
    the row stops being selected even if it never reached max_hold_days. A
    delisted ticker or a gap in history would otherwise be retried forever.

    The margin is calendar days against a trading-day window, so it carries
    slack: 2x covers weekends and holidays comfortably without pulling in rows
    that genuinely still have bars coming.
    """
    import time as _time
    from datetime import timedelta

    from sqlalchemy import or_, select, update
    from app.core.database import AsyncSessionLocal
    from app.models.signal_outcome import SignalOutcome

    started = _time.monotonic()
    summary = {
        "candidates": 0, "enriched": 0, "completed": 0,
        "tickers_covered": 0, "truncated": False, "elapsed_s": 0.0,
    }

    now = datetime.now(timezone.utc)
    margin_cutoff = now - timedelta(days=max_hold_days * 2)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(SignalOutcome).where(
                SignalOutcome.status != "pending",
                or_(
                    SignalOutcome.full_window_days.is_(None),
                    SignalOutcome.full_window_days < max_hold_days,
                ),
            )
        )).scalars().all()

    candidates = [
        r for r in rows
        if r.full_window_days is None or r.generated_at > margin_cutoff
    ]
    summary["candidates"] = len(candidates)
    if not candidates:
        summary["elapsed_s"] = round(_time.monotonic() - started, 1)
        return summary

    by_ticker: dict[str, list] = {}
    for r in candidates:
        by_ticker.setdefault(r.ticker, []).append(r)

    # Oldest first, same rationale as check_pending_outcomes: a run that is cut
    # short should have made progress on the longest-waiting rows.
    ordered = sorted(by_ticker.items(),
                     key=lambda kv: (min(r.generated_at for r in kv[1]), kv[0]))

    payloads: list[dict] = []
    for ticker, trows in ordered:
        if _time.monotonic() - started > budget_s:
            summary["truncated"] = True
            break
        try:
            hist = await _fetch_daily_bars(ticker, min(r.generated_at for r in trows))
        except Exception as exc:
            logger.warning("enrich_incomplete_excursions: bars fetch failed for %s: %s",
                           ticker, exc)
            continue
        if hist is None or hist.empty:
            continue
        summary["tickers_covered"] += 1

        for row in trows:
            res = _resolve_one(row, hist, max_hold_days)
            if res is None:
                continue
            *_resolution, mfe_full_pct, mae_full_pct, window = res
            if row.full_window_days is not None and window <= int(row.full_window_days):
                continue          # no new bars — nothing to say
            payloads.append({
                "id": row.id,
                "mfe_full_pct": Decimal(str(round(mfe_full_pct, 4))),
                "mae_full_pct": Decimal(str(round(mae_full_pct, 4))),
                "full_window_days": window,
            })
            if window >= max_hold_days:
                summary["completed"] += 1

    if payloads:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                for i in range(0, len(payloads), _WRITE_CHUNK):
                    await session.execute(update(SignalOutcome), payloads[i:i + _WRITE_CHUNK])
        summary["enriched"] = len(payloads)

    summary["elapsed_s"] = round(_time.monotonic() - started, 1)
    logger.info("excursion enrichment: %s", summary)
    return summary


def _risk_per_share(o: dict) -> Optional[float]:
    """1R in price terms. None when the row cannot support the maths."""
    entry, stop = o.get("entry_price"), o.get("stop_price")
    if entry is None or stop is None:
        return None
    risk = abs(float(entry) - float(stop))
    return risk if risk > 0 else None


def _realised_r(o: dict) -> Optional[float]:
    """What the signal actually returned, in R, signed for its direction."""
    risk = _risk_per_share(o)
    exit_price, entry = o.get("exit_price"), o.get("entry_price")
    if risk is None or exit_price is None or entry is None:
        return None
    move = float(exit_price) - float(entry)
    if str(o.get("action", "")).upper() == "SELL":
        move = -move
    return move / risk


def _is_uncensored(o: dict, max_hold_days: int = DEFAULT_MAX_HOLD_DAYS) -> bool:
    """True when this row's excursion was measured over a COMPLETE window.

    Two ways a row fails this. It predates the uncensoring fix, so
    mfe_full_pct is NULL and nothing can recover it — the excursion past the
    target was never recorded and the bars are gone. Or it resolved so
    recently that fewer than max_hold_days bars existed when it was measured,
    which is censoring by data availability rather than by the target: the
    same understatement wearing different clothes.
    """
    if o.get("mfe_full_pct") is None:
        return False
    window = o.get("full_window_days")
    return window is not None and int(window) >= max_hold_days


def _mfe_r(o: dict) -> Optional[float]:
    """Max favourable excursion in R — how far it went before it turned.

    Prefers the UNCENSORED excursion when the row has a complete one. For a
    target_hit row the censored value is capped at its own target, so it says
    the signal reached 2.0R and stays silent on whether it would have reached
    3.0R — which is the only thing a target-placement question is asking.

    Falls back to max_favorable_pct for rows written before the fix. Those
    rows understate winners, so an aggregate mixing the two is conservative
    about how far the system runs, never optimistic. _censoring_ceiling_r()
    is what stops the counterfactual over-claiming on that mix.

    Both columns are a fraction OF ENTRY (see _resolve_one), so either has to
    be taken back to price before it can be divided by risk.
    """
    risk = _risk_per_share(o)
    entry = o.get("entry_price")
    mfe = o.get("mfe_full_pct") if _is_uncensored(o) else o.get("max_favorable_pct")
    if risk is None or mfe is None or entry is None:
        return None
    return (float(mfe) * float(entry)) / risk


def _target_distance_r(o: dict) -> Optional[float]:
    """Where the target sits, in R. 4×ATR over a 2×ATR stop is 2.0R."""
    risk = _risk_per_share(o)
    target, entry = o.get("target_price"), o.get("entry_price")
    if risk is None or target is None or entry is None:
        return None
    return abs(float(target) - float(entry)) / risk


MFE_BUCKETS_R = [
    ("0.0-0.5R", 0.0, 0.5),
    ("0.5-1.0R", 0.5, 1.0),
    ("1.0-1.5R", 1.0, 1.5),
    ("1.5-2.0R", 1.5, 2.0),
    ("2.0-3.0R", 2.0, 3.0),
    ("3.0R+",    3.0, float("inf")),
]

# Candidate target distances to report a counterfactual expectancy for.
CANDIDATE_TARGETS_R = [1.0, 1.5, 2.0, 2.5, 3.0]


def _censoring_ceiling_r(outcomes: list[dict]) -> float:
    """Above this distance, MFE cannot answer the counterfactual question.

    _resolve_one RETURNS the moment the target is touched, so a target_hit
    row's max_favorable_pct is censored at its own target — it records that
    the signal reached 2.0R, never whether it would have gone on to 3.0R.
    Asking "what if the target were 2.5R" of such a row is unanswerable from
    stored data: it falls through to the realised 2.0R exit, which is not a
    counterfactual for a larger target, it is the old answer wearing a new
    label. Raised in review on #61.

    stop_hit and expired rows are NOT censored — the walk continued past any
    favourable excursion to the stop or the horizon — so the limit is set by
    the nearest target among the rows that did hit one.

    A target_hit row with a COMPLETE uncensored excursion no longer
    constrains this: the walk continued past its target, so it does record
    whether the move went on to 3.0R. Only rows still carrying a censored MFE
    set the limit, which means the ceiling lifts on its own as uncensored
    rows accumulate — and stays put while pre-fix rows are still in the
    sample, because those genuinely cannot answer.
    """
    hit_distances = [
        d for d in (_target_distance_r(o) for o in outcomes
                    if o.get("status") == "target_hit" and not _is_uncensored(o))
        if d is not None
    ]
    return min(hit_distances) if hit_distances else float("inf")


def _counterfactual_expectancy(
    outcomes: list[dict], target_r: float, ceiling_r: Optional[float] = None
) -> Optional[float]:
    """Roughly what expectancy would have been with the target at `target_r`.

    TWO limits, and both are real. Neither is a reason to drop the readout —
    it is the only thing in the codebase that speaks to where the target
    belongs — but it is a reason not to quote a number from it as a return.

    1. ORDERING. Bar data records how far a signal went, not when, so for a
       signal that ended at its stop having first run past `target_r`, this
       cannot tell whether the favourable excursion came before the stop or
       after it. It assumes before, making every number an OPTIMISTIC bound,
       most optimistic at small target_r where more stopped-out rows qualify.

    2. CENSORING. Above _censoring_ceiling_r the answer is not merely
       optimistic, it is unavailable — see that function. Those candidates
       return None rather than a confident wrong number.

    So this ranks candidates worth testing properly. It is not a backtest.
    """
    if ceiling_r is None:
        ceiling_r = _censoring_ceiling_r(outcomes)
    # Compared at the precision the API REPORTS the ceiling at, not at full
    # float precision. entry/stop/target persist as Numeric(12, 4), so a
    # nominal 2:1 row can compute a ratio of 1.99995 — enough for a raw
    # `2.0 > ceiling` to reject the shipped target candidate while
    # counterfactual_ceiling_r displays that same ceiling as 2.0. Real data
    # would have returned null for the one candidate that matters most, with
    # the response contradicting itself. Raised in review on #61.
    if round(target_r, 2) > round(ceiling_r, 2):
        return None

    rows = []
    for o in outcomes:
        if o.get("status") == "pending":
            continue
        mfe, realised = _mfe_r(o), _realised_r(o)
        if mfe is None or realised is None:
            continue
        rows.append(target_r if mfe >= target_r else realised)
    return round(sum(rows) / len(rows), 3) if rows else None


def compute_signal_outcome_stats(outcomes: list[dict]) -> dict:
    """
    Aggregate hit rate / days-to-resolve stats from a list of outcome dicts
    (each with at least: status, confidence, days_to_resolve, ticker,
    regime).

    hit_rate excludes "expired" and "pending" from the denominator — it
    answers "of the signals that actually resolved one way or the other,
    how many hit target before stop", not "of everything ever generated".
    """
    total = len(outcomes)
    pending = [o for o in outcomes if o["status"] == "pending"]
    target_hit = [o for o in outcomes if o["status"] == "target_hit"]
    stop_hit = [o for o in outcomes if o["status"] == "stop_hit"]
    expired = [o for o in outcomes if o["status"] == "expired"]

    resolved_decisive = len(target_hit) + len(stop_hit)
    hit_rate = (len(target_hit) / resolved_decisive) if resolved_decisive > 0 else None

    def _avg_days(rows: list[dict]) -> Optional[float]:
        days = [r["days_to_resolve"] for r in rows if r.get("days_to_resolve") is not None]
        return round(sum(days) / len(days), 1) if days else None

    by_ticker: dict[str, dict] = {}
    for o in outcomes:
        t = o["ticker"]
        by_ticker.setdefault(t, {"total": 0, "target_hit": 0, "stop_hit": 0})
        by_ticker[t]["total"] += 1
        if o["status"] in ("target_hit", "stop_hit"):
            by_ticker[t][o["status"]] += 1
    ticker_breakdown = []
    for t, s in by_ticker.items():
        decisive = s["target_hit"] + s["stop_hit"]
        ticker_breakdown.append({
            "ticker": t,
            "total": s["total"],
            "hit_rate": round(s["target_hit"] / decisive, 3) if decisive > 0 else None,
        })
    ticker_breakdown.sort(key=lambda x: x["total"], reverse=True)

    by_confidence = _confidence_buckets(outcomes)

    regimes = sorted({o.get("regime") for o in outcomes if o.get("regime")})
    by_regime = {
        r: _confidence_buckets([o for o in outcomes if o.get("regime") == r])
        for r in regimes
    }

    # ── Is the target in the right place? ────────────────────────────
    # hit_rate cannot answer that: it drops expiries, and expiry is not
    # neutral between winners and losers. These are measured over every
    # RESOLVED signal, expiries included, so nothing is silently excluded.
    resolved = [o for o in outcomes if o["status"] != "pending"]
    realised = [r for r in (_realised_r(o) for o in resolved) if r is not None]
    mfes = [m for m in (_mfe_r(o) for o in resolved) if m is not None]
    target_rs = [t for t in (_target_distance_r(o) for o in outcomes)
                 if t is not None]

    by_mfe_bucket = {}
    for label, lo, hi in MFE_BUCKETS_R:
        by_mfe_bucket[label] = sum(1 for m in mfes if lo <= m < hi)

    target_mix: dict[str, int] = {}
    for t in target_rs:
        key = f"{round(t, 2):g}"
        target_mix[key] = target_mix.get(key, 0) + 1
    target_distance_r = (
        round(target_rs[0], 2) if target_mix and len(target_mix) == 1 else None
    )

    # The headline: mean R per resolved signal. Positive means the system
    # makes money, whatever the hit rate says. A 2R target reached a third of
    # the time and a 1R loss the rest is expectancy 0 — hit rate alone never
    # shows that.
    expectancy_r = round(sum(realised) / len(realised), 3) if realised else None
    ceiling = _censoring_ceiling_r(resolved)

    return {
        "total": total,
        "pending": len(pending),
        "target_hit": len(target_hit),
        "stop_hit": len(stop_hit),
        "expired": len(expired),
        "hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
        "expectancy_r": expectancy_r,
        "resolved_with_r": len(realised),
        # A scalar ONLY when every row shares one geometry. Averaging a
        # cohort that holds both 2.0R and 1.5R signals reports 1.75R, which
        # describes neither and silently breaks the one comparison this
        # readout exists for — is the target within reach of by_mfe_bucket_r.
        # After a retune the mix is exactly what you have, so it is reported
        # instead of averaged away. Raised in review on #61.
        "target_distance_r": target_distance_r,
        "target_distance_r_mix": target_mix,
        "mfe_r_median": round(median(mfes), 2) if mfes else None,
        "by_mfe_bucket_r": by_mfe_bucket,
        # None above the censoring ceiling rather than a confident wrong
        # number — see _counterfactual_expectancy.
        "counterfactual_expectancy_r": {
            str(t): _counterfactual_expectancy(resolved, t, ceiling)
            for t in CANDIDATE_TARGETS_R
        },
        "counterfactual_ceiling_r": None if ceiling == float("inf") else round(ceiling, 2),
        "avg_days_to_target": _avg_days(target_hit),
        "avg_days_to_stop": _avg_days(stop_hit),
        "by_confidence_bucket": by_confidence,
        "by_regime": by_regime,
        "by_ticker": ticker_breakdown[:25],
    }
