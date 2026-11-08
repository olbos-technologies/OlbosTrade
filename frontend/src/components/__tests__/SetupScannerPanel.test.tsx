import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", () => ({ api: { getSetupScanner: vi.fn() } }));
import { api } from "../../api/client";
import SetupScannerPanel from "../SetupScannerPanel";

const row = (over: Record<string, unknown>) => ({
  symbol: "NVDA", bias: "bearish", confirmation_score: 57,
  structure_state: "range_bound", timeframe_alignment: "mixed", macro_risk: "elevated",
  status: "blocked", blocked_reasons: ["Daily timeframe disagrees with the 15m setup", "Macro risk elevated"],
  unblock_condition: "Daily closes back above the 20-day average", modes_allowed: [], strategy_fit: [],
  ...over,
});

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("SetupScannerPanel", () => {
  it("shows why a setup is blocked on the row itself, without a click", async () => {
    vi.mocked(api.getSetupScanner).mockResolvedValue({ results: [row({})] } as never);
    render(<SetupScannerPanel />);
    const why = await screen.findByText(/Blocked: Daily timeframe disagrees/);
    expect(why.textContent).toContain("(+1 more)");
    expect(why).toHaveAttribute("title", "Daily timeframe disagrees with the 15m setup; Macro risk elevated");
  });

  it("rows are real buttons that expand to the full reason and unblock condition", async () => {
    vi.mocked(api.getSetupScanner).mockResolvedValue({ results: [row({})] } as never);
    render(<SetupScannerPanel />);
    const button = await screen.findByRole("button", { name: /NVDA/ });
    expect(button).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Macro risk elevated/)).toBeInTheDocument();
    expect(screen.getByText(/Daily closes back above/)).toBeInTheDocument();
    // the one-line summary gives way to the full list
    expect(screen.queryByText(/\(\+1 more\)/)).toBeNull();
  });

  it("shows no reason line for an eligible setup", async () => {
    vi.mocked(api.getSetupScanner).mockResolvedValue({
      results: [row({ symbol: "QQQ", status: "eligible", blocked_reasons: [], unblock_condition: null })],
    } as never);
    render(<SetupScannerPanel />);
    await screen.findByRole("button", { name: /QQQ/ });
    expect(screen.queryByText(/Blocked:/)).toBeNull();
    expect(screen.queryByText(/Caution:/)).toBeNull();
  });

  it("labels a caution row as caution, not blocked", async () => {
    vi.mocked(api.getSetupScanner).mockResolvedValue({
      results: [row({ symbol: "SPY", status: "caution", blocked_reasons: ["Earnings inside the window"] })],
    } as never);
    render(<SetupScannerPanel />);
    expect(await screen.findByText(/Caution: Earnings inside the window/)).toBeInTheDocument();
    expect(screen.queryByText(/Blocked:/)).toBeNull();
  });

  it("says unavailable, not an empty list, when the read fails", async () => {
    vi.mocked(api.getSetupScanner).mockRejectedValue(new Error("down"));
    render(<SetupScannerPanel />);
    expect(await screen.findByText(/Setup readiness unavailable/)).toBeInTheDocument();
  });
});
