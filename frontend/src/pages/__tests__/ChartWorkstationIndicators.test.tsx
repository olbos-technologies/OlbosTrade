/**
 * The Chart page's indicator picker: it hands its selection to the chart, saves it where the Workspace
 * reads it, and the compact Equity and Options desk charts have neither. The chart is mocked to record
 * what it is asked for.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import ChartWorkstation from "../ChartWorkstation";
import { INDICATORS_PREF_KEY } from "../../components/chart/indicatorPrefs";
import { DEFAULT_INDICATORS } from "../../components/chart/indicators";

const asked: Array<{ indicators?: readonly string[] }> = [];
vi.mock("../../components/CandlestickChart", () => ({
  default: (props: { indicators?: readonly string[] }) => { asked.push({ indicators: props.indicators }); return <div data-testid="chart" />; },
}));
vi.mock("../../hooks/usePaperTrade", () => ({ usePaperTrade: () => ({ positions: [], positionsError: null }) }));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../api/client", () => ({
  getOperatorApiKey: () => "", setOperatorApiKey: vi.fn(),
  api: {
    getSnapshot: vi.fn().mockResolvedValue({ last_close: 100, change_pct: 0 }),
    getEquitySignals: vi.fn().mockResolvedValue({ signals: [] }),
    getIVRank: vi.fn().mockResolvedValue({}),
    getCatalystCalendar: vi.fn().mockResolvedValue({ events: [] }),
    getEquityChart: vi.fn().mockResolvedValue({ bars: [{ timestamp: "2026-08-14T13:30:00Z", open: 1, high: 2, low: 1, close: 2, volume: 10 }] }),
  },
}));

const lastAsk = () => asked[asked.length - 1];
const pickerButton = () => screen.queryByRole("button", { name: "Indicators" });

beforeEach(() => { asked.length = 0; localStorage.clear(); });
afterEach(() => { cleanup(); localStorage.clear(); });

describe("the Chart page", () => {
  it("has the picker and asks its chart for the default indicators", async () => {
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(pickerButton()).toBeInTheDocument();
    expect(lastAsk().indicators).toEqual([...DEFAULT_INDICATORS]);
  });

  it("a choice reaches the chart and is saved under the key the Workspace reads", async () => {
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    fireEvent.click(pickerButton()!);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("checkbox", { name: "RSI" }));
    await waitFor(() => expect(lastAsk().indicators).toEqual([...DEFAULT_INDICATORS, "rsi"]));
    expect(JSON.parse(localStorage.getItem(INDICATORS_PREF_KEY)!)).toEqual([...DEFAULT_INDICATORS, "rsi"]);
  });

  it("starts from what the Workspace saved", async () => {
    localStorage.setItem(INDICATORS_PREF_KEY, JSON.stringify(["ma50", "bb"]));
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(lastAsk().indicators).toEqual(["ma50", "bb"]);
  });

  it("Reset restores the default and saves it", async () => {
    localStorage.setItem(INDICATORS_PREF_KEY, JSON.stringify(["rsi"]));
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    fireEvent.click(pickerButton()!);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reset to default" }));
    await waitFor(() => expect(lastAsk().indicators).toEqual([...DEFAULT_INDICATORS]));
    expect(JSON.parse(localStorage.getItem(INDICATORS_PREF_KEY)!)).toEqual([...DEFAULT_INDICATORS]);
  });

  it("falls back to the default when the saved value is corrupt", async () => {
    localStorage.setItem(INDICATORS_PREF_KEY, "{ not json");
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(lastAsk().indicators).toEqual([...DEFAULT_INDICATORS]);
  });
});

describe("the compact Equity and Options desk chart", () => {
  it("is unchanged: no picker, and no selection handed to the chart", async () => {
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} compact />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(pickerButton()).toBeNull();
    expect(asked.every((a) => a.indicators === undefined)).toBe(true);
  });
});
