/**
 * Mobile collapse behaviour for the capital-at-risk row.
 *
 * Separate file from GlobalRiskStatus.test.tsx on purpose: vi.mock is hoisted
 * to the top of whichever file it appears in, so forcing useIsMobile true in
 * the shared file would silently put every desktop assertion there into
 * mobile mode.
 *
 * Six chips wrapped to three rows on a phone, mostly to report that nothing
 * was wrong. These pin the rule that makes collapsing safe: it is by severity,
 * never by name, so an alarm can never be the thing that folds away.
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import GlobalRiskStatus from "../GlobalRiskStatus";
import { api } from "../../api/client";

vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => true }));

vi.mock("../../api/client", () => ({
  api: {
    getExecutionMode: vi.fn(),
    getTradeDeskKillSwitch: vi.fn(),
    getKillSwitchStatus: vi.fn(),
    getPortfolioState: vi.fn(),
  },
}));

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function mockBroker(body: object, ok = true) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: () => Promise.resolve(body),
  }) as unknown as typeof fetch;
}

/** Everything reporting normally — the case that should collapse. */
function mockAllNominal() {
  mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
  mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
  mockedApi.getPortfolioState.mockResolvedValue({
    state: { daily_loss_pct: 0.001, max_daily_loss_pct: 0.03 },
  });
  mockBroker({ broker: "IBKR", paper_mode: false, connected: true });
}

// Braces matter: a bare arrow returns VitestUtils, which does not satisfy the
// hook's Awaitable<void> return type.
beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("GlobalRiskStatus — mobile collapse", () => {
  it("folds nominal chips behind a count", async () => {
    mockAllNominal();
    render(<GlobalRiskStatus />);

    const toggle = await screen.findByRole("button", { name: /show \d+ nominal/i });
    expect(toggle).toHaveTextContent(/\+\d+ OK/);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("NOT ARMED")).not.toBeInTheDocument();
  });

  it("opens the folded chips on tap", async () => {
    mockAllNominal();
    render(<GlobalRiskStatus />);

    fireEvent.click(await screen.findByRole("button", { name: /show \d+ nominal/i }));
    await waitFor(() => expect(screen.getByText("NOT ARMED")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /hide nominal/i })).toBeInTheDocument();
  });

  it("never folds away an armed kill switch", async () => {
    mockAllNominal();
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: true });
    render(<GlobalRiskStatus />);

    expect(await screen.findByText("ARMED")).toBeInTheDocument();
    expect(screen.queryByText("NOT ARMED")).not.toBeInTheDocument();
  });

  it("never folds away a disconnected broker", async () => {
    mockAllNominal();
    mockBroker({ broker: "IBKR", paper_mode: false, connected: false });
    render(<GlobalRiskStatus />);
    expect(await screen.findByText("DISCONNECTED")).toBeInTheDocument();
  });

  it("never folds away autopilot being on", async () => {
    mockAllNominal();
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "autopilot" });
    render(<GlobalRiskStatus />);
    expect(await screen.findByText("AUTOPILOT ON")).toBeInTheDocument();
  });

  it("never folds away a status it could not read", async () => {
    // A failed read is summarised, never painted as a nominal/safe value.
    mockAllNominal();
    mockedApi.getExecutionMode.mockRejectedValue(new Error("boom"));
    render(<GlobalRiskStatus />);
    expect(await screen.findByText("LIVE STATUS UNAVAILABLE")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show details/i }));
    expect(await screen.findByText("AUTOPILOT UNKNOWN")).toBeInTheDocument();
  });

  it("offers no toggle when every chip needs attention", async () => {
    // Nothing to fold — the toggle would claim a saving it cannot make.
    mockedApi.getExecutionMode.mockRejectedValue(new Error("boom"));
    mockedApi.getTradeDeskKillSwitch.mockRejectedValue(new Error("boom"));
    mockedApi.getKillSwitchStatus.mockRejectedValue(new Error("boom"));
    mockedApi.getPortfolioState.mockRejectedValue(new Error("boom"));
    mockBroker({}, false);
    render(<GlobalRiskStatus />);

    await screen.findByText("LIVE STATUS UNAVAILABLE");
    expect(screen.queryByRole("button", { name: /nominal/i })).not.toBeInTheDocument();
  });
});
