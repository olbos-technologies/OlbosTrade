import { describe, expect, it } from "vitest";
import { distToSegment, hitTest, layoutDrawing, type Plot } from "./geometry";
import { FIB_LEVELS, type Drawing } from "./model";
import { snapPoint } from "./snap";

// 1 second = 1 pixel, and price p is at y = 1000 - p
const toX = (t: number) => t;
const toY = (p: number) => 1000 - p;
const plot: Plot = { w: 800, h: 500 };
const mk = (kind: Drawing["kind"], points: Drawing["points"], id = "d"): Drawing => ({ id, kind, points, color: "#7daaff", width: 2 });
const A = { time: 100, price: 700 }, B = { time: 300, price: 800 }; // pixels (100,300) and (300,200)

describe("layout", () => {
  it("a trend line is one segment between its anchors", () => {
    const s = layoutDrawing(mk("trend", [A, B]), toX, toY, plot)!;
    expect(s.segments).toEqual([[{ x: 100, y: 300 }, { x: 300, y: 200 }]]);
    expect(s.anchors).toEqual([{ x: 100, y: 300 }, { x: 300, y: 200 }]);
  });
  it("a horizontal line spans the whole plot at its price, with its handle kept on screen", () => {
    const s = layoutDrawing(mk("hline", [{ time: 5000, price: 700 }]), toX, toY, plot)!;
    expect(s.segments[0]).toEqual([{ x: 0, y: 300 }, { x: 800, y: 300 }]);
    expect(s.anchors[0].x).toBeLessThanOrEqual(800); // anchor time is off to the right, handle is clamped in
    expect(s.anchors[0].y).toBe(300);
  });
  it("a vertical line spans the whole plot height at its time", () => {
    const s = layoutDrawing(mk("vline", [{ time: 250, price: 100 }]), toX, toY, plot)!;
    expect(s.segments[0]).toEqual([{ x: 250, y: 0 }, { x: 250, y: 500 }]);
  });
  it("a rectangle is normalised however its corners were dragged", () => {
    const s = layoutDrawing(mk("rect", [B, A]), toX, toY, plot)!;
    expect(s.rect).toEqual({ x: 100, y: 200, w: 200, h: 100 });
  });
  it("fib levels sit at the right prices between point 1 (0%) and point 2 (100%)", () => {
    const s = layoutDrawing(mk("fib", [A, B]), toX, toY, plot)!;
    expect(s.levels!.map((l) => l.level)).toEqual([...FIB_LEVELS]);
    const at = (lv: number) => s.levels!.find((l) => l.level === lv)!;
    expect(at(0).price).toBe(700);
    expect(at(1).price).toBe(800);
    expect(at(0.5).price).toBe(750);
    expect(at(0.5).y).toBe(250);
    expect(at(0.618).price).toBeCloseTo(761.8, 6);
    expect(at(0.5).x1).toBe(100);
    expect(at(0.5).x2).toBe(300);
    expect(at(0.236).label).toMatch(/^23\.6%  \d+\.\d{2}$/);
  });
  it("fib works for a downward move too", () => {
    const s = layoutDrawing(mk("fib", [B, A]), toX, toY, plot)!;
    expect(s.levels!.find((l) => l.level === 0.5)!.price).toBe(750);
    expect(s.levels!.find((l) => l.level === 0)!.price).toBe(800);
  });
  it("returns null when a position cannot be converted, instead of guessing", () => {
    expect(layoutDrawing(mk("trend", [A, B]), () => null, toY, plot)).toBeNull();
    expect(layoutDrawing(mk("trend", [A, B]), toX, () => null, plot)).toBeNull();
    expect(layoutDrawing(mk("trend", [A, B]), () => NaN, toY, plot)).toBeNull();
  });
});

describe("distToSegment", () => {
  const a = { x: 0, y: 0 }, b = { x: 10, y: 0 };
  it("measures to the nearest point on the segment, not the infinite line", () => {
    expect(distToSegment({ x: 5, y: 3 }, a, b)).toBe(3);
    expect(distToSegment({ x: -4, y: 3 }, a, b)).toBe(5); // beyond the end: distance to the endpoint
    expect(distToSegment({ x: 14, y: 3 }, a, b)).toBe(5);
  });
  it("copes with a zero-length segment", () => {
    expect(distToSegment({ x: 3, y: 4 }, a, a)).toBe(5);
  });
});

describe("hitTest", () => {
  const shapes = [
    layoutDrawing(mk("trend", [A, B], "t"), toX, toY, plot)!,
    layoutDrawing(mk("rect", [{ time: 400, price: 600 }, { time: 600, price: 700 }], "r"), toX, toY, plot)!,
    layoutDrawing(mk("hline", [{ time: 50, price: 650 }], "h"), toX, toY, plot)!,
    layoutDrawing(mk("fib", [{ time: 100, price: 400 }, { time: 300, price: 500 }], "f"), toX, toY, plot)!,
  ];
  it("finds a line's body near it and not far from it", () => {
    expect(hitTest(shapes, { x: 200, y: 250 })).toEqual({ id: "t", part: "body" });
    expect(hitTest(shapes, { x: 200, y: 250 + 30 })).toBeNull();
  });
  it("a rectangle is picked by its edges, not its inside, so the chart can still pan there", () => {
    expect(hitTest(shapes, { x: 400, y: 320 })).toEqual({ id: "r", part: "body" }); // left edge
    expect(hitTest(shapes, { x: 500, y: 320 })).toBeNull(); // inside, clear of every line
  });
  it("a horizontal line is picked anywhere along it", () => {
    expect(hitTest(shapes, { x: 700, y: 350 })).toEqual({ id: "h", part: "body" });
  });
  it("a fib is picked on a level line, only within its width", () => {
    const y50 = 1000 - 450;
    expect(hitTest(shapes, { x: 200, y: y50 })).toEqual({ id: "f", part: "body" });
    expect(hitTest(shapes, { x: 700, y: y50 })).toBeNull();
  });
  it("only the selected drawing's handles can be grabbed", () => {
    expect(hitTest(shapes, { x: 100, y: 300 }, { selectedId: "t" })).toEqual({ id: "t", part: "handle", index: 0 });
    expect(hitTest(shapes, { x: 300, y: 200 }, { selectedId: "t" })).toEqual({ id: "t", part: "handle", index: 1 });
    expect(hitTest(shapes, { x: 100, y: 300 })).toEqual({ id: "t", part: "body" }); // not selected: a body hit only
  });
  it("the topmost drawing wins where two overlap", () => {
    const over = [
      layoutDrawing(mk("hline", [{ time: 1, price: 700 }], "under"), toX, toY, plot)!,
      layoutDrawing(mk("hline", [{ time: 1, price: 700 }], "over"), toX, toY, plot)!,
    ];
    expect(hitTest(over, { x: 100, y: 300 })).toEqual({ id: "over", part: "body" });
  });
  it("a selected drawing's handle beats a different drawing's line under it", () => {
    const two = [
      layoutDrawing(mk("trend", [A, B], "sel"), toX, toY, plot)!,
      layoutDrawing(mk("hline", [{ time: 1, price: 700 }], "line"), toX, toY, plot)!, // passes through (100,300)
    ];
    expect(hitTest(two, { x: 100, y: 300 }, { selectedId: "sel" })).toEqual({ id: "sel", part: "handle", index: 0 });
  });
  it("nothing is hit in empty space or with no shapes", () => {
    expect(hitTest(shapes, { x: 700, y: 50 })).toBeNull();
    expect(hitTest([], { x: 1, y: 1 })).toBeNull();
  });
});

describe("magnet (snapPoint)", () => {
  const bars = [{ open: 100, high: 110, low: 95, close: 105 }, { open: 105, high: 120, low: 100, close: 118 }];
  const base = { bars, xOfLogical: (l: number) => 100 + l * 50, yOfPrice: (p: number) => 1000 - p };
  it("snaps to the nearest bar and its nearest OHLC price when close enough", () => {
    const r = snapPoint({ ...base, x: 153, y: 1000 - 119, logical: 1.06, price: 119 });
    expect(r.logical).toBe(1);
    expect(r.price).toBe(120); // the high, 1px away, beats the close
  });
  it("leaves a point alone when it is not close", () => {
    const r = snapPoint({ ...base, x: 130, y: 1000 - 150, logical: 0.6, price: 150 });
    expect(r.logical).toBe(0.6);
    expect(r.price).toBe(150);
  });
  it("snaps one axis and not the other when only one is near", () => {
    const r = snapPoint({ ...base, x: 150, y: 1000 - 200, logical: 1, price: 200 });
    expect(r.logical).toBe(1);
    expect(r.price).toBe(200);
  });
  it("picks the CLOSEST of open, high, low and close", () => {
    const r = snapPoint({ ...base, x: 100, y: 1000 - 96, logical: 0, price: 96 });
    expect(r.price).toBe(95);
  });
  it("copes with no bars and with unconvertible positions", () => {
    expect(snapPoint({ ...base, bars: [], x: 1, y: 1, logical: 3.3, price: 7 })).toEqual({ logical: 3.3, price: 7 });
    const r = snapPoint({ ...base, xOfLogical: () => null, yOfPrice: () => null, x: 100, y: 900, logical: 0.2, price: 100 });
    expect(r).toEqual({ logical: 0.2, price: 100 });
  });
});
