import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import OptionsFunnelCard from "../OptionsFunnelCard";
import { api } from "../../api/client";
import type { OptionsFunnel } from "../../types/optionsFunnel";

vi.mock("../../api/client", () => ({ api: { getOptionsFunnel: vi.fn() } }));

const funnel = (over: Partial<OptionsFunnel> = {}): OptionsFunnel => ({
  window_days: 7, since: "2026-10-30T15:00:00+00:00", generated_at: "2026-11-06T15:00:00+00:00",
  execution_mode: "autopilot",
  regime: { name: "high_vol_trending", options_allowed: true, strategies_allowed: ["bull_put_spread", "bear_call_spread"], signal_threshold: 0.72 },
  frequency_rule: { trading_mode: "aggressive", min_confidence: 0.7, min_pop: 0.65, max_risk_score: 40, hard_max_per_day: 10, credit_spread_min_pop: 0.7 },
  scanned: 1300,
  stages: [
    { key: "scan_rejected", label: "Rejected at the scan", count: 1290, reasons: [
      { reason: "entry_rule: price_vs_20day_average", count: 700 }, { reason: "entry_rule: rsi", count: 400 },
      { reason: "entry_rule: iv_rank_too_low", count: 100 }, { reason: "insufficient_price_history", count: 50 },
      { reason: "sized_to_zero_contracts", count: 30 }, { reason: "scan_error", count: 10 } ] },
    { key: "qualified", label: "Qualified at the scan", count: 10, reasons: [] },
    { key: "execution_blocked", label: "Blocked at execution", count: 10, reasons: [
      { reason: "frequency_controller: below_min_confidence", count: 9 }, { reason: "frequency_controller: pop_below_floor", count: 1 } ] },
    { key: "execution_skipped", label: "Skipped at execution", count: 0, reasons: [] },
    { key: "submitted", label: "Orders submitted", count: 0, reasons: [] },
  ],
  verdict: { code: "blocked_at_execution", text: "10 candidate(s) qualified and 10 execution attempt(s) were made, but none was submitted. Biggest reason: frequency_controller: below_min_confidence (9)." },
  notes: ["Counts are attempts, not distinct trades: the scan re-evaluates every symbol each 30 minutes."],
  ...over,
});

const ok = (f = funnel()) => vi.mocked(api.getOptionsFunnel).mockResolvedValue(f);
const mount = async () => { await act(async () => { render(<OptionsFunnelCard />); }); };

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("OptionsFunnelCard", () => {
  it("states the verdict in words, with a label that does not rely on colour", async () => {
    ok(); await mount();
    const v = screen.getByTestId("funnel-verdict");
    expect(within(v).getByText("BLOCKED AT EXECUTION")).toBeInTheDocument();
    expect(v.textContent).toContain("Biggest reason: frequency_controller: below_min_confidence (9)");
  });

  it.each([
    ["trading", "TRADING"], ["no_data", "NO DATA"], ["nothing_qualifies", "NOTHING QUALIFIES"],
    ["mode_manual", "MANUAL MODE"], ["waiting_for_approval", "WAITING FOR APPROVAL"],
    ["qualified_but_never_dispatched", "NOT DISPATCHED"], ["blocked_at_execution", "BLOCKED AT EXECUTION"],
  ])("labels the %s verdict %s", async (code, label) => {
    ok(funnel({ verdict: { code, text: "x" } })); await mount();
    expect(within(screen.getByTestId("funnel-verdict")).getByText(label)).toBeInTheDocument();
  });

  it("shows each stage's count and its biggest reasons in readable form", async () => {
    ok(); await mount();
    const scan = screen.getByTestId("funnel-stage-scan_rejected");
    expect(scan.textContent).toContain("1,290");
    expect(scan.textContent).toContain("entry rule: price vs 20day average");
    expect(scan.textContent).toContain("700");
    const blocked = screen.getByTestId("funnel-stage-execution_blocked");
    expect(blocked.textContent).toContain("frequency controller: below min confidence");
  });

  it("lists at most five reasons per stage", async () => {
    ok(); await mount();
    expect(within(screen.getByTestId("funnel-stage-scan_rejected")).getAllByRole("listitem")).toHaveLength(5);
  });

  it("scales bars to the largest stage and keeps a visible sliver for a small non-zero one", async () => {
    ok(); await mount();
    const width = (k: string) => (screen.getByTestId(`funnel-stage-${k}`).querySelector(".bar-fill") as HTMLElement).style.width;
    expect(width("scan_rejected")).toBe("100%");
    expect(width("qualified")).toBe("2%");              // 10 of 1290 would be <1%: still visible
    expect(width("submitted")).toBe("0%");              // a true zero stays zero
  });

  it("does not list reasons under a stage that has none", async () => {
    ok(); await mount();
    expect(within(screen.getByTestId("funnel-stage-submitted")).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("shows the execution mode, the regime and the frequency floors that decide the result", async () => {
    ok(); await mount();
    const c = screen.getByTestId("funnel-context").textContent!;
    expect(c).toContain("AUTOPILOT");
    expect(c).toContain("high vol trending · options allowed (bull put spread, bear call spread)");
    expect(c).toContain("aggressive · confidence ≥ 0.70 · POP ≥ 0.70 · risk score ≤ 40");   // POP: the credit-spread floor beats the mode's
  });

  it("says plainly when the regime does not allow options", async () => {
    ok(funnel({ regime: { name: "crisis", options_allowed: false, strategies_allowed: [] } })); await mount();
    expect(screen.getByTestId("funnel-context").textContent).toContain("crisis · options NOT allowed");
  });

  it("shows 'not available' for context the backend could not supply, never an invented value", async () => {
    ok(funnel({ regime: null, frequency_rule: null })); await mount();
    const c = screen.getByTestId("funnel-context").textContent!;
    expect(c.match(/not available/g)).toHaveLength(2);
  });

  it("carries the caveats with the numbers", async () => {
    ok(); await mount();
    expect(screen.getByText(/Counts are attempts, not distinct trades/)).toBeInTheDocument();
  });

  it("shows loading before the first answer", async () => {
    vi.mocked(api.getOptionsFunnel).mockReturnValue(new Promise(() => {}) as never);
    await mount();
    expect(screen.getByText(/Loading the options funnel/)).toBeInTheDocument();
    expect(screen.queryByTestId("funnel-verdict")).toBeNull();
  });

  it("a failed read is UNAVAILABLE, not a funnel of zeros", async () => {
    vi.mocked(api.getOptionsFunnel).mockRejectedValue(new Error("500 Internal Server Error"));
    await mount();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("UNAVAILABLE");
    expect(alert.textContent).toContain("500 Internal Server Error");
    expect(alert.textContent).toContain("not the same as zero");
    expect(screen.queryByTestId("funnel-verdict")).toBeNull();
    expect(screen.queryByTestId("funnel-stage-scan_rejected")).toBeNull();
  });

  it("reads the default window first, and a window button reloads with that window", async () => {
    ok(); await mount();
    expect(api.getOptionsFunnel).toHaveBeenLastCalledWith(7);
    expect(screen.getByRole("button", { name: "7D" })).toHaveAttribute("aria-pressed", "true");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "30D" })); });
    expect(api.getOptionsFunnel).toHaveBeenLastCalledWith(30);
    expect(screen.getByRole("button", { name: "30D" })).toHaveAttribute("aria-pressed", "true");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "1D" })); });
    expect(api.getOptionsFunnel).toHaveBeenLastCalledWith(1);
  });

  it("does not keep showing the old funnel after a later read fails", async () => {
    ok(); await mount();
    expect(screen.getByTestId("funnel-verdict")).toBeInTheDocument();
    vi.mocked(api.getOptionsFunnel).mockRejectedValue(new Error("offline"));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "30D" })); });
    expect(screen.queryByTestId("funnel-verdict")).toBeNull();      // stale numbers must not look current
    expect(screen.getByRole("alert").textContent).toContain("UNAVAILABLE");
  });

  it("recovers when a later read succeeds", async () => {
    vi.mocked(api.getOptionsFunnel).mockRejectedValue(new Error("offline"));
    await mount();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    ok();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "30D" })); });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("funnel-verdict")).toBeInTheDocument();
  });

  it("refreshes on its own every two minutes", async () => {
    vi.useFakeTimers();
    ok(); await mount();
    const before = vi.mocked(api.getOptionsFunnel).mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(120000); });
    expect(vi.mocked(api.getOptionsFunnel).mock.calls.length).toBe(before + 1);
  });
});
