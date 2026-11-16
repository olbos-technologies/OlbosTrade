/**
 * Which charts get the volume profile. The Chart page (Markets > Chart) asks for it, with a
 * toggle whose saved choice it shares with the Pro Workspace; the compact Equity and Options desk
 * charts do not, so they are unchanged. The chart is mocked to record what it is asked for.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import ChartWorkstation from "../ChartWorkstation";
import { PROFILE_PREF_KEY } from "../../workspace/profilePref";

const asked: Array<{ volumeProfile?: boolean }> = [];
vi.mock("../../components/CandlestickChart", () => ({
  default: (props: { volumeProfile?: boolean }) => { asked.push({ volumeProfile: props.volumeProfile }); return <div data-testid="chart" />; },
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
    getEquityChart: vi.fn().mockResolvedValue({ bars: [
      { timestamp: "2026-08-14T13:30:00Z", open: 1, high: 2, low: 1, close: 2, volume: 10 },
    ] }),
  },
}));

const lastAsk = () => asked[asked.length - 1];
const profileButton = () => screen.queryByRole("button", { name: "Volume profile" });

beforeEach(() => { asked.length = 0; localStorage.clear(); });
afterEach(() => { cleanup(); localStorage.clear(); });

describe("the Chart page", () => {
  it("asks its chart for the volume profile by default and has a toggle", async () => {
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(lastAsk().volumeProfile).toBe(true);
    expect(profileButton()).toHaveAttribute("aria-pressed", "true");
  });

  it("the toggle turns it off and on, and remembers the choice in the shared preference", async () => {
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    fireEvent.click(profileButton()!);
    await waitFor(() => expect(lastAsk().volumeProfile).toBe(false));
    expect(profileButton()).toHaveAttribute("aria-pressed", "false");
    expect(localStorage.getItem(PROFILE_PREF_KEY)).toBe("off");        // the same key the Workspace reads
    fireEvent.click(profileButton()!);
    await waitFor(() => expect(lastAsk().volumeProfile).toBe(true));
    expect(localStorage.getItem(PROFILE_PREF_KEY)).toBe("on");
  });

  it("starts off when the shared preference was turned off in the Workspace", async () => {
    localStorage.setItem(PROFILE_PREF_KEY, "off");
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(lastAsk().volumeProfile).toBe(false);
    expect(profileButton()).toHaveAttribute("aria-pressed", "false");
  });

  it("survives blocked browser storage and falls back to the default (on)", async () => {
    const real = Object.getOwnPropertyDescriptor(window, "localStorage")!;
    Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new Error("blocked"); } });
    try {
      render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} />);
      await waitFor(() => expect(asked.length).toBeGreaterThan(0));
      expect(lastAsk().volumeProfile).toBe(true);
      fireEvent.click(profileButton()!);                               // must not throw
      await waitFor(() => expect(lastAsk().volumeProfile).toBe(false));
    } finally {
      Object.defineProperty(window, "localStorage", real);
    }
  });
});

describe("the compact Equity and Options desk chart", () => {
  it("is unchanged: no volume profile asked for, and no toggle", async () => {
    render(<ChartWorkstation symbol="AAPL" onSymbolChange={() => {}} compact />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(asked.every((a) => !a.volumeProfile)).toBe(true);
    expect(profileButton()).toBeNull();
  });
});
