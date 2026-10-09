/**
 * The mobile execution-mode trigger must not present an unconfirmed mode
 * confidently.
 *
 * Raised in review on #84, and it is the sharper half of the staleness work.
 * On a phone the freshness warning lives inside a CLOSED BottomSheet, so this
 * fixed header button is the only execution-state indicator an operator sees
 * until they tap it. Left as-is it kept showing MANUAL / COPILOT / AUTOPILOT
 * with a live-coloured dot after a failed read — the mobile surface quietly
 * contradicting the desktop one, and the exact failure the staleness work
 * exists to prevent.
 *
 * A separate file because vi.mock is hoisted per-module: forcing useIsMobile
 * true has to happen in a file that wants it true everywhere.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => true }));

vi.mock("../../api/client", () => ({
  api: {
    getExecutionMode: vi.fn().mockResolvedValue({ mode: "manual" }),
    setExecutionMode: vi.fn().mockResolvedValue({ mode: "manual" }),
    getCurrentMode: vi.fn().mockResolvedValue({ mode: "balanced" }),
    getGuardrailStatus: vi.fn().mockResolvedValue({
      trading_allowed: true, paper_mode: true, position_rotation_on_max: false,
    }),
    getTradeDeskKillSwitch: vi.fn().mockResolvedValue({ engaged: false }),
    getKillSwitchStatus: vi.fn().mockResolvedValue({ engaged: false }),
    setTradeDeskKillSwitch: vi.fn().mockResolvedValue({ engaged: true }),
    getPortfolioState: vi.fn().mockResolvedValue({ state: {} }),
  },
}));

import TerminalLayout from "../TerminalLayout";

let execModeFails = false;

function installFetch() {
  globalThis.fetch = vi.fn().mockImplementation((url: string) => {
    if (url.includes("/api/trade-desk/execution-mode")) {
      return execModeFails
        ? Promise.reject(new Error("network down"))
        : Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: "copilot" }) });
    }
    if (url.includes("/api/mode/current")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: "balanced" }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  }) as unknown as typeof fetch;
}

const renderShell = async () => {
  render(
    <TerminalLayout activePage="dashboard" onNav={() => {}}>
      <div>page content</div>
    </TerminalLayout>
  );
  await waitFor(() => expect(screen.getByText("page content")).toBeInTheDocument());
};

describe("mobile execution-mode trigger", () => {
  beforeEach(() => { execModeFails = false; installFetch(); });
  afterEach(() => { vi.clearAllMocks(); });

  it("marks the mode as unconfirmed when the read fails", async () => {
    execModeFails = true;
    await renderShell();

    const trigger = await screen.findByTestId("mobile-exec-trigger");
    // Not colour alone — the accessible name has to say it too (PLAN rule 3).
    await waitFor(() =>
      expect(trigger).toHaveAccessibleName(/unconfirmed/i)
    );
    expect(trigger).toHaveTextContent(/\?/);
  });

  it("presents the mode plainly once the server confirms it", async () => {
    await renderShell();

    const trigger = await screen.findByTestId("mobile-exec-trigger");
    await waitFor(() =>
      expect(trigger).toHaveAccessibleName(/Execution mode COPILOT/i)
    );
    expect(trigger).not.toHaveTextContent(/\?/);
    expect(trigger).not.toHaveAccessibleName(/unconfirmed/i);
  });
});
