"""Why the desk can hold N positions - the arithmetic, spelled out for the dashboard.

The question this answers: "the limit is 5, so why is it only trading 3?" The rule
is the portfolio gate's (execution_portfolio_gate.effective_max_positions): the
tighter of the global cap and the trading style's cap, then tightened by the
regime. This module only DESCRIBES that; it decides nothing and gates nothing.

Two things it will not do: guess a number it could not read (an unknown open count
is None, not 0), and invent a regime (an unclassified market is reported as such).
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.execution_portfolio_gate import REGIME_CONCURRENCY_MULTIPLIER, effective_max_positions

#: The reason text before its first colon is the category; the rest is detail
#: ("Max concurrent positions reached: 3/3 ...").
_TOP_REASONS = 5


def reason_category(reason: Any) -> str:
    text = str(reason or "").strip()
    return (text.split(":", 1)[0].strip() or "no reason recorded")[:80]


def top_block_reasons(rows: list[tuple[Optional[str], Any]], limit: int = _TOP_REASONS) -> list[dict]:
    """rows: (status, payload) of recent execution events. Counts blocked/skipped
    by reason category, most frequent first."""
    counts: dict[str, int] = {}
    for status, payload in rows:
        if status not in ("blocked", "skipped"):
            continue
        reason = (payload or {}).get("reason") if isinstance(payload, dict) else None
        key = reason_category(reason)
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"reason": k, "count": v} for k, v in ranked[:limit]]


def describe_capacity(
    *, global_cap: int, style: str, style_cap: int, regime: Optional[str],
    open_positions: Optional[int], blocks: list[dict],
) -> dict:
    regime_known = bool(regime) and regime != "unknown"
    effective, multiplier, tightened = effective_max_positions(global_cap, style_cap, regime or "unknown")
    base = min(global_cap, style_cap)
    limiting = "style" if style_cap < global_cap else "global"
    room = None if open_positions is None else max(0, effective - open_positions)
    steps = [
        f"Global limit {global_cap}",
        f"Trading style ({style}) allows {style_cap}",
        f"Tighter of the two: {base}",
    ]
    if tightened:
        steps.append(
            f"Market regime ({regime or 'unknown'}) scales that by {multiplier:g}: {base} → {effective}"
        )
    return {
        "global_cap": global_cap,
        "style": {"name": style, "cap": style_cap},
        "base_cap": base,
        "limited_by": limiting,
        "regime": {
            "name": regime or "unknown",
            "known": regime_known,
            "multiplier": multiplier,
            "tightened": tightened,
            "multipliers": dict(REGIME_CONCURRENCY_MULTIPLIER),
        },
        "effective_cap": effective,
        "open_positions": open_positions,
        "room": room,
        "steps": steps,
        "recent_blocks": blocks,
    }
