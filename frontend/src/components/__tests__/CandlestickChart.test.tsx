import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

const mockSeries = {
  setData: vi.fn(),
  createPriceLine: vi.fn(() => ({})),
  removePriceLine: vi.fn(),
  priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
};
const mockPane = {
  addSeries: vi.fn(() => mockSeries),
  setStretchFactor: vi.fn(),
};
const mockChart = {
  addSeries: vi.fn(() => mockSeries),
  addPane: vi.fn(() => mockPane),
  panes: vi.fn(() => [mockPane]),
  remove: vi.fn(),
  timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
};

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => mockChart),
  CandlestickSeries: "CandlestickSeries",
  HistogramSeries: "HistogramSeries",
  LineSeries: "LineSeries",
}));

import CandlestickChart, {
  toUnixSeconds,
  barsToCandlestickData,
  barsToVolumeData,
  axisTitleForLevel,
  computeMacd,
  lastIndicatorReadout,
  type ChartBar,
} from "../CandlestickChart";

const sampleBars: ChartBar[] = Array.from({ length: 40 }, (_, i) => ({
  timestamp: `2026-08-14T${String(9 + Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}:00-04:00`,
  open: 100 + i * 0.2,
  high: 101 + i * 0.2,
  low: 99 + i * 0.2,
  close: 100.5 + i * 0.2,
  volume: 1000 + i * 10,
}));

describe("data-mapping functions (pure, no DOM)", () => {
  it("toUnixSeconds converts an ISO timestamp to UNIX seconds", () => {
    expect(toUnixSeconds("2026-08-14T09:30:00-04:00")).toBe(Math.floor(new Date("2026-08-14T09:30:00-04:00").getTime() / 1000));
  });

  it("barsToCandlestickData maps OHLC fields and converts time", () => {
    const data = barsToCandlestickData(sampleBars.slice(0, 2));
    expect(data).toHaveLength(2);
    expect(data[0]).toMatchObject({ open: sampleBars[0].open, high: sampleBars[0].high });
    expect(data[0].time).toBe(toUnixSeconds(sampleBars[0].timestamp));
  });

  it("barsToVolumeData colors up bars green and down bars red", () => {
    const upDown: ChartBar[] = [
      { timestamp: "2026-08-14T09:30:00-04:00", open: 100, high: 102, low: 99, close: 101, volume: 1000 },
      { timestamp: "2026-08-14T09:45:00-04:00", open: 101, high: 101.5, low: 98, close: 98.5, volume: 2000 },
    ];
    const data = barsToVolumeData(upDown);
    expect(data[0]).toMatchObject({ value: 1000, color: "rgba(24,195,126,0.45)" });
    expect(data[1]).toMatchObject({ value: 2000, color: "rgba(255,95,109,0.45)" });
  });

  it("axisTitleForLevel keeps full labels on desktop and shortens phone axis titles", () => {
    expect(axisTitleForLevel("Resistance 1", false)).toBe("Resistance 1");
    expect(axisTitleForLevel("Resistance 1", true)).toBe("R");
    expect(axisTitleForLevel("Support 2", true)).toBe("S");
    expect(axisTitleForLevel("Entry", true)).toBe("E");
    expect(axisTitleForLevel("VWAP", true)).toBe("V");
  });

  it("computeMacd emits macd/signal/histogram once enough bars exist", () => {
    const { macd, signal, histogram } = computeMacd(sampleBars);
    expect(macd.length).toBeGreaterThan(0);
    expect(signal.length).toBeGreaterThan(0);
    expect(histogram.length).toBeGreaterThan(0);
    expect(histogram[histogram.length - 1].color).toMatch(/rgba/);
  });

  it("lastIndicatorReadout returns finite MA and MACD tails", () => {
    const r = lastIndicatorReadout(sampleBars);
    expect(r.ma5).not.toBeNull();
    expect(r.ma10).not.toBeNull();
    expect(r.ma20).not.toBeNull();
    expect(r.vwap).not.toBeNull();
    expect(r.macd).not.toBeNull();
    expect(r.signal).not.toBeNull();
    expect(r.hist).not.toBeNull();
  });
});

describe("CandlestickChart component", () => {
  it("shows the empty state when there are no bars", () => {
    render(<CandlestickChart bars={[]} levels={[]} />);
    expect(screen.getByText("NO CHART DATA")).toBeInTheDocument();
  });

  it("creates price/volume/macd panes and nine series on mount", () => {
    cleanup();
    mockChart.addPane.mockClear();
    mockPane.addSeries.mockClear();
    mockSeries.setData.mockClear();
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    // Default price pane + volume pane + MACD pane
    expect(mockChart.panes).toHaveBeenCalled();
    expect(mockChart.addPane).toHaveBeenCalledTimes(2);
    // price: candle + ma5 + ma10 + ma20 + vwap; volume: 1; macd: hist + line + signal
    expect(mockPane.addSeries).toHaveBeenCalledTimes(9);
    expect(mockSeries.setData).toHaveBeenCalled();
  });

  it("draws a price line per support/resistance level with full titles on desktop", () => {
    cleanup();
    mockSeries.createPriceLine.mockClear();
    render(
      <CandlestickChart
        bars={sampleBars}
        levels={[{ label: "Resistance 1", value: 105, color: "#f00" }, { label: "Support 1", value: 95, color: "#0f0" }]}
      />,
    );
    expect(mockSeries.createPriceLine).toHaveBeenCalledTimes(2);
    expect(mockSeries.createPriceLine).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Resistance 1" }),
    );
  });

  it("shows a live indicator readout above the canvas", () => {
    cleanup();
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    expect(screen.getByLabelText("Indicator readout")).toBeInTheDocument();
    expect(screen.getByLabelText("Indicator readout").textContent).toMatch(/MA5/);
    expect(screen.getByLabelText("Indicator readout").textContent).toMatch(/MACD/);
  });

  it("keeps the legend open on desktop without a collapse toggle", () => {
    cleanup();
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    expect(screen.getByLabelText("Chart legend")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /legend/i })).not.toBeInTheDocument();
  });

  it("removes the chart on unmount", () => {
    cleanup();
    mockChart.remove.mockClear();
    const { unmount } = render(<CandlestickChart bars={sampleBars} levels={[]} />);
    unmount();
    expect(mockChart.remove).toHaveBeenCalledTimes(1);
  });
});
