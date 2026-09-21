/**
 * Changing your password, from the screen's side.
 *
 * The route shipped in #70 and had no UI until now, so this is the first test
 * of the form at all. What matters here is not the happy path — the server
 * does the real work — but the three ways a password form mistreats people:
 *
 *   1. losing what they typed on a failed attempt, so a single typo costs
 *      three fields;
 *   2. sending a request that cannot succeed, which on THIS route burns quota
 *      against the login rate limiter it deliberately shares;
 *   3. hiding that other sessions were signed out, which is the whole point
 *      of changing a password and the thing the user needs told.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const changePassword = vi.fn();

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
  api: { changePassword: (...a: unknown[]) => changePassword(...a) },
  ApiError: FakeApiError,
  MIN_PASSWORD_LEN: 12,
  MAX_PASSWORD_LEN: 1024,
}));

import AccountPassword from "../account/AccountPassword";

const CURRENT = "current-password-here";
const NEXT = "a-sufficiently-long-new-one";

beforeEach(() => { vi.clearAllMocks(); });

function fill(current: string, next: string, confirm: string) {
  const fields = screen.getAllByDisplayValue("");
  fireEvent.change(fields[0], { target: { value: current } });
  fireEvent.change(fields[1], { target: { value: next } });
  fireEvent.change(fields[2], { target: { value: confirm } });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: /change password/i }));
}

describe("AccountPassword", () => {
  it("says how many other sessions were signed out", async () => {
    changePassword.mockResolvedValue({ ok: true, other_sessions_revoked: 2 });
    render(<AccountPassword />);
    fill(CURRENT, NEXT, NEXT);
    submit();
    expect(await screen.findByText(/2 other sessions were signed out/i)).toBeInTheDocument();
  });

  it("says so plainly when nothing else was signed in", async () => {
    changePassword.mockResolvedValue({ ok: true, other_sessions_revoked: 0 });
    render(<AccountPassword />);
    fill(CURRENT, NEXT, NEXT);
    submit();
    expect(await screen.findByText(/no other sessions were signed in/i)).toBeInTheDocument();
  });

  it("uses the singular for exactly one", async () => {
    changePassword.mockResolvedValue({ ok: true, other_sessions_revoked: 1 });
    render(<AccountPassword />);
    fill(CURRENT, NEXT, NEXT);
    submit();
    expect(await screen.findByText(/1 other session was signed out/i)).toBeInTheDocument();
  });

  it("clears every field after a successful change", async () => {
    changePassword.mockResolvedValue({ ok: true, other_sessions_revoked: 0 });
    render(<AccountPassword />);
    fill(CURRENT, NEXT, NEXT);
    submit();
    await screen.findByText(/no other sessions/i);
    // Three empty fields again means nothing was left behind in the DOM.
    await waitFor(() => expect(screen.getAllByDisplayValue("")).toHaveLength(3));
  });

  it("keeps what was typed when the current password is wrong", async () => {
    changePassword.mockRejectedValue(new FakeApiError(401, "401: Current password is incorrect"));
    render(<AccountPassword />);
    fill(CURRENT, NEXT, NEXT);
    submit();
    await screen.findByText(/current password is incorrect/i);
    // getAllByDisplayValue, not getBy: NEXT is in both the new and the confirm
    // field, so the singular query throws "multiple elements" and the test
    // fails for a reason that has nothing to do with what it is checking.
    expect(screen.getAllByDisplayValue(NEXT)).toHaveLength(2);
    expect(screen.getByDisplayValue(CURRENT)).toBeInTheDocument();
  });

  it("does not call the API when the confirmation does not match", async () => {
    render(<AccountPassword />);
    fill(CURRENT, NEXT, NEXT + "typo");
    submit();
    expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
    expect(changePassword).not.toHaveBeenCalled();
  });

  it("does not call the API for a password under the minimum", async () => {
    // This route shares the LOGIN rate limiter, so a request that cannot
    // succeed costs the user quota on the limiter that locks out logins.
    render(<AccountPassword />);
    fill(CURRENT, "short", "short");
    submit();
    // Queried through role=alert: the field LABEL also says "at least 12
    // characters", so a plain text query matches two nodes and cannot tell a
    // rendered error from the static label next to it.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/at least 12 characters/i);
    expect(changePassword).not.toHaveBeenCalled();
  });

  it("refuses a new password identical to the current one", async () => {
    render(<AccountPassword />);
    fill(CURRENT, CURRENT, CURRENT);
    submit();
    expect(await screen.findByText(/must be different/i)).toBeInTheDocument();
    expect(changePassword).not.toHaveBeenCalled();
  });

  it("sends the field names the backend expects", async () => {
    changePassword.mockResolvedValue({ ok: true, other_sessions_revoked: 0 });
    render(<AccountPassword />);
    fill(CURRENT, NEXT, NEXT);
    submit();
    await waitFor(() => expect(changePassword).toHaveBeenCalledWith({
      current_password: CURRENT, new_password: NEXT,
    }));
  });

  it("marks every field as a password input", () => {
    render(<AccountPassword />);
    for (const field of screen.getAllByDisplayValue("")) {
      expect(field).toHaveAttribute("type", "password");
    }
  });
});
