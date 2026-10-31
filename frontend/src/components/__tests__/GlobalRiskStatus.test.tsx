import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import GlobalRiskStatus from "../GlobalRiskStatus";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    getExecutionMode: vi.fn(),
    getTradeDeskKillSwitch: vi.fn(),
    getKillSwitchStatus: vi.fn(),
    getPortfolioState: vi.fn(),
  },
}));

const mockedApi = api as unknown as {
  getExecutionMode: ReturnType<typeof vi.fn>;
  getTradeDeskKillSwitch: ReturnType<typeof vi.fn>;
  getKillSwitchStatus: ReturnType<typeof vi.fn>;
  getPortfolioState: ReturnType<typeof vi.fn>;
};

function mockBroker(
  body: object,
  ok = true,
  regime: object = { equity_allowed: true, regime: "risk_on" },
) {
  globalThis.fetch = vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("/api/market/regime")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(regime),
      });
    }
    return Promise.resolve({
      ok,
      status: ok ? 200 : 500,
      json: () => Promise.resolve(body),
    });
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GlobalRiskStatus", () => {
  it.each([{}, { state: null }, { state: {} }, { state: { daily_loss_pct: "0" } }])(
    "keeps malformed risk state visible as unavailable: %j", async (response) => {
      mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
      mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
      mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
      mockedApi.getPortfolioState.mockResolvedValue(response);
      render(<GlobalRiskStatus />);
      await waitFor(() => expect(screen.getAllByText("UNAVAILABLE").length).toBeGreaterThanOrEqual(2));
      expect(screen.getByRole("region", { name: "Capital-at-risk status" })).toBeInTheDocument();
      expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
    }
  );
  it("shows LIVE — IBKR when connected and not in paper mode", async () => {
    mockBroker({ broker: "ibkr", paper_mode: false, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    mockedApi.getPortfolioState.mockResolvedValue({ state: { daily_loss_pct: 0.01, max_daily_loss_pct: 0.02 } });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("LIVE — IBKR")).toBeInTheDocument());
  });

  it("shows PAPER environment distinctly from live", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "autopilot" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    mockedApi.getPortfolioState.mockResolvedValue({ state: {} });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("PAPER — IBKR")).toBeInTheDocument());
    expect(screen.getByText("AUTOPILOT ON")).toBeInTheDocument();
  });

  it("shows AUTOPILOT OFF for manual/copilot mode", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "copilot" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    mockedApi.getPortfolioState.mockResolvedValue({ state: {} });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("AUTOPILOT OFF")).toBeInTheDocument());
  });

  it("shows KILL HALTED when the kill switch is engaged", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: true });
    mockedApi.getPortfolioState.mockResolvedValue({ state: {} });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("HALTED")).toBeInTheDocument());
  });

  it("shows KILL UNKNOWN rather than defaulting to not-armed when both kill-switch reads fail", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
    mockedApi.getTradeDeskKillSwitch.mockRejectedValue(new Error("network error"));
    mockedApi.getKillSwitchStatus.mockRejectedValue(new Error("network error"));
    mockedApi.getPortfolioState.mockResolvedValue({ state: {} });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("UNKNOWN")).toBeInTheDocument());
    // Must never render "NOT ARMED" (a safe-looking default) when the read failed.
    expect(screen.queryByText("NOT ARMED")).not.toBeInTheDocument();
  });

  it("shows ENV DISCONNECTED when the broker reports not connected", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "disconnected", error: "socket closed" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    mockedApi.getPortfolioState.mockResolvedValue({ state: {} });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("DISCONNECTED")).toBeInTheDocument());
  });

  it("derives remaining risk budget only from two verified same-source values", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    // daily_loss_pct is a signed P&L ratio — negative here means an actual
    // 0.5% loss, consuming half a percent of the 2% budget.
    mockedApi.getPortfolioState.mockResolvedValue({ state: { daily_loss_pct: -0.005, max_daily_loss_pct: 0.02 } });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("1.5%")).toBeInTheDocument());
  });

  it("shows the full risk budget on a gain day, not a reduced one", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    // A positive daily_loss_pct is a gain — it must not eat into the
    // displayed loss budget (the bug this fix addresses).
    mockedApi.getPortfolioState.mockResolvedValue({ state: { daily_loss_pct: 0.005, max_daily_loss_pct: 0.02 } });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("2.0%")).toBeInTheDocument());
  });

  it("shows risk budget as unavailable rather than guessing when the limit is missing", async () => {
    mockBroker({ broker: "ibkr", paper_mode: true, status: "connected" });
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "manual" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    mockedApi.getPortfolioState.mockResolvedValue({ state: { daily_loss_pct: 0.005 } });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getAllByText("UNAVAILABLE").length).toBeGreaterThan(0));
  });

  it("shows ENTRIES BLOCKED when the regime refuses new equity entries", async () => {
    mockBroker(
      { broker: "ibkr", paper_mode: true, status: "connected" },
      true,
      { equity_allowed: false, regime: "crisis" },
    );
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "autopilot" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    mockedApi.getPortfolioState.mockResolvedValue({
      state: { daily_loss_pct: 0, max_daily_loss_pct: 0.02 },
    });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("BLOCKED")).toBeInTheDocument());
    expect(screen.getByText("AUTOPILOT ON")).toBeInTheDocument();
    expect(screen.getByText("ENTRIES")).toBeInTheDocument();
  });

  it("omits the entries chip when the regime allows equity entries", async () => {
    mockBroker(
      { broker: "ibkr", paper_mode: true, status: "connected" },
      true,
      { equity_allowed: true, regime: "risk_on" },
    );
    mockedApi.getExecutionMode.mockResolvedValue({ mode: "autopilot" });
    mockedApi.getTradeDeskKillSwitch.mockResolvedValue({ engaged: false });
    mockedApi.getPortfolioState.mockResolvedValue({
      state: { daily_loss_pct: 0, max_daily_loss_pct: 0.02 },
    });

    render(<GlobalRiskStatus />);
    await waitFor(() => expect(screen.getByText("AUTOPILOT ON")).toBeInTheDocument());
    expect(screen.queryByText("BLOCKED")).not.toBeInTheDocument();
    expect(screen.queryByText("ENTRIES")).not.toBeInTheDocument();
  });
});
