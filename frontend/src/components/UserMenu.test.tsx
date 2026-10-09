/**
 * The Account-group identity chip.
 *
 * Mostly about when it must render NOTHING. It sits inside TerminalLayout, so
 * every way it can misbehave takes the whole shell with it — and it first
 * shipped throwing outside an AuthProvider, which broke 14 existing layout
 * tests before this file existed.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import UserMenu from "./UserMenu";
import { AuthProvider } from "../auth/AuthContext";
import { uninstallSessionExpiryInterceptor } from "../auth/sessionExpiry";

const USER = { id: "u1", email: "trader@example.com", tier: "pro" };

function stubStatus(body: unknown) {
  window.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path === "/api/auth/status") {
      return new Response(JSON.stringify(body), { status: 200 });
    }
    if (path === "/api/auth/logout") {
      // The route's real success envelope. A bare {} would now (correctly) be
      // read as "cannot prove the route ran", so the client would keep the user
      // signed in — the stub has to answer like the real endpoint.
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }) as never;
}

afterEach(() => {
  uninstallSessionExpiryInterceptor();
  vi.restoreAllMocks();
});

describe("without an AuthProvider", () => {
  it("renders nothing instead of throwing", () => {
    // TerminalLayout is rendered standalone by other tests and by any future
    // reuse. Optional chrome must degrade, not explode.
    const { container } = render(<UserMenu />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("with auth disabled", () => {
  it("renders nothing — there is no identity on a single-operator install", async () => {
    stubStatus({ auth_enabled: false, authenticated: false, user: null });
    const { container } = render(<AuthProvider><UserMenu /></AuthProvider>);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});

describe("signed in", () => {
  it("shows the local part of the address and the tier", async () => {
    stubStatus({ auth_enabled: true, authenticated: true, user: USER });
    render(<AuthProvider><UserMenu /></AuthProvider>);

    const chip = await screen.findByRole("button", { name: /trader@example\.com/i });
    expect(chip).toHaveTextContent("trader");
    expect(chip).toHaveTextContent(/pro/i);
  });

  it("reveals the full address and a sign-out control when opened", async () => {
    stubStatus({ auth_enabled: true, authenticated: true, user: USER });
    render(<AuthProvider><UserMenu /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /trader@example\.com/i }));
    expect(screen.getByRole("menu")).toHaveTextContent("trader@example.com");
    expect(screen.getByRole("menuitem", { name: /sign out/i })).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    stubStatus({ auth_enabled: true, authenticated: true, user: USER });
    render(<AuthProvider><UserMenu /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /trader@example\.com/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("hides the chip after signing out", async () => {
    stubStatus({ auth_enabled: true, authenticated: true, user: USER });
    const { container } = render(<AuthProvider><UserMenu /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /trader@example\.com/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("renders the menu outside scrollable navigation so it is not clipped", async () => {
    /**
     * On mobile the Account group is inside a scrollable navigation drawer.
     * The menu opens outside that box so an ancestor cannot clip Sign out.
     *
     * The fix portals it to <body>, so the test asserts the structural
     * property that makes clipping impossible rather than a pixel measurement
     * jsdom cannot produce.
     */
    stubStatus({ auth_enabled: true, authenticated: true, user: USER });
    const { container } = render(
      <div aria-label="Account navigation" style={{ overflowX: "hidden", overflowY: "auto" }}>
        <AuthProvider><UserMenu /></AuthProvider>
      </div>
    );

    fireEvent.click(await screen.findByRole("button", { name: /trader@example\.com/i }));

    const menu = screen.getByRole("menu");
    expect(container.contains(menu)).toBe(false);      // escaped the scroller
    expect(document.body.contains(menu)).toBe(true);
    expect(menu).toHaveStyle({ position: "fixed" });   // not clipped by an ancestor
  });

  it("still closes on a click outside, now that the menu is portalled", async () => {
    // The menu is no longer a DOM descendant of the wrapper, so naive
    // click-away logic would treat a click INSIDE it as a click-away and close
    // it before Sign out ran.
    stubStatus({ auth_enabled: true, authenticated: true, user: USER });
    render(<AuthProvider><UserMenu /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /trader@example\.com/i }));
    fireEvent.mouseDown(screen.getByRole("menu"));
    expect(screen.getByRole("menu")).toBeInTheDocument();   // a click inside keeps it open

    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("stays open and keeps the user signed in when sign-out cannot reach the server", async () => {
    /**
     * The request never lands, so nothing happened: the session row is live and
     * the cookie is still in the browser. The cookie is httpOnly, so script
     * cannot clear it — there is no client-side fallback.
     *
     * Closing the menu here would hide the warning explaining that, and the
     * chip would vanish as if signed out. Someone on a borrowed machine walks
     * away believing they are safe.
     */
    window.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input), window.location.origin).pathname;
      if (path === "/api/auth/status") {
        return new Response(
          JSON.stringify({ auth_enabled: true, authenticated: true, user: USER }),
          { status: 200 }
        );
      }
      if (path === "/api/auth/logout") throw new TypeError("network down");
      return new Response("{}", { status: 200 });
    }) as never;

    render(<AuthProvider><UserMenu /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /trader@example\.com/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/still signed in/i);
    });
    expect(screen.getByRole("menu")).toBeInTheDocument();          // not dismissed
    expect(screen.getByRole("menuitem", { name: /sign out/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /trader@example\.com/i })
    ).toBeInTheDocument();                                          // still signed in
  });

  it("closes normally when sign-out succeeds", async () => {
    // The other half: the failure handling must not make the success path
    // sticky, or every sign-out leaves a menu hanging open.
    stubStatus({ auth_enabled: true, authenticated: true, user: USER });
    const { container } = render(<AuthProvider><UserMenu /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /trader@example\.com/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("does not render the tier chip for a free account", async () => {
    // "free" next to someone's name is noise, not information.
    stubStatus({ auth_enabled: true, authenticated: true, user: { ...USER, tier: "free" } });
    render(<AuthProvider><UserMenu /></AuthProvider>);

    const chip = await screen.findByRole("button", { name: /trader@example\.com/i });
    expect(chip.textContent).toBe("trader");
  });
});
