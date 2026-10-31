/**
 * signalRationale — turn the equity scanner's factor map into a sentence.
 *
 * Why this exists: the backend's equity scan (app/main.py, _scan_one) stores
 * `reasons` — a DICT of factor name -> signed point contribution, e.g.
 * `{ "macd_bull_cross": 10, "below_vwap": -4 }` — produced by
 * score_equity_signal(). The signal card read `sig.reason` (singular, a
 * string), a field the scanner has never emitted. The key never matched and
 * the type never matched, so every card fell through to its "no plain-language
 * rationale supplied by this producer" placeholder, and every detail drawer
 * showed an empty evidence list. The rationale was always there; nothing read
 * it.
 *
 * These phrases describe WHAT THE INDICATOR DID. They are not predictions, and
 * the point values are the engine's own weights, not probabilities of profit.
 * Unknown keys are surfaced in their raw form rather than dropped — a factor
 * added to the engine and not mapped here must still be visible, because a
 * silently discarded reason is indistinguishable from one that never fired.
 */

export type SignalReasons = Record<string, number> | string | string[] | null | undefined;

const PHRASES: Record<string, string> = {
  rsi_oversold:           "RSI is oversold",
  rsi_overbought:         "RSI is overbought",
  macd_above_signal:      "MACD is above its signal line",
  macd_below_signal:      "MACD is below its signal line",
  macd_bull_cross:        "MACD crossed up through its signal line",
  macd_bear_cross:        "MACD crossed down through its signal line",
  bb_oversold:            "price is at the lower Bollinger band",
  bb_overbought:          "price is at the upper Bollinger band",
  volume_confirmed_bull:  "the move up came on above-average volume",
  volume_confirmed_bear:  "the move down came on above-average volume",
  above_ema200:           "price is above the 200-period EMA",
  below_ema200:           "price is below the 200-period EMA",
  ema_aligned_bull:       "the short, medium and long EMAs are stacked bullish",
  ema_aligned_bear:       "the short, medium and long EMAs are stacked bearish",
  above_vwap:             "price is above VWAP",
  below_vwap:             "price is below VWAP",
  stoch_oversold:         "stochastic is oversold",
  stoch_overbought:       "stochastic is overbought",
  orderflow_bull:         "order flow is leaning to the bid",
  orderflow_bear:         "order flow is leaning to the offer",
  sentiment_bull:         "news sentiment is positive",
  sentiment_bear:         "news sentiment is negative",
};

/** Readable fallback for a factor the engine emits but this map has not caught up with. */
export function phraseFor(key: string): string {
  return PHRASES[key] ?? key.replace(/_/g, " ");
}

/**
 * Factors ordered by the size of their contribution, largest first.
 * Zero-weight factors are dropped: they fired but moved the score nowhere,
 * so naming them would overstate the evidence.
 */
export function rankedReasons(reasons: SignalReasons): { key: string; points: number }[] {
  if (!reasons || typeof reasons === "string" || Array.isArray(reasons)) return [];
  return Object.entries(reasons)
    .filter(([, pts]) => typeof pts === "number" && Number.isFinite(pts) && pts !== 0)
    .map(([key, points]) => ({ key, points: points as number }))
    .sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
}

/** The evidence list the detail drawer renders, one phrase per factor. */
export function reasonList(reasons: SignalReasons): string[] {
  if (typeof reasons === "string") return reasons.trim() ? [reasons] : [];
  if (Array.isArray(reasons)) return reasons.filter((r): r is string => typeof r === "string");
  return rankedReasons(reasons).map(
    ({ key, points }) => `${phraseFor(key)} (${points > 0 ? "+" : ""}${points})`,
  );
}

/**
 * One sentence naming the strongest contributors, for the card.
 * Returns null when there is genuinely nothing to say, so the caller can keep
 * its own placeholder rather than print an empty string.
 */
export function describeSignalReasons(reasons: SignalReasons, limit = 3): string | null {
  if (typeof reasons === "string" && reasons.trim()) return reasons.trim();
  const list = Array.isArray(reasons)
    ? reasons.filter((r): r is string => typeof r === "string")
    : rankedReasons(reasons).slice(0, limit).map(({ key }) => phraseFor(key));
  if (!list.length) return null;
  const total = Array.isArray(reasons) ? list.length : rankedReasons(reasons).length;
  const head = list.length === 1
    ? list[0]
    : `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
  const more = total > list.length ? `, plus ${total - list.length} more factor${total - list.length === 1 ? "" : "s"}` : "";
  return `${head.charAt(0).toUpperCase()}${head.slice(1)}${more}.`;
}
