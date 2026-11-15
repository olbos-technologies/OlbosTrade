import { describe, expect, it, vi } from "vitest";
import { MAX_WIDTH_SHARE, PROFILE_COLORS, VolumeProfilePrimitive, drawVolumeProfile, type ProfileContext } from "./VolumeProfilePrimitive";
import { computeVolumeProfile, emptyProfile } from "./volumeProfile";

function fakeContext() {
  const rects: { x: number; y: number; w: number; h: number; fill: string }[] = [];
  const lines: { from: [number, number]; to: [number, number]; stroke: string; dash: number[] }[] = [];
  let fill = "", stroke = "", dash: number[] = [], pen: [number, number] | null = null, pending: [number, number][] = [];
  const ctx = {
    save: vi.fn(), restore: vi.fn(), beginPath: () => { pending = []; },
    moveTo: (x: number, y: number) => { pending.push([x, y]); },
    lineTo: (x: number, y: number) => { pending.push([x, y]); },
    stroke: () => { if (pending.length >= 2) lines.push({ from: pending[0], to: pending[pending.length - 1], stroke, dash }); },
    setLineDash: (d: number[]) => { dash = d; },
    fillRect: (x: number, y: number, w: number, h: number) => { rects.push({ x, y, w, h, fill }); },
    set fillStyle(v: string) { fill = v; }, get fillStyle() { return fill; },
    set strokeStyle(v: string) { stroke = v; }, get strokeStyle() { return stroke; },
    lineWidth: 1,
  } as unknown as ProfileContext;
  void pen;
  return { ctx, rects, lines };
}

const bars = [
  { open: 10, high: 20, low: 10, close: 20, volume: 100 },
  { open: 14, high: 16, low: 14, close: 16, volume: 5000 },
  { open: 10, high: 20, low: 10, close: 20, volume: 100 },
];
const profile = computeVolumeProfile(bars, { bins: 20 });
// 10 -> bottom of a 200px pane, 20 -> top: y = 200 - (price-10)*20
const priceToY = (p: number) => 200 - (p - 10) * 20;

describe("drawVolumeProfile", () => {
  it("draws one bar per bin that has volume, anchored to the right edge", () => {
    const { ctx, rects } = fakeContext();
    drawVolumeProfile(ctx, profile, 1000, priceToY);
    expect(rects.length).toBe(profile.rows.filter((r) => r.total > 0).length);
    for (const r of rects) expect(r.x + r.w).toBeCloseTo(1000); // every bar ends at the pane's right edge
  });
  it("the busiest bin is the longest, at the maximum share of the width, and others scale to it", () => {
    const { ctx, rects } = fakeContext();
    drawVolumeProfile(ctx, profile, 1000, priceToY);
    const longest = Math.max(...rects.map((r) => r.w));
    expect(longest).toBeCloseTo(1000 * MAX_WIDTH_SHARE);
    const sorted = [...profile.rows].filter((r) => r.total > 0);
    const ratio = Math.min(...sorted.map((r) => r.total)) / profile.rows[profile.pocIndex].total;
    expect(Math.min(...rects.map((r) => r.w))).toBeCloseTo(Math.max(1, ratio * longest), 1);
    expect(longest).toBeLessThanOrEqual(1000 * MAX_WIDTH_SHARE + 1e-9);
  });
  it("each bar sits at its own price: higher prices are drawn higher on screen", () => {
    const { ctx, rects } = fakeContext();
    drawVolumeProfile(ctx, profile, 1000, priceToY);
    const idx = profile.rows.map((r, i) => (r.total > 0 ? i : -1)).filter((i) => i >= 0);
    for (let k = 1; k < rects.length; k++) expect(rects[k].y).toBeLessThan(rects[k - 1].y);
    const first = profile.rows[idx[0]];
    expect(rects[0].y).toBeCloseTo(priceToY(first.high));
  });
  it("value-area bins use the stronger colour and the rest the lighter one", () => {
    const { ctx, rects } = fakeContext();
    drawVolumeProfile(ctx, profile, 1000, priceToY);
    const fills = new Set(rects.map((r) => r.fill));
    expect(fills.has(PROFILE_COLORS.valueArea)).toBe(true);
    expect(fills.has(PROFILE_COLORS.bar)).toBe(true);
  });
  it("draws a dashed point-of-control line across the whole pane at the busiest price", () => {
    const { ctx, lines } = fakeContext();
    drawVolumeProfile(ctx, profile, 1000, priceToY);
    expect(lines).toHaveLength(1);
    const l = lines[0];
    expect(l.from[0]).toBe(0);
    expect(l.to[0]).toBe(1000);
    expect(l.stroke).toBe(PROFILE_COLORS.poc);
    expect(l.dash.length).toBeGreaterThan(0);
    const expectedY = priceToY(profile.pocPrice!);
    expect(Math.abs(l.from[1] - expectedY)).toBeLessThanOrEqual(1.5);
  });
  it("skips bins whose price is off the visible scale instead of drawing them in the wrong place", () => {
    const { ctx, rects, lines } = fakeContext();
    drawVolumeProfile(ctx, profile, 1000, (p) => (p > 15 ? null : priceToY(p)));
    expect(rects.length).toBeGreaterThan(0);
    expect(rects.length).toBeLessThan(profile.rows.filter((r) => r.total > 0).length);
    for (const r of rects) expect(Number.isFinite(r.y)).toBe(true);
    void lines;
    const none = fakeContext();
    drawVolumeProfile(none.ctx, profile, 1000, () => null);
    expect(none.rects).toHaveLength(0);
    expect(none.lines).toHaveLength(0);
  });
  it("draws nothing for an empty profile, zero width or a volumeless profile", () => {
    for (const [p, w] of [[emptyProfile(), 1000], [profile, 0], [profile, -5], [profile, NaN]] as const) {
      const { ctx, rects, lines } = fakeContext();
      drawVolumeProfile(ctx, p, w, priceToY);
      expect(rects).toHaveLength(0);
      expect(lines).toHaveLength(0);
    }
  });
  it("restores the canvas state it changed", () => {
    const { ctx } = fakeContext();
    drawVolumeProfile(ctx, profile, 1000, priceToY);
    expect((ctx.save as ReturnType<typeof vi.fn>).mock.calls.length).toBe((ctx.restore as ReturnType<typeof vi.fn>).mock.calls.length);
  });
});

describe("VolumeProfilePrimitive", () => {
  it("draws behind the candles and requests a redraw when the profile changes", () => {
    const prim = new VolumeProfilePrimitive();
    const requestUpdate = vi.fn();
    prim.attached({ chart: {} as never, series: {} as never, requestUpdate, horzScaleBehavior: {} as never });
    expect(prim.paneViews()).toHaveLength(1);
    expect(prim.paneViews()[0].zOrder?.()).toBe("bottom");
    prim.setProfile(profile);
    expect(requestUpdate).toHaveBeenCalledTimes(1);
    expect(prim.profile).toBe(profile);
    prim.setProfile(null);
    expect(requestUpdate).toHaveBeenCalledTimes(2);
  });
  it("after detach it holds no series and a late update does not throw", () => {
    const prim = new VolumeProfilePrimitive();
    prim.attached({ chart: {} as never, series: {} as never, requestUpdate: vi.fn(), horzScaleBehavior: {} as never });
    prim.detached();
    expect(prim.series).toBeNull();
    expect(() => prim.setProfile(profile)).not.toThrow();
  });
  it("its renderer draws through the chart's own price mapping", () => {
    const prim = new VolumeProfilePrimitive();
    const priceToCoordinate = vi.fn((p: number) => priceToY(p));
    prim.attached({ chart: {} as never, series: { priceToCoordinate } as never, requestUpdate: vi.fn(), horzScaleBehavior: {} as never });
    prim.setProfile(profile);
    const { ctx, rects } = fakeContext();
    const target = { useMediaCoordinateSpace: (cb: (s: unknown) => void) => cb({ context: ctx, mediaSize: { width: 800, height: 200 } }) };
    prim.paneViews()[0].renderer()!.draw(target as never);
    expect(priceToCoordinate).toHaveBeenCalled();
    expect(rects.length).toBeGreaterThan(0);
  });
  it("its renderer draws nothing when there is no profile or the primitive is detached", () => {
    const prim = new VolumeProfilePrimitive();
    const target = { useMediaCoordinateSpace: vi.fn() };
    prim.paneViews()[0].renderer()!.draw(target as never);
    expect(target.useMediaCoordinateSpace).not.toHaveBeenCalled();
  });
});
