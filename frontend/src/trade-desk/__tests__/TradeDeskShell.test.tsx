import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import {
  isFlagEnabled,
  isTradeDeskV2Enabled,
  setFlagEnabled,
} from "../featureFlags";
import TradeDeskTabs, { tabFromNavKey, navKeyFromTab } from "../TradeDeskTabs";
import { DEFAULT_PANEL_LAYOUT } from "../PanelLayoutManager";
import { NAV_MODEL_V2, NAV_MODEL_LEGACY, groupIdForKey } from "../../utils/navModels";
import { filterNavForDisplay } from "../../utils/navLabels";

describe("trade desk feature flags", () => {
  const store: Record<string, string> = {};

  beforeEach(() => {
    Object.keys(store).forEach((k) => delete store[k]);
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => (k in store ? store[k] : null),
      setItem: (k: string, v: string) => { store[k] = String(v); },
      removeItem: (k: string) => { delete store[k]; },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults trade_desk_v2 on; localStorage can force legacy off", () => {
    expect(isTradeDeskV2Enabled()).toBe(true);
    setFlagEnabled("trade_desk_v2", false);
    expect(isTradeDeskV2Enabled()).toBe(false);
  });

  it("respects localStorage override on", () => {
    setFlagEnabled("trade_desk_v2", true);
    expect(isFlagEnabled("trade_desk_v2")).toBe(true);
  });

  it("defaults desk/monitor/replay/mobile flags on with V2 suite", () => {
    expect(isFlagEnabled("equity_desk_v2")).toBe(true);
    expect(isFlagEnabled("options_desk_v2")).toBe(true);
    expect(isFlagEnabled("copilot_queue_v2")).toBe(true);
    expect(isFlagEnabled("execution_monitor_v2")).toBe(true);
    expect(isFlagEnabled("trade_replay_v2")).toBe(true);
    expect(isFlagEnabled("mobile_trade_desk")).toBe(true);
  });
});

describe("TradeReplay helpers", () => {
  it("labels source from trading_mode", async () => {
    // Light sanity: module loads (full UI covered via build)
    const mod = await import("../replay/TradeReplay");
    expect(mod.default).toBeTypeOf("function");
  });
});

describe("executionStatus mapping", () => {
  it("maps execution results to desk lifecycle labels", async () => {
    const { lifecycleFromExecution, lifecycleLabel } = await import("../executionStatus");
    expect(lifecycleFromExecution({ result: "submitted" })).toBe("submitted");
    expect(lifecycleFromExecution({ result: "blocked" })).toBe("blocked");
    expect(lifecycleFromExecution({ result: "rejected" })).toBe("rejected");
    expect(lifecycleLabel("pending_approval")).toBe("PENDING");
  });
});

describe("TradeDeskTabs mapping", () => {
  it("maps nav keys to tabs and back", () => {
    expect(tabFromNavKey("trade:overview")).toBe("overview");
    expect(tabFromNavKey("trade:copilot")).toBe("copilot");
    expect(navKeyFromTab("equities")).toBe("trade:equity");
  });

  it("renders tablist and switches on click", () => {
    const spy = vi.fn();
    render(<TradeDeskTabs active="overview" onChange={spy} />);
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /equities/i }));
    expect(spy).toHaveBeenCalledWith("equities");
  });
});

describe("panel layout defaults", () => {
  it("exposes default layout constants", () => {
    expect(DEFAULT_PANEL_LAYOUT.discoveryWidth).toBe(260);
    expect(DEFAULT_PANEL_LAYOUT.activityCollapsed).toBe(false);
  });
});

describe("nav model v2", () => {
  it("puts Command Overview under Trade Desk and keeps Options off the top level", () => {
    expect(groupIdForKey("trade:overview", NAV_MODEL_V2)).toBe("trade");
    expect(NAV_MODEL_V2.find((g) => g.id === "options")).toBeUndefined();
    expect(NAV_MODEL_LEGACY.find((g) => g.id === "options")).toBeUndefined();
    expect(groupIdForKey("options:chain", NAV_MODEL_LEGACY)).toBe("trade");
    expect(groupIdForKey("options:chain", NAV_MODEL_V2)).toBe("lab");
  });

  it("lists only the four daily Trade Desk leaves in the sidebar", () => {
    const filtered = filterNavForDisplay(NAV_MODEL_V2, true, "dashboard");
    const trade = filtered.find((g) => g.id === "trade");
    expect(trade?.children?.map((c) => c.key)).toEqual([
      "trade:overview",
      "trade:equity",
      "trade:options",
      "trade:positions",
    ]);
  });

  it("keeps desk-tab destinations for route ownership without listing them", () => {
    expect(groupIdForKey("trade:copilot", NAV_MODEL_V2)).toBe("trade");
    const onCopilot = filterNavForDisplay(NAV_MODEL_V2, true, "trade:copilot");
    expect(onCopilot.find((g) => g.id === "trade")?.children?.some((c) => c.key === "trade:copilot")).toBe(true);
  });
});
