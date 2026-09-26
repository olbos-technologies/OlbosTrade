/**
 * The chrome shared by every credential screen: sign in, request access, and
 * setup-token redemption.
 *
 * Shared rather than copied because most of what is here is a record of
 * something that was wrong once — the 16px input font that stops iOS Safari
 * zooming the page on focus, the 44px tap targets, the tinted notice tones
 * that silently rendered as nothing when they were built by string
 * concatenation. A copy diverges from the reason it exists the first time one
 * of them is edited.
 *
 * Styling lives in auth.css, following landing.css's conventions rather than
 * inventing a third visual language. What stays inline is the notice tinting,
 * which has to go through tint()/color-mix to work at all.
 */

import React from "react";
import { Link } from "react-router-dom";

import { tint } from "../utils/tint";
import "../auth.css";

/**
 * Notice tones as literal hex, NOT `var(--amber)`.
 *
 * These used to be built as `${tone}55` on top of a var() reference, matching a
 * pattern already in the codebase. It does not work: var() substitutes at the
 * token level, so `var(--amber)55` is two tokens rather than an 8-digit colour,
 * and the whole declaration is dropped. Verified in Chromium — the border came
 * back `border-style: none` and the background fully transparent, so the error
 * notice rendered as bare text with no tint at all.
 *
 * Same values as --amber, --red and --green in index.css; keep them in step.
 */
export const AMBER = "#f59e0b";
export const RED = "#ef4444";
export const GREEN = "#22c55e";

export function noticeStyle(tone: string): React.CSSProperties {
  return {
    border: `1px solid ${tint(tone, 0.333)}`,
    background: tint(tone, 0.08),
    color: tone,
  };
}

export function Notice({
  tone, role = "status", children,
}: {
  tone: string;
  role?: "status" | "alert";
  children: React.ReactNode;
}) {
  return (
    <div role={role} className="auth-notice" style={noticeStyle(tone)}>
      {children}
    </div>
  );
}

/** The pitch beside the form. On /claim it is the only thing explaining what
 *  the token in someone's hand is for, so it stacks rather than hides on a
 *  phone. */
export function Pitch({
  eyebrow, title, lede, points,
}: {
  eyebrow: string;
  title: string;
  lede: React.ReactNode;
  points?: string[];
}) {
  return (
    <div>
      <div className="auth-eyebrow">{eyebrow}</div>
      <h1 className="auth-h1">{title}</h1>
      <p className="auth-lede">{lede}</p>
      {points && points.length > 0 && (
        <ul className="auth-points">
          {points.map((p) => (
            <li key={p} className="auth-point">
              <span className="auth-point-mark" aria-hidden="true">✓</span>
              <span>{p}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AuthPage({
  pitch, cardTitle, onSubmit, navAction, children,
}: {
  pitch: React.ReactNode;
  /** The small monospaced line at the top of the card. */
  cardTitle: string;
  onSubmit: (e: React.FormEvent) => void;
  /** The contextual link in the nav — "Sign in" from the request form, and
   *  the other way round. Omitted where there is nowhere useful to go. */
  navAction?: { to: string; label: string; external?: boolean };
  children: React.ReactNode;
}) {
  return (
    <div className="auth-page">
      <nav className="auth-nav">
        <div className="auth-nav-row">
          {/* A plain <a>, not a router <Link>: Login renders inside AuthGate,
              below the route that mounts the terminal, and a client-side
              navigation out of that subtree would leave the gate mounted
              around a page that is not the terminal. */}
          <a className="auth-brand" href="/">
            <img src="/olbos-mark-sm.webp" alt="" width={55} height={24} />
            <span className="auth-wordmark">OLBOS</span>
          </a>
          {navAction && (
            navAction.external
              ? <a className="auth-nav-link" href={navAction.to}>{navAction.label}</a>
              : <Link className="auth-nav-link" to={navAction.to}>{navAction.label}</Link>
          )}
        </div>
      </nav>

      <main className="auth-main">
        <div className="auth-layout">
          {pitch}
          <form className="auth-card" onSubmit={onSubmit}>
            <p className="auth-card-title">{cardTitle}</p>
            {children}
          </form>
        </div>
      </main>
    </div>
  );
}

export function Field({
  label, children,
}: { label: string; children: React.ReactNode }) {
  return (
    <label className="auth-field">
      <span className="auth-label">{label}</span>
      {children}
    </label>
  );
}

export function SubmitButton({
  busy, disabled, idleLabel, busyLabel,
}: {
  busy: boolean; disabled: boolean; idleLabel: string; busyLabel: string;
}) {
  return (
    <button
      type="submit"
      className={busy ? "auth-submit busy" : "auth-submit"}
      disabled={busy || disabled}
    >
      {busy ? busyLabel : idleLabel}
    </button>
  );
}
