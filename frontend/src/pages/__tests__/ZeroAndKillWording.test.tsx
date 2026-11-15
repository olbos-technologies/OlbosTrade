/**
 * Two things the owner flagged on the Command Center (2026-11-06):
 *
 *  1. A flat day printed "+$0.00" in green. Zero is neither a gain nor a loss.
 *  2. The kill switch read "NOT ARMED" in the status strip, "CLEAR" on the card, and "OFF"
 *     (hardcoded, whatever the state) in the Risk rules list.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import DesktopCommandCenter from "../DesktopCommandCenter";
import Guardrails from "../Guardrails";
import { api } from "../../api/client";
import { useRisk } from "../../hooks/useRisk";

vi.mock("../../hooks/useRisk", () => ({ useRisk: vi.fn() }));
vi.mock("../../api/client", () => ({ getOperatorApiKey: () => "", setOperatorApiKey: vi.fn(), api: {
  getExecutionMode: vi.fn(), getTradeDeskKillSwitch: vi.fn(), getPositions: vi.fn(), getPendingApprovals: vi.fn(),
  getTradeHistory: vi.fn(), getCurrentMode: vi.fn(), getGuardrailHistory: vi.fn(), runReconciliation: vi.fn(),
} }));

function stubFetch(day: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path.includes("dashboard") ? { total_equity: 125000, day_pnl: day, total_pnl: 2870.42 }
      : path.includes("broker") ? { broker: "ibkr", paper_mode: true, status: "connected" } : {},
  })));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getExecutionMode).mockResolvedValue({ mode: "autopilot", persistence: "confirmed" } as never);
  vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({ engaged: false } as never);
  vi.mocked(api.getPositions).mockResolvedValue({ positions: [] } as never);
  vi.mocked(api.getPendingApprovals).mockResolvedValue({ pending: [] } as never);
  vi.mocked(api.getTradeHistory).mockResolvedValue({ trades: [] } as never);
  vi.mocked(api.getCurrentMode).mockResolvedValue({ mode: "aggressive", signal_threshold: 0.62 } as never);
  vi.mocked(api.getGuardrailHistory).mockResolvedValue({ events: [] } as never);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

/** The big number and its colour for a Command Center metric. */
const metric = (label: string) => {
  const strong = screen.getByText(label).parentElement!.querySelector("strong") as HTMLElement;
  return { text: strong.textContent, color: strong.style.color };
};

describe("Command Center day P&L", () => {
  it("says it is realized P&L on the Eastern market day, so a red open book is not mistaken for it", async () => {
    stubFetch(0);
    render(<DesktopCommandCenter />);
    await screen.findByText("$125,000.00");
    const note = screen.getByText("Olbos day P&L").parentElement!.querySelector("small") as HTMLElement;
    expect(note.textContent).toMatch(/realized/i);
    expect(note.textContent).toMatch(/ET/);
    expect(note.textContent).toMatch(/closed trades only/i);
  });

  it("a flat day is $0.00 in the neutral colour: no plus sign, not green", async () => {
    stubFetch(0);
    render(<DesktopCommandCenter />);
    await screen.findByText("$125,000.00");
    expect(metric("Olbos day P&L")).toEqual({ text: "$0.00", color: "var(--ink)" });
  });

  it("a gain keeps its plus and green, a loss its minus and red", async () => {
    stubFetch(12);
    render(<DesktopCommandCenter />);
    await screen.findByText("$125,000.00");
    expect(metric("Olbos day P&L")).toEqual({ text: "+$12.00", color: "var(--green)" });
    cleanup();
    stubFetch(-12);
    render(<DesktopCommandCenter />);
    await screen.findByText("$125,000.00");
    expect(metric("Olbos day P&L")).toEqual({ text: "−$12.00", color: "var(--red)" });
  });

  it("an unreadable day P&L is a dash in the faint colour, never green", async () => {
    stubFetch(null);
    render(<DesktopCommandCenter />);
    await screen.findByText("$125,000.00");
    expect(metric("Olbos day P&L")).toEqual({ text: "—", color: "var(--ink-faint)" });
  });
});

describe("Command Center kill switch card", () => {
  const card = () => screen.getByText("Kill switch").parentElement!.querySelector("dd")!.textContent;

  it.each([[false, "CLEAR"], [true, "ENGAGED"]])("engaged=%s reads %s", async (engaged, word) => {
    vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({ engaged } as never);
    stubFetch(0);
    render(<DesktopCommandCenter />);
    await waitFor(() => expect(card()).toBe(word));
  });

  it("a failed read is UNVERIFIED, not CLEAR", async () => {
    vi.mocked(api.getTradeDeskKillSwitch).mockRejectedValue(new Error("offline") as never);
    stubFetch(0);
    render(<DesktopCommandCenter />);
    await waitFor(() => expect(card()).toBe("UNVERIFIED"));
  });
});

describe("Risk rules list: the kill switch row reflects the real state", () => {
  const use = () => vi.mocked(useRisk).mockReturnValue({
    guardrailStatus: { trading_allowed: true, daily_loss_pct: 0, weekly_loss_pct: 0, monthly_loss_pct: 0, drawdown_pct: 0,
      consecutive_losses: 0, trades_today: 0, capital_pct_remaining: 1, max_daily_loss_pct: 0.05 },
    reconciliation: null, refresh: vi.fn(),
  } as unknown as ReturnType<typeof useRisk>);
  const row = () => screen.getByText("Kill Switch").parentElement!.parentElement as HTMLElement;

  it("reads CLEAR / OK when the switch is not engaged", async () => {
    use();
    await act(async () => { render(<Guardrails />); });
    await waitFor(() => expect(row().textContent).toContain("CLEAR"));
    expect(row().textContent).toContain("OK");
  });

  it("reads ENGAGED / Breach when it is: it used to say OFF / OK whatever the state", async () => {
    vi.mocked(api.getTradeDeskKillSwitch).mockResolvedValue({ engaged: true } as never);
    use();
    await act(async () => { render(<Guardrails />); });
    await waitFor(() => expect(row().textContent).toContain("ENGAGED"));
    expect(row().textContent).toContain("Breach");
    expect(row().textContent).not.toContain("OFF");
  });

  it("a failed read is UNVERIFIED, never OK", async () => {
    vi.mocked(api.getTradeDeskKillSwitch).mockRejectedValue(new Error("offline") as never);
    use();
    await act(async () => { render(<Guardrails />); });
    await waitFor(() => expect(row().textContent).toContain("UNVERIFIED"));
    expect(row().textContent).toContain("Unverified");
    expect(row().textContent).not.toContain("OK");
  });
});
