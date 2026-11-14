import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * What happens, in what ORDER, when a chart is torn down.
 *
 * The charting library logs "Object is disposed" when something touches a chart
 * after chart.remove(). Whether that shows up in a browser depends on timing, so a
 * browser test cannot pin it down (a count-based one failed 7 times in 30 on clean
 * main under load). The order of calls is deterministic, so it is checked here, on a
 * fake chart that records every call.
 */

const order: string[] = [];

vi.mock("lightweight-charts", () => {
  const series = () => ({
    setData: vi.fn(),
    createPriceLine: vi.fn(),
    removePriceLine: vi.fn(),
    attachPrimitive: vi.fn(() => { order.push("attach"); }),
    detachPrimitive: vi.fn(() => { order.push("detach"); }),
    priceToCoordinate: vi.fn(() => 0),
    coordinateToPrice: vi.fn(() => 0),
  });
  const pane = () => ({ addSeries: vi.fn(() => series()), setStretchFactor: vi.fn(), getHeight: vi.fn(() => 300) });
  const timeScale = {
    fitContent: vi.fn(),
    getVisibleLogicalRange: vi.fn(() => null),
    subscribeVisibleLogicalRangeChange: vi.fn(() => { order.push("subscribe"); }),
    unsubscribeVisibleLogicalRangeChange: vi.fn(() => { order.push("unsubscribe"); }),
    width: vi.fn(() => 800),
    logicalToCoordinate: vi.fn(() => 0),
    coordinateToLogical: vi.fn(() => 0),
  };
  const chart = {
    panes: () => [pane()],
    addPane: vi.fn(() => pane()),
    timeScale: () => timeScale,
    remove: vi.fn(() => { order.push("remove"); }),
  };
  return { createChart: vi.fn(() => chart), CandlestickSeries: {}, HistogramSeries: {}, LineSeries: {} };
});

import CandlestickChart, { type ChartBar, type ChartHandle } from "./CandlestickChart";

const bars: ChartBar[] = Array.from({ length: 30 }, (_, i) => ({
  timestamp: new Date(Date.UTC(2026, 8, 1, 14, 30) + i * 3600e3).toISOString(),
  open: 100 + i, high: 103 + i, low: 98 + i, close: 101 + i, volume: 1000 + i,
}));

beforeEach(() => { order.length = 0; });
afterEach(cleanup);

describe("chart teardown order", () => {
  it("detaches the volume profile and its range listener BEFORE the chart is removed", () => {
    const { unmount } = render(<CandlestickChart bars={bars} levels={[]} volumeProfile />);
    expect(order).toContain("attach");
    order.length = 0;
    unmount();
    expect(order.indexOf("detach")).toBeGreaterThan(-1);
    expect(order.indexOf("unsubscribe")).toBeGreaterThan(-1);
    expect(order.indexOf("detach")).toBeLessThan(order.indexOf("remove"));
    expect(order.indexOf("unsubscribe")).toBeLessThan(order.indexOf("remove"));
    expect(order.filter((c) => c === "remove")).toHaveLength(1); // removed exactly once
  });

  it("hands a caller a handle, runs the caller's disposers before the chart is removed, then reports null", () => {
    const seen: (ChartHandle | null)[] = [];
    const onReady = vi.fn((h: ChartHandle | null) => {
      seen.push(h);
      if (h) h.onDispose(() => { order.push("caller-dispose"); });
      else order.push("ready-null");
    });
    const { unmount } = render(<CandlestickChart bars={bars} levels={[]} onReady={onReady} />);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ chart: expect.anything(), series: expect.anything(), container: expect.any(HTMLElement) });
    order.length = 0;
    unmount();
    expect(order).toEqual(["caller-dispose", "ready-null", "remove"]);
    expect(seen[1]).toBeNull();
  });

  it("a disposer that unregisters itself is not run", () => {
    const onReady = (h: ChartHandle | null) => {
      if (h) { const off = h.onDispose(() => { order.push("should-not-run"); }); off(); }
    };
    const { unmount } = render(<CandlestickChart bars={bars} levels={[]} onReady={onReady} />);
    unmount();
    expect(order).not.toContain("should-not-run");
    expect(order).toContain("remove");
  });

  it("a disposer that throws does not stop the others or the chart being removed", () => {
    const onReady = (h: ChartHandle | null) => {
      if (!h) return;
      h.onDispose(() => { throw new Error("boom"); });
      h.onDispose(() => { order.push("second-ran"); });
    };
    const { unmount } = render(<CandlestickChart bars={bars} levels={[]} onReady={onReady} />);
    expect(() => unmount()).not.toThrow();
    expect(order).toContain("second-ran");
    expect(order.indexOf("second-ran")).toBeLessThan(order.indexOf("remove"));
  });

  it("without onReady or the profile it does only what it always did: create, then remove once", () => {
    const { unmount } = render(<CandlestickChart bars={bars} levels={[]} />);
    expect(order).not.toContain("attach");
    unmount();
    expect(order).toEqual(["remove"]);
  });
});
