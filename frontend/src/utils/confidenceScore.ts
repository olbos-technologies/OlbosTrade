/**
 * How a signal's "confidence" is worded on screen.
 *
 * The producers emit a number in 0-1 that ranks and gates signals. It is a model
 * score, not the probability that the trade wins, and a "72%" next to a ticker
 * reads as exactly that probability. So it is shown as a score out of 100.
 *
 * Probabilities that really are probabilities (POP, VaR level, regime confidence)
 * are NOT routed through here and keep their percent sign.
 */

export const CONFIDENCE_NOTE =
  "A 0-100 model score used to rank and gate signals. It is not the probability that the trade wins.";

/** 0-1 -> whole-number score 0-100, or null when there is no usable value. */
export function confidenceScore(c: number | null | undefined): number | null {
  return typeof c === "number" && Number.isFinite(c) ? Math.round(c * 100) : null;
}

/** "72/100", or the fallback when the producer gave no usable value. */
export function confidenceScoreText(c: number | null | undefined, fallback = "—"): string {
  const s = confidenceScore(c);
  return s == null ? fallback : `${s}/100`;
}
