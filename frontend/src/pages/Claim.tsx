/**
 * Redeem a one-time setup token and choose a password.
 *
 * THE TOKEN ARRIVES IN THE URL FRAGMENT, not the query string — /claim#token=…
 * rather than /claim?token=…. A fragment is never sent to the server, so it
 * does not land in Caddy's access log, nginx's, or any proxy's in between. A
 * query string does, and a setup token in a log file is a live account grant
 * sitting in a file that gets shipped, rotated and read by people debugging
 * something unrelated. The difference costs one line of parsing.
 *
 * Pasting the token by hand works too, for the operator who sends the token
 * rather than a link — and it is the only path if a client strips fragments.
 *
 * The password is chosen HERE, by the person, and never travels to the
 * operator. That is the whole reason approval hands over a token instead of a
 * credential: the operator provisions an account without ever knowing how to
 * get into it.
 */

import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AccessError, AccountsDisabledError, claimAccess } from "../auth/accessApi";
import {
  AMBER, AuthPage, Field, GREEN, Notice, Pitch, RED, SubmitButton,
} from "./authShell";

/** Matches MIN_PASSWORD_LEN in backend/app/services/auth_service.py. */
const MIN_PASSWORD = 12;

/**
 * Read `token` out of the fragment.
 *
 * Tolerates a leading "#", a bare token with no "key=", and percent-encoding,
 * because the token travels by whatever channel the operator already trusts
 * and some of them mangle links.
 */
export function tokenFromHash(hash: string): string {
  const raw = (hash || "").replace(/^#/, "");
  if (!raw) return "";
  if (!raw.includes("=")) {
    // decodeURIComponent THROWS a URIError on a malformed percent escape —
    // "%zz", a trailing "%", a truncated "a%2". This runs inside a useState
    // initializer, so an unhandled throw here takes the whole setup page down
    // before it renders and the person cannot even paste their token by hand.
    // That also contradicted the line above about tolerating mangled links:
    // mangling is precisely what produces these.
    //
    // URLSearchParams does not throw on the same input (it hands back the raw
    // text, or a replacement character), so only this branch needs guarding.
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;                        // better a wrong token than no page
    }
  }
  return new URLSearchParams(raw).get("token") || "";
}

const PITCH = (
  <Pitch
    eyebrow="Approved"
    title="Set your password."
    lede={
      "Your request was approved. Choose a password — it is never sent to the "
      + "operator, and nobody but you ever sees it."
    }
    points={[
      "The setup link works once, and expires seven days after approval",
      "Your account opens in paper trading",
      "You can change this password, and end other sessions, from the terminal",
    ]}
  />
);

export default function Claim() {
  const navigate = useNavigate();
  const [token, setToken] = useState(() =>
    typeof window === "undefined" ? "" : tokenFromHash(window.location.hash));
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const firstRef = useRef<HTMLInputElement>(null);

  useEffect(() => { firstRef.current?.focus(); }, []);

  // Drop the token from the address bar once it has been read into state, so
  // it is not left in the browser's history, in a screenshot, or in whatever
  // the next "copy this URL" does with it. replaceState rather than a router
  // navigation: this must not add a history entry of its own.
  useEffect(() => {
    if (typeof window === "undefined" || !window.location.hash) return;
    window.history.replaceState(null, "", window.location.pathname);
  }, []);

  const mismatch = confirm.length > 0 && password !== confirm;
  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await claimAccess(token.trim(), password);
      setDone(true);
    } catch (err) {
      if (err instanceof AccountsDisabledError) {
        setDisabled(true);
      } else {
        setError(err instanceof AccessError ? err.message : "Could not reach the server.");
      }
      // Clear both password fields on failure, but KEEP the token: the token
      // is the part they cannot retype from memory, and wiping it would make
      // a mistyped password cost them the link.
      setPassword("");
      setConfirm("");
    } finally {
      setBusy(false);
    }
  }

  if (disabled) {
    return (
      <AuthPage pitch={PITCH} cardTitle="Set up" onSubmit={e => e.preventDefault()}>
        <Notice tone={AMBER}>
          This instance does not use accounts. The terminal is reachable directly.
        </Notice>
        <a href="/terminal" className="auth-note auth-link">Open the terminal →</a>
      </AuthPage>
    );
  }

  if (done) {
    return (
      <AuthPage pitch={PITCH} cardTitle="Set up" onSubmit={e => e.preventDefault()}>
        <Notice tone={GREEN}>Account created. You can sign in now.</Notice>
        <button
          type="button"
          className="auth-submit"
          onClick={() => navigate("/terminal")}
        >
          Sign in
        </button>
      </AuthPage>
    );
  }

  return (
    <AuthPage pitch={PITCH} cardTitle="Set your password" onSubmit={onSubmit}>
      <Field label="Setup token">
        <input
          ref={firstRef}
          className="auth-input mono"
          type="text"
          value={token}
          onChange={e => setToken(e.target.value)}
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          required
          disabled={busy}
        />
      </Field>

      <Field label={`Password (${MIN_PASSWORD}+ characters)`}>
        <input
          className="auth-input"
          type="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          autoComplete="new-password"
          minLength={MIN_PASSWORD}
          required
          disabled={busy}
        />
      </Field>

      <Field label="Confirm password">
        <input
          className="auth-input"
          type="password"
          value={confirm}
          onChange={e => setConfirm(e.target.value)}
          autoComplete="new-password"
          required
          disabled={busy}
        />
      </Field>

      <div aria-live="polite">
        {error && <Notice tone={RED} role="alert">{error}</Notice>}
        {!error && mismatch && (
          <Notice tone={AMBER} role="alert">The two passwords do not match.</Notice>
        )}
        {!error && !mismatch && tooShort && (
          <Notice tone={AMBER} role="alert">At least {MIN_PASSWORD} characters.</Notice>
        )}
      </div>

      <SubmitButton
        busy={busy}
        disabled={!token || password.length < MIN_PASSWORD || password !== confirm}
        idleLabel="Create account"
        busyLabel="Creating…"
      />
    </AuthPage>
  );
}
