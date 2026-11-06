import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import RiskMonitor from "../RiskMonitor";
import { api } from "../../api/client";
import { useRisk } from "../../hooks/useRisk";

vi.mock("../../hooks/useRisk", () => ({ useRisk: vi.fn() }));
vi.mock("../../components/RotationReviewPanel", () => ({ default: () => null }));
vi.mock("../../api/client", () => ({ getOperatorApiKey: () => "", setOperatorApiKey: vi.fn(), api: {
  getTradeDeskKillSwitch: vi.fn(), getCatalystCalendar: vi.fn(), setTradeDeskKillSwitch: vi.fn(),
} }));

const withGuard = (guardrailStatus: object) => vi.mocked(useRisk).mockReturnValue({
  riskState: { state: { account_value: 25000, daily_pnl: 0, open_positions: 0 } },
  guardrailStatus: { trading_allowed: true, daily_loss_pct: 0, weekly_loss_pct: 0, monthly_loss_pct: 0,
    drawdown_pct: 0, consecutive_losses: 0, ...guardrailStatus },
  portfolioStateError: false,
} as unknown as ReturnType<typeof useRisk>);

const tradesToday = () => screen.getByText("Trades Today").parentElement!.parentElement as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({ engaged: false });
  vi.mocked(api.getCatalystCalendar).mockResolvedValue({ events: [] });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Risk page Trades Today meter", () => {
  it("uses the server's cap", async () => {
    withGuard({ trades_today: 4, max_trades_per_day: 30 });
    await act(async () => { render(<RiskMonitor />); });
    const row = tradesToday();
    expect(row.textContent).toContain("4.00");
    expect(row.textContent).toContain("/ 30");
    expect(row.textContent).not.toMatch(/\/ 3(?!\d)/);
    expect(row.querySelector(".bar-fill")).not.toBeNull();
  });

  it("shows the count with an unknown limit and no bar when the cap is not reported", async () => {
    withGuard({ trades_today: 4 });
    await act(async () => { render(<RiskMonitor />); });
    const row = tradesToday();
    expect(row.textContent).toContain("4.00");
    expect(row.textContent).toContain("/ —");
    expect(row.querySelector(".bar-fill")).toBeNull();
  });

  it("still reports an unknown count as Unavailable", async () => {
    withGuard({ max_trades_per_day: 30 });
    await act(async () => { render(<RiskMonitor />); });
    const row = tradesToday();
    expect(row.textContent).toContain("Unavailable");
    expect(row.querySelector(".bar-fill")).toBeNull();
  });
});
