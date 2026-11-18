/**
 * The compact Equity and Options desk chart: a failed signal read must not look like "no signal".
 * A successful empty read is still an honest NO SIGNAL; a failed refresh clears the old evidence
 * instead of leaving it on screen looking current; a slower, older answer never overwrites a newer one.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import ChartWorkstation from "../ChartWorkstation";

const getEquitySignals = vi.fn();
vi.mock("../../components/CandlestickChart", () => ({ default: () => <div data-testid="chart" /> }));
vi.mock("../../components/SignalAttribution", () => ({ default: ({ data }: any) => <span>ATTR {data?.ticker ?? "?"}</span> }));
vi.mock("../../utils/chartWorkstationDisplay", async (orig) => ({
  ...(await orig<any>()),
  toChartSignalAttribution: (s: any) => ({ ticker: s.ticker }),
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../api/client", () => ({
  getOperatorApiKey: () => "", setOperatorApiKey: vi.fn(),
  api: {
    getSnapshot: vi.fn().mockResolvedValue({ last_close: 100, change_pct: 0 }),
    getEquitySignals: (...a: unknown[]) => getEquitySignals(...a),
    getIVRank: vi.fn().mockResolvedValue({}),
    getCatalystCalendar: vi.fn().mockResolvedValue({ events: [] }),
    getEquityChart: vi.fn().mockResolvedValue({ bars: [{ timestamp: "2026-08-14T13:30:00Z", open: 1, high: 2, low: 1, close: 2, volume: 10 }] }),
  },
}));

const sig = { ticker: "AAPL", action: "BUY", confidence: 0.8, indicators: {}, reasons: [] };
const mount = () => render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} compact />);
const tile = () => screen.getByText(/SIGNALS UNAVAILABLE|NO SIGNAL|LOADING SIGNALS|ATTR AAPL/);

beforeEach(() => { getEquitySignals.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("compact desk chart signal state", () => {
  it("a failed read says SIGNALS UNAVAILABLE, not NO SIGNAL", async () => {
    getEquitySignals.mockRejectedValue(new Error("503"));
    mount();
    await waitFor(() => expect(tile()).toHaveTextContent("SIGNALS UNAVAILABLE"));
    expect(screen.queryByText("NO SIGNAL")).toBeNull();
  });

  it("a malformed answer (no signals list) is unavailable too", async () => {
    getEquitySignals.mockResolvedValue({ nope: true });
    mount();
    await waitFor(() => expect(tile()).toHaveTextContent("SIGNALS UNAVAILABLE"));
  });

  it("a successful empty read is NO SIGNAL", async () => {
    getEquitySignals.mockResolvedValue({ signals: [] });
    mount();
    await waitFor(() => expect(tile()).toHaveTextContent("NO SIGNAL"));
    expect(screen.queryByText("SIGNALS UNAVAILABLE")).toBeNull();
  });

  it("shows loading before the first answer, not NO SIGNAL", () => {
    getEquitySignals.mockReturnValue(new Promise(() => {}));
    mount();
    expect(tile()).toHaveTextContent("LOADING SIGNALS");
  });

  it("a signal is shown when the read succeeds", async () => {
    getEquitySignals.mockResolvedValue({ signals: [sig] });
    mount();
    await waitFor(() => expect(tile()).toHaveTextContent("ATTR AAPL"));
  });

  it("a failed refresh clears the old signal instead of leaving it looking current", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getEquitySignals.mockResolvedValueOnce({ signals: [sig] });
    mount();
    await waitFor(() => expect(tile()).toHaveTextContent("ATTR AAPL"));
    getEquitySignals.mockRejectedValue(new Error("503"));
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    await waitFor(() => expect(tile()).toHaveTextContent("SIGNALS UNAVAILABLE"));
    expect(screen.queryByText("ATTR AAPL")).toBeNull();
  });

  it("a slower, older answer does not overwrite a newer one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let releaseOld!: (v: unknown) => void;
    getEquitySignals.mockReturnValueOnce(new Promise((res) => { releaseOld = res; }));   // first read: slow
    mount();
    getEquitySignals.mockRejectedValueOnce(new Error("503"));                          // second read: fails fast
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    await waitFor(() => expect(tile()).toHaveTextContent("SIGNALS UNAVAILABLE"));
    await act(async () => { releaseOld({ signals: [sig] }); });                        // the old answer finally lands
    expect(tile()).toHaveTextContent("SIGNALS UNAVAILABLE");                           // and is ignored
  });

  it("a slower, older FAILURE does not overwrite a newer success", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let failOld!: (e: unknown) => void;
    getEquitySignals.mockReturnValueOnce(new Promise((_, rej) => { failOld = rej; }));   // first read: slow, will fail
    mount();
    getEquitySignals.mockResolvedValueOnce({ signals: [sig] });                          // second read: succeeds
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    await waitFor(() => expect(tile()).toHaveTextContent("ATTR AAPL"));
    await act(async () => { failOld(new Error("late")); });                              // the old failure lands late
    expect(tile()).toHaveTextContent("ATTR AAPL");                                       // and is ignored
  });
});
