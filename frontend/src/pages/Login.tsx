/**
 * Sign-in screen.
 *
 * Deliberately plain in what it OFFERS: there is no "forgot password" (no
 * reset flow exists yet — Phase 4) and no "remember me" (session length is an
 * operator setting, not a per-login choice). Offering either would be a link
 * to nowhere.
 *
 * There IS now a "request access" link, because that one goes somewhere: the
 * waitlist at /request-access. It is a plain <a>, not a router <Link> — this
 * component renders inside AuthGate, below the route that mounts the terminal,
 * and a client-side navigation out of that subtree would leave the gate
 * mounted around a page that is not the terminal.
 */

import React, { useEffect, useRef, useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { LoginError } from "../auth/authApi";
import {
  AMBER, AuthPage, Field, Notice, Pitch, RED, SubmitButton,
} from "./authShell";

const PITCH = (
  <Pitch
    mark
    eyebrow="Operator access"
    title="Sign in to the terminal."
    lede={
      "Regime detection, signal attribution and a fail-closed risk gate sit in "
      + "front of every trade decision. Nothing executes without passing them."
    }
    points={[
      "Sessions are revocable — ending one takes effect immediately, not at expiry",
      "Live orders stay blocked until the account and environment are both switched",
    ]}
  />
);

export default function Login() {
  const { signIn, expiredNotice, logoutWarning } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => { emailRef.current?.focus(); }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await signIn(email.trim(), password);
    } catch (err) {
      setError(err instanceof LoginError ? err.message : "Could not reach the server.");
      // Clear the password but keep the email: the password is the part they
      // need to retype, and wiping both makes a typo cost twice as much.
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <AuthPage
      pitch={PITCH}
      cardTitle="Operator sign-in"
      onSubmit={onSubmit}
      navAction={{ to: "/request-access", label: "Request access", external: true }}
    >
      {expiredNotice && <Notice tone={AMBER}>{expiredNotice}</Notice>}

      {/* Shown here because this is where the operator lands after signing
          out. It used to be set on the context and rendered nowhere: the
          only component that displayed it was UserMenu, which returns null
          the instant the phase leaves "signed-in". A warning that server-side
          revocation may have failed was therefore unreachable by exactly the
          person who needs it. */}
      {logoutWarning && <Notice tone={AMBER}>{logoutWarning}</Notice>}

      <Field label="Email">
        <input
          ref={emailRef}
          className="auth-input"
          type="email"
          value={email}
          onChange={e => setEmail(e.target.value)}
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          inputMode="email"
          required
          disabled={busy}
        />
      </Field>

      <Field label="Password">
        <input
          className="auth-input"
          type="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          autoComplete="current-password"
          required
          disabled={busy}
        />
      </Field>

      {/* aria-live so a screen reader announces a failure that appears after
          submit, rather than leaving the user waiting on a silent form. */}
      <div aria-live="polite">
        {error && <Notice tone={RED} role="alert">{error}</Notice>}
      </div>

      <SubmitButton
        busy={busy}
        disabled={!email || !password}
        idleLabel="Sign in"
        busyLabel="Signing in…"
      />

      <p className="auth-note">
        Accounts are issued by the operator — there is no self-service signup.{" "}
        <a href="/request-access" className="auth-link">Request access</a>.
      </p>
    </AuthPage>
  );
}
