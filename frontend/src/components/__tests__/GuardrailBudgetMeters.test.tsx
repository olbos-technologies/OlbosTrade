import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import GuardrailBudgetMeters from "../GuardrailBudgetMeters";

const hint = (l: string) => l;
const meter = (label: string) => screen.getByTestId(`guardrail-meter-${label}`);

afterEach(cleanup);

describe("Dashboard Trades Today meter", () => {
  it("shows the server's cap, not a hardcoded 3 (the 4 / 3 'breach' on a 30-trade desk)", () => {
    render(<GuardrailBudgetMeters guard={{ trades_today: 4, max_trades_per_day: 30 }} hintFor={hint} />);
    expect(within(meter("Trades Today")).getByText(/4\.0 \/ 30/)).toBeInTheDocument();
    expect(within(meter("Trades Today")).queryByText(/\/ 3(?!\d)/)).not.toBeInTheDocument();
    // 4 of 30 is ~13%: the green zone, with a bar
    const fill = meter("Trades Today").querySelector(".bar-fill") as HTMLElement;
    expect(fill.style.width).toMatch(/^13\./);
    expect(fill.style.background).toBe("var(--green)");
  });

  it("goes red only when the real cap is nearly used", () => {
    render(<GuardrailBudgetMeters guard={{ trades_today: 29, max_trades_per_day: 30 }} hintFor={hint} />);
    const fill = meter("Trades Today").querySelector(".bar-fill") as HTMLElement;
    expect(fill.style.background).toBe("var(--red)");
  });

  it("caps the bar at 100% when over the limit", () => {
    render(<GuardrailBudgetMeters guard={{ trades_today: 40, max_trades_per_day: 30 }} hintFor={hint} />);
    expect((meter("Trades Today").querySelector(".bar-fill") as HTMLElement).style.width).toBe("100%");
  });

  it.each([
    ["not reported", {}],
    ["null", { max_trades_per_day: null }],
    ["zero", { max_trades_per_day: 0 }],
    ["a string", { max_trades_per_day: "30" }],
  ])("a cap that is %s stays unknown: no bar, an em dash, amber", (_n, extra) => {
    render(<GuardrailBudgetMeters guard={{ trades_today: 4, ...extra }} hintFor={hint} />);
    const m = meter("Trades Today");
    expect(within(m).getByText(/4\.0 \/ —/)).toBeInTheDocument();
    expect(m.querySelector(".bar-fill")).toBeNull();
    expect((within(m).getByText(/4\.0 \/ —/) as HTMLElement).style.color).toBe("var(--amber)");
  });

  it("with no guardrail status at all, nothing is made up", () => {
    render(<GuardrailBudgetMeters guard={null} hintFor={hint} />);
    expect(within(meter("Trades Today")).getByText(/0\.0 \/ —/)).toBeInTheDocument();
  });

  it("leaves the other three meters' budgets as they were", () => {
    render(<GuardrailBudgetMeters guard={{ daily_loss_pct: -0.01, weekly_loss_pct: 0, consecutive_losses: 1, max_trades_per_day: 30 }} hintFor={hint} />);
    expect(within(meter("Daily Loss")).getByText(/1\.0% \/ 2%/)).toBeInTheDocument();
    expect(within(meter("Weekly Loss")).getByText(/0\.0% \/ 5%/)).toBeInTheDocument();
    expect(within(meter("Consecutive losses")).getByText(/1\.0 \/ 3/)).toBeInTheDocument();
  });
});
