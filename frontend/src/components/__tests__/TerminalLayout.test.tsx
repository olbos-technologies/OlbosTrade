import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import TerminalLayout from "../TerminalLayout";

vi.mock("../../api/client", () => ({
  api: {
    getExecutionMode: vi.fn().mockResolvedValue({ mode: "manual" }),
    setExecutionMode: vi.fn().mockResolvedValue({ mode: "manual" }),
    getCurrentMode: vi.fn().mockResolvedValue({ mode: "balanced" }),
    getGuardrailStatus: vi.fn().mockResolvedValue({
      trading_allowed: true,
      paper_mode: true,
      position_rotation_on_max: false,
    }),
    getTradeDeskKillSwitch: vi.fn().mockResolvedValue({ engaged: false }),
    getKillSwitchStatus: vi.fn().mockResolvedValue({ engaged: false }),
    setTradeDeskKillSwitch: vi.fn().mockResolvedValue({ engaged: true }),
    getPortfolioState: vi.fn().mockResolvedValue({ state: {} }),
  },
}));

// Regression test for a real crash found via live-backend verification: when
// /api/market/snapshot/{symbol} errors out (a documented yfinance-failure
// condition, see AUDIT_2026-06.md), the JSON payload omits last_close /
// change_pct entirely, so they arrive as `undefined` rather than `null`.
// TickerCell used to check `price !== null`, which is true for `undefined`,
// so it called `.toFixed()` on `undefined` and crashed the whole terminal
// shell with no error boundary catching it.
describe("TerminalLayout ticker strip", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/market/snapshot/")) {
        // Real error-shape payload observed from the backend: no last_close/
        // prev_close/change_pct fields at all.
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ symbol: "SPY", error: "No data returned" }),
        });
      }
      if (url.includes("/api/market/regime")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (url.includes("/api/mode/current")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: "balanced" }) });
      }
      if (url.includes("/api/trade-desk/execution-mode")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: "manual" }) });
      }
      if (url.includes("/api/market/broker")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ broker: "ibkr", paper_mode: true, status: "disconnected" }),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    // Not restoreAllMocks(): that would wipe the mockResolvedValue set on
    // the module-level api.* vi.fn()s from the vi.mock() factory above,
    // leaving them returning undefined in later tests within this file.
    vi.clearAllMocks();
  });

  it("renders '—' instead of crashing when a snapshot payload has no price fields", async () => {
    render(
      <TerminalLayout activePage="dashboard" onNav={() => {}}>
        <div>page content</div>
      </TerminalLayout>
    );

    // The shell must still render — no "failed to render" error boundary
    // fallback, and the actual page content underneath is reachable.
    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    expect(screen.queryByText(/failed to render/i)).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("does not crash when /api/market/regime returns a payload without a regime field", async () => {
    // The mocked regime response in beforeEach is `{}` — a truthy object
    // with no `.regime` string, reproducing the second half of the same bug
    // class (regime.regime.includes()/.replace() on undefined).
    render(
      <TerminalLayout activePage="dashboard" onNav={() => {}}>
        <div>page content</div>
      </TerminalLayout>
    );

    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    expect(screen.queryByText(/failed to render/i)).not.toBeInTheDocument();
    expect(screen.queryByText("REGIME")).not.toBeInTheDocument();
  });

  it("shows a human status label for the active page, not a hardcoded TRADE DESK", async () => {
    render(
      <TerminalLayout activePage="markets:chart" onNav={() => {}}>
        <div>page content</div>
      </TerminalLayout>
    );

    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    expect(screen.getByText("MARKETS · CHART")).toBeInTheDocument();
    // The old bug was a second status span always reading TRADE DESK.
    const matches = screen.queryAllByText("TRADE DESK");
    expect(matches.length).toBe(0);
  });

  it("renders a tri-state execution mode control instead of dual ON/OFF chips", async () => {
    render(
      <TerminalLayout activePage="dashboard" onNav={() => {}}>
        <div>page content</div>
      </TerminalLayout>
    );

    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    const modeGroup = screen.getByRole("group", { name: /execution mode/i });
    expect(modeGroup).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^manual$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^copilot$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^autopilot$/i })).toBeInTheDocument();
    // Old dual chips were "COPILOT ON" / "AUTOPILOT OFF" inside the header control.
    expect(modeGroup).not.toHaveTextContent(/COPILOT ON/i);
    expect(modeGroup).not.toHaveTextContent(/AUTOPILOT OFF/i);
  });

  it("sidebar kill switch engages confirm dialog instead of navigating to risk", async () => {
    const onNav = vi.fn();
    render(
      <TerminalLayout activePage="dashboard" onNav={onNav}>
        <div>page content</div>
      </TerminalLayout>
    );

    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    const kill = screen.getByRole("button", { name: /engage kill switch/i });
    fireEvent.click(kill);
    expect(onNav).not.toHaveBeenCalledWith("risk");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/engage kill switch\?/i)).toBeInTheDocument();
  });

  it("applies desk-shell class when the V2 desk shell is active", async () => {
    const { container } = render(
      <TerminalLayout activePage="paper" onNav={() => {}} isDeskV2Shell>
        <div>page content</div>
      </TerminalLayout>
    );
    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    expect(container.querySelector(".app-shell")?.className).toContain("app-shell--desk");
  });

  it("does not apply desk-shell class for non-V2 desk pages", async () => {
    const { container } = render(
      <TerminalLayout activePage="trade:overview" onNav={() => {}}>
        <div>page content</div>
      </TerminalLayout>
    );
    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
    expect(container.querySelector(".app-shell")?.className).toBe("app-shell");
  });

  // ── Execution-mode toggle: a safety control must never show an unconfirmed
  // state ────────────────────────────────────────────────────────────────────
  // Found in production 2026-08-27: the route is api-key gated and the browser
  // holds no key, so every mode change returned 403. The control updated
  // optimistically and then silently reverted, so clicking MANUAL made the
  // toggle visibly flip to MANUAL and snap back to AUTOPILOT — an operator
  // could reasonably read that as "trading is paused" while autopilot kept
  // running. The error text existed but was a 9px right-aligned span.

  const renderShell = async () => {
    render(
      <TerminalLayout activePage="dashboard" onNav={() => {}}>
        <div>page content</div>
      </TerminalLayout>
    );
    await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
  };

  const pressed = (name: RegExp) =>
    screen.getByRole("button", { name }).getAttribute("aria-pressed");

  it("does NOT move the toggle when the server refuses the change (403)", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error("403 Forbidden"));

    await renderShell();
    expect(pressed(/^manual$/i)).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: /^autopilot$/i }));

    await waitFor(() =>
      expect(screen.getByTestId("exec-mode-error")).toBeInTheDocument()
    );
    // The whole point: the displayed mode never moved.
    expect(pressed(/^manual$/i)).toBe("true");
    expect(pressed(/^autopilot$/i)).toBe("false");
  });

  it("names the mode still in force when a change is refused", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error("403 Forbidden"));

    await renderShell();
    fireEvent.click(screen.getByRole("button", { name: /^autopilot$/i }));

    const alert = await screen.findByRole("alert");
    // "Change failed" alone leaves the operator to infer the current state —
    // the one thing they most need to be certain about.
    expect(alert).toHaveTextContent(/REFUSED/i);
    expect(alert).toHaveTextContent(/still MANUAL/i);
    expect(alert).toHaveTextContent(/Operator API Key/i);
  });

  it("applies the mode the SERVER returns, not the one requested", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ mode: "copilot" });

    await renderShell();
    fireEvent.click(screen.getByRole("button", { name: /^autopilot$/i }));

    // Guards against re-introducing optimism: autopilot was requested, the
    // server said copilot, and copilot is what must show.
    await waitFor(() => expect(pressed(/^copilot$/i)).toBe("true"));
    expect(pressed(/^autopilot$/i)).toBe("false");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports an unconfirmed state when a 2xx carries no mode", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({});

    await renderShell();
    fireEvent.click(screen.getByRole("button", { name: /^copilot$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/unconfirmed/i);
    // Neither applied nor reverted — it says so rather than guessing.
    expect(pressed(/^manual$/i)).toBe("true");
  });

  it("surfaces a non-403 failure with the mode still in force", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error("500 Internal Server Error"));

    await renderShell();
    fireEvent.click(screen.getByRole("button", { name: /^copilot$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/FAILED/i);
    expect(alert).toHaveTextContent(/still MANUAL/i);
    expect(pressed(/^manual$/i)).toBe("true");
  });
});
