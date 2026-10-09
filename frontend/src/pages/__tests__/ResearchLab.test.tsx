import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";

import ResearchLab from "../ResearchLab";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    runBacktest: vi.fn(),
    getBacktestResults: vi.fn(),
    transitionExperiment: vi.fn(),
  },
}));

const mockedApi = api as unknown as {
  runBacktest: ReturnType<typeof vi.fn>;
  getBacktestResults: ReturnType<typeof vi.fn>;
  transitionExperiment: ReturnType<typeof vi.fn>;
};

function experiment(overrides: object) {
  return {
    id: "exp-1", name: "SPY bull put", strategy: "bull_put_spread", hypothesis: null,
    stage: "draft", backtest_metrics: null, paper_perf: null, baseline: null,
    ...overrides,
  };
}

function mockExperimentList(experiments: object[]) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ experiments }),
  }) as unknown as typeof fetch;
}

async function flushPolls(times: number) {
  for (let i = 0; i < times; i++) {
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ResearchLab — real backtest wiring", () => {
  it("runs a real backtest and sends the run id, never the numbers", async () => {
    mockExperimentList([experiment({})]);
    mockedApi.runBacktest.mockResolvedValue({ run_id: "run-1", status: "queued" });
    mockedApi.getBacktestResults
      .mockResolvedValueOnce({ status: "running" })
      .mockResolvedValueOnce({ status: "completed", sharpe_ratio: 1.42, total_return_pct: 18.3, max_drawdown_pct: 9.1 });
    mockedApi.transitionExperiment.mockResolvedValue({ ok: true, experiment: experiment({ stage: "backtested" }) });

    render(<ResearchLab />);
    await waitFor(() => expect(screen.getByText("SPY bull put")).toBeInTheDocument());

    fireEvent.click(screen.getByText("→ backtested"));
    await waitFor(() => expect(screen.getByText(/running backtest…/)).toBeInTheDocument());

    await flushPolls(2);

    // The client posts the RUN ID. It used to post the metrics it had mapped
    // itself, which left the server unable to tell measured numbers from
    // typed ones — the same door the hardcoded demo values walked through.
    // Provenance and the sharpe_ratio → sharpe mapping are now server-side.
    await waitFor(() => expect(mockedApi.transitionExperiment).toHaveBeenCalledWith(
      "exp-1",
      { target: "backtested", backtest_run_id: "run-1" },
    ));
    const calls = mockedApi.transitionExperiment.mock.calls;
    const [, body] = calls[calls.length - 1];
    expect(body).not.toHaveProperty("metrics");
  });

  it("shows an error and never calls transition when the backtest fails", async () => {
    mockExperimentList([experiment({})]);
    mockedApi.runBacktest.mockResolvedValue({ run_id: "run-1", status: "queued" });
    mockedApi.getBacktestResults.mockResolvedValue({ status: "failed", error: "data provider unavailable" });

    render(<ResearchLab />);
    await waitFor(() => expect(screen.getByText("SPY bull put")).toBeInTheDocument());
    fireEvent.click(screen.getByText("→ backtested"));
    await flushPolls(1);

    await waitFor(() => expect(screen.getByText(/backtest error — data provider unavailable/)).toBeInTheDocument());
    expect(mockedApi.transitionExperiment).not.toHaveBeenCalled();
  });

  it("times out after 3 minutes of polling without a fake-metrics fallback", async () => {
    mockExperimentList([experiment({})]);
    mockedApi.runBacktest.mockResolvedValue({ run_id: "run-1", status: "queued" });
    mockedApi.getBacktestResults.mockResolvedValue({ status: "running" });

    render(<ResearchLab />);
    await waitFor(() => expect(screen.getByText("SPY bull put")).toBeInTheDocument());
    fireEvent.click(screen.getByText("→ backtested"));

    await flushPolls(121); // 121 * 1500ms > 180_000ms cap

    await waitFor(() => expect(screen.getByText(/timed out after 3 minutes/)).toBeInTheDocument());
    expect(mockedApi.transitionExperiment).not.toHaveBeenCalled();
  });

  it("surfaces a network error mid-poll instead of hanging", async () => {
    mockExperimentList([experiment({})]);
    mockedApi.runBacktest.mockResolvedValue({ run_id: "run-1", status: "queued" });
    mockedApi.getBacktestResults.mockRejectedValue(new Error("network error"));

    render(<ResearchLab />);
    await waitFor(() => expect(screen.getByText("SPY bull put")).toBeInTheDocument());
    fireEvent.click(screen.getByText("→ backtested"));
    await flushPolls(1);

    await waitFor(() => expect(screen.getByText(/backtest error — network error/)).toBeInTheDocument());
    expect(mockedApi.transitionExperiment).not.toHaveBeenCalled();
  });

  it("distinguishes a legitimate gate rejection from an infra failure", async () => {
    mockExperimentList([experiment({})]);
    mockedApi.runBacktest.mockResolvedValue({ run_id: "run-1", status: "queued" });
    mockedApi.getBacktestResults.mockResolvedValue({
      status: "completed", sharpe_ratio: 0.1, total_return_pct: -2.0, max_drawdown_pct: 40.0,
    });
    mockedApi.transitionExperiment.mockResolvedValue({
      ok: false, reason: "backtest gate failed: sharpe 0.10 < 0.50",
    });

    render(<ResearchLab />);
    await waitFor(() => expect(screen.getByText("SPY bull put")).toBeInTheDocument());
    fireEvent.click(screen.getByText("→ backtested"));
    await flushPolls(1);

    const msg = await screen.findByText(/backtest gate failed: sharpe 0.10 < 0.50/);
    // Gate rejection is amber (var(--amber)), not red (var(--red)) — distinct
    // from every infra-failure path, which uses red.
    expect(msg).toHaveStyle({ color: "var(--amber)" });
  });

  it("labels the still-fake walk-forward → paper transition as a demo", async () => {
    mockExperimentList([experiment({ stage: "walk_forward" })]);
    render(<ResearchLab />);
    await waitFor(() => expect(screen.getByText("SPY bull put")).toBeInTheDocument());
    expect(screen.getByText("(demo)")).toBeInTheDocument();
  });

  it("keeps other experiments' buttons live while one is mid-backtest", async () => {
    mockExperimentList([
      experiment({ id: "exp-1", name: "SPY bull put" }),
      experiment({ id: "exp-2", name: "QQQ iron condor", strategy: "iron_condor" }),
    ]);
    mockedApi.runBacktest.mockResolvedValue({ run_id: "run-1", status: "queued" });
    mockedApi.getBacktestResults.mockReturnValue(new Promise(() => {})); // never resolves — stays mid-poll

    render(<ResearchLab />);
    await waitFor(() => expect(screen.getByText("SPY bull put")).toBeInTheDocument());

    const buttons = screen.getAllByText("→ backtested");
    fireEvent.click(buttons[0]);

    await waitFor(() => expect(screen.getByText(/running backtest…/)).toBeInTheDocument());
    const remaining = screen.getAllByText("→ backtested");
    expect(remaining).toHaveLength(1);
    expect(remaining[0]).not.toBeDisabled();
  });
});
