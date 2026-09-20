/**
 * The chrome shared by every credential screen: sign-in, request access, and
 * setup-token redemption.
 *
 * Extracted from Login.tsx rather than copied into the two new pages, because
 * most of what is here is a record of something that was wrong once — the
 * 16px input font that stops iOS Safari zooming the page on focus, the 44px
 * tap targets, the literal-hex notice tones. A copy diverges from the reason
 * it exists the first time one of them is edited.
 */

import React from "react";

import { tint } from "../utils/tint";

export function FormShell({
  caption, onSubmit, children,
}: {
  /** The small monospaced line under the wordmark. */
  caption: string;
  onSubmit: (e: React.FormEvent) => void;
  children: React.ReactNode;
}) {
  return (
    <div style={{
      minHeight: "100dvh",
      display: "flex", alignItems: "center", justifyContent: "center",
      background: "var(--bg)", color: "var(--ink)",
      fontFamily: "var(--sans)",
      padding: 16,                       // gutter survives at phone width
    }}>
      <form
        onSubmit={onSubmit}
        style={{
          width: "100%", maxWidth: 380,
          background: "var(--bg-2)",
          border: "1px solid var(--line-dim)",
          borderRadius: "var(--radius-card)",
          boxShadow: "var(--raised-bezel)",
          padding: 28,
          display: "flex", flexDirection: "column", gap: 18,
        }}
      >
        <header style={{
          display: "flex", flexDirection: "column", alignItems: "center", gap: 10,
        }}>
          <img src="/favicon-32x32.png" alt="" width={40} height={40} />
          <span className="brand-wordmark" style={{
            fontSize: 20, letterSpacing: "0.14em", color: "var(--brand)", fontWeight: 700,
          }}>
            OLBOS
          </span>
          <span style={{
            fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em",
            textTransform: "uppercase", color: "var(--ink-faint)",
          }}>
            {caption}
          </span>
        </header>
        {children}
      </form>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{
        fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em",
        textTransform: "uppercase", color: "var(--ink-dim)",
      }}>
        {label}
      </span>
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
      disabled={busy || disabled}
      style={{
        height: 44,                      // >= 44px: a real tap target on phones
        borderRadius: "var(--radius-control)",
        border: "1px solid var(--line)",
        background: busy ? "var(--bg-3)" : "var(--fill-active)",
        color: busy ? "var(--ink-faint)" : "var(--ink)",
        fontFamily: "var(--mono)", fontSize: 12, letterSpacing: "0.08em",
        textTransform: "uppercase",
        cursor: busy ? "progress" : "pointer",
        opacity: !busy && disabled ? 0.55 : 1,
      }}
    >
      {busy ? busyLabel : idleLabel}
    </button>
  );
}

export const inputStyle: React.CSSProperties = {
  height: 44,
  padding: "0 12px",
  borderRadius: "var(--radius-control)",
  border: "1px solid var(--line-dim)",
  background: "var(--bg-1)",
  color: "var(--ink)",
  // 16px: anything smaller makes iOS Safari zoom the page on focus, which on a
  // phone leaves the form half off-screen behind the keyboard.
  fontSize: 16,
  fontFamily: "var(--sans)",
  width: "100%",
};

export const textAreaStyle: React.CSSProperties = {
  ...inputStyle,
  height: undefined,
  minHeight: 88,
  padding: "10px 12px",
  resize: "vertical",
  lineHeight: 1.45,
};

export const footnoteStyle: React.CSSProperties = {
  fontSize: 11, lineHeight: 1.5, color: "var(--ink-faint)",
  textAlign: "center", margin: 0,
};

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
    padding: "9px 11px",
    borderRadius: "var(--radius-control)",
    border: `1px solid ${tint(tone, 0.333)}`,
    background: tint(tone, 0.08),
    color: tone,
    fontSize: 12,
    lineHeight: 1.45,
  };
}
