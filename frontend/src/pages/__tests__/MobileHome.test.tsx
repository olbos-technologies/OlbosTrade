import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import MobileHome, { splitPending } from "../MobileHome";
import { api } from "../../api/client";

const nav = vi.fn();
vi.mock("../../components/TerminalNavContext", () => ({ useTerminalNav: () => nav }));
vi.mock("../../components/KillSwitchButton", () => ({ default: () => <button>Existing kill switch</button> }));
vi.mock("../../api/client", () => ({ api: {
  getExecutionMode: vi.fn(), getTradeDeskKillSwitch: vi.fn(), getPositions: vi.fn(),
  getPendingApprovals: vi.fn(), setExecutionMode: vi.fn(),
} }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getExecutionMode).mockResolvedValue({ mode: "autopilot", persistence: "confirmed" });
  vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({ engaged: false });
  vi.mocked(api.getPositions).mockResolvedValue({ positions: [{ symbol: "SPY", unrealized_pnl: -88, tracked: true }] });
  vi.mocked(api.getPendingApprovals).mockResolvedValue({ pending: [{ id: "a", queued_at: new Date().toISOString() }] });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, json: async () => url.includes("dashboard") ? { total_equity: 261074.34, total_pnl: 100, day_pnl: -25 }
      : url.includes("broker") ? { broker: "ibkr", paper_mode: true, status: "connected" }
      : { available: true, source: "refreshed", unprotected_is_reliable: true, positions_without_stop: [] },
  })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Mobile Home truth and controls", () => {
  it("shows broker balance and trade P&L with their distinct scopes", async () => {
    render(<MobileHome />);
    expect(await screen.findByText("$261,074.34")).toBeInTheDocument();
    expect(screen.getByText("−$25.00")).toBeInTheDocument();
    expect(screen.getByText("−$88.00")).toBeInTheDocument();
    expect(screen.getByText("IBKR · PAPER")).toBeInTheDocument();
    expect(screen.getByText("Olbos day P&L")).toBeInTheDocument();
  });
  it("never turns failed reads into an empty queue or zero balance", async () => {
    vi.mocked(api.getPendingApprovals).mockRejectedValue(new Error("offline"));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<MobileHome />);
    expect(await screen.findByText("Signal queue unavailable.")).toBeInTheDocument();
    expect(await screen.findByText("Account data unavailable. Refresh to retry.")).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
    expect(screen.getByText("Stop coverage unverified")).toBeInTheDocument();
  });
  it("does not claim stop protection from cached orders", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ available: true, source: "cache", unprotected_is_reliable: false, positions_without_stop: [] }),
    }));
    render(<MobileHome />);
    await waitFor(() => expect(vi.mocked(api.getPositions)).toHaveBeenCalled());
    expect(screen.getByText("Stop coverage unverified")).toBeInTheDocument();
    expect(screen.queryByText("No missing stops reported")).not.toBeInTheDocument();
  });
  it("labels failed persistence as unconfirmed after stopping automation", async () => {
    vi.mocked(api.setExecutionMode).mockResolvedValue({ mode: "manual", persistence: "unconfirmed" });
    render(<MobileHome />);
    await screen.findByText("AUTOPILOT");
    fireEvent.click(screen.getByRole("button", { name: "Stop automation" }));
    expect(await screen.findByRole("status")).toHaveTextContent("saved state is unconfirmed");
    expect(api.setExecutionMode).toHaveBeenCalledWith("manual");
  });
  it("surfaces a rejected stop without claiming Manual was applied", async () => {
    vi.mocked(api.setExecutionMode).mockRejectedValue(new Error("Not authorized"));
    render(<MobileHome />);
    await screen.findByText("AUTOPILOT");
    fireEvent.click(screen.getByRole("button", { name: "Stop automation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not authorized");
    expect(screen.queryByText("Manual mode confirmed. New automated entries stopped.")).not.toBeInTheDocument();
  });
  it("HALTED overrides the mode and disables the stop-automation button", async () => {
    vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({ engaged: true });
    render(<MobileHome />);
    await screen.findByText("HALTED");
    expect(screen.getByRole("button", { name: "Stop automation" })).toBeDisabled();
  });
  it("routes review into the existing approval queue without submitting orders", async () => {
    render(<MobileHome />);
    await screen.findByText("AUTOPILOT");
    fireEvent.click(screen.getByRole("button", { name: /Review signal queue/ }));
    expect(nav).toHaveBeenCalledWith("trade:copilot");
    expect(api.setExecutionMode).not.toHaveBeenCalled();
  });
  it("keeps recent, older and invalid/future timestamps separate", () => {
    const now = Date.UTC(2026, 9, 17);
    const result = splitPending([
      { id: "recent", queued_at: new Date(now - 1000).toISOString() },
      { id: "boundary", queued_at: new Date(now - 86400000).toISOString() },
      { id: "invalid", queued_at: "bad" },
      { id: "future", queued_at: new Date(now + 1).toISOString() },
      { id: "missing" },
    ], now);
    expect(result).toEqual({ recent: 1, older: 1, unknown: 3 });
  });
});

