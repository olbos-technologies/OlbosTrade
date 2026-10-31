import { describe, it, expect } from "vitest";
import {
  formatExecutionModeDisplay,
  chartLevelColors,
  toChartSignalAttribution,
  tradePlanCardTitle,
} from "../chartWorkstationDisplay";
import { statusLabelForPage, filterNavForDisplay, type NavGroup } from "../navLabels";

describe("formatExecutionModeDisplay", () => {
  it("shows HALTED when kill switch is active regardless of mode", () => {
    expect(formatExecutionModeDisplay("autopilot", true)).toEqual({
      label: "Execution mode",
      value: "HALTED",
      tone: "var(--red)",
    });
  });

  it("never shows AUTOPILOT READY for manual mode", () => {
    const result = formatExecutionModeDisplay("manual", false);
    expect(result.value).toBe("MANUAL");
    expect(result.value).not.toMatch(/READY/i);
    expect(result.label).toBe("Execution mode");
  });

  it("labels real autopilot only when mode is autopilot", () => {
    expect(formatExecutionModeDisplay("autopilot", false).value).toBe("AUTOPILOT");
    expect(formatExecutionModeDisplay("copilot", false).value).toBe("COPILOT");
  });
});

describe("chartLevelColors", () => {
  it("gives entry and resistance distinct colors", () => {
    const c = chartLevelColors();
    expect(c.entry).not.toBe(c.resistance);
  });
});

describe("tradePlanCardTitle", () => {
  it("uses Chart levels when there is no signal", () => {
    expect(tradePlanCardTitle(false)).toBe("Chart levels");
  });

  it("uses Trade plan when a signal is selected", () => {
    expect(tradePlanCardTitle(true, "background_scanner")).toBe("Trade plan");
    expect(tradePlanCardTitle(true)).toBe("Trade plan");
  });
});

describe("toChartSignalAttribution", () => {
  it("does not invent a source name", () => {
    const data = toChartSignalAttribution({
      action: "BUY",
      confidence: 0.7,
      generated_at: "2026-07-18T12:00:00Z",
    });
    expect(data.source).toBe("unknown");
    expect(data.timeframe).toBeNull();
    expect(data.authority).toBe("unknown");
  });

  it("passes through a real source from the payload", () => {
    const data = toChartSignalAttribution({
      action: "SELL",
      confidence: 0.6,
      source: "equity_signal_engine",
    });
    expect(data.source).toBe("equity_signal_engine");
    expect(data.direction).toBe("SELL");
  });
});

const SAMPLE_NAV: NavGroup[] = [
  { id: "dashboard", label: "Command Center", icon: "dashboard", key: "dashboard" },
  {
    id: "markets",
    label: "Markets",
    icon: "markets",
    children: [
      { key: "markets:chart", label: "Chart" },
      { key: "markets:watchlists", label: "Watchlists" },
      { key: "markets:heatmaps", label: "Heatmaps", advanced: true },
    ],
  },
  {
    id: "trade",
    label: "Trade Desk",
    icon: "paper",
    children: [
      { key: "trade:logs", label: "P&L Breakdown" },
      { key: "trade:execlog", label: "Execution Log" },
    ],
  },
  {
    id: "lab",
    label: "Research",
    icon: "lab",
    advanced: true,
    children: [{ key: "lab:scenario", label: "Scenario Lab" }],
  },
];

describe("statusLabelForPage", () => {
  it("labels a top-level leaf", () => {
    expect(statusLabelForPage("dashboard", SAMPLE_NAV)).toBe("COMMAND CENTER");
  });

  it("labels a nested leaf as GROUP · LEAF", () => {
    expect(statusLabelForPage("markets:chart", SAMPLE_NAV)).toBe("MARKETS · CHART");
  });

  it("does not hardcode TRADE DESK for every page", () => {
    expect(statusLabelForPage("markets:chart", SAMPLE_NAV)).not.toBe("TRADE DESK");
    expect(statusLabelForPage("trade:logs", SAMPLE_NAV)).toBe("TRADE DESK · P&L BREAKDOWN");
  });
});

describe("filterNavForDisplay", () => {
  it("hides advanced groups and leaves by default", () => {
    const core = filterNavForDisplay(SAMPLE_NAV, false, "dashboard");
    expect(core.map((g) => g.id)).toEqual(["dashboard", "markets", "trade"]);
    const markets = core.find((g) => g.id === "markets")!;
    expect(markets.children?.map((c) => c.key)).toEqual([
      "markets:chart",
      "markets:watchlists",
    ]);
  });

  it("keeps an advanced leaf visible when it is the active page", () => {
    const core = filterNavForDisplay(SAMPLE_NAV, false, "markets:heatmaps");
    const markets = core.find((g) => g.id === "markets")!;
    expect(markets.children?.map((c) => c.key)).toContain("markets:heatmaps");
  });

  it("shows advanced groups when Advanced is open", () => {
    const all = filterNavForDisplay(SAMPLE_NAV, true, "dashboard");
    expect(all.map((g) => g.id)).toContain("lab");
    const markets = all.find((g) => g.id === "markets")!;
    expect(markets.children?.map((c) => c.key)).toContain("markets:heatmaps");
  });

  it("keeps core destinations reachable without Advanced", () => {
    const core = filterNavForDisplay(SAMPLE_NAV, false, "dashboard");
    const keys = core.flatMap((g) => [
      ...(g.key ? [g.key] : []),
      ...(g.children?.map((c) => c.key) ?? []),
    ]);
    expect(keys).toEqual(
      expect.arrayContaining(["dashboard", "markets:chart", "markets:watchlists", "trade:logs"]),
    );
    expect(keys).not.toContain("lab:scenario");
  });

  it("omits sidebar:false groups unless they own the active page", () => {
    const deskLoop: NavGroup[] = [
      { id: "dashboard", label: "Command Center", icon: "dashboard", key: "dashboard" },
      {
        id: "lab",
        label: "Research",
        icon: "lab",
        sidebar: false,
        children: [{ key: "lab:scenario", label: "Scenario Lab" }],
      },
    ];
    expect(filterNavForDisplay(deskLoop, true, "dashboard").map((g) => g.id)).toEqual(["dashboard"]);
    expect(
      filterNavForDisplay(deskLoop, true, "lab:scenario").find((g) => g.id === "lab")?.children?.map(
        (c) => c.key,
      ),
    ).toEqual(["lab:scenario"]);
  });
});
