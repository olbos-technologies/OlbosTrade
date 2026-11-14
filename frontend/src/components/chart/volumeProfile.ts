/**
 * Volume profile: how much volume traded at each price level.
 *
 * Plain logic with no React and no charting library, so every rule is testable.
 *
 * IMPORTANT, and shown to the user in the chart's readout: this is built from
 * OHLCV bars, not from individual trades. A bar says how much traded in total
 * and the range it covered, not where inside that range. So each bar's volume is
 * spread EVENLY across the price bins its high-low range touches. That is the
 * usual approximation for a bar-based profile. It is not tick-accurate, and a
 * coarser bar interval makes it coarser.
 *
 * The profile covers a window of bars (the ones currently visible on the chart).
 * It reports the point of control (the busiest price) and the value area (the
 * contiguous band around it that holds a set share of the volume, 70% by
 * convention). It describes where trading happened. It is not a signal and does
 * not predict price.
 */

export interface ProfileBar {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ProfileRow {
  /** Bottom of this price bin. */
  low: number;
  /** Top of this price bin. */
  high: number;
  /** Volume attributed to this bin. */
  total: number;
}

export interface VolumeProfile {
  rows: ProfileRow[];
  /** Index into `rows` of the busiest bin, or -1 when there is no volume. */
  pocIndex: number;
  /** Midpoint price of the busiest bin, or null when there is no volume. */
  pocPrice: number | null;
  /** First and last row index of the value area (inclusive), or null. */
  valueArea: { from: number; to: number } | null;
  /** Price span of the value area, or null. */
  valueAreaPrices: { low: number; high: number } | null;
  /** Sum of all rows. Equals the summed volume of the bars used. */
  totalVolume: number;
  /** How many bars contributed. */
  barsUsed: number;
}

export interface ProfileOptions {
  /** First bar index to include (inclusive). Default 0. */
  from?: number;
  /** Last bar index to include (inclusive). Default: the last bar. */
  to?: number;
  /** Number of price bins. Clamped to MIN_BINS..MAX_BINS. Default 48. */
  bins?: number;
  /** Share of the volume the value area holds, 0..1. Default 0.7. */
  valueAreaShare?: number;
}

export const DEFAULT_BINS = 48;
export const MIN_BINS = 8;
export const MAX_BINS = 200;
export const DEFAULT_VALUE_AREA = 0.7;

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

/** A bar we can use: finite prices, high at or above low, and non-negative volume. */
export function usableBar(b: ProfileBar | null | undefined): b is ProfileBar {
  return !!b && finite(b.high) && finite(b.low) && finite(b.volume) && b.high >= b.low && b.volume >= 0;
}

export function emptyProfile(): VolumeProfile {
  return { rows: [], pocIndex: -1, pocPrice: null, valueArea: null, valueAreaPrices: null, totalVolume: 0, barsUsed: 0 };
}

function clampInt(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.floor(n)));
}

export function computeVolumeProfile(bars: readonly ProfileBar[], opts: ProfileOptions = {}): VolumeProfile {
  if (!bars.length) return emptyProfile();

  const first = clampInt(finite(opts.from) ? opts.from : 0, 0, bars.length - 1);
  const last = clampInt(finite(opts.to) ? opts.to : bars.length - 1, 0, bars.length - 1);
  if (last < first) return emptyProfile();

  const used: ProfileBar[] = [];
  for (let i = first; i <= last; i++) if (usableBar(bars[i])) used.push(bars[i]);
  if (!used.length) return emptyProfile();

  let lo = Infinity, hi = -Infinity;
  for (const b of used) { if (b.low < lo) lo = b.low; if (b.high > hi) hi = b.high; }

  const binCount = clampInt(finite(opts.bins) ? opts.bins : DEFAULT_BINS, MIN_BINS, MAX_BINS);
  const span = hi - lo;
  // A window where nothing moved (every bar at one price) is a single level.
  const n = span > 0 ? binCount : 1;
  const size = span > 0 ? span / n : 0;

  const rows: ProfileRow[] = Array.from({ length: n }, (_, i) => ({
    low: span > 0 ? lo + i * size : lo,
    high: span > 0 ? (i === n - 1 ? hi : lo + (i + 1) * size) : hi,
    total: 0,
  }));

  for (const b of used) {
    if (b.volume === 0) continue;
    const range = b.high - b.low;
    if (!(range > 0) || size === 0) {
      // A bar with no range (or a flat window) trades at one price: one bin.
      const idx = size === 0 ? 0 : Math.min(n - 1, Math.max(0, Math.floor((b.low - lo) / size)));
      rows[idx].total += b.volume;
      continue;
    }
    const firstBin = Math.min(n - 1, Math.max(0, Math.floor((b.low - lo) / size)));
    const lastBin = Math.min(n - 1, Math.max(0, Math.floor((b.high - lo) / size)));
    for (let i = firstBin; i <= lastBin; i++) {
      const overlap = Math.min(b.high, rows[i].high) - Math.max(b.low, rows[i].low);
      if (overlap > 0) rows[i].total += (overlap / range) * b.volume;
    }
  }

  let totalVolume = 0;
  let pocIndex = -1, best = 0;
  rows.forEach((r, i) => {
    totalVolume += r.total;
    if (r.total > best) { best = r.total; pocIndex = i; }
  });

  if (pocIndex < 0) {
    // Bars exist but none carried volume: nothing to profile.
    return { ...emptyProfile(), rows, barsUsed: used.length };
  }

  const share = finite(opts.valueAreaShare) ? Math.min(1, Math.max(0, opts.valueAreaShare)) : DEFAULT_VALUE_AREA;
  const valueArea = findValueArea(rows, pocIndex, totalVolume * share);
  return {
    rows,
    pocIndex,
    pocPrice: (rows[pocIndex].low + rows[pocIndex].high) / 2,
    valueArea,
    valueAreaPrices: { low: rows[valueArea.from].low, high: rows[valueArea.to].high },
    totalVolume,
    barsUsed: used.length,
  };
}

/**
 * The standard market-profile value area: start at the busiest row and keep
 * adding whichever neighbouring PAIR of rows (two above, or two below) holds
 * more volume, until the target share is reached. Always contiguous, always
 * contains the point of control.
 */
export function findValueArea(rows: readonly ProfileRow[], poc: number, target: number): { from: number; to: number } {
  let from = poc, to = poc;
  let sum = rows[poc].total;
  const at = (i: number) => (i >= 0 && i < rows.length ? rows[i].total : 0);
  while (sum < target && (from > 0 || to < rows.length - 1)) {
    const above = at(to + 1) + at(to + 2);
    const below = at(from - 1) + at(from - 2);
    const canUp = to < rows.length - 1, canDown = from > 0;
    if (canUp && (!canDown || above >= below)) {
      const take = Math.min(2, rows.length - 1 - to);
      for (let k = 1; k <= take; k++) sum += rows[to + k].total;
      to += take;
    } else {
      const take = Math.min(2, from);
      for (let k = 1; k <= take; k++) sum += rows[from - k].total;
      from -= take;
    }
  }
  return { from, to };
}

/** The text shown in the chart's readout. */
export function summarizeProfile(p: VolumeProfile): string | null {
  if (p.pocPrice === null || !p.valueAreaPrices) return null;
  const f = (n: number) => n.toFixed(2);
  return `${p.barsUsed} bars · POC ${f(p.pocPrice)} · value area ${f(p.valueAreaPrices.low)}–${f(p.valueAreaPrices.high)}`;
}
