/**
 * Sign out must be reachable from the rendered shell, in every nav state.
 *
 * UserMenu holds the ONLY sign-out in this application, and it used to render
 * inside the sidebar's Account group children — so it existed only while that
 * group rendered. Collapsing Account behind the Advanced toggle removed the
 * ability to sign out of the product, and nothing failed, because no test
 * asserted the affordance existed at all.
 *
 * UserMenu.test.tsx covers how Sign out BEHAVES once found — the click, the
 * network failure, the warning, Escape, the chip disappearing afterwards. It
 * renders UserMenu directly, so it passes whether or not the shell renders it.
 * That is the asymmetry: the mechanism was tested thoroughly and the one place
 * it has to be applied was not. This file asserts it from TerminalLayout, with
 * Advanced closed, which is the state the regression lived in.
 */
import React from "react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import TerminalLayout from "../TerminalLayout";
import { AuthProvider } from "../../auth/AuthContext";
import { uninstallSessionExpiryInterceptor } from "../../auth/sessionExpiry";

const USER = { id: "u1", email: "operator@olbos.us", tier: "pro" };

function stubSignedIn() {
  window.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path === "/api/auth/status") {
      return new Response(
        JSON.stringify({ auth_enabled: true, authenticated: true, user: USER }),
        { status: 200 },
      );
    }
    if (path === "/api/auth/logout") {
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }) as never;
}

function renderShell(activePage: string) {
  stubSignedIn();
  return render(
    <AuthProvider>
      <TerminalLayout activePage={activePage} onNav={() => {}}>
        <div>page body</div>
      </TerminalLayout>
    </AuthProvider>,
  );
}

afterEach(() => {
  uninstallSessionExpiryInterceptor();
  vi.restoreAllMocks();
});

describe("sign out is reachable from the shell", () => {
  it.each(["dashboard", "equity", "trade:positions", "risk:heat"])(
    "the account chip renders on %s with Advanced closed",
    async (page) => {
      renderShell(page);
      expect(
        await screen.findByRole("button", { name: /operator@olbos\.us/i }),
      ).toBeInTheDocument();
    },
  );

  it("opens to a real Sign out control, not just a name chip", async () => {
    renderShell("dashboard");
    fireEvent.click(await screen.findByRole("button", { name: /operator@olbos\.us/i }));
    expect(screen.getByRole("menuitem", { name: /sign out/i })).toBeInTheDocument();
  });

  it("the control signs the operator out", async () => {
    renderShell("dashboard");
    fireEvent.click(await screen.findByRole("button", { name: /operator@olbos\.us/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /operator@olbos\.us/i })).not.toBeInTheDocument(),
    );
  });
});
