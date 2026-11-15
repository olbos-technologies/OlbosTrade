/**
 * Pure helpers for the chart page's research dock. Free of React so vitest can
 * cover the honesty rules: a missing number is a dash, never zero, and a level's
 * distance is always measured from the same entry price.
 */

export const DASH = "—";

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

/** A price to two decimals, or a dash when it is missing. */
export function fmtPrice(n: number | null | undefined): string {
  return finite(n) ? n.toFixed(2) : DASH;
}

/** A signed percentage with a true minus sign, or a dash when it is missing. */
export function fmtSignedPct(n: number | null | undefined, digits = 1): string {
  if (!finite(n)) return DASH;
  const sign = n < 0 ? "−" : n > 0 ? "+" : "";
  return `${sign}${Math.abs(n).toFixed(digits)}%`;
}

/** Distance of `value` from `entry`, in percent of entry. Null when either is unusable. */
export function distancePct(value: number | null | undefined, entry: number | null | undefined): number | null {
  if (!finite(value) || !finite(entry) || entry <= 0) return null;
  return ((value - entry) / entry) * 100;
}

/**
 * Reward-to-risk of a plan: distance to target over distance to stop, both from
 * entry. Null when the plan is not coherent (stop on the wrong side of entry, a
 * zero risk, or a target on the wrong side), so a broken plan shows a dash and
 * not a flattering ratio.
 */
export function rewardToRisk(
  entry: number | null | undefined,
  stop: number | null | undefined,
  target: number | null | undefined,
): number | null {
  if (!finite(entry) || !finite(stop) || !finite(target)) return null;
  const risk = entry - stop;
  const reward = target - entry;
  const long = risk > 0 && reward > 0;
  const short = risk < 0 && reward < 0;
  if (!long && !short) return null;
  return Math.abs(reward) / Math.abs(risk);
}

/** "5m ago" / "3h ago" / "2d ago" from an ISO timestamp, or a dash when unparsable. */
export function ageLabel(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return DASH;
  const secs = Math.max(0, Math.round((now - t) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export const INSIGHT_TABS = ["signal", "levels", "volatility", "catalysts"] as const;
export type InsightTab = (typeof INSIGHT_TABS)[number];

/** Arrow-key navigation for the tab list: wraps at both ends. */
export function nextTab(current: InsightTab, key: string): InsightTab | null {
  const i = INSIGHT_TABS.indexOf(current);
  if (key === "ArrowRight") return INSIGHT_TABS[(i + 1) % INSIGHT_TABS.length];
  if (key === "ArrowLeft") return INSIGHT_TABS[(i - 1 + INSIGHT_TABS.length) % INSIGHT_TABS.length];
  if (key === "Home") return INSIGHT_TABS[0];
  if (key === "End") return INSIGHT_TABS[INSIGHT_TABS.length - 1];
  return null;
}
