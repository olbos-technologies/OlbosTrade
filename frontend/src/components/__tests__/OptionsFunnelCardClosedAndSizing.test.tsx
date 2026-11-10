import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import OptionsFunnelCard from "../OptionsFunnelCard";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({ api: { getOptionsFunnel: vi.fn() } }));

const base = {
  window_days: 7, since: "", generated_at: "", execution_mode: "autopilot", regime: null, frequency_rule: null, scanned: 10,
  verdict: { code: "blocked_at_execution", text: "x" }, notes: [],
};

const mount = async () => { await act(async () => { render(<OptionsFunnelCard />); }); };

beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

describe("OptionsFunnelCard: closed-market attempts and sizing", () => {
  it("shows closed-market attempts as their own stage, apart from refusals", async () => {
    vi.mocked(api.getOptionsFunnel).mockResolvedValue({
      ...base,
      stages: [
        { key: "outside_market_hours", label: "Attempted while the market was closed (not a refusal)", count: 294, reasons: [{ reason: "market_closed: pre_market", count: 192 }] },
        { key: "execution_blocked", label: "Refused at execution", count: 96, reasons: [{ reason: "frequency_controller: below_min_confidence", count: 96 }] },
      ],
    } as never);
    await mount();
    expect(screen.getByTestId("funnel-stage-outside_market_hours").textContent).toContain("not a refusal");
    expect(screen.getByTestId("funnel-stage-execution_blocked").textContent).toContain("Refused at execution");
  });

  it("labels an all-closed-market verdict MARKET CLOSED, not a block", async () => {
    vi.mocked(api.getOptionsFunnel).mockResolvedValue({
      ...base, stages: [], verdict: { code: "market_closed_only", text: "all attempts were made while the market was closed" },
    } as never);
    await mount();
    expect(screen.getByText("MARKET CLOSED")).toBeInTheDocument();
  });

  it("explains a zero-size result with the budget against the spread's max loss", async () => {
    vi.mocked(api.getOptionsFunnel).mockResolvedValue({
      ...base, stages: [],
      sizing: { attempts: 6308, max_loss_median: 812, max_loss_min: 360, risk_budget: 400, cheapest_fits_one_contract: true },
    } as never);
    await mount();
    const text = screen.getByTestId("funnel-sizing").textContent ?? "";
    expect(text).toContain("$400");
    expect(text).toContain("$812");
    expect(text).toContain("2.0×");
    expect(text).toContain("would fit one contract");
  });

  it("says when even the cheapest spread is over budget", async () => {
    vi.mocked(api.getOptionsFunnel).mockResolvedValue({
      ...base, stages: [],
      sizing: { attempts: 3, max_loss_median: 812, max_loss_min: 700, risk_budget: 400, cheapest_fits_one_contract: false },
    } as never);
    await mount();
    expect(screen.getByTestId("funnel-sizing").textContent).toContain("Even the cheapest spread seen ($700) costs more than the budget");
  });

  it("says the budget is not recorded yet instead of guessing it", async () => {
    vi.mocked(api.getOptionsFunnel).mockResolvedValue({
      ...base, stages: [], sizing: { attempts: 5, max_loss_median: 812, max_loss_min: 812, risk_budget: null },
    } as never);
    await mount();
    expect(screen.getByTestId("funnel-sizing").textContent).toContain("recorded from the next scan");
  });

  it("shows nothing about sizing when no candidate was sized to zero", async () => {
    vi.mocked(api.getOptionsFunnel).mockResolvedValue({ ...base, stages: [], sizing: null } as never);
    await mount();
    expect(screen.queryByTestId("funnel-sizing")).toBeNull();
  });
});
