/**
 * Connecting your own broker account, from the screen's side.
 *
 * Three things here are worth a test, and the rest is layout:
 *
 *   1. THE SECRET MUST NOT SURVIVE THE SUBMIT. It is write-only by design —
 *      no endpoint returns it — so the only place it could leak back into
 *      view is this component's own state. A field that keeps its value after
 *      a successful save leaves a live brokerage secret sitting in the DOM
 *      for anyone who walks past the screen.
 *
 *   2. THE PAGE MUST NOT CLAIM MORE THAN IT DOES. Storing a key is not
 *      trading with it. Per-user order routing is a separate change, and
 *      until it ships this page says so — driven by the server's
 *      `execution_routing_enabled`, so the warning disappears on its own
 *      rather than needing a frontend deploy nobody remembers to do.
 *
 *   3. A FAILED ATTEMPT MUST NOT COST THE USER THEIR TYPING. Pasting a key
 *      pair is tedious; clearing both fields because one was wrong is the
 *      kind of small cruelty that makes people give up.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const listBrokerConnections = vi.fn();
const connectBroker = vi.fn();
const disconnectBroker = vi.fn();
const verifyBrokerConnection = vi.fn();

// vi.hoisted, because vi.mock is lifted to the top of the file and a plain
// class declaration is not — the factory would run before the class existed.
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
    listBrokerConnections: (...a: unknown[]) => listBrokerConnections(...a),
    connectBroker: (...a: unknown[]) => connectBroker(...a),
    disconnectBroker: (...a: unknown[]) => disconnectBroker(...a),
    verifyBrokerConnection: (...a: unknown[]) => verifyBrokerConnection(...a),
  },
  ApiError: FakeApiError,
}));

import MyBrokers from "../account/MyBrokers";

const SECRET = "s3cr3t-alpaca-secret-value-0000";
const KEY = "PKTEST0000EXAMPLEKEY";

const CONNECTION = {
  id: "c1", broker: "alpaca", environment: "paper" as const, label: "main",
  key_last4: "1234", status: "active" as const,
  created_at: "2026-09-01T00:00:00Z", last_verified_at: "2026-09-02T00:00:00Z",
  verified: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  listBrokerConnections.mockResolvedValue({
    connections: [], execution_routing_enabled: false,
  });
});

async function fillAndSubmit() {
  fireEvent.change(screen.getByPlaceholderText(/^PK/), { target: { value: KEY } });
  fireEvent.change(screen.getByPlaceholderText(/never shown again/i),
                   { target: { value: SECRET } });
  fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
}

describe("MyBrokers", () => {
  it("clears the secret from the form once it has been saved", async () => {
    connectBroker.mockResolvedValue({
      ok: true, connection: CONNECTION, verification: { account_status: "ACTIVE" },
      unverified_reason: null, execution_routing_enabled: false,
    });
    render(<MyBrokers />);
    await waitFor(() => expect(listBrokerConnections).toHaveBeenCalled());

    await fillAndSubmit();
    await waitFor(() => expect(connectBroker).toHaveBeenCalled());

    // The secret went UP exactly once, with the right field names.
    expect(connectBroker).toHaveBeenCalledWith(expect.objectContaining({
      broker: "alpaca", api_key: KEY, secret_key: SECRET,
    }));

    await waitFor(() => {
      const secretField = screen.getByPlaceholderText(/never shown again/i) as HTMLInputElement;
      expect(secretField.value).toBe("");
    });
    // And nowhere else in the rendered page either.
    expect(document.body.innerHTML).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain(KEY);
  });

  it("would notice a secret left in the DOM", () => {
    // The mutation for the assertion above: prove the check can see a secret
    // that IS present, otherwise it is decoration.
    render(<div>{SECRET}</div>);
    expect(document.body.innerHTML).toContain(SECRET);
  });

  it("keeps what was typed when the connect attempt fails", async () => {
    connectBroker.mockRejectedValue(new FakeApiError(400, "400: Alpaca rejected these credentials."));
    render(<MyBrokers />);
    await waitFor(() => expect(listBrokerConnections).toHaveBeenCalled());

    await fillAndSubmit();

    await screen.findByText(/Alpaca rejected these credentials/i);
    const keyField = screen.getByPlaceholderText(/^PK/) as HTMLInputElement;
    const secretField = screen.getByPlaceholderText(/never shown again/i) as HTMLInputElement;
    expect(keyField.value).toBe(KEY);
    expect(secretField.value).toBe(SECRET);
  });

  it("says orders are not routed to the connected account yet", async () => {
    render(<MyBrokers />);
    expect(await screen.findByText(/does not route your trades to your own broker yet/i))
      .toBeInTheDocument();
  });

  it("drops that warning when the server says routing is live", async () => {
    // Server-driven, so shipping per-user routing does not require remembering
    // to delete a hardcoded banner here.
    listBrokerConnections.mockResolvedValue({
      connections: [CONNECTION], execution_routing_enabled: true,
    });
    render(<MyBrokers />);
    await screen.findByText(/ALPACA/);
    expect(screen.queryByText(/does not route your trades/i)).not.toBeInTheDocument();
  });

  it("shows a stored connection by its last four characters, never the key", async () => {
    listBrokerConnections.mockResolvedValue({
      connections: [CONNECTION], execution_routing_enabled: false,
    });
    render(<MyBrokers />);
    await screen.findByText(/ALPACA/);
    expect(screen.getByText(/••••1234/)).toBeInTheDocument();
  });

  it("warns when the credential was stored but could not be verified", async () => {
    connectBroker.mockResolvedValue({
      ok: true, connection: { ...CONNECTION, verified: false },
      verification: null,
      unverified_reason: "Could not reach the broker to verify these keys.",
      execution_routing_enabled: false,
    });
    render(<MyBrokers />);
    await waitFor(() => expect(listBrokerConnections).toHaveBeenCalled());
    await fillAndSubmit();
    expect(await screen.findByText(/Saved, but not verified/i)).toBeInTheDocument();
  });

  it("explains itself instead of rendering an empty form when the plan does not allow it", async () => {
    listBrokerConnections.mockRejectedValue(
      new FakeApiError(403, "403: Connecting a broker requires the Elite plan."));
    render(<MyBrokers />);
    expect(await screen.findByText(/requires the Elite plan/i)).toBeInTheDocument();
    // No form to fill in, so nobody types a live secret into a dead screen.
    expect(screen.queryByPlaceholderText(/never shown again/i)).not.toBeInTheDocument();
  });

  it("shows the blocked state when the deployment has no encryption key", async () => {
    // The backend's list route answers 503 when BROKER_ENCRYPTION_KEY is
    // unset. It did not, until review on #78 — it returned 200 with an empty
    // list, so this form rendered and a user could type a live brokerage
    // secret into a screen that could not store it. This pins the UI half of
    // that fix: a 503 must blank the form, not just show an error above it.
    listBrokerConnections.mockRejectedValue(
      new FakeApiError(503, "503: Broker connections are not available on this deployment yet."));
    render(<MyBrokers />);
    expect(await screen.findByText(/not available on this deployment/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/never shown again/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^connect$/i })).not.toBeInTheDocument();
  });

  it("refuses to submit an empty field without calling the API", async () => {
    render(<MyBrokers />);
    await waitFor(() => expect(listBrokerConnections).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText(/^PK/), { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: /^connect$/i }));
    expect(await screen.findByText(/Both the API key and the secret are required/i))
      .toBeInTheDocument();
    expect(connectBroker).not.toHaveBeenCalled();
  });

  it("confirms before destroying stored keys", async () => {
    listBrokerConnections.mockResolvedValue({
      connections: [CONNECTION], execution_routing_enabled: false,
    });
    disconnectBroker.mockResolvedValue({ ok: true, detail: "Disconnected." });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<MyBrokers />);
    await screen.findByText(/ALPACA/);
    fireEvent.click(screen.getByRole("button", { name: /disconnect/i }));

    expect(confirmSpy).toHaveBeenCalled();
    expect(disconnectBroker).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: /disconnect/i }));
    await waitFor(() => expect(disconnectBroker).toHaveBeenCalledWith("c1"));
    confirmSpy.mockRestore();
  });

  it("keeps the secret field out of the browser's password manager", async () => {
    render(<MyBrokers />);
    await waitFor(() => expect(listBrokerConnections).toHaveBeenCalled());
    const secretField = screen.getByPlaceholderText(/never shown again/i);
    expect(secretField).toHaveAttribute("type", "password");
    // new-password, not "off": Chrome ignores "off" on password inputs, and a
    // brokerage secret saved under this site's name would then be offered
    // back on an unrelated login form.
    expect(secretField).toHaveAttribute("autocomplete", "new-password");
  });
});
