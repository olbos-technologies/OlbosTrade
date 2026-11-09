/**
 * The chart page's two time controls, like TradingView's: the BAR INTERVAL (how
 * long one candle is) and the RANGE (how much history is shown). They mirror the
 * backend's `services/chart_request.py`.
 */
export type IntervalKey = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w" | "1M";
export type RangeKey = "1d" | "5d" | "1m" | "3m" | "6m" | "ytd" | "1y" | "5y" | "all";

// Labels follow TradingView: "1m" is a minute, "M" is a month, so they never read alike.
export const INTERVAL_OPTIONS: { key: IntervalKey; label: string; title: string }[] = [
  { key: "1m", label: "1m", title: "1 minute" },
  { key: "5m", label: "5m", title: "5 minutes" },
  { key: "15m", label: "15m", title: "15 minutes" },
  { key: "30m", label: "30m", title: "30 minutes" },
  { key: "1h", label: "1h", title: "1 hour" },
  { key: "4h", label: "4h", title: "4 hours" },
  { key: "1d", label: "D", title: "1 day" },
  { key: "1w", label: "W", title: "1 week" },
  { key: "1M", label: "M", title: "1 month" },
];

export const RANGE_OPTIONS: { key: RangeKey; label: string; title: string }[] = [
  { key: "1d", label: "1D", title: "1 day" },
  { key: "5d", label: "5D", title: "5 days" },
  { key: "1m", label: "1M", title: "1 month" },
  { key: "3m", label: "3M", title: "3 months" },
  { key: "6m", label: "6M", title: "6 months" },
  { key: "ytd", label: "YTD", title: "Year to date" },
  { key: "1y", label: "1Y", title: "1 year" },
  { key: "5y", label: "5Y", title: "5 years" },
  { key: "all", label: "All", title: "All available history" },
];

/** Picking a range also picks a sensible bar size for it. */
export const RANGE_DEFAULT_INTERVAL: Record<RangeKey, IntervalKey> = {
  "1d": "5m", "5d": "15m", "1m": "1h", "3m": "1d", "6m": "1d",
  ytd: "1d", "1y": "1d", "5y": "1w", all: "1M",
};

export const DEFAULT_INTERVAL: IntervalKey = "15m";
export const DEFAULT_RANGE: RangeKey = "5d";

/** Wording for a range the data provider could not fully cover at this interval. */
export function clampNote(intervalTitle: string, period: string | null | undefined): string {
  const days = String(period ?? "").replace(/d$/, "");
  const span = /^\d+$/.test(days) ? `the last ${days} days` : "less history";
  return `Showing ${span}: ${intervalTitle} bars don't go back further.`;
}
