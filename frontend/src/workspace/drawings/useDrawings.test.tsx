import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChartBar, ChartHandle } from "../../components/CandlestickChart";
import { useDrawings } from "./useDrawings";

afterEach(cleanup);

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
