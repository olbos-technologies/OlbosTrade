/**
 * The multi-timeframe page's layout: how many charts, and each one's bar interval. Saved in this
 * browser; storage can be missing, blocked or edited by hand, so every read is validated and falls
 * back to a sensible default. Each interval comes with a range that shows enough history to give
 * the chart context without drawing thousands of bars in a small tile.
 */
import { INTERVAL_OPTIONS, type IntervalKey, type RangeKey } from "./timeframes";

export const MTF_LAYOUT_KEY = "olbos.multitf.layout";
export const MTF_INDICATORS_KEY = "olbos.multitf.indicators";
export const MIN_TILES = 2;
export const MAX_TILES = 4;

/** Higher timeframes first, so the picture reads from the big trend down to the entry chart. */
export const DEFAULT_INTERVALS: Record<number, IntervalKey[]> = {
  2: ["1d", "15m"],
  3: ["1d", "1h", "15m"],
  4: ["1w", "1d", "1h", "15m"],
};

/** The range each tile asks for with its interval. All are ones the backend can serve without clamping. */
export const RANGE_FOR_INTERVAL: Record<IntervalKey, RangeKey> = {
  "1m": "1d", "5m": "5d", "15m": "5d", "30m": "1m", "1h": "1m", "4h": "6m", "1d": "1y", "1w": "5y", "1M": "all",
};

export interface MtfLayout { intervals: IntervalKey[] }

const KNOWN = new Set<string>(INTERVAL_OPTIONS.map((o) => o.key));
const isInterval = (v: unknown): v is IntervalKey => typeof v === "string" && KNOWN.has(v);

export function defaultLayout(count = 3): MtfLayout {
  const n = Math.min(MAX_TILES, Math.max(MIN_TILES, Math.trunc(count) || 3));
  return { intervals: [...DEFAULT_INTERVALS[n]] };
}

/** Any input turned into a valid layout: known intervals only, between MIN_TILES and MAX_TILES of them. */
export function normaliseLayout(input: unknown): MtfLayout {
  const raw = input && typeof input === "object" ? (input as { intervals?: unknown }).intervals : undefined;
  if (!Array.isArray(raw)) return defaultLayout();
  const clean = raw.filter(isInterval).slice(0, MAX_TILES);
  return clean.length >= MIN_TILES ? { intervals: clean } : defaultLayout();
}

/**
 * Change the number of charts. The charts already chosen stay; each new one takes the first interval from the
 * defaults for that size that is not already on screen, so adding a chart never adds a copy of one you have.
 */
export function resizeLayout(layout: MtfLayout, count: number): MtfLayout {
  const n = Math.min(MAX_TILES, Math.max(MIN_TILES, Math.trunc(count) || layout.intervals.length));
  const intervals = layout.intervals.slice(0, n);
  const defaults = DEFAULT_INTERVALS[n];
  while (intervals.length < n) {
    intervals.push(defaults.find((k) => !intervals.includes(k)) ?? defaults[intervals.length]);
  }
  return { intervals };
}

export function readLayout(storage?: Pick<Storage, "getItem"> | null): MtfLayout {
  try {
    const raw = storage?.getItem(MTF_LAYOUT_KEY);
    return raw == null ? defaultLayout() : normaliseLayout(JSON.parse(raw));
  } catch {
    return defaultLayout();
  }
}

export function writeLayout(storage: Pick<Storage, "setItem"> | null | undefined, layout: MtfLayout): void {
  try { storage?.setItem(MTF_LAYOUT_KEY, JSON.stringify(normaliseLayout(layout))); } catch { /* storage unavailable: lasts this visit */ }
}
