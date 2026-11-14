import { describe, expect, it } from "vitest";
import {
  DEFAULT_BINS, MAX_BINS, MIN_BINS, computeVolumeProfile, emptyProfile, findValueArea, summarizeProfile, usableBar,
  type ProfileBar,
} from "./volumeProfile";

const bar = (low: number, high: number, volume: number, open = low, close = high): ProfileBar => ({ open, high, low, close, volume });
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const close = (a: number, b: number, eps = 1e-6) => Math.abs(a - b) <= eps * Math.max(1, Math.abs(a), Math.abs(b));

describe("computeVolumeProfile: conservation", () => {
  it("the rows add up to exactly the bars' volume", () => {
    const bars = [bar(10, 12, 1000), bar(11, 15, 2500), bar(9.5, 10.5, 400), bar(14, 14.2, 800)];
    const p = computeVolumeProfile(bars);
    expect(close(sum(p.rows.map((r) => r.total)), 4700)).toBe(true);
    expect(close(p.totalVolume, 4700)).toBe(true);
    expect(p.barsUsed).toBe(4);
  });
  it("holds for many random bars and any bin count", () => {
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const bars = Array.from({ length: 400 }, () => { const c = 50 + rnd() * 20; return bar(c - rnd() * 2, c + rnd() * 2, Math.round(rnd() * 1e6)); });
    const want = sum(bars.map((b) => b.volume));
    for (const bins of [MIN_BINS, 13, DEFAULT_BINS, 97, MAX_BINS]) {
      const p = computeVolumeProfile(bars, { bins });
      expect(close(p.totalVolume, want, 1e-9), `bins=${bins}`).toBe(true);
      expect(p.rows).toHaveLength(bins);
    }
  });
  it("rows tile the price range with no gaps or overlaps", () => {
    const p = computeVolumeProfile([bar(10, 20, 100), bar(12, 18, 50)], { bins: 20 });
    expect(p.rows[0].low).toBe(10);
    expect(p.rows[p.rows.length - 1].high).toBe(20);
    for (let i = 1; i < p.rows.length; i++) expect(close(p.rows[i].low, p.rows[i - 1].high, 1e-12)).toBe(true);
  });
});

describe("computeVolumeProfile: how a bar's volume is spread", () => {
  it("spreads one bar evenly across the bins its range covers", () => {
    // Window is exactly this bar, 4 bins of 1.0 each, so each gets a quarter.
    const p = computeVolumeProfile([bar(100, 108, 800)], { bins: 8 });
    for (const r of p.rows) expect(close(r.total, 100)).toBe(true);
  });
  it("puts volume only in the bins a narrow bar touches", () => {
    const p = computeVolumeProfile([bar(0, 100, 0), bar(40, 50, 1000)], { bins: 10 });
    const touched = p.rows.filter((r) => r.total > 0);
    expect(touched.length).toBeGreaterThanOrEqual(1);
    for (const r of touched) { expect(r.high).toBeGreaterThan(40); expect(r.low).toBeLessThan(50); }
    expect(close(p.totalVolume, 1000)).toBe(true);
  });
  it("a bar with no range puts all its volume in one bin", () => {
    const p = computeVolumeProfile([bar(10, 20, 0), bar(15, 15, 500)], { bins: 10 });
    expect(p.rows.filter((r) => r.total > 0)).toHaveLength(1);
    expect(close(p.totalVolume, 500)).toBe(true);
  });
  it("a bar at the very top of the range lands in the last bin, not off the end", () => {
    const p = computeVolumeProfile([bar(10, 20, 0), bar(20, 20, 300)], { bins: 10 });
    expect(p.rows[p.rows.length - 1].total).toBe(300);
  });
  it("a window where nothing moved is a single level", () => {
    const p = computeVolumeProfile([bar(50, 50, 100), bar(50, 50, 250)]);
    expect(p.rows).toHaveLength(1);
    expect(p.rows[0].total).toBe(350);
    expect(p.pocPrice).toBe(50);
    expect(p.valueAreaPrices).toEqual({ low: 50, high: 50 });
  });
});

describe("computeVolumeProfile: point of control and value area", () => {
  const heavyMiddle = [bar(10, 20, 100), bar(14, 16, 5000), bar(10, 20, 100)];
  it("the point of control is the busiest price", () => {
    const p = computeVolumeProfile(heavyMiddle, { bins: 20 });
    expect(p.pocPrice).not.toBeNull();
    expect(p.pocPrice!).toBeGreaterThan(14);
    expect(p.pocPrice!).toBeLessThan(16);
    expect(p.rows[p.pocIndex].total).toBe(Math.max(...p.rows.map((r) => r.total)));
  });
  it("the value area is contiguous, contains the point of control and holds at least 70%", () => {
    const p = computeVolumeProfile(heavyMiddle, { bins: 20 });
    const { from, to } = p.valueArea!;
    expect(from).toBeLessThanOrEqual(p.pocIndex);
    expect(to).toBeGreaterThanOrEqual(p.pocIndex);
    const inside = sum(p.rows.slice(from, to + 1).map((r) => r.total));
    expect(inside).toBeGreaterThanOrEqual(0.7 * p.totalVolume - 1e-6);
  });
  it("a larger share needs a wider or equal value area", () => {
    const bars = Array.from({ length: 60 }, (_, i) => bar(100 + i * 0.3, 102 + i * 0.3, 1000 + (i % 7) * 300));
    const w = (share: number) => { const v = computeVolumeProfile(bars, { valueAreaShare: share }).valueArea!; return v.to - v.from; };
    expect(w(0.5)).toBeLessThanOrEqual(w(0.7));
    expect(w(0.7)).toBeLessThanOrEqual(w(0.95));
    expect(w(1)).toBeGreaterThanOrEqual(w(0.95));
  });
  it("a share of 1 covers every row that has volume", () => {
    const p = computeVolumeProfile([bar(10, 20, 100), bar(12, 19, 300)], { valueAreaShare: 1 });
    const { from, to } = p.valueArea!;
    const inside = sum(p.rows.slice(from, to + 1).map((r) => r.total));
    expect(close(inside, p.totalVolume)).toBe(true);
  });
  it("findValueArea at the edge of the range does not run past it", () => {
    const rows = [100, 50, 10, 5].map((t, i) => ({ low: i, high: i + 1, total: t }));
    expect(findValueArea(rows, 0, 140)).toEqual({ from: 0, to: 2 });
    expect(findValueArea(rows, 0, 1e9)).toEqual({ from: 0, to: 3 });
    expect(findValueArea(rows, 3, 1e9)).toEqual({ from: 0, to: 3 });
  });
});

describe("computeVolumeProfile: the window and bad input", () => {
  const bars = [bar(10, 11, 100), bar(20, 21, 200), bar(30, 31, 300), bar(40, 41, 400)];
  it("from and to select a window, inclusive", () => {
    const p = computeVolumeProfile(bars, { from: 1, to: 2 });
    expect(p.barsUsed).toBe(2);
    expect(close(p.totalVolume, 500)).toBe(true);
    expect(p.rows[0].low).toBe(20);
    expect(p.rows[p.rows.length - 1].high).toBe(31);
  });
  it("out-of-range and fractional window edges are clamped, never crash", () => {
    expect(computeVolumeProfile(bars, { from: -5, to: 99 }).barsUsed).toBe(4);
    expect(computeVolumeProfile(bars, { from: 1.9, to: 2.2 }).barsUsed).toBe(2);
    expect(computeVolumeProfile(bars, { from: 3, to: 1 })).toEqual(emptyProfile());
    expect(computeVolumeProfile(bars, { from: NaN, to: NaN }).barsUsed).toBe(4);
  });
  it("skips unusable bars instead of poisoning the profile", () => {
    const dirty: ProfileBar[] = [
      bar(10, 12, 100),
      { open: NaN, high: NaN, low: NaN, close: NaN, volume: 500 },
      { open: 1, high: 5, low: 9, close: 2, volume: 500 },          // high below low
      { open: 1, high: 5, low: 4, close: 2, volume: -50 },          // negative volume
      { open: 1, high: Infinity, low: 4, close: 2, volume: 10 },
      bar(11, 13, 200),
    ];
    const p = computeVolumeProfile(dirty);
    expect(p.barsUsed).toBe(2);
    expect(close(p.totalVolume, 300)).toBe(true);
    expect(p.rows.every((r) => Number.isFinite(r.total) && Number.isFinite(r.low) && Number.isFinite(r.high))).toBe(true);
  });
  it("empty, all-invalid and zero-volume input give an empty or volumeless profile", () => {
    expect(computeVolumeProfile([])).toEqual(emptyProfile());
    expect(computeVolumeProfile([{ open: NaN, high: NaN, low: NaN, close: NaN, volume: NaN }])).toEqual(emptyProfile());
    const zero = computeVolumeProfile([bar(10, 12, 0), bar(11, 14, 0)]);
    expect(zero.pocIndex).toBe(-1);
    expect(zero.pocPrice).toBeNull();
    expect(zero.valueArea).toBeNull();
    expect(zero.totalVolume).toBe(0);
    expect(summarizeProfile(zero)).toBeNull();
  });
  it("clamps the bin count to a safe range and ignores junk", () => {
    expect(computeVolumeProfile(bars, { bins: 1 }).rows).toHaveLength(MIN_BINS);
    expect(computeVolumeProfile(bars, { bins: 99999 }).rows).toHaveLength(MAX_BINS);
    expect(computeVolumeProfile(bars, { bins: NaN }).rows).toHaveLength(DEFAULT_BINS);
    expect(computeVolumeProfile(bars, { bins: 12.9 }).rows).toHaveLength(12);
  });
  it("does not mutate its input", () => {
    const copy = JSON.parse(JSON.stringify(bars));
    computeVolumeProfile(bars, { bins: 20 });
    expect(bars).toEqual(copy);
  });
  it("handles a large window quickly", () => {
    const big = Array.from({ length: 5000 }, (_, i) => bar(100 + (i % 50), 101 + (i % 50) + 2, 1000 + (i % 13)));
    const t0 = performance.now();
    const p = computeVolumeProfile(big, { bins: 100 });
    expect(performance.now() - t0).toBeLessThan(200);
    expect(p.barsUsed).toBe(5000);
  });
});

describe("usableBar and summarizeProfile", () => {
  it("usableBar rejects null and malformed bars", () => {
    expect(usableBar(null)).toBe(false);
    expect(usableBar(undefined)).toBe(false);
    expect(usableBar(bar(1, 2, 3))).toBe(true);
    expect(usableBar(bar(2, 2, 0))).toBe(true);
    expect(usableBar(bar(3, 2, 1))).toBe(false);
  });
  it("the summary states the bar count, point of control and value area", () => {
    const text = summarizeProfile(computeVolumeProfile([bar(10, 20, 100), bar(14, 16, 5000)], { bins: 20 }))!;
    expect(text).toMatch(/^2 bars · POC \d+\.\d{2} · value area \d+\.\d{2}–\d+\.\d{2}$/);
  });
});
