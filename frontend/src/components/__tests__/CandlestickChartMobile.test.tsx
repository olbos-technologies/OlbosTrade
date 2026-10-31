/**
 * Mobile legend collapse for CandlestickChart.
 *
 * Separate file on purpose: vi.mock is hoisted, so forcing useIsMobile true
 * here must not put the desktop CandlestickChart assertions into phone mode.
 * Phones start with the overlay key collapsed so candles stay readable; the
 * operator can expand it on demand.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => true }));

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

import CandlestickChart, { type ChartBar } from "../CandlestickChart";

const sampleBars: ChartBar[] = [
  { timestamp: "2026-08-14T09:30:00-04:00", open: 100, high: 102, low: 99, close: 101, volume: 1000 },
  { timestamp: "2026-08-14T09:45:00-04:00", open: 101, high: 101.5, low: 98, close: 98.5, volume: 2000 },
];

beforeEach(() => {
  cleanup();
  mockSeries.createPriceLine.mockClear();
});

describe("CandlestickChart — mobile legend", () => {
  it("starts collapsed with a Legend toggle and no overlay key", () => {
    render(
      <CandlestickChart
        bars={sampleBars}
        levels={[{ label: "Resistance 1", value: 105, color: "#f00" }]}
      />,
    );
    const toggle = screen.getByRole("button", { name: "Legend" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Chart legend")).not.toBeInTheDocument();
  });

  it("expands and hides the legend on demand", () => {
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Legend" }));
    expect(screen.getByLabelText("Chart legend")).toBeInTheDocument();
    expect(screen.getByText("MA20")).toBeInTheDocument();
    expect(screen.getByText("MACD")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hide legend" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: "Hide legend" }));
    expect(screen.queryByLabelText("Chart legend")).not.toBeInTheDocument();
  });

  it("keeps the MA/MACD readout visible while the legend is collapsed", () => {
    render(<CandlestickChart bars={sampleBars} levels={[]} />);
    expect(screen.getByLabelText("Indicator readout")).toBeInTheDocument();
    expect(screen.getByLabelText("Indicator readout").textContent).toMatch(/MA5/);
  });

  it("shortens price-line axis titles so labels do not cover candles", () => {
    render(
      <CandlestickChart
        bars={sampleBars}
        levels={[
          { label: "Resistance 1", value: 105, color: "#f00" },
          { label: "Support 1", value: 95, color: "#0f0" },
          { label: "Entry", value: 100, color: "#ff0" },
        ]}
      />,
    );
    expect(mockSeries.createPriceLine).toHaveBeenCalledWith(
      expect.objectContaining({ title: "R" }),
    );
    expect(mockSeries.createPriceLine).toHaveBeenCalledWith(
      expect.objectContaining({ title: "S" }),
    );
    expect(mockSeries.createPriceLine).toHaveBeenCalledWith(
      expect.objectContaining({ title: "E" }),
    );
  });
});
