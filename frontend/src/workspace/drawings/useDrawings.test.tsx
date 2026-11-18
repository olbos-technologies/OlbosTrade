import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartBar, ChartHandle } from "../../components/CandlestickChart";
import { useDrawings } from "./useDrawings";
import { makeDrawing } from "./model";
import type { Shape } from "./geometry";

afterEach(cleanup);
// Drawings are saved per symbol in localStorage, so one test's drawing must not appear in the next.
beforeEach(() => { window.localStorage.clear(); });

const bars: ChartBar[] = Array.from({ length: 10 }, (_, i) => ({
  timestamp: new Date(Date.UTC(2026, 8, 1, 14, 30) + i * 3600e3).toISOString(),
  open: 100, high: 105, low: 95, close: 102, volume: 1000,
}));

/** A stand-in chart handle that records what the hook does to it, and lets a test run the disposers. */
function fakeHandle() {
  const disposers = new Set<() => void>();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const series = { attachPrimitive: vi.fn(), detachPrimitive: vi.fn() };
  const handle = {
    chart: {}, series, container,
    onDispose: (fn: () => void) => { disposers.add(fn); return () => { disposers.delete(fn); }; },
  } as unknown as ChartHandle;
  return { handle, series, container, runDisposers: () => { [...disposers].forEach((fn) => fn()); disposers.clear(); } };
}

const run = (handle: ChartHandle | null, interactive = true) =>
  renderHook(() => useDrawings({ handle, bars, symbol: "AAPL", interactive, keyTarget: null }));

describe("useDrawings and the chart's lifetime", () => {
  it("puts one painting primitive on the chart when it appears", () => {
    const f = fakeHandle();
    run(f.handle);
    expect(f.series.attachPrimitive).toHaveBeenCalledTimes(1);
    expect(f.series.detachPrimitive).not.toHaveBeenCalled();
  });

  it("lets go when the chart's own disposers run (BEFORE it is removed), exactly once", () => {
    const f = fakeHandle();
    const { unmount } = run(f.handle);
    act(() => f.runDisposers());
    expect(f.series.detachPrimitive).toHaveBeenCalledTimes(1);
    unmount(); // React's own cleanup afterwards must not detach a second time
    expect(f.series.detachPrimitive).toHaveBeenCalledTimes(1);
  });

  it("detaches on unmount too, if the chart never ran its disposers", () => {
    const f = fakeHandle();
    const { unmount } = run(f.handle);
    unmount();
    expect(f.series.detachPrimitive).toHaveBeenCalledTimes(1);
  });

  it("removes its mouse listeners the moment the chart's disposers run, not later", () => {
    const f = fakeHandle();
    const removed = vi.spyOn(f.container, "removeEventListener");
    const { unmount } = run(f.handle);
    expect(removed).not.toHaveBeenCalled();
    act(() => f.runDisposers()); // before React has unmounted anything
    const types = removed.mock.calls.map((c) => c[0]);
    expect(types).toEqual(expect.arrayContaining(["mousedown", "mousemove", "mouseleave"]));
    const count = removed.mock.calls.length;
    unmount();
    expect(removed.mock.calls.length).toBe(count); // and they are not removed a second time
  });

  it("claims nothing while live on an empty chart, so the chart can still pan", () => {
    const f = fakeHandle();
    run(f.handle);
    const e = new MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0 });
    f.container.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(false); // no tool, nothing under the pointer: not used
  });

  it("a detach that throws (the chart already gone) is swallowed", () => {
    const f = fakeHandle();
    f.series.detachPrimitive.mockImplementation(() => { throw new Error("Object is disposed"); });
    const { unmount } = run(f.handle);
    expect(() => { act(() => f.runDisposers()); unmount(); }).not.toThrow();
  });

  it("does nothing without a chart, and registers no mouse listeners when not interactive", () => {
    const none = run(null);
    expect(none.result.current.state.drawings).toEqual([]);
    const f = fakeHandle();
    const add = vi.spyOn(f.container, "addEventListener");
    run(f.handle, false);
    expect(add).not.toHaveBeenCalled(); // phone layout: drawings may show, but there is no editing
    expect(f.series.attachPrimitive).toHaveBeenCalledTimes(1); // still painted
  });

  it("re-attaches to a NEW chart after the old one is rebuilt", () => {
    const a = fakeHandle(), b = fakeHandle();
    const { rerender } = renderHook(({ h }) => useDrawings({ handle: h, bars, symbol: "AAPL", interactive: true, keyTarget: null }), { initialProps: { h: a.handle as ChartHandle | null } });
    act(() => a.runDisposers());
    rerender({ h: null });
    rerender({ h: b.handle });
    expect(b.series.attachPrimitive).toHaveBeenCalledTimes(1);
    expect(a.series.detachPrimitive).toHaveBeenCalledTimes(1);
  });
});

// ── touch ────────────────────────────────────────────────────────────────────

type FakeTouch = { identifier: number; clientX: number; clientY: number };
const T = (x: number, y: number, id = 1): FakeTouch => ({ identifier: id, clientX: x, clientY: y });

/** jsdom has no Touch constructor, so a touch event is a plain event carrying the two lists the hook reads. */
function touch(type: string, el: HTMLElement, changed: FakeTouch[], all: FakeTouch[] = changed) {
  const e = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(e, { touches: type === "touchend" || type === "touchcancel" ? [] : all, changedTouches: changed });
  el.dispatchEvent(e);
  return e;
}

/** A hook on a fake chart whose primitive converts every pixel into a price, so gestures can be driven. */
function drawable(shapes: Shape[] = []) {
  const f = fakeHandle();
  const hook = run(f.handle);
  const prim = f.series.attachPrimitive.mock.calls[0][0];
  prim.toAnchor = (p: { x: number; y: number }) => ({ time: 1000 + p.x, price: 200 - p.y });
  prim.inPlot = () => true;
  prim.shapes = () => shapes;
  prim.toPixel = (a: { time: number; price: number }) => ({ x: a.time - 1000, y: 200 - a.price });
  return { f, hook, engine: () => hook.result.current.engine, state: () => hook.result.current.state };
}

describe("useDrawings: touch", () => {
  it("registers touch listeners only while interactive, and removes them when the chart's disposers run", () => {
    const off = fakeHandle();
    const add = vi.spyOn(off.container, "addEventListener");
    run(off.handle, false);
    expect(add).not.toHaveBeenCalled();

    const f = fakeHandle();
    const removed = vi.spyOn(f.container, "removeEventListener");
    run(f.handle);
    act(() => f.runDisposers());
    const types = removed.mock.calls.map((c) => c[0]);
    expect(types).toEqual(expect.arrayContaining(["touchstart", "touchmove", "touchend", "touchcancel"]));
  });

  it("leaves a one-finger press on empty chart alone, so the chart can still pan", () => {
    const d = drawable();
    const e = touch("touchstart", d.f.container, [T(50, 50)]);
    expect(e.defaultPrevented).toBe(false);
    expect(d.state().drawings).toEqual([]);
  });

  it("a tap with a one-point tool draws and keeps the press from the chart", () => {
    const d = drawable();
    act(() => d.engine().setTool("hline"));
    let e!: Event;
    act(() => { e = touch("touchstart", d.f.container, [T(50, 60)]); });
    expect(e.defaultPrevented).toBe(true);
    act(() => { touch("touchend", d.f.container, [T(50, 60)]); });
    expect(d.state().drawings).toHaveLength(1);
    expect(d.state().drawings[0].kind).toBe("hline");
  });

  it("press, drag and release draws a two-point line from the two finger positions", () => {
    const d = drawable();
    act(() => d.engine().setTool("trend"));
    act(() => { touch("touchstart", d.f.container, [T(20, 20)]); });
    const mv = touch("touchmove", d.f.container, [T(120, 80)]);
    expect(mv.defaultPrevented).toBe(true); // the chart must not pan while the line is being drawn
    act(() => { touch("touchend", d.f.container, [T(120, 80)]); });
    const [line] = d.state().drawings;
    expect(line.kind).toBe("trend");
    expect(line.points).toEqual([{ time: 1020, price: 180 }, { time: 1120, price: 120 }]);
  });

  it("picks up a line a fingertip away from it, where a mouse at the same spot misses", () => {
    const shape: Shape = { id: "d1", kind: "trend", color: "#fff", width: 1, anchors: [{ x: 0, y: 100 }, { x: 200, y: 100 }], segments: [[{ x: 0, y: 100 }, { x: 200, y: 100 }]] };
    const d = drawable([shape]);
    act(() => d.engine().load([makeDrawing("trend", [{ time: 1000, price: 100 }, { time: 1200, price: 100 }])], "AAPL"));
    const mouse = new MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0, clientX: 100, clientY: 111 });
    d.f.container.dispatchEvent(mouse);
    expect(mouse.defaultPrevented).toBe(false); // 11px off: beyond a mouse's reach
    const e = touch("touchstart", d.f.container, [T(100, 111)]);
    expect(e.defaultPrevented).toBe(true); // within a fingertip's
    act(() => { touch("touchend", d.f.container, [T(100, 111)]); });
  });

  it("does not reach for a line beyond a fingertip's tolerance", () => {
    const shape: Shape = { id: "d1", kind: "trend", color: "#fff", width: 1, anchors: [{ x: 0, y: 100 }, { x: 200, y: 100 }], segments: [[{ x: 0, y: 100 }, { x: 200, y: 100 }]] };
    const d = drawable([shape]);
    act(() => d.engine().load([makeDrawing("trend", [{ time: 1000, price: 100 }, { time: 1200, price: 100 }])], "AAPL"));
    const e = touch("touchstart", d.f.container, [T(100, 130)]);
    expect(e.defaultPrevented).toBe(false);
  });

  it("a second finger cancels a half-drawn line and gives the gesture back to the chart", () => {
    const d = drawable();
    act(() => d.engine().setTool("trend"));
    act(() => { touch("touchstart", d.f.container, [T(20, 20, 1)]); });
    expect(d.engine().busy).toBe(true);
    act(() => { touch("touchstart", d.f.container, [T(90, 90, 2)], [T(20, 20, 1), T(90, 90, 2)]); });
    expect(d.engine().busy).toBe(false);
    // the first finger lifting afterwards must not finish the line
    act(() => { touch("touchend", d.f.container, [T(20, 20, 1)]); });
    expect(d.state().drawings).toEqual([]);
  });

  it("a cancelled touch (the system took it) abandons the gesture", () => {
    const d = drawable();
    act(() => d.engine().setTool("trend"));
    act(() => { touch("touchstart", d.f.container, [T(20, 20)]); });
    act(() => { touch("touchcancel", d.f.container, [T(20, 20)]); });
    expect(d.engine().busy).toBe(false);
    expect(d.state().drawings).toEqual([]);
  });

  it("ignores moves from a finger it is not following", () => {
    const d = drawable();
    act(() => d.engine().setTool("trend"));
    act(() => { touch("touchstart", d.f.container, [T(20, 20, 1)]); });
    const e = touch("touchmove", d.f.container, [T(300, 300, 7)]);
    expect(e.defaultPrevented).toBe(false);
  });
});
