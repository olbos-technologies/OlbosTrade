import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import CommandOverview from "../CommandOverview";

vi.mock("../../api/client", () => ({ api: {
  getPendingApprovals: vi.fn().mockRejectedValue(new Error("offline")),
  getExecutionLog: vi.fn().mockRejectedValue(new Error("offline")),
  getPositions: vi.fn().mockRejectedValue(new Error("offline")),
  getPortfolioState: vi.fn().mockRejectedValue(new Error("offline")),
  getEquitySignals: vi.fn().mockRejectedValue(new Error("offline")),
  getOptionsSignals: vi.fn().mockRejectedValue(new Error("offline")),
} }));
vi.mock("../../hooks/useDeskBlockContext", () => ({ useDeskBlockContext: () => ({
  minConfidence: null, killEngaged: false, tradingAllowed: true, guardrailAvailable: false,
  killStatusAvailable: false, consecutiveLosses: 0, maxConsecutiveLosses: null,
  maxConcurrent: 5, guardrailFlags: [],
}) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("Trade Desk overview failed reads", () => {
  it("does not present missing account and queue data as safe or empty", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<CommandOverview onNavigateTab={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Approval queue unavailable.")).toBeInTheDocument());
    expect(screen.getByText("Execution log unavailable.")).toBeInTheDocument();
    expect(screen.getByText("Equity signals unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("Allowed")).not.toBeInTheDocument();
    expect(screen.queryByText("Clear")).not.toBeInTheDocument();
    expect(screen.queryByText(/Flat · run a scan/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(3);
  });
});
