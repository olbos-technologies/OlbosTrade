import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

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
const mockTimeScale = {
  fitContent: vi.fn(),
  getVisibleLogicalRange: vi.fn(() => null as { from: number; to: number } | null),
  setVisibleLogicalRange: vi.fn(),
};
const mockChart = {
  addSeries: vi.fn(() => mockSeries),
  addPane: vi.fn(() => mockPane),
  panes: vi.fn(() => [mockPane]),
  remove: vi.fn(),
  timeScale: vi.fn(() => mockTimeScale),
};

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => mockChart),
  CandlestickSeries: "CandlestickSeries",
  HistogramSeries: "HistogramSeries",
  LineSeries: "LineSeries",
}));

import { createChart } from "lightweight-charts";
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

  it("axisTitleForLevel keeps the day-level labels distinct on a phone", () => {
    for (const label of ["PDH", "PDL", "PDC", "ORH", "ORL", "~ORH", "~ORL"]) {
      expect(axisTitleForLevel(label, true)).toBe(label);
      expect(axisTitleForLevel(label, false)).toBe(label);
    }
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

  it("collapses the legend on desktop too, so it does not cover the price line", () => {
    cleanup();
    window.localStorage.clear();
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    expect(screen.queryByLabelText("Chart legend")).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Legend" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // the readout stays visible while the key is collapsed
    expect(screen.getByLabelText("Indicator readout").textContent).toMatch(/MA5/);
  });

  it("opens the legend on demand and remembers the choice", () => {
    cleanup();
    window.localStorage.clear();
    const { unmount } = render(<CandlestickChart bars={sampleBars} levels={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Legend" }));
    expect(screen.getByLabelText("Chart legend")).toBeInTheDocument();
    unmount();
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    expect(screen.getByLabelText("Chart legend")).toBeInTheDocument();
    window.localStorage.clear();
  });

  it("still works when storage is unavailable", () => {
    cleanup();
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Legend" }));
    expect(screen.getByLabelText("Chart legend")).toBeInTheDocument();
    get.mockRestore(); set.mockRestore();
  });

  it("removes the chart on unmount", () => {
    cleanup();
    mockChart.remove.mockClear();
    const { unmount } = render(<CandlestickChart bars={sampleBars} levels={[]} />);
    unmount();
    expect(mockChart.remove).toHaveBeenCalledTimes(1);
  });
});


describe("the indicator selection", () => {
  const reset = () => {
    cleanup();
    vi.mocked(createChart).mockClear();
    mockChart.addPane.mockClear();
    mockChart.remove.mockClear();
    mockPane.addSeries.mockClear();
    mockSeries.setData.mockClear();
    mockSeries.createPriceLine.mockClear();
    mockTimeScale.fitContent.mockClear();
    mockTimeScale.setVisibleLogicalRange.mockClear();
    mockTimeScale.getVisibleLogicalRange.mockReturnValue(null);
  };
  const readoutText = () => screen.getByLabelText("Indicator readout").textContent ?? "";

  it("with no selection draws exactly what it always has, and reads out exactly what it always did", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    expect(mockChart.addPane).toHaveBeenCalledTimes(2);
    expect(mockPane.addSeries).toHaveBeenCalledTimes(9);
    const r = lastIndicatorReadout(sampleBars);
    const f = (n: number | null, d = 2) => (n == null ? "—" : n.toFixed(d));
    expect(readoutText()).toBe(
      `MA5 ${f(r.ma5)}MA10 ${f(r.ma10)}MA20 ${f(r.ma20)}VWAP ${f(r.vwap)}MACD ${f(r.macd, 3)}Sig ${f(r.signal, 3)}Hist ${f(r.hist, 3)}`);
  });

  it("an explicit default selection is the same chart as no selection", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5", "ma10", "ma20", "vwap", "volume", "macd"]} />);
    expect(mockChart.addPane).toHaveBeenCalledTimes(2);
    expect(mockPane.addSeries).toHaveBeenCalledTimes(9);
  });

  it("RSI gets its own pane with guides at 30 and 70, and its readout", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["rsi"]} />);
    expect(mockChart.addPane).toHaveBeenCalledTimes(1);
    expect(mockPane.addSeries).toHaveBeenCalledTimes(2);                 // candles + the RSI line
    expect(mockSeries.createPriceLine.mock.calls.map((c) => (c as unknown as [{ price: number }])[0].price)).toEqual([30, 70]);
    expect(readoutText()).toMatch(/^RSI \d/);
  });

  it("overlays share the price pane: no new panes, and Bollinger is three lines", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma50", "ema9", "bb"]} />);
    expect(mockChart.addPane).not.toHaveBeenCalled();
    expect(mockPane.addSeries).toHaveBeenCalledTimes(1 + 1 + 1 + 3);     // candles + MA50 + EMA9 + three bands
    expect(readoutText()).toMatch(/MA50.*EMA9.*BB /);
  });

  it("no indicators at all is candles only", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} indicators={[]} />);
    expect(mockChart.addPane).not.toHaveBeenCalled();
    expect(mockPane.addSeries).toHaveBeenCalledTimes(1);
    expect(readoutText()).toBe("");
  });

  it("unknown ids are ignored, never drawn", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["nope", "rsi", "__proto__"] as never} />);
    expect(mockPane.addSeries).toHaveBeenCalledTimes(2);
    expect(readoutText()).toMatch(/^RSI/);
  });

  it("the legend lists the chosen indicators and not the ones that are off", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["rsi", "ma5"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Legend" }));
    const legend = screen.getByLabelText("Chart legend");
    expect(legend.textContent).toContain("RSI");
    expect(legend.textContent).toContain("MA5");
    expect(legend.textContent).not.toContain("MACD");
    expect(legend.textContent).not.toContain("VWAP");
  });

  it("a different selection rebuilds the chart: the old one is removed and a new one built", () => {
    reset();
    const { rerender } = render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5"]} />);
    expect(vi.mocked(createChart)).toHaveBeenCalledTimes(1);
    rerender(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5", "rsi"]} />);
    expect(vi.mocked(createChart)).toHaveBeenCalledTimes(2);
    expect(mockChart.remove).toHaveBeenCalledTimes(1);
    expect(readoutText()).toMatch(/MA5.*RSI/);
  });

  it("the same selection in another order, or a new array of the same ids, does not rebuild", () => {
    reset();
    const { rerender } = render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["rsi", "ma5"]} />);
    rerender(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5", "rsi"]} />);
    rerender(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5", "rsi"]} />);
    expect(vi.mocked(createChart)).toHaveBeenCalledTimes(1);
    expect(mockChart.remove).not.toHaveBeenCalled();
  });

  it("keeps the zoom across a rebuild when the bars have not changed", () => {
    reset();
    const { rerender } = render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5"]} />);
    mockTimeScale.fitContent.mockClear();
    mockTimeScale.getVisibleLogicalRange.mockReturnValue({ from: 5, to: 30 });
    rerender(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5", "rsi"]} />);
    expect(mockTimeScale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 5, to: 30 });
    expect(mockTimeScale.fitContent).not.toHaveBeenCalled();
  });

  it("fits everything instead when the bars changed along with the selection", () => {
    reset();
    const { rerender } = render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma5"]} />);
    mockTimeScale.fitContent.mockClear();
    mockTimeScale.getVisibleLogicalRange.mockReturnValue({ from: 5, to: 30 });
    rerender(<CandlestickChart bars={sampleBars.slice(0, 30)} levels={[]} indicators={["ma5", "rsi"]} />);
    expect(mockTimeScale.setVisibleLogicalRange).not.toHaveBeenCalled();
    expect(mockTimeScale.fitContent).toHaveBeenCalled();
  });

  it("changing only the bars never rebuilds the chart", () => {
    reset();
    const { rerender } = render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["rsi"]} />);
    rerender(<CandlestickChart bars={sampleBars.slice(0, 30)} levels={[]} indicators={["rsi"]} />);
    expect(vi.mocked(createChart)).toHaveBeenCalledTimes(1);
  });

  it("an indicator with too few bars to start still renders, with a dash in the readout", () => {
    reset();
    render(<CandlestickChart bars={sampleBars} levels={[]} indicators={["ma200"]} />);
    expect(readoutText()).toBe("MA200 —");
  });
});
