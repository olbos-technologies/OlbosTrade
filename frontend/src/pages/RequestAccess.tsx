/**
 * "Ask for an account" — what the landing page's Start Free button now points
 * at, instead of a terminal the visitor cannot get into.
 *
 * Two things this screen deliberately does not do.
 *
 * It does not tell you whether your address already has an account. The server
 * answers identically either way, and echoing anything more specific here
 * would rebuild the enumeration oracle the backend gives up helpfulness to
 * avoid — on a platform whose users are, by construction, people with
 * brokerage accounts.
 *
 * It does not promise a timeline or an email. There is no mail server in this
 * stack; an operator reads the queue and gets in touch. Saying "check your
 * inbox" would be a lie the system cannot make true, and the person would
 * wait for nothing.
 */

import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  AccessError, AccountsDisabledError, requestAccess,
} from "../auth/accessApi";
import {
  AMBER, AuthPage, Field, GREEN, Notice, Pitch, RED, SubmitButton,
} from "./authShell";

const MAX_REASON = 2000;                 // matches MAX_REASON_LEN on the server

const PITCH = (
  <Pitch
    eyebrow="Invite only"
    title="Request access to Olbos Trade."
    lede={
      "Accounts are reviewed by a person before they are issued. Tell us how "
      + "you trade, and what you want the system to do for you."
    }
    points={[
      "Every account opens in paper trading — no live order until you switch it",
      "Reviewed by the operator, not by an automated filter",
      "No card, no billing: tiers are assigned by hand while pricing is unsettled",
    ]}
  />
);

export default function RequestAccess() {
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => { emailRef.current?.focus(); }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await requestAccess(email.trim(), reason.trim());
      setSent(true);
    } catch (err) {
      if (err instanceof AccountsDisabledError) {
        setDisabled(true);
      } else {
        setError(err instanceof AccessError ? err.message : "Could not reach the server.");
      }
    } finally {
      setBusy(false);
    }
  }

  // AUTH_ENABLED is false on a single-operator install, and the routes 404
  // there. Send the visitor to the terminal rather than leaving them filling
  // in a form that cannot succeed — on that deployment the terminal genuinely
  // is open to them.
  if (disabled) {
    return (
      <AuthPage
        pitch={PITCH}
        cardTitle="Access"
        onSubmit={e => e.preventDefault()}
      >
        <Notice tone={AMBER}>
          This instance does not use accounts. The terminal is reachable directly.
        </Notice>
        <Link to="/terminal" className="auth-note auth-link">
          Open the terminal →
        </Link>
      </AuthPage>
    );
  }

  if (sent) {
    return (
      <AuthPage
        pitch={PITCH}
        cardTitle="Access"
        onSubmit={e => e.preventDefault()}
        navAction={{ to: "/", label: "Back to olbostrade" }}
      >
        <Notice tone={GREEN}>
          Request recorded. If it is approved, the operator will send you a
          one-time setup link.
        </Notice>
        <p className="auth-note">Nothing is sent automatically — a person reads these.</p>
      </AuthPage>
    );
  }

  return (
    <AuthPage
      pitch={PITCH}
      cardTitle="Request access"
      onSubmit={onSubmit}
      navAction={{ to: "/terminal", label: "Sign in", external: true }}
    >
      <Field label="Email">
        <input
          ref={emailRef}
          className="auth-input"
          type="email"
          value={email}
          onChange={e => setEmail(e.target.value)}
          autoComplete="email"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          inputMode="email"
          required
          disabled={busy}
        />
      </Field>

      <Field label="Why (optional)">
        <textarea
          className="auth-textarea"
          value={reason}
          onChange={e => setReason(e.target.value.slice(0, MAX_REASON))}
          maxLength={MAX_REASON}
          disabled={busy}
          placeholder="What you trade, and what you want out of it."
        />
      </Field>

      {/* aria-live so a screen reader announces a failure that appears after
          submit, rather than leaving the user waiting on a silent form. */}
      <div aria-live="polite">
        {error && <Notice tone={RED} role="alert">{error}</Notice>}
      </div>

      <SubmitButton
        busy={busy}
        disabled={!email}
        idleLabel="Request access"
        busyLabel="Sending…"
      />

      <p className="auth-note">
        Already have an account?{" "}
        <a href="/terminal" className="auth-link">Sign in</a>
      </p>
    </AuthPage>
  );
}
