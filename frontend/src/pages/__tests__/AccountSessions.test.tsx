/**
 * Active sessions, from the screen's side.
 *
 * The one behaviour worth guarding hard: the CURRENT session must never offer
 * a revoke button. Revoking it server-side leaves the cookie in place and the
 * tab in a state where every request 401s while the UI still looks signed in —
 * a confusing half-logged-out limbo with no obvious way out. Sign out is the
 * control for that, and it clears the cookie too.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const listSessions = vi.fn();
const revokeSession = vi.fn();

const { FakeApiError } = vi.hoisted(() => ({
  FakeApiError: class extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  },
}));

vi.mock("../../api/client", () => ({
  api: {
    listSessions: (...a: unknown[]) => listSessions(...a),
    revokeSession: (...a: unknown[]) => revokeSession(...a),
  },
  ApiError: FakeApiError,
}));

import AccountSessions from "../account/AccountSessions";

const CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/152.0.0.0 Safari/537.36";
const SAFARI_IOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 Safari/604.1";

const THIS_ONE = {
  id: "s1", created_at: "2026-09-20T10:00:00Z", last_seen_at: "2026-09-21T09:00:00Z",
  user_agent: CHROME_MAC, ip: "203.0.113.4", current: true,
};
const OTHER = {
  id: "s2", created_at: "2026-09-18T10:00:00Z", last_seen_at: "2026-09-19T09:00:00Z",
  user_agent: SAFARI_IOS, ip: "203.0.113.9", current: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  listSessions.mockResolvedValue({ sessions: [THIS_ONE, OTHER] });
});

describe("AccountSessions", () => {
  it("never offers to revoke the session you are using", async () => {
    render(<AccountSessions />);
    await screen.findByText(/this browser/i);

    // One row is current, one is not — so exactly one Sign out button.
    const buttons = screen.getAllByRole("button", { name: /sign out/i });
    expect(buttons).toHaveLength(1);
  });

  it("would catch a revoke button appearing on the current session", async () => {
    // The mutation: if both rows were revocable there would be two buttons.
    listSessions.mockResolvedValue({
      sessions: [{ ...THIS_ONE, current: false }, OTHER],
    });
    render(<AccountSessions />);
    await screen.findByText(/Chrome on macOS/i);
    expect(screen.getAllByRole("button", { name: /sign out/i })).toHaveLength(2);
  });

  it("names the browser and the machine in readable terms", async () => {
    render(<AccountSessions />);
    expect(await screen.findByText("Chrome on macOS")).toBeInTheDocument();
    expect(screen.getByText("Safari on iOS")).toBeInTheDocument();
  });

  it("keeps the raw user agent available rather than discarding it", async () => {
    // The parser is deliberately crude, so the original has to survive
    // somewhere for the cases it guesses wrong.
    render(<AccountSessions />);
    const label = await screen.findByText("Chrome on macOS");
    expect(label).toHaveAttribute("title", CHROME_MAC);
  });

  it("confirms before signing another browser out", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AccountSessions />);
    await screen.findByText(/this browser/i);

    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(revokeSession).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    revokeSession.mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(revokeSession).toHaveBeenCalledWith("s2"));
    confirmSpy.mockRestore();
  });

  it("reloads the list after a revoke, so the row actually goes away", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    revokeSession.mockResolvedValue({ ok: true });
    render(<AccountSessions />);
    await screen.findByText(/this browser/i);
    expect(listSessions).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(listSessions).toHaveBeenCalledTimes(2));
  });

  it("survives a session with no user agent recorded", async () => {
    listSessions.mockResolvedValue({
      sessions: [{ ...OTHER, user_agent: null, ip: null, last_seen_at: null }],
    });
    render(<AccountSessions />);
    expect(await screen.findByText("Unknown browser")).toBeInTheDocument();
  });

  it("reports a failed load instead of rendering an empty list", async () => {
    // An empty list and a failed read look identical otherwise, and "no other
    // browsers are signed in" is exactly the wrong thing to imply when the
    // read failed.
    listSessions.mockRejectedValue(new FakeApiError(500, "500: boom"));
    render(<AccountSessions />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/boom|could not load/i);
    expect(screen.queryByText(/no active sessions/i)).not.toBeInTheDocument();
  });
});
