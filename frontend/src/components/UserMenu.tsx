/**
 * Signed-in identity and sign-out, shown inside the Account navigation group.
 *
 * Renders nothing at all when auth is disabled — on a single-operator install
 * there is no identity to show, and an empty account affordance that does
 * nothing is worse than no affordance.
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useAuthOptional } from "../auth/AuthContext";
import { tint } from "../utils/tint";

/** Same value as --amber in index.css. See noticeStyle in pages/Login.tsx for
 *  why this is a literal rather than a var() reference. */
const AMBER = "#f59e0b";

export default function UserMenu() {
  // Optional on purpose: this is chrome inside TerminalLayout, and a missing
  // provider must mean "nothing to show", not a crash that takes the shell
  // down. See useAuthOptional.
  const auth = useAuthOptional();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [anchor, setAnchor] = useState<{ bottom: number; right: number } | null>(null);

  /**
   * The menu is portalled to <body> and positioned from the button's rect,
   * rather than absolutely positioned inside navigation chrome.
   *
   * The Account group lives inside the scrollable sidebar on phones. Portalling
   * keeps the menu from being clipped by that container and makes Sign out
   * reachable at every sidebar scroll position.
   */
  const place = useCallback(() => {
    const r = wrapRef.current?.getBoundingClientRect();
    if (!r) return;
    setAnchor({ bottom: window.innerHeight - r.top + 6, right: window.innerWidth - r.right });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      // The menu is no longer a DOM descendant of the wrapper, so a click
      // inside it would otherwise read as a click-away and close it before the
      // Sign out handler ran.
      if (!wrapRef.current?.contains(t) && !menuRef.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    // capture:true catches sidebar scrolling too, so the menu tracks its
    // button instead of detaching from it.
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  if (!auth || auth.phase !== "signed-in" || !auth.user) return null;
  const { user, signOut, logoutWarning } = auth;

  // Local part only: the status bar is tight, and the domain is the same for
  // everyone on an instance.
  const shortName = user.email.split("@")[0];

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "flex", alignItems: "center" }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account: ${user.email}`}
        style={{
          display: "flex", alignItems: "center", gap: 6,
          height: 22, padding: "0 8px", borderRadius: 11,
          background: open ? "var(--bg-4)" : "transparent",
          border: "1px solid var(--line-dim)",
          color: "var(--ink-dim)", cursor: "pointer",
          fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.06em",
          maxWidth: 180,
        }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {shortName}
        </span>
        {user.tier !== "free" && (
          <span style={{ color: "var(--brand)", textTransform: "uppercase" }}>{user.tier}</span>
        )}
      </button>

      {open && anchor && createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={{
            // Fixed, positioned from the button's rect — see place().
            position: "fixed", bottom: anchor.bottom, right: anchor.right,
            minWidth: 220, maxWidth: "min(300px, calc(100vw - 32px))",
            background: "var(--bg-2)", border: "1px solid var(--line-dim)",
            borderRadius: "var(--radius-control)", boxShadow: "var(--raised-bezel)",
            padding: 10, zIndex: 200,
            display: "flex", flexDirection: "column", gap: 8,
          }}
        >
          <div style={{
            fontSize: 11, color: "var(--ink)", wordBreak: "break-all", lineHeight: 1.4,
          }}>
            {user.email}
          </div>
          <div style={{
            fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.08em",
            textTransform: "uppercase", color: "var(--ink-faint)",
          }}>
            {user.tier} tier
          </div>

          {/* Two cases land here, and they differ in where the warning is read:
              - unreachable: the phase stays "signed-in" on purpose, so this
                menu is still mounted and THIS is where the warning is seen.
              - cookie cleared but not revoked: the phase flips to anonymous,
                this unmounts, and the login screen carries the warning.
              Plus the leftover case — a warning from an earlier failed sign-out
              that the operator has since signed back in over. */}
          {logoutWarning && (
            <div role="status" style={{
              fontSize: 10, lineHeight: 1.4, color: AMBER,
              border: `1px solid ${tint(AMBER, 0.333)}`, background: tint(AMBER, 0.08),
              borderRadius: 4, padding: "6px 8px",
            }}>
              {logoutWarning}
            </div>
          )}

          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                // Closing unconditionally would hide the warning explaining
                // that sign-out FAILED and the session is still live — the
                // same "built it, then made it unreachable" mistake the
                // logout warning already suffered once. Stay open on false so
                // the message is visible and Sign out can be tried again.
                if (await signOut()) setOpen(false);
              } finally {
                setBusy(false);
              }
            }}
            style={{
              height: 36, borderRadius: "var(--radius-control)",
              border: "1px solid var(--line-dim)", background: "var(--bg-3)",
              color: "var(--ink)", cursor: busy ? "progress" : "pointer",
              fontFamily: "var(--mono)", fontSize: 11, letterSpacing: "0.06em",
              textTransform: "uppercase",
            }}
          >
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>,
        document.body
      )}
    </div>
  );
}
