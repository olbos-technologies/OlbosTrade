"""Why are options not trading?  The options funnel, from what the system already records.

An options entry passes a chain of gates, and each one records its own kind of "no":

    scan        regime allows options -> a strategy's entry rules -> equity-signal confluence
                -> spread priced -> AI scorer -> sized > 0 -> concentration
                (rejections: `options_scan_rejections`; qualified: `options_signal_history`)
    dispatch    MANUAL records the signal and stops; COPILOT queues it for approval;
                AUTOPILOT sends it to execution
                (copilot queue and execution results: `execution_events`)
    execution   liquidity -> frequency controller -> guardrails -> portfolio gate ->
                ownership -> duplicate guard -> order submitted

Until now the reasons lived in two tables and a script. This reads both and answers the
question in one place: where do candidates die, and what is the biggest reason.

What it is NOT.  A count of attempts, not of distinct trades: the scan re-evaluates the
whole watchlist every 30 minutes, so one stubborn symbol repeats. Not recorded, so not
shown per symbol: the regime stage. When the regime disallows options (CRISIS), the scan
returns before writing anything; the current regime is reported separately so that case is
visible rather than silent.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

KNOWN_STRATEGIES = ("bull_put_spread", "bear_call_spread", "iron_condor",
                    "bull_call_debit_spread", "bear_put_spread")

#: Execution-block prefixes whose second segment is the informative part
#: ("frequency_controller: below_min_confidence: 0.62 < 0.70 (aggressive)").
_TWO_LEVEL = {"frequency_controller", "liquidity_gate", "guardrail", "portfolio_gate",
              "emotion_guard", "market_closed", "capital_preservation", "strategy_health"}

# (substring of the lower-cased reason, bucket). First match wins.
_ENTRY_RULES = (
    ("iv rank too low", "entry_rule: iv_rank_too_low"),
    ("iv rank too high", "entry_rule: iv_rank_too_high"),
    ("20-day sma", "entry_rule: price_vs_20day_average"),
    ("rsi", "entry_rule: rsi"),
    ("adx", "entry_rule: adx"),
    ("could not find", "entry_rule: no_strike_at_target_delta"),
    ("vix out of range", "entry_rule: vix_out_of_range"),
    ("capital preservation", "guardrail: capital_preservation_mode"),
)
_GUARDRAIL_HINTS = ("limit hit", "suspended", "cooling", "not allowed", "drawdown", "trading mode")

# A number standing on its own ("0.62", "$812", "12.5%"), not one inside a token ("0dte_near_close").
_NUMBERS = re.compile(r"(?<![A-Za-z_])[-+]?\$?\d[\d.,]*%?(?![A-Za-z_])")
_PARENS = re.compile(r"\([^)]*\)")


def _clean(text: str) -> str:
    """Strip numbers and parenthesised detail so 'RSI too weak: 44.0' and '41.2' are one reason."""
    text = _PARENS.sub(" ", text or "")
    text = _NUMBERS.sub(" ", text)
    text = re.sub(r"\s[/<>=±≥≤]+(?=\s|$)", " ", text)      # operators left behind by the numbers
    return re.sub(r"\s+", " ", text).strip(" :;,.-—<>=/").lower()


def bucket_execution_reason(reason: Optional[str]) -> str:
    """'frequency_controller: below_min_confidence: 0.62 < 0.70 (aggressive)' ->
    'frequency_controller: below_min_confidence'. Unknown shapes keep their first segment."""
    if not reason:
        return "unspecified"
    parts = [p.strip() for p in reason.split(":")]
    head = parts[0].lower()
    if head in _TWO_LEVEL and len(parts) > 1:
        sub = _clean(parts[1])[:80]
        return f"{head}: {sub}" if sub else head
    return _clean(parts[0])[:80] or "unspecified"


def split_entry_reasons(reason: str) -> list[tuple[Optional[str], str]]:
    """The scan joins every tried strategy's reason into one string ('a: x; b: y')."""
    out: list[tuple[Optional[str], str]] = []
    for part in (reason or "").split("; "):
        name, sep, rest = part.partition(": ")
        if sep and name in KNOWN_STRATEGIES:
            out.append((name, rest))
        elif part.strip():
            out.append((None, part))
    return out


def _bucket_entry_text(text: str) -> str:
    low = text.lower()
    for needle, bucket in _ENTRY_RULES:
        if needle in low:
            return bucket
    if any(h in low for h in _GUARDRAIL_HINTS):
        return f"guardrail: {_clean(text)[:50] or 'strategy not allowed'}"
    return f"other: {_clean(text)[:50]}"


def bucket_scan_reason(reason: Optional[str]) -> list[str]:
    """One stored rejection -> the buckets it counts toward (a combined entry-rule row
    counts once per tried strategy)."""
    r = (reason or "").strip()
    low = r.lower()
    if not r:
        return ["unspecified"]
    if low.startswith("ai scorer"):
        sub = _clean(r.split(":", 1)[1]) if ":" in r else ""
        return [f"ai_scorer_rejected: {sub[:50]}" if sub else "ai_scorer_rejected"]
    if low.startswith("insufficient price history"):
        return ["insufficient_price_history"]
    if low.startswith("sized to 0 contracts"):
        return ["sized_to_zero_contracts"]
    if "concentration would be" in low:
        return ["sector_concentration" if "sector" in low else "underlying_concentration"]
    if low.startswith("debit too small"):
        return ["debit_too_small"]
    if low.startswith("no usable spread"):
        return ["no_usable_spread"]
    if low.startswith("scan error"):
        return ["scan_error"]
    if low.startswith("conflicts with this ticker's own equity signal"):
        return ["equity_signal_conflict"]
    parts = split_entry_reasons(r)
    return [_bucket_entry_text(text) for _, text in parts] or [f"other: {_clean(r)[:50]}"]


# ── Verdict ──────────────────────────────────────────────────────────────────

def verdict(*, mode: str, scanned: int, qualified: int, dispatched: int, submitted: int,
            queued_pending: int, queued_approved: int, top_scan: Optional[dict],
            top_block: Optional[dict]) -> dict[str, str]:
    """One sentence saying where options are dying. `mode` is the CURRENT execution mode."""
    if submitted > 0:
        return {"code": "trading", "text": f"{submitted} options order(s) were submitted in this window."}
    if scanned == 0 and qualified == 0:
        return {"code": "no_data", "text": (
            "Nothing was recorded in this window: the options scan is not running, or has not run since "
            "the tables were created. Check the regime below (a CRISIS regime scans nothing and records nothing).")}
    if qualified == 0:
        why = f" Biggest reason: {top_scan['reason']} ({top_scan['count']})." if top_scan else ""
        return {"code": "nothing_qualifies", "text": (
            f"No candidate qualified at the scan ({scanned} evaluations rejected), so nothing reached execution.{why}")}
    if mode == "manual":
        return {"code": "mode_manual", "text": (
            f"{qualified} candidate(s) qualified, but execution mode is MANUAL: options signals are recorded and never sent anywhere.")}
    if mode == "copilot" and queued_approved == 0:
        return {"code": "waiting_for_approval", "text": (
            f"{qualified} candidate(s) qualified and {queued_pending + queued_approved} were queued for approval; none was approved "
            f"({queued_pending} still pending). In COPILOT mode nothing trades until a person approves.")}
    if dispatched == 0:
        return {"code": "qualified_but_never_dispatched", "text": (
            f"{qualified} candidate(s) qualified but no execution attempt was recorded. Execution mode is {mode.upper()}: "
            "check the backend log for 'Options execution handler failed'.")}
    why = f" Biggest reason: {top_block['reason']} ({top_block['count']})." if top_block else ""
    return {"code": "blocked_at_execution", "text": (
        f"{qualified} candidate(s) qualified and {dispatched} execution attempt(s) were made, but none was submitted.{why}")}


# ── Assembly ─────────────────────────────────────────────────────────────────

def _top(counter: Counter, limit: int = 8) -> list[dict[str, Any]]:
    return [{"reason": k, "count": v} for k, v in counter.most_common(limit)]


async def build_funnel(session: AsyncSession, *, days: int, now: Optional[datetime] = None,
                       mode: str = "unknown", regime: Optional[dict] = None,
                       frequency_rule: Optional[dict] = None) -> dict[str, Any]:
    from app.models.execution_event import ExecutionEvent
    from app.models.options_scan_rejection import OptionsScanRejection
    from app.models.options_signal_history import OptionsSignalHistory

    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # Scan stage: rejections (one row per ticker/strategy/reason/UTC day, `occurrences` repeats).
    rej_rows = (await session.execute(
        select(OptionsScanRejection.reason, OptionsScanRejection.occurrences)
        .where(OptionsScanRejection.last_seen_at >= since)
    )).all()
    scan_reasons: Counter = Counter()
    rejected = 0
    for reason, occurrences in rej_rows:
        n = int(occurrences or 1)
        rejected += n
        for b in bucket_scan_reason(reason):
            scan_reasons[b] += n

    qualified = int((await session.execute(
        select(func.count()).select_from(OptionsSignalHistory)
        .where(OptionsSignalHistory.generated_at >= since)
    )).scalar() or 0)

    # Dispatch and execution stages.
    queue = Counter({s or "unknown": int(n) for s, n in (await session.execute(
        select(ExecutionEvent.status, func.count()).where(
            ExecutionEvent.kind == "pending_approval", ExecutionEvent.asset_type == "options",
            ExecutionEvent.created_at >= since).group_by(ExecutionEvent.status))).all()})

    # One labelled expression, used in both SELECT and GROUP BY. Writing the JSON lookup twice sends
    # the key as two different bind parameters, which PostgreSQL rejects as "must appear in the
    # GROUP BY clause" — a failure no mocked session would ever show.
    reason_expr = ExecutionEvent.payload["reason"].as_string().label("reason")
    results = (await session.execute(
        select(ExecutionEvent.status, reason_expr, func.count()).where(
            ExecutionEvent.kind == "execution", ExecutionEvent.asset_type == "options",
            ExecutionEvent.created_at >= since).group_by(ExecutionEvent.status, reason_expr))).all()
    by_status: Counter = Counter()
    block_reasons: Counter = Counter()
    skip_reasons: Counter = Counter()
    for status, reason, n in results:
        n = int(n)
        by_status[status or "unknown"] += n
        if status == "blocked":
            block_reasons[bucket_execution_reason(reason)] += n
        elif status == "skipped":
            skip_reasons[bucket_execution_reason(reason)] += n

    dispatched = sum(by_status.values())
    submitted = by_status.get("submitted", 0)
    top_scan = _top(scan_reasons, 1)[0] if scan_reasons else None
    top_block = _top(block_reasons, 1)[0] if block_reasons else None

    stages = [
        {"key": "scan_rejected", "label": "Rejected at the scan", "count": rejected, "reasons": _top(scan_reasons)},
        {"key": "qualified", "label": "Qualified at the scan", "count": qualified, "reasons": []},
    ]
    if queue:
        stages.append({"key": "copilot_queue", "label": "Queued for approval (copilot)", "count": sum(queue.values()),
                       "reasons": [{"reason": k, "count": v} for k, v in queue.most_common()]})
    stages += [
        {"key": "execution_blocked", "label": "Blocked at execution", "count": by_status.get("blocked", 0), "reasons": _top(block_reasons)},
        {"key": "execution_skipped", "label": "Skipped at execution", "count": by_status.get("skipped", 0), "reasons": _top(skip_reasons)},
        {"key": "execution_error", "label": "Errored at execution", "count": by_status.get("error", 0), "reasons": []},
        {"key": "submitted", "label": "Orders submitted", "count": submitted, "reasons": []},
    ]

    return {
        "window_days": days,
        "since": since.isoformat(),
        "generated_at": now.isoformat(),
        "execution_mode": mode,
        "regime": regime,
        "frequency_rule": frequency_rule,
        "scanned": rejected + qualified,
        "stages": stages,
        "verdict": verdict(mode=mode, scanned=rejected + qualified, qualified=qualified, dispatched=dispatched,
                           submitted=submitted, queued_pending=queue.get("pending", 0),
                           queued_approved=queue.get("approved", 0), top_scan=top_scan, top_block=top_block),
        "notes": [
            "Counts are attempts, not distinct trades: the scan re-evaluates every symbol each 30 minutes.",
            "Scan rejections are stored per day, so the window edge is day-granular.",
            "The regime stage is not recorded per symbol. A regime that disallows options scans nothing and records nothing.",
        ],
    }
