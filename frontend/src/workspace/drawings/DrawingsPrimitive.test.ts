import { describe, expect, it, vi } from "vitest";
import { DrawingsPrimitive, paintAll, paintShape, type PaintContext } from "./DrawingsPrimitive";
import { layoutDrawing, type Plot } from "./geometry";
import type { Drawing } from "./model";

// ── A recording stand-in for a canvas ───────────────────────────────────────
function fakeCtx() {
  const calls: string[] = [];
  const strokes: { color: string; width: number; dash: number[]; alpha: number }[] = [];
  const texts: string[] = [];
  const arcs: { x: number; y: number }[] = [];
  const fills: { x: number; y: number; w: number; h: number; alpha: number }[] = [];
  let dash: number[] = [];
  const st = { fillStyle: "", strokeStyle: "", lineWidth: 1, globalAlpha: 1, font: "", textAlign: "left", textBaseline: "alphabetic", lineCap: "butt" } as Record<string, unknown>;
  const ctx = {
    save: () => calls.push("save"), restore: () => calls.push("restore"),
    beginPath: () => calls.push("begin"), moveTo: () => calls.push("move"), lineTo: () => calls.push("line"),
    rect: () => calls.push("rect"), clip: () => calls.push("clip"), fill: () => calls.push("fill"),
    stroke: () => { calls.push("stroke"); strokes.push({ color: String(st.strokeStyle), width: Number(st.lineWidth), dash: [...dash], alpha: Number(st.globalAlpha) }); },
    fillRect: (x: number, y: number, w: number, h: number) => { calls.push("fillRect"); fills.push({ x, y, w, h, alpha: Number(st.globalAlpha) }); },
    arc: (x: number, y: number) => { calls.push("arc"); arcs.push({ x, y }); },
    fillText: (t: string) => { texts.push(t); },
    setLineDash: (d: number[]) => { dash = d; },
  } as Record<string, unknown>;
  for (const k of Object.keys(st)) Object.defineProperty(ctx, k, { get: () => st[k], set: (v) => { st[k] = v; } });
  return { ctx: ctx as unknown as PaintContext, calls, strokes, texts, arcs, fills };
}

const plot: Plot = { w: 800, h: 500 };
const toX = (t: number) => t, toY = (p: number) => 1000 - p;
const mk = (kind: Drawing["kind"], points: Drawing["points"], id = "d", color = "#7daaff", width: Drawing["width"] = 2): Drawing => ({ id, kind, points, color, width });
const A = { time: 100, price: 700 }, B = { time: 300, price: 800 };
const shape = (d: Drawing) => layoutDrawing(d, toX, toY, plot)!;
const plain = { selected: false, hover: false, preview: false };

describe("paintShape", () => {
  it("strokes a trend line in its own colour and width", () => {
    const { ctx, strokes } = fakeCtx();
    paintShape(ctx, shape(mk("trend", [A, B], "t", "#f2b84b", 3)), plain);
    expect(strokes).toHaveLength(1);
    expect(strokes[0]).toMatchObject({ color: "#f2b84b", width: 3, dash: [], alpha: 1 });
  });
  it("shows handles only when selected, one per anchor, at the anchors", () => {
    const a = fakeCtx(); paintShape(a.ctx, shape(mk("trend", [A, B])), plain);
    expect(a.arcs).toHaveLength(0);
    const b = fakeCtx(); paintShape(b.ctx, shape(mk("trend", [A, B])), { ...plain, selected: true });
    expect(b.arcs).toEqual([{ x: 100, y: 300 }, { x: 300, y: 200 }]);
  });
  it("a hovered, unselected drawing is one pixel thicker", () => {
    const { ctx, strokes } = fakeCtx();
    paintShape(ctx, shape(mk("trend", [A, B], "t", "#7daaff", 2)), { ...plain, hover: true });
    expect(strokes[0].width).toBe(3);
  });
  it("a preview is dashed and lighter", () => {
    const { ctx, strokes } = fakeCtx();
    paintShape(ctx, shape(mk("trend", [A, B])), { ...plain, preview: true });
    expect(strokes[0].dash.length).toBeGreaterThan(0);
    expect(strokes[0].alpha).toBeLessThan(1);
  });
  it("a rectangle is filled lightly and outlined", () => {
    const { ctx, fills, strokes } = fakeCtx();
    paintShape(ctx, shape(mk("rect", [A, B])), plain);
    expect(fills).toHaveLength(1);
    expect(fills[0]).toMatchObject({ x: 100, y: 200, w: 200, h: 100 });
    expect(fills[0].alpha).toBeLessThan(0.3);
    expect(strokes).toHaveLength(1);
  });
  it("a fib draws a line and a label for every level, plus a dashed connector", () => {
    const { ctx, strokes, texts } = fakeCtx();
    paintShape(ctx, shape(mk("fib", [A, B])), plain);
    expect(texts).toHaveLength(7);
    expect(texts[0]).toMatch(/^0%  700\.00$/);
    expect(texts).toContain("61.8%  761.80");
    expect(strokes).toHaveLength(1 + 7);
    expect(strokes[0].dash.length).toBeGreaterThan(0); // the connector
    expect(strokes[1].dash).toEqual([]);               // level lines are solid
  });
  it("a horizontal line is labelled with its price; a vertical line has no label", () => {
    const h = fakeCtx(); paintShape(h.ctx, shape(mk("hline", [A])), plain);
    expect(h.texts).toEqual(["700.00"]);
    const v = fakeCtx(); paintShape(v.ctx, shape(mk("vline", [A])), plain);
    expect(v.texts).toEqual([]);
  });
  it("leaves the canvas state as it found it", () => {
    const { ctx, calls } = fakeCtx();
    paintShape(ctx, shape(mk("fib", [A, B])), { selected: true, hover: false, preview: true });
    expect(calls.filter((c) => c === "save")).toHaveLength(calls.filter((c) => c === "restore").length);
  });
});

describe("paintAll", () => {
  const state = (over: Partial<Parameters<typeof paintAll>[1]> = {}) => ({ drawings: [], selectedId: null, hoverId: null, preview: null, ...over });
  const layout = (d: Drawing) => layoutDrawing(d, toX, toY, plot);
  it("clips to the plot BEFORE painting, so nothing can spill over the axes", () => {
    const { ctx, calls } = fakeCtx();
    paintAll(ctx, state({ drawings: [mk("trend", [A, B])] }), plot, layout);
    expect(calls.indexOf("clip")).toBeGreaterThan(-1);
    expect(calls.indexOf("clip")).toBeLessThan(calls.indexOf("stroke"));
  });
  it("paints every drawing, marks only the selected one with handles, and paints the preview last", () => {
    const { ctx, arcs, strokes } = fakeCtx();
    paintAll(ctx, state({
      drawings: [mk("trend", [A, B], "one"), mk("trend", [A, { time: 500, price: 900 }], "two")],
      selectedId: "two",
      preview: mk("rect", [A, B], "preview"),
    }), plot, layout);
    expect(arcs).toHaveLength(2); // only "two"
    expect(strokes).toHaveLength(2 + 2 + 1); // two lines, the selected one's two handle rings, and the preview outline
    expect(strokes[strokes.length - 1].dash.length).toBeGreaterThan(0); // the preview is painted last
  });
  it("skips a drawing whose position cannot be worked out, and paints the rest", () => {
    const { ctx, strokes } = fakeCtx();
    const picky = (d: Drawing) => (d.id === "bad" ? null : layout(d));
    paintAll(ctx, state({ drawings: [mk("trend", [A, B], "bad"), mk("trend", [A, B], "ok")] }), plot, picky);
    expect(strokes).toHaveLength(1);
  });
  it("paints nothing for an empty state", () => {
    const { ctx, strokes, fills } = fakeCtx();
    paintAll(ctx, state(), plot, layout);
    expect(strokes).toHaveLength(0);
    expect(fills).toHaveLength(0);
  });
});

// ── The bridge to the chart, with a chart whose scales are known ────────────
const T0 = 1_000_000, STEP = 3600;
const bars = Array.from({ length: 20 }, (_, i) => ({ timeSec: T0 + i * STEP, open: 100 + i, high: 110 + i, low: 95 + i, close: 105 + i }));

function fakeChart() {
  // 10 px per bar starting at x=0; price p is at y = 500 - p (so price 400 is at y=100)
  const timeScale = {
    logicalToCoordinate: (l: number) => l * 10,
    coordinateToLogical: (x: number) => x / 10,
    width: () => 800,
  };
  const chart = { timeScale: () => timeScale, panes: () => [{ getHeight: () => 500 }] };
  const series = { priceToCoordinate: (p: number) => 500 - p, coordinateToPrice: (y: number) => 500 - y };
  const prim = new DrawingsPrimitive();
  prim.attached({ chart: chart as never, series: series as never, requestUpdate: vi.fn(), horzScaleBehavior: {} as never });
  prim.setBars(bars);
  return prim;
}

describe("DrawingsPrimitive: time and price to pixels and back", () => {
  it("maps a bar's time to its x and a price to its y", () => {
    const p = fakeChart();
    expect(p.toX(T0 + 5 * STEP)).toBe(50);
    expect(p.toX(T0 + 5.5 * STEP)).toBe(55); // between bars
    expect(p.toX(T0 + 25 * STEP)).toBe(250); // the future, past the last bar
    expect(p.toY(400)).toBe(100);
  });
  it("round-trips a pixel through an anchor and back", () => {
    const p = fakeChart();
    for (const pt of [{ x: 120, y: 80 }, { x: 33, y: 410 }, { x: 799, y: 1 }]) {
      const a = p.toAnchor(pt, false)!;
      const back = p.toPixel(a)!;
      expect(back.x).toBeCloseTo(pt.x, 6);
      expect(back.y).toBeCloseTo(pt.y, 6);
    }
  });
  it("a click in the future past the last bar becomes a time after the last bar", () => {
    const p = fakeChart();
    const a = p.toAnchor({ x: 260, y: 100 }, false)!;
    expect(a.time).toBeGreaterThan(bars[bars.length - 1].timeSec);
  });
  it("refuses points outside the plot (over the price or time axis)", () => {
    const p = fakeChart();
    expect(p.toAnchor({ x: 801, y: 100 }, false)).toBeNull();
    expect(p.toAnchor({ x: 100, y: 501 }, false)).toBeNull();
    expect(p.toAnchor({ x: -1, y: 100 }, false)).toBeNull();
    expect(p.inPlot({ x: 800, y: 500 })).toBe(true);
    expect(p.inPlot({ x: 800.5, y: 500 })).toBe(false);
  });
  it("the magnet pulls a point onto the nearest bar and its high", () => {
    const p = fakeChart();
    // bar 5 is at x=50; its high is 115 -> y = 385
    const a = p.toAnchor({ x: 53, y: 387 }, true)!;
    expect(a.time).toBeCloseTo(T0 + 5 * STEP, 3);
    expect(a.price).toBe(115);
    const free = p.toAnchor({ x: 53, y: 387 }, false)!;
    expect(free.price).toBeCloseTo(113, 6); // without the magnet it is exactly where you clicked
  });
  it("gives up cleanly before the chart is ready", () => {
    const p = new DrawingsPrimitive();
    expect(p.toX(1)).toBeNull();
    expect(p.toY(1)).toBeNull();
    expect(p.plot()).toBeNull();
    expect(p.toAnchor({ x: 1, y: 1 }, false)).toBeNull();
    expect(p.shapes()).toEqual([]);
    expect(() => p.setState({ drawings: [], selectedId: null, hoverId: null, preview: null })).not.toThrow();
  });
  it("shapes() lays the drawings out through the chart's scales, for picking", () => {
    const p = fakeChart();
    const d: Drawing = { id: "t", kind: "trend", color: "#7daaff", width: 2, points: [{ time: T0 + 2 * STEP, price: 400 }, { time: T0 + 6 * STEP, price: 450 }] };
    p.setState({ drawings: [d], selectedId: null, hoverId: null, preview: null });
    const [s] = p.shapes();
    expect(s.anchors).toEqual([{ x: 20, y: 100 }, { x: 60, y: 50 }]);
  });
  it("detach drops the chart, so a late call cannot touch a removed chart", () => {
    const p = fakeChart();
    p.detached();
    expect(p.toX(T0)).toBeNull();
    expect(p.plot()).toBeNull();
    expect(() => p.setBars(bars)).not.toThrow();
  });
  it("draws behind nothing: it paints on top, and renders through the chart's own canvas space", () => {
    const p = fakeChart();
    expect(p.paneViews()[0].zOrder?.()).toBe("top");
    const d: Drawing = { id: "t", kind: "trend", color: "#7daaff", width: 2, points: [{ time: T0 + 2 * STEP, price: 400 }, { time: T0 + 6 * STEP, price: 450 }] };
    p.setState({ drawings: [d], selectedId: null, hoverId: null, preview: null });
    const { ctx, strokes } = fakeCtx();
    const target = { useMediaCoordinateSpace: (cb: (s: unknown) => void) => cb({ context: ctx, mediaSize: { width: 800, height: 500 } }) };
    p.paneViews()[0].renderer()!.draw(target as never);
    expect(strokes).toHaveLength(1);
  });
  it("its renderer paints nothing when detached", () => {
    const p = new DrawingsPrimitive();
    const target = { useMediaCoordinateSpace: vi.fn() };
    p.paneViews()[0].renderer()!.draw(target as never);
    expect(target.useMediaCoordinateSpace).not.toHaveBeenCalled();
  });
});
