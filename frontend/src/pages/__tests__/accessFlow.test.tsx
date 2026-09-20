/**
 * The request-access and setup-token screens.
 *
 * The assertions that matter are about what these screens REFUSE to say. The
 * backend gives up helpfulness on purpose — one answer whether or not an
 * address has an account, one answer for every kind of bad token — and a
 * client that improves on those messages puts the oracle straight back. That
 * is a regression no backend test can catch, because the backend is still
 * behaving perfectly when it happens.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import RequestAccess from "../RequestAccess";
import Claim, { tokenFromHash } from "../Claim";

const REAL_FETCH = globalThis.fetch;

/** What a stubbed response needs to carry. Deliberately not Partial<Response>:
 *  `body` there is a ReadableStream, and the collision typechecks as an error
 *  while the test still passes, which is a confusing way to be wrong. */
type StubResponse = { ok?: boolean; status?: number; body?: unknown };

function mockFetch(impl: (url: string, init?: RequestInit) => StubResponse) {
  const spy = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const r = impl(String(url), init);
    return {
      ok: r.ok ?? true,
      status: r.status ?? 200,
      json: async () => r.body ?? {},
    } as Response;
  });
  globalThis.fetch = spy as unknown as typeof fetch;
  return spy;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  globalThis.fetch = REAL_FETCH;
  vi.restoreAllMocks();
});

function renderAt(el: React.ReactElement) {
  return render(<MemoryRouter>{el}</MemoryRouter>);
}

async function submitRequest(email: string) {
  renderAt(<RequestAccess />);
  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: email } });
  fireEvent.click(screen.getByRole("button", { name: /request access/i }));
}

// ── request access ──────────────────────────────────────────────────────────

describe("RequestAccess", () => {
  it("posts the email and reason to the public route", async () => {
    const spy = mockFetch(() => ({ ok: true }));
    renderAt(<RequestAccess />);

    fireEvent.change(screen.getByLabelText(/email/i),
      { target: { value: "  Trader@Example.COM " } });
    fireEvent.change(screen.getByLabelText(/why/i),
      { target: { value: "I run a covered-call book" } });
    fireEvent.click(screen.getByRole("button", { name: /request access/i }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/access-requests");
    expect(JSON.parse(String(init.body))).toEqual({
      email: "Trader@Example.COM",             // trimmed; the server lower-cases
      reason: "I run a covered-call book",
    });
  });

  it("says the same thing whoever asked", async () => {
    // The whole point. The server returns one body for a new address, one that
    // already has an account, one already queued and one previously denied —
    // so this screen must have exactly one success state, with nothing in it
    // that could differ between those cases.
    const seen = new Set<string>();
    for (const email of ["new@example.com", "known@example.com", "denied@example.com"]) {
      mockFetch(() => ({ ok: true }));
      const { unmount } = renderAt(<RequestAccess />);
      fireEvent.change(screen.getByLabelText(/email/i), { target: { value: email } });
      fireEvent.click(screen.getByRole("button", { name: /request access/i }));
      await screen.findByRole("status");
      seen.add(screen.getByRole("status").textContent || "");
      unmount();
    }
    expect(seen.size).toBe(1);
  });

  it("never claims an email is on its way", async () => {
    // There is no mail server in this stack. "Check your inbox" would be a lie
    // the system cannot make true, and the person would wait for nothing.
    mockFetch(() => ({ ok: true }));
    await submitRequest("new@example.com");

    const text = (await screen.findByRole("status")).textContent || "";
    expect(text).not.toMatch(/check your (inbox|email)/i);
    expect(text).not.toMatch(/we.?ve sent|email is on its way/i);
    expect(document.body.textContent).toMatch(/a person reads these/i);
  });

  it("offers the terminal instead of a form that cannot succeed", async () => {
    // AUTH_ENABLED is false on a single-operator install and the routes 404
    // there. Leaving someone filling in a form that 404s is the dead end this
    // whole page exists to remove.
    mockFetch(() => ({ ok: false, status: 404, body: { detail: "..." } }));
    await submitRequest("new@example.com");

    await screen.findByRole("status");
    expect(screen.getByRole("link", { name: /open the terminal/i }))
      .toHaveAttribute("href", "/terminal");
  });

  it("reports a rate limit as a rate limit", async () => {
    mockFetch(() => ({ ok: false, status: 429, body: { detail: "..." } }));
    await submitRequest("new@example.com");

    expect((await screen.findByRole("alert")).textContent).toMatch(/too many/i);
  });

  it("lets a failed submission be retried", async () => {
    // busy must clear on the error path. It did not in an earlier draft — the
    // button stayed disabled and the only way out was a page reload.
    mockFetch(() => ({ ok: false, status: 500, body: { detail: "..." } }));
    await submitRequest("new@example.com");

    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: /request access/i })).not.toBeDisabled();
  });
});

// ── claiming ────────────────────────────────────────────────────────────────

describe("tokenFromHash", () => {
  it("reads the token out of a fragment", () => {
    expect(tokenFromHash("#token=abc123")).toBe("abc123");
    expect(tokenFromHash("token=abc123")).toBe("abc123");
  });

  it("accepts a bare token, since links get mangled in transit", () => {
    expect(tokenFromHash("#abc123")).toBe("abc123");
  });

  it("decodes percent-encoding", () => {
    expect(tokenFromHash("#ab%2Dc")).toBe("ab-c");
  });

  it("is empty when there is no fragment", () => {
    expect(tokenFromHash("")).toBe("");
    expect(tokenFromHash("#")).toBe("");
  });

  it.each(["#%zz", "#%", "#a%2"])(
    "survives the malformed escape %s instead of throwing", (hash) => {
      // decodeURIComponent throws a URIError on these, and this runs inside a
      // useState initializer — so an unhandled throw takes the whole setup
      // page down before it renders, leaving the person unable to even paste
      // their token by hand. Which is the opposite of the "mangled links are
      // tolerated" this function claims: mangling is what produces them.
      expect(() => tokenFromHash(hash)).not.toThrow();
      expect(tokenFromHash(hash)).toBe(hash.slice(1));
    });

  it("does not throw on a malformed escape inside a key=value fragment", () => {
    // URLSearchParams does not throw on the same input, so this branch never
    // needed the guard — pinned so a future "simplification" that routes both
    // branches through decodeURIComponent reintroduces the crash loudly.
    expect(() => tokenFromHash("#token=%zz")).not.toThrow();
  });
});

describe("Claim, given a mangled setup link", () => {
  it("still renders a form the token can be pasted into", async () => {
    window.history.replaceState(null, "", "/claim#token=%zz");
    mockFetch(() => ({ ok: true }));

    renderAt(<Claim />);

    expect(screen.getByLabelText(/setup token/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create account/i })).toBeInTheDocument();
  });
});

describe("Claim", () => {
  it("prefills the token from the fragment and clears it from the URL", async () => {
    // A fragment never reaches the server, so the token stays out of every
    // access log between the browser and the app. Clearing it afterwards keeps
    // it out of the history entry and out of a screenshot of the address bar.
    window.history.replaceState(null, "", "/claim#token=secret-token-value");
    mockFetch(() => ({ ok: true }));

    renderAt(<Claim />);

    expect(screen.getByLabelText(/setup token/i)).toHaveValue("secret-token-value");
    await waitFor(() => expect(window.location.hash).toBe(""));
  });

  it("creates the account with the password the person chose", async () => {
    const spy = mockFetch(() => ({ ok: true }));
    renderAt(<Claim />);

    fireEvent.change(screen.getByLabelText(/setup token/i), { target: { value: "tok" } });
    fireEvent.change(screen.getByLabelText(/^password/i),
      { target: { value: "a long enough passphrase" } });
    fireEvent.change(screen.getByLabelText(/confirm/i),
      { target: { value: "a long enough passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/access-requests/claim");
    expect(JSON.parse(String(init.body)))
      .toEqual({ token: "tok", password: "a long enough passphrase" });
  });

  it("will not submit a password shorter than the server accepts", () => {
    // A cap here below MIN_PASSWORD_LEN would let someone submit something the
    // server rejects; above it, they get a 422 with no explanation. Both are
    // the same class of bug that made create_user.py and the login route
    // disagree, which is why that bound is imported rather than retyped.
    mockFetch(() => ({ ok: true }));
    renderAt(<Claim />);

    fireEvent.change(screen.getByLabelText(/setup token/i), { target: { value: "tok" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "short" } });
    fireEvent.change(screen.getByLabelText(/confirm/i), { target: { value: "short" } });

    expect(screen.getByRole("button", { name: /create account/i })).toBeDisabled();
    expect(screen.getByRole("alert").textContent).toMatch(/at least 12/i);
  });

  it("will not submit two passwords that differ", () => {
    mockFetch(() => ({ ok: true }));
    renderAt(<Claim />);

    fireEvent.change(screen.getByLabelText(/setup token/i), { target: { value: "tok" } });
    fireEvent.change(screen.getByLabelText(/^password/i),
      { target: { value: "a long enough passphrase" } });
    fireEvent.change(screen.getByLabelText(/confirm/i),
      { target: { value: "a long enough passphrasx" } });

    expect(screen.getByRole("button", { name: /create account/i })).toBeDisabled();
    expect(screen.getByRole("alert").textContent).toMatch(/do not match/i);
  });

  it("does not repeat back whatever the server said a rejection was", async () => {
    // The server returns one 400 for wrong, expired, already-used and denied,
    // so today there is nothing to leak. The risk is the CLIENT echoing
    // `detail`: that reads as harmless, passes every test written against one
    // response, and silently re-exports the distinction the moment the server
    // ever gets more specific. So the guard is that the message the person
    // sees does not vary with what the server put in the body.
    const messages = new Set<string>();
    for (const detail of ["token expired", "already claimed", "no such token"]) {
      mockFetch(() => ({ ok: false, status: 400, body: { detail } }));
      const { unmount } = renderAt(<Claim />);
      fireEvent.change(screen.getByLabelText(/setup token/i), { target: { value: "tok" } });
      fireEvent.change(screen.getByLabelText(/^password/i),
        { target: { value: "a long enough passphrase" } });
      fireEvent.change(screen.getByLabelText(/confirm/i),
        { target: { value: "a long enough passphrase" } });
      fireEvent.click(screen.getByRole("button", { name: /create account/i }));
      messages.add((await screen.findByRole("alert")).textContent || "");
      unmount();
    }

    expect(messages.size).toBe(1);
    expect([...messages][0]).toMatch(/not valid/i);
  });

  it("keeps the token but clears the passwords after a failure", async () => {
    // The token is the part nobody can retype from memory. Wiping it would
    // make a mistyped password cost them the link.
    mockFetch(() => ({ ok: false, status: 400, body: { detail: "..." } }));
    renderAt(<Claim />);

    fireEvent.change(screen.getByLabelText(/setup token/i), { target: { value: "tok" } });
    fireEvent.change(screen.getByLabelText(/^password/i),
      { target: { value: "a long enough passphrase" } });
    fireEvent.change(screen.getByLabelText(/confirm/i),
      { target: { value: "a long enough passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await screen.findByRole("alert");
    expect(screen.getByLabelText(/setup token/i)).toHaveValue("tok");
    expect(screen.getByLabelText(/^password/i)).toHaveValue("");
  });
});
