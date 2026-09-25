/**
 * PLAN Batch 5.4 — the execution safety surface.
 *
 * This control decides whether the desk submits orders with nobody watching, so
 * the properties below are the ones worth spending tests on:
 *
 *   1. Entering AUTOPILOT takes a second deliberate act, and cancelling it
 *      really does leave the server untouched — not merely un-rendered.
 *   2. Leaving autonomy is never gated. A step DOWN applies on one click.
 *   3. An applied change is acknowledged; before this, only failures spoke.
 *   4. A mode the server has not confirmed does not render as a confident one.
 *
 * Property 4 is the one that reaches furthest. The poll previously ended in
 * `.catch(() => {})`, so for five minutes at a time an unreachable backend and a
 * confirmed MANUAL were pixel-identical. That is the plan's own rule — "failed
 * data must never resemble a safe value" — broken by the control the rule
 * matters most for.
 */

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
      trading_allowed: true, paper_mode: true, position_rotation_on_max: false,
    }),
    getTradeDeskKillSwitch: vi.fn().mockResolvedValue({ engaged: false }),
    getKillSwitchStatus: vi.fn().mockResolvedValue({ engaged: false }),
    setTradeDeskKillSwitch: vi.fn().mockResolvedValue({ engaged: true }),
    getPortfolioState: vi.fn().mockResolvedValue({ state: {} }),
  },
}));

/** Lets a single test make the execution-mode read fail. */
let execModeFails = false;

function installFetch() {
  globalThis.fetch = vi.fn().mockImplementation((url: string) => {
    if (url.includes("/api/trade-desk/execution-mode")) {
      return execModeFails
        ? Promise.reject(new Error("network down"))
        : Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: "manual" }) });
    }
    if (url.includes("/api/mode/current")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ mode: "balanced" }) });
    }
    if (url.includes("/api/market/broker")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ broker: "ibkr", paper_mode: true, status: "disconnected" }),
      });
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

const chip = (name: RegExp) => screen.getByRole("button", { name });

describe("execution mode safety surface (PLAN 5.4)", () => {
  beforeEach(() => {
    execModeFails = false;
    installFetch();
  });
  // clearAllMocks, not restoreAllMocks: the latter would wipe the
  // mockResolvedValue set in the vi.mock factory above.
  afterEach(() => { vi.clearAllMocks(); });

  it("does not enter autopilot on a single click", async () => {
    const { api } = await import("../../api/client");
    await renderShell();

    fireEvent.click(chip(/^autopilot$/i));

    expect(screen.getByTestId("autopilot-confirm")).toBeInTheDocument();
    // The decisive assertion: the gate is not cosmetic. Nothing was requested.
    expect(api.setExecutionMode).not.toHaveBeenCalled();
  });

  it("names the mode being left, and the guardrails that still apply", async () => {
    await renderShell();
    fireEvent.click(chip(/^autopilot$/i));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(/MANUAL/);
    expect(dialog).toHaveTextContent(/without asking you first/i);
    expect(dialog).toHaveTextContent(/kill switch/i);
  });

  it("cancelling leaves the server untouched and the mode unmoved", async () => {
    const { api } = await import("../../api/client");
    await renderShell();

    fireEvent.click(chip(/^autopilot$/i));
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    await waitFor(() =>
      expect(screen.queryByTestId("autopilot-confirm")).not.toBeInTheDocument()
    );
    expect(api.setExecutionMode).not.toHaveBeenCalled();
    expect(chip(/^manual$/i)).toHaveAttribute("aria-pressed", "true");
  });

  it("confirming requests autopilot exactly once", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ mode: "autopilot" });

    await renderShell();
    fireEvent.click(chip(/^autopilot$/i));
    fireEvent.click(screen.getByRole("button", { name: /enable autopilot/i }));

    await waitFor(() =>
      expect(chip(/^autopilot$/i)).toHaveAttribute("aria-pressed", "true")
    );
    expect(api.setExecutionMode).toHaveBeenCalledTimes(1);
    expect(api.setExecutionMode).toHaveBeenCalledWith("autopilot");
  });

  it("stepping DOWN to copilot is not gated", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ mode: "copilot" });

    await renderShell();
    fireEvent.click(chip(/^copilot$/i));

    // Reducing autonomy must never need a second click.
    expect(screen.queryByTestId("autopilot-confirm")).not.toBeInTheDocument();
    await waitFor(() => expect(api.setExecutionMode).toHaveBeenCalledWith("copilot"));
  });

  it("acknowledges a confirmed return to MANUAL", async () => {
    const { api } = await import("../../api/client");
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ mode: "copilot" })
      .mockResolvedValueOnce({ mode: "manual" });

    await renderShell();
    fireEvent.click(chip(/^copilot$/i));
    await waitFor(() => expect(chip(/^copilot$/i)).toHaveAttribute("aria-pressed", "true"));

    fireEvent.click(chip(/^manual$/i));

    const ack = await screen.findByTestId("exec-mode-ack");
    expect(ack).toHaveTextContent(/confirmed MANUAL/i);
    expect(ack).toHaveTextContent(/nothing will be submitted/i);
  });

  it("states the mode in force in words, not only as a lit chip", async () => {
    await renderShell();
    await waitFor(() =>
      expect(screen.getByTestId("exec-mode-state")).toHaveTextContent(/MANUAL in force/i)
    );
    expect(screen.getByTestId("exec-mode-state")).toHaveTextContent(/signals only/i);
  });

  it("says a requested mode is not yet applied while it is in flight", async () => {
    const { api } = await import("../../api/client");
    let release: (v: unknown) => void = () => {};
    (api.setExecutionMode as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(() => new Promise((res) => { release = res; }));

    await renderShell();
    fireEvent.click(chip(/^copilot$/i));

    await waitFor(() =>
      expect(screen.getByTestId("exec-mode-state"))
        .toHaveTextContent(/Requesting COPILOT — not applied/i)
    );
    // And the chip still has not moved.
    expect(chip(/^manual$/i)).toHaveAttribute("aria-pressed", "true");
    release({ mode: "copilot" });
  });

  it("marks the mode unconfirmed when the read fails, instead of showing it plainly", async () => {
    execModeFails = true;
    await renderShell();

    const stale = await screen.findByTestId("exec-mode-stale");
    // The wording must not let an unread state pass for a confirmed one.
    expect(stale).toHaveTextContent(/could not be read/i);
    expect(stale).toHaveTextContent(/not a confirmation/i);
  });

  it("clears the unconfirmed warning once the server answers", async () => {
    await renderShell();
    await waitFor(() =>
      expect(screen.queryByTestId("exec-mode-stale")).not.toBeInTheDocument()
    );
  });
});
