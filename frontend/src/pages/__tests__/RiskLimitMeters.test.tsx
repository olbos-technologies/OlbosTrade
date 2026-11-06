import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import RiskMonitor from "../RiskMonitor";
import { api } from "../../api/client";
import { useRisk } from "../../hooks/useRisk";

vi.mock("../../hooks/useRisk", () => ({ useRisk: vi.fn() }));
vi.mock("../../components/RotationReviewPanel", () => ({ default: () => null }));
vi.mock("../../api/client", () => ({ getOperatorApiKey: () => "", setOperatorApiKey: vi.fn(), api: {
  getTradeDeskKillSwitch: vi.fn(), getCatalystCalendar: vi.fn(), setTradeDeskKillSwitch: vi.fn(),
} }));

const LIMITS = {
  max_daily_loss_pct: 0.05, max_weekly_loss_pct: 0.10, max_monthly_loss_pct: 0.20, max_drawdown_pct: 0.30,
  max_consecutive_losses: 4, max_trades_per_day: 30, max_concurrent_positions: 3,
};
const use = (guardrailStatus: object) => vi.mocked(useRisk).mockReturnValue({
  riskState: { state: { account_value: 25000, daily_pnl: 0, open_positions: 2 } },
  guardrailStatus: { trading_allowed: true, daily_loss_pct: -0.01, weekly_loss_pct: -0.02, monthly_loss_pct: -0.04,
    drawdown_pct: 0.06, consecutive_losses: 1, trades_today: 4, ...guardrailStatus },
  portfolioStateError: false,
} as unknown as ReturnType<typeof useRisk>);

// The Meter's label and its "value / limit" text share one header row.
const row = (label: string) => {
  // Meter labels are .kicker spans; "Open Positions" also appears in the stat strip.
  const el = Array.from(document.querySelectorAll(".kicker"))
    .find(e => e.textContent === label && (e.parentElement?.textContent ?? "").includes("/"));
  if (!el) throw new Error(`no meter labelled ${label}`);
  return el.parentElement as HTMLElement;
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({ engaged: false });
  vi.mocked(api.getCatalystCalendar).mockResolvedValue({ events: [] });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Risk page limit meters use the limits the server reports", () => {
  it("shows each configured limit, not the fixed 2 / 5 / 10 / 15 / 3 / 5", async () => {
    use(LIMITS);
    await act(async () => { render(<RiskMonitor />); });
    expect(row("Daily Loss").textContent).toContain("/ 5%");
    expect(row("Weekly Loss").textContent).toContain("/ 10%");
    expect(row("Monthly Loss").textContent).toContain("/ 20%");
    expect(row("Drawdown").textContent).toContain("/ 30%");
    expect(row("Consecutive Losses").textContent).toContain("/ 4");
    expect(row("Trades Today").textContent).toContain("/ 30");
    expect(row("Open Positions").textContent).toContain("/ 3");
    for (const l of ["Daily Loss", "Weekly Loss", "Monthly Loss", "Drawdown", "Consecutive Losses", "Trades Today", "Open Positions"]) {
      expect(row(l).parentElement!.querySelector(".bar-fill")).not.toBeNull();
    }
  });

  it("a 1% daily loss is 20% of a 5% budget", async () => {
    use(LIMITS);
    await act(async () => { render(<RiskMonitor />); });
    const fill = row("Daily Loss").parentElement!.querySelector(".bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("20%");
  });

  it("limits that are not reported stay unknown: the value shows, the limit is a dash, there is no bar", async () => {
    use({});
    await act(async () => { render(<RiskMonitor />); });
    for (const l of ["Daily Loss", "Weekly Loss", "Monthly Loss", "Drawdown", "Consecutive Losses", "Trades Today", "Open Positions"]) {
      expect(row(l).textContent).toContain("/ —");
      expect(row(l).textContent).not.toContain("—%");
      expect(row(l).parentElement!.querySelector(".bar-fill")).toBeNull();
    }
  });
});
