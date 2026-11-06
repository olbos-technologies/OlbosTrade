/**
 * Colour and sign for a profit-and-loss figure.
 *
 * `x >= 0 ? green : red` was repeated across the app, so a flat day printed "+$0.00" in
 * green: an exact zero is neither a gain nor a loss. It also made an unreadable value look
 * good, because call sites wrote `(pnl ?? 0) >= 0` and a failed read became green zero.
 *
 * Colour carries meaning here (green = verified positive, red = verified negative), so:
 *   - a figure that rounds to $0.00 (or 0.00%) is neutral: plain text colour, no sign;
 *   - a value that is not a finite number is "unknown": faint, never green.
 */

export const PNL_POSITIVE = "var(--green)";
export const PNL_NEGATIVE = "var(--red)";
export const PNL_FLAT = "var(--ink)";
export const PNL_UNKNOWN = "var(--ink-faint)";

/** Whole-cents (or hundredths of a percent) are what the screen can show, so zero is judged at that precision. */
function isFlat(n: number, decimals: number): boolean {
  const k = 10 ** decimals;
  return Math.round(n * k) === 0;
}

export function pnlColor(n: unknown, decimals = 2): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return PNL_UNKNOWN;
  if (isFlat(n, decimals)) return PNL_FLAT;
  return n > 0 ? PNL_POSITIVE : PNL_NEGATIVE;
}

/** "+" for a gain, "" for flat or a loss (the minus sign comes from the number itself). */
export function gainSign(n: unknown, decimals = 2): string {
  return typeof n === "number" && Number.isFinite(n) && !isFlat(n, decimals) && n > 0 ? "+" : "";
}
