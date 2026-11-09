/**
 * Which position cap and open count the "would Autopilot block this" preview uses.
 *
 * The real gate applies the tighter of the global and trading-style caps and then
 * tightens it by the market regime (see execution_portfolio_gate). The browser can
 * only see the style cap, so on its own it says "room for 5" while the gate says
 * "3 of 3". /api/trade-desk/capacity reports the gate's own effective cap and the
 * open count it uses (the larger of the database and the broker), so when that read
 * succeeds it wins. When it does not, the old estimate is still used but is flagged
 * as an estimate, so callers can say "not checked" instead of "clear".
 */

export type CapacityRead = { effective_cap?: unknown; open_positions?: unknown } | null | undefined;

export type ResolvedCapacity = {
  maxConcurrent: number | null;
  openCount: number;
  /** True only when both numbers came from the gate's own capacity read. */
  authoritative: boolean;
};

const isCount = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;

export function resolveCapacity(args: {
  capacity: CapacityRead;
  /** Count from the positions list the browser already fetched. */
  listedOpenCount: number;
  /** The trading style's own cap, if known. */
  styleCap: number | null;
  fallbackCap: number;
}): ResolvedCapacity {
  const { capacity, listedOpenCount, styleCap, fallbackCap } = args;
  if (isCount(capacity?.effective_cap) && isCount(capacity?.open_positions)) {
    return { maxConcurrent: capacity.effective_cap, openCount: capacity.open_positions, authoritative: true };
  }
  // Estimate: the open count may still be taken from the capacity read if only the
  // cap was missing, but a partial read is never called authoritative.
  return {
    maxConcurrent: styleCap ?? fallbackCap,
    openCount: isCount(capacity?.open_positions) ? capacity.open_positions : listedOpenCount,
    authoritative: false,
  };
}
