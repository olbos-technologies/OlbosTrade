/**
 * The multi-timeframe page: which requests go out, that each chart stands on its own, and that what it
 * says about the timeframes is description, never advice. The chart is mocked to record what it is
 * asked to draw; the data source is controlled so the order answers arrive in can be chosen.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import MultiTimeframe from "../MultiTimeframe";
import { api } from "../../api/client";
import { MTF_INDICATORS_KEY, MTF_LAYOUT_KEY } from "../../components/chart/multiTimeframePrefs";
import { INDICATORS_PREF_KEY } from "../../components/chart/indicatorPrefs";

const drawn: Array<{ bars: number; indicators?: readonly string[] }> = [];
vi.mock("../../components/CandlestickChart", () => ({
  default: (p: { bars: unknown[]; indicators?: readonly string[] }) => {
    drawn.push({ bars: p.bars.length, indicators: p.indicators });
    return <div data-testid="chart" data-bars={p.bars.length} />;
  },
}));
vi.mock("../../api/client", () => ({ getOperatorApiKey: () => "", setOperatorApiKey: vi.fn(), api: { getEquityChart: vi.fn() } }));

const getChart = vi.mocked(api.getEquityChart);

const bars = (closes: number[]) => closes.map((c, i) => ({ timestamp: new Date(Date.UTC(2026, 0, 1) + i * 3600e3).toISOString(), open: c, high: c + 1, low: c - 1, close: c, volume: 1000 }));
const RISING = { bars: bars(Array.from({ length: 60 }, (_, i) => 100 + i)) };
const FALLING = { bars: bars(Array.from({ length: 60 }, (_, i) => 200 - i)) };

type Params = { timeframe?: string; range?: string; limit?: number };
const calls = () => getChart.mock.calls.map((c) => ({ symbol: c[0], ...(c[1] as Params) }));

/** Answer every request with `by(interval)`: a result, or a rejection. */
function answerWith(by: (interval: string, symbol: string) => unknown) {
  getChart.mockImplementation(((symbol: string, p: Params) => {
    const r = by(p.timeframe as string, symbol);
    return r instanceof Error ? Promise.reject(r) : Promise.resolve(r);
  }) as never);
}

beforeEach(() => { drawn.length = 0; getChart.mockReset(); localStorage.clear(); window.history.pushState({}, "", "/"); answerWith(() => RISING); });
afterEach(() => { cleanup(); localStorage.clear(); });

const tiles = () => screen.getAllByRole("region", { name: /^Chart \d/ });
const settle = async () => { await waitFor(() => expect(screen.queryByText(/Loading the timeframes/)).toBeNull()); };

describe("what it asks for", () => {
  it("by default shows three charts, Daily, 1h and 15m, each with the range that suits it", async () => {
    render(<MultiTimeframe />);
    await settle();
    expect(tiles().map((t) => t.getAttribute("aria-label"))).toEqual(["Chart 1: 1 day", "Chart 2: 1 hour", "Chart 3: 15 minutes"]);
    expect(calls()).toEqual([
      { symbol: "AAPL", timeframe: "1d", range: "1y", limit: 5000 },
      { symbol: "AAPL", timeframe: "1h", range: "1m", limit: 5000 },
      { symbol: "AAPL", timeframe: "15m", range: "5d", limit: 5000 },
    ]);
    expect(screen.getAllByTestId("chart")).toHaveLength(3);
  });

  it("changing one chart's interval fetches only that chart", async () => {
    render(<MultiTimeframe />);
    await settle();
    getChart.mockClear();
    fireEvent.change(screen.getByLabelText("Bar interval for chart 2"), { target: { value: "4h" } });
    await waitFor(() => expect(getChart).toHaveBeenCalledTimes(1));
    expect(calls()).toEqual([{ symbol: "AAPL", timeframe: "4h", range: "6m", limit: 5000 }]);
    expect(tiles()[1].getAttribute("aria-label")).toBe("Chart 2: 4 hours");
  });

  it("changing the symbol fetches all of them again, for the new symbol", async () => {
    render(<MultiTimeframe />);
    await settle();
    getChart.mockClear();
    fireEvent.change(screen.getByPlaceholderText("Symbol"), { target: { value: "msft" } });
    fireEvent.submit(screen.getByPlaceholderText("Symbol").closest("form")!);
    await waitFor(() => expect(getChart).toHaveBeenCalledTimes(3));
    expect(calls().every((c) => c.symbol === "MSFT")).toBe(true);
  });

  it("a watchlist chip changes the symbol too", async () => {
    render(<MultiTimeframe />);
    await settle();
    getChart.mockClear();
    fireEvent.click(within(screen.getByRole("group", { name: "Watchlist symbols" })).getByRole("button", { name: "NVDA" }));
    await waitFor(() => expect(getChart).toHaveBeenCalledTimes(3));
    expect(calls().every((c) => c.symbol === "NVDA")).toBe(true);
    expect(screen.getByRole("button", { name: "NVDA" })).toHaveAttribute("aria-pressed", "true");
  });

  it("a symbol in the address (?symbol=) is used", async () => {
    window.history.pushState({}, "", "/?symbol=tsla");
    render(<MultiTimeframe />);
    await settle();
    expect(calls().every((c) => c.symbol === "TSLA")).toBe(true);
  });

  it("an invalid symbol is refused with a message and nothing is fetched", async () => {
    render(<MultiTimeframe />);
    await settle();
    getChart.mockClear();
    fireEvent.change(screen.getByPlaceholderText("Symbol"), { target: { value: "bad symbol!" } });
    fireEvent.submit(screen.getByPlaceholderText("Symbol").closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("Not a valid symbol");
    expect(getChart).not.toHaveBeenCalled();
  });

  it("re-rendering with nothing changed does not fetch again", async () => {
    const { rerender } = render(<MultiTimeframe />);
    await settle();
    rerender(<MultiTimeframe />);
    rerender(<MultiTimeframe />);
    expect(getChart).toHaveBeenCalledTimes(3);
  });
});

describe("the number of charts", () => {
  it("going to four adds one chart, with the default interval for that size, and fetches only it", async () => {
    render(<MultiTimeframe />);
    await settle();
    getChart.mockClear();
    fireEvent.change(screen.getByLabelText("Number of charts"), { target: { value: "4" } });
    await waitFor(() => expect(tiles()).toHaveLength(4));
    expect(tiles()[3].getAttribute("aria-label")).toBe("Chart 4: 1 week");           // not a copy of a chart already shown
    expect(calls()).toEqual([{ symbol: "AAPL", timeframe: "1w", range: "5y", limit: 5000 }]);
  });

  it("going to two removes the last chart and fetches nothing", async () => {
    render(<MultiTimeframe />);
    await settle();
    getChart.mockClear();
    fireEvent.change(screen.getByLabelText("Number of charts"), { target: { value: "2" } });
    await waitFor(() => expect(tiles()).toHaveLength(2));
    expect(getChart).not.toHaveBeenCalled();
  });

  it("the layout is saved and comes back", async () => {
    const { unmount } = render(<MultiTimeframe />);
    await settle();
    fireEvent.change(screen.getByLabelText("Bar interval for chart 3"), { target: { value: "5m" } });
    expect(JSON.parse(localStorage.getItem(MTF_LAYOUT_KEY)!).intervals).toEqual(["1d", "1h", "5m"]);
    unmount();
    cleanup();
    render(<MultiTimeframe />);
    await settle();
    expect(tiles().map((t) => t.getAttribute("aria-label"))).toEqual(["Chart 1: 1 day", "Chart 2: 1 hour", "Chart 3: 5 minutes"]);
  });
});

describe("each chart stands on its own", () => {
  it("one that fails says so for itself and the others still draw", async () => {
    answerWith((interval) => (interval === "1h" ? new Error("feed down") : RISING));
    render(<MultiTimeframe />);
    await settle();
    expect(within(tiles()[1]).getByRole("status")).toHaveTextContent("Chart data unavailable for this timeframe. feed down");
    expect(within(tiles()[1]).queryByTestId("chart")).toBeNull();
    expect(within(tiles()[0]).getByTestId("chart")).toBeInTheDocument();
    expect(within(tiles()[2]).getByTestId("chart")).toBeInTheDocument();
  });

  it("one that returns nothing says so, and never draws made-up bars", async () => {
    answerWith((interval) => (interval === "15m" ? { bars: [] } : RISING));
    render(<MultiTimeframe />);
    await settle();
    expect(within(tiles()[2]).getByRole("status")).toHaveTextContent("No bars returned for this timeframe.");
    expect(within(tiles()[2]).queryByTestId("chart")).toBeNull();
  });

  it("shows the provider's note when a range had to be cut down", async () => {
    answerWith((interval) => (interval === "15m" ? { ...RISING, range_clamped: true, period: "60d" } : RISING));
    render(<MultiTimeframe />);
    await settle();
    expect(within(tiles()[2]).getByText(/Showing the last 60 days/)).toBeInTheDocument();
  });

  it("a slower, older answer never overwrites the newest pick", async () => {
    const pending: Record<string, (v: unknown) => void> = {};
    getChart.mockImplementation(((_s: string, p: Params) => new Promise((res) => { pending[`${p.timeframe}`] = res; })) as never);
    render(<MultiTimeframe />);
    // all three are in flight; switch chart 3 from 15m to 5m, then answer the OLD 15m request last
    fireEvent.change(screen.getByLabelText("Bar interval for chart 3"), { target: { value: "5m" } });
    await act(async () => { pending["5m"]?.({ bars: bars([1, 2, 3, 4, 5, 6, 7]) }); pending["1d"]?.(RISING); pending["1h"]?.(RISING); });
    await act(async () => { pending["15m"]?.(RISING); });
    await settle();
    const third = within(tiles()[2]).getByTestId("chart");
    expect(third.getAttribute("data-bars")).toBe("7");        // the 5m answer (7 bars), not the late 15m one (60)
  });

  it("an answer that arrives after its chart was taken off screen is not reused when that chart comes back", async () => {
    const pending: Array<{ tf: string; resolve: (v: unknown) => void }> = [];
    getChart.mockImplementation(((_s: string, p: Params) => new Promise((resolve) => { pending.push({ tf: p.timeframe as string, resolve }); })) as never);
    render(<MultiTimeframe />);
    const first15 = () => pending.find((p) => p.tf === "15m")!;
    fireEvent.change(screen.getByLabelText("Bar interval for chart 3"), { target: { value: "5m" } });     // 15m leaves the screen
    await act(async () => { first15().resolve(RISING); });                                                  // its old answer arrives now
    fireEvent.change(screen.getByLabelText("Bar interval for chart 3"), { target: { value: "15m" } });    // and 15m comes back
    await waitFor(() => expect(getChart.mock.calls.filter((c) => (c[1] as Params).timeframe === "15m")).toHaveLength(2));
  });

  it("shows loading until every chart has answered", async () => {
    let release!: (v: unknown) => void;
    getChart.mockImplementation(((_s: string, p: Params) => (p.timeframe === "1d" ? new Promise((r) => { release = r; }) : Promise.resolve(RISING))) as never);
    render(<MultiTimeframe />);
    expect(await screen.findByText(/Loading the timeframes/)).toBeInTheDocument();
    expect(within(tiles()[0]).getByLabelText(/Loading chart 1/)).toBeInTheDocument();
    await act(async () => { release(RISING); });
    await settle();
  });
});

describe("what it says about the timeframes", () => {
  it("states agreement in words when every timeframe has price above its 50-bar average", async () => {
    render(<MultiTimeframe />);
    await settle();
    expect(screen.getByText(/Price is above its 50-bar average on all 3 timeframes\./)).toBeInTheDocument();
  });

  it("calls a disagreement mixed", async () => {
    answerWith((interval) => (interval === "1d" ? FALLING : RISING));
    render(<MultiTimeframe />);
    await settle();
    expect(screen.getByText(/Mixed: above the 50-bar average on 2 of 3, below on 1\./)).toBeInTheDocument();
  });

  it("each chart shows its own arrows and words, not colour alone", async () => {
    answerWith((interval) => (interval === "1d" ? FALLING : RISING));
    render(<MultiTimeframe />);
    await settle();
    expect(within(tiles()[0]).getByLabelText("Price is below its 50-bar average")).toHaveTextContent("MA50 ▼");
    expect(within(tiles()[1]).getByLabelText("Price is above its 20-bar average")).toHaveTextContent("MA20 ▲");
  });

  it("with too few bars it says it cannot compare, rather than guessing", async () => {
    answerWith(() => ({ bars: bars([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) }));
    render(<MultiTimeframe />);
    await settle();
    expect(screen.getByText("Not enough bars loaded to compare the timeframes.")).toBeInTheDocument();
    expect(within(tiles()[0]).getByLabelText("MA50: not enough bars loaded")).toHaveTextContent("MA50 —");
  });

  it("a failed chart is left out of the comparison and the sentence says how many were compared", async () => {
    answerWith((interval) => (interval === "1h" ? new Error("down") : RISING));
    render(<MultiTimeframe />);
    await settle();
    expect(screen.getByText(/above its 50-bar average on the 2 of 3 timeframes with enough bars/)).toBeInTheDocument();
  });

  it("says plainly that this is not a signal, and never uses advice words", async () => {
    render(<MultiTimeframe />);
    await settle();
    const page = screen.getByLabelText("Multi-timeframe view").textContent ?? "";
    expect(page).toContain("not a signal");
    expect(page.toLowerCase()).not.toMatch(/\b(buy now|sell now|bullish|bearish|go long|go short)\b/);
  });
});

describe("indicators", () => {
  it("the charts start light: two averages and volume", async () => {
    render(<MultiTimeframe />);
    await settle();
    expect(drawn.length).toBeGreaterThan(0);
    expect(drawn.every((d) => JSON.stringify(d.indicators) === JSON.stringify(["ma20", "ma50", "volume"]))).toBe(true);
  });

  it("one picker changes every chart and is kept apart from the main chart's choice", async () => {
    render(<MultiTimeframe />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Indicators" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("checkbox", { name: "RSI" }));
    await waitFor(() => expect(drawn[drawn.length - 1].indicators).toEqual(["ma20", "ma50", "volume", "rsi"]));
    expect(JSON.parse(localStorage.getItem(MTF_INDICATORS_KEY)!)).toEqual(["ma20", "ma50", "volume", "rsi"]);
    expect(localStorage.getItem(INDICATORS_PREF_KEY)).toBeNull();           // the Chart page's choice is untouched
  });

  it("Reset goes back to the tiles' own default, not the main chart's", async () => {
    localStorage.setItem(MTF_INDICATORS_KEY, JSON.stringify(["rsi"]));
    render(<MultiTimeframe />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Indicators" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reset to default" }));
    await waitFor(() => expect(drawn[drawn.length - 1].indicators).toEqual(["ma20", "ma50", "volume"]));
  });
});
