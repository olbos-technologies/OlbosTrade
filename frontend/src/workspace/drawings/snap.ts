/**
 * "Magnet": pull a new or moved point onto the nearest bar and its nearest price
 * (open, high, low or close), when the pointer is close enough. Pure: the caller
 * supplies the conversions, so it is tested without a chart.
 */

export interface SnapBar { open: number; high: number; low: number; close: number }

export interface SnapInput {
  /** Pointer position and its bar position and price before snapping. */
  x: number;
  y: number;
  logical: number;
  price: number;
  bars: readonly SnapBar[];
  /** Pixel position of a bar index and of a price. */
  xOfLogical: (logical: number) => number | null;
  yOfPrice: (price: number) => number | null;
  /** Snap only when within this many pixels. */
  radius?: number;
}

export const SNAP_RADIUS = 12;

export function snapPoint(i: SnapInput): { logical: number; price: number } {
  const radius = i.radius ?? SNAP_RADIUS;
  let logical = i.logical, price = i.price;
  if (!i.bars.length) return { logical, price };

  const idx = Math.min(i.bars.length - 1, Math.max(0, Math.round(i.logical)));
  const bx = i.xOfLogical(idx);
  if (bx !== null && Math.abs(bx - i.x) <= radius) logical = idx;

  const bar = i.bars[idx];
  let best: number | null = null, bestDist = Infinity;
  for (const candidate of [bar.open, bar.high, bar.low, bar.close]) {
    if (!Number.isFinite(candidate)) continue;
    const cy = i.yOfPrice(candidate);
    if (cy === null) continue;
    const dist = Math.abs(cy - i.y);
    if (dist < bestDist) { bestDist = dist; best = candidate; }
  }
  if (best !== null && bestDist <= radius) price = best;
  return { logical, price };
}
