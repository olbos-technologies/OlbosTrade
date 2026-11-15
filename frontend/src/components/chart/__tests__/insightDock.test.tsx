import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import InsightDock, { type InsightSignal } from "../InsightDock";

afterEach(cleanup);

const signal: InsightSignal = {
  action: "BUY", confidence: 0.62, generated_at: "2026-11-14T14:00:00Z", source: "equity_scan",
  reasons: ["Trend up", "Volume above average"],
  trade_plan: { entry_price: 100, stop_price: 95, target_price: 110 },
  indicators: { rsi: 55.2, volume_ratio: 1.4 },
};
const levels = [
  { label: "ENTRY", value: 100, color: "var(--amber)" },
  { label: "STOP", value: 95, color: "var(--red)" },
  { label: "TP3", value: 110, color: "var(--green)" },
];
const base = {
  symbol: "AAPL", signal, planned: true, entry: 100, levels,
  vol: { iv_rank: 40, iv_now_pct: 22, iv_high_pct: 35, iv_low_pct: 14, method: "atr_rv_proxy" },
  volFailed: false, catalysts: [], catalystsFailed: false,
};

describe("InsightDock", () => {
  it("has four tabs, the signal first, and names the model score as not a probability", () => {
    render(<InsightDock {...base} />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Signal", "Levels", "Volatility", "Catalysts"]);
    expect(screen.getByRole("tab", { name: "Signal" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("62%")).toBeInTheDocument();
    expect(screen.getByText(/not a probability/)).toBeInTheDocument();
    expect(screen.getByText("2.00 : 1")).toBeInTheDocument();
  });

  it("says there is no signal instead of showing another ticker's or a neutral reading", () => {
    render(<InsightDock {...base} signal={null} />);
    expect(screen.getByText(/No signal for AAPL/)).toBeInTheDocument();
    expect(screen.getByText(/not a neutral reading/)).toBeInTheDocument();
  });

  it("shows levels with signed distance from entry, and says when they are not a trade plan", () => {
    const { rerender } = render(<InsightDock {...base} />);
    fireEvent.click(screen.getByRole("tab", { name: "Levels" }));
    expect(screen.getByText("−5.0%")).toBeInTheDocument();
    expect(screen.getByText("+10.0%")).toBeInTheDocument();
    expect(screen.getByText(/The signal's own plan/)).toBeInTheDocument();
    rerender(<InsightDock {...base} planned={false} />);
    expect(screen.getByText(/Not a trade plan/)).toBeInTheDocument();
  });

  it("labels the volatility rank as a realized-vol proxy, not implied volatility", () => {
    render(<InsightDock {...base} />);
    fireEvent.click(screen.getByRole("tab", { name: "Volatility" }));
    expect(screen.getByText("40 / 100")).toBeInTheDocument();
    expect(screen.getByText(/not implied volatility/)).toBeInTheDocument();
  });

  it("a failed volatility or catalyst request does not read as empty or safe", () => {
    render(<InsightDock {...base} vol={null} volFailed catalystsFailed />);
    fireEvent.click(screen.getByRole("tab", { name: "Volatility" }));
    expect(screen.getByText(/request failed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Catalysts" }));
    expect(screen.getByText(/not an empty calendar/)).toBeInTheDocument();
    expect(screen.queryByText(/No catalysts listed/)).toBeNull();
  });

  it("an empty, successful catalyst list says so", () => {
    render(<InsightDock {...base} />);
    fireEvent.click(screen.getByRole("tab", { name: "Catalysts" }));
    expect(screen.getByText(/No catalysts listed/)).toBeInTheDocument();
  });

  it("sorts catalysts soonest first", () => {
    render(<InsightDock {...base} catalysts={[
      { name: "Later", date: "2026-12-01", days_away: 17, severity: "high" },
      { name: "Sooner", date: "2026-11-16", days_away: 2, severity: "very_high" },
    ]} />);
    fireEvent.click(screen.getByRole("tab", { name: "Catalysts" }));
    const rows = screen.getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toContain("Sooner");
    expect(rows[1]).toContain("Later");
    expect(rows[0]).toContain("very high");
  });

  it("moves between tabs with the arrow keys", () => {
    render(<InsightDock {...base} />);
    const first = screen.getByRole("tab", { name: "Signal" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Levels" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Levels" })).toHaveAttribute("tabindex", "0");
    expect(first).toHaveAttribute("tabindex", "-1");
  });

  it("collapses and reopens, and opens when a tab is chosen", () => {
    render(<InsightDock {...base} defaultOpen={false} />);
    expect(screen.queryByRole("tabpanel")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Levels" }));
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByRole("tabpanel")).toBeNull();
  });
});
