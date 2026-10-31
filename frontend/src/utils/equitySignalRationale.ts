/**
 * Turn the equity scanner's `reasons` score map into a short plain-language
 * line for "Why it appeared".
 *
 * The live feed API stores contributing factors as
 * `{ rsi_oversold: 0.15, macd_above_signal: 0.1, … }` under `reasons`.
 * The card UI historically read a string `reason` that the scanner never
 * sets — every card then showed the same empty-state copy even though the
 * producer data was present. This helper is the bridge.
 */

const REASON_LABELS: Record<string, string> = {
  rsi_oversold: "RSI oversold",
  rsi_overbought: "RSI overbought",
  macd_above_signal: "MACD above signal",
  macd_below_signal: "MACD below signal",
  macd_bull_cross: "MACD bullish cross",
  macd_bear_cross: "MACD bearish cross",
  bb_oversold: "Bollinger oversold",
  bb_overbought: "Bollinger overbought",
  volume_confirmed_bull: "Volume confirmed above VWAP",
  volume_confirmed_bear: "Volume confirmed below VWAP",
  above_ema200: "Price above EMA-200",
  below_ema200: "Price below EMA-200",
  ema_aligned_bull: "EMAs aligned bullish",
  ema_aligned_bear: "EMAs aligned bearish",
  above_vwap: "Price above VWAP",
  below_vwap: "Price below VWAP",
  stoch_oversold: "Stochastic oversold",
  stoch_overbought: "Stochastic overbought",
  orderflow_bull: "Bullish order flow",
  orderflow_bear: "Bearish order flow",
  sentiment_bull: "Bullish sentiment",
  sentiment_bear: "Bearish sentiment",
};

const EMPTY = "No plain-language rationale supplied by this producer.";

function humanizeKey(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function labelFor(key: string): string {
  return REASON_LABELS[key] ?? humanizeKey(key);
}

/** Rank contributing factors by absolute weight and return plain phrases. */
export function phrasesFromReasonsMap(
  reasons: Record<string, unknown> | null | undefined,
  limit = 4,
): string[] {
  if (!reasons || typeof reasons !== "object") return [];
  return Object.entries(reasons)
    .filter(([, v]) => typeof v === "number" && Number.isFinite(v) && v !== 0)
    .sort((a, b) => Math.abs(b[1] as number) - Math.abs(a[1] as number))
    .slice(0, limit)
    .map(([key]) => labelFor(key));
}

/**
 * Prefer an explicit string `reason`, else format the scanner `reasons` map,
 * else the shared empty-state copy (honest: producer sent nothing usable).
 */
export function equitySignalRationale(signal: {
  reason?: string | null;
  reasons?: Record<string, unknown> | string[] | string | null;
}): string {
  if (typeof signal.reason === "string" && signal.reason.trim()) {
    return signal.reason.trim();
  }
  const raw = signal.reasons;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  if (Array.isArray(raw)) {
    const lines = raw.filter((item): item is string => typeof item === "string" && !!item.trim());
    if (lines.length) return lines.join(" · ");
  }
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const phrases = phrasesFromReasonsMap(raw as Record<string, unknown>);
    if (phrases.length) return phrases.join(" · ");
  }
  return EMPTY;
}

export { EMPTY as EQUITY_RATIONALE_EMPTY, REASON_LABELS };
