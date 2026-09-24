/**
 * TerminalLayout — the Bloomberg-style shell.
 *
 * Structure:
 *   [TICKER STRIP]        ← top, full width, live market data
 *   [NAV SIDEBAR] [MAIN]  ← left sidebar + content area
 *   [STATUS BAR]          ← bottom, full width, system status
 *
 * The sidebar is icon-only (48px) with tooltips.
 * Active route gets a cyan left-border accent.
 */

import React, { useState, useEffect } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import GlobalRiskStatus from "./GlobalRiskStatus";
import UserMenu from "./UserMenu";
import { tint } from "../utils/tint";
import ErrorBoundary from "./ErrorBoundary";
import KillSwitchButton from "./KillSwitchButton";
import { api } from "../api/client";
import { statusLabelForPage, filterNavForDisplay, type NavGroup } from "../utils/navLabels";
import { useAuthOptional } from "../auth/AuthContext";
import { NAV_MODEL_LEGACY, NAV_MODEL_V2, groupIdForKey } from "../utils/navModels";
import BottomSheet from "./BottomSheet";
import MobileBottomNav from "./MobileBottomNav";
import { isTradeDeskV2Enabled } from "../trade-desk/featureFlags";
import { TerminalNavProvider } from "./TerminalNavContext";
import { Badge } from "./ui";

// ── Icons (inline SVG — no dep) ───────────────────────────────────────────────
const Icon = ({ d, size = 16 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);

const ICONS: Record<string, string> = {
  dashboard:  "M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z M9 22V12h6v10",
  symphony:   "M9 18V5l12-2v13 M9 13l12-2 M6 21a3 3 0 100-6 3 3 0 000 6z M18 19a3 3 0 100-6 3 3 0 000 6z",
  equity:     "M23 6l-9.5 9.5-5-5L1 18 M17 6h6v6",
  backtest:   "M18 20V10 M12 20V4 M6 20v-6",
  paper:      "M3 3v18h18 M7 15l3-3 3 3 5-6",
  risk:       "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z M12 9v4 M12 17h.01",
  guardrails: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  journal:    "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z M14 2v6h6 M16 13H8 M16 17H8 M10 9H8",
  research:   "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z",
  lab:        "M9 2v6l-5 9a2 2 0 002 3h12a2 2 0 002-3l-5-9V2 M7 2h10 M8 14h8",
  flow:       "M3 12h4l3 8 4-16 3 8h4",
  strategy:   "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
  analytics:  "M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z",
  markets:    "M3 3v18h18 M7 14v3 M7 8v2 M12 6v11 M12 3v1 M17 11v6 M17 8v1",
  data:       "M12 3c4.418 0 8 1.343 8 3s-3.582 3-8 3-8-1.343-8-3 3.582-3 8-3z M4 6v6c0 1.657 3.582 3 8 3s8-1.343 8-3V6 M4 12v6c0 1.657 3.582 3 8 3s8-1.343 8-3v-6",
  mode:       "M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  settings:   "M12 15a3 3 0 100-6 3 3 0 000 6z M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z",
  logout:     "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4 M16 17l5-5-5-5 M21 12H9",
  help:       "M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3 M12 17h.01 M12 22a10 10 0 100-20 10 10 0 000 20z",
};

// Grouped navigation. A group is either a leaf (has `key`, navigates directly)
// or a section (has `children`, expands to sub-items). Each leaf `key` routes
// through the alias map in App.tsx. Models live in utils/navModels.ts —
// Trade Desk 2.0 swaps NAV_MODEL_V2 when the feature flag is on.
const NAV_ADVANCED_KEY = "olbos.nav.advanced";

function activeNavModel(): NavGroup[] {
  return isTradeDeskV2Enabled() ? NAV_MODEL_V2 : NAV_MODEL_LEGACY;
}

// ── Ticker strip ──────────────────────────────────────────────────────────────
type SnapShot = { last_close: number | null; prev_close: number | null; change_pct: number | null };

function TickerCell({ label, snap }: { label: string; snap: SnapShot }) {
  // Snapshot fields come back `undefined` (not `null`) whenever the backend's
  // market-data payload errors out (e.g. yfinance failure — a real,
  // documented condition, see AUDIT_2026-06.md). A `!== null` check treats
  // `undefined` as present and crashes the whole app on `.toFixed()`; use
  // `typeof === "number"` so any missing/malformed value renders as "—"
  // instead of taking down the terminal shell.
  const price = typeof snap.last_close === "number" ? snap.last_close : null;
  const pct   = typeof snap.change_pct === "number" ? snap.change_pct : null;
  const up    = pct !== null && pct >= 0;
  return (
    <>
      <span style={{ color: "var(--ink-dim)", marginRight: 6 }}>{label}</span>
      <span style={{ color: "var(--ink)", fontWeight: 600, marginRight: 4 }}>
        {price !== null ? price.toFixed(2) : "—"}
      </span>
      <span style={{ color: pct === null ? "var(--ink-faint)" : up ? "var(--green)" : "var(--red)", marginRight: 20 }}>
        {pct !== null ? `${up ? "▲" : "▼"} ${Math.abs(pct).toFixed(2)}%` : ""}
      </span>
    </>
  );
}

// A single row in the operational system popover.
function AccountMenuItem({ icon, label, onClick, danger = false }: {
  icon: string; label: string; onClick: () => void; danger?: boolean;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: "100%", display: "flex", alignItems: "center", gap: 10,
        padding: "9px 12px", background: hover ? "var(--bg-3)" : "transparent",
        border: "none", cursor: "pointer", textAlign: "left",
        color: danger ? "var(--red)" : "var(--ink)",
      }}
    >
      <Icon d={ICONS[icon]} size={14} />
      <span style={{ fontFamily: "var(--sans)", fontSize: 12 }}>{label}</span>
    </button>
  );
}

// Header control for execution mode — single tri-state (Manual / Copilot / Autopilot).
function ExecutionModeControl({
  mode,
  onChange,
  busy = false,
  error = null,
  pending = null,
  size = "compact",
}: {
  mode: "manual" | "copilot" | "autopilot";
  onChange: (m: "manual" | "copilot" | "autopilot") => void;
  busy?: boolean;
  error?: string | null;
  /** "compact" is the desktop header's 18px-tall row. "touch" meets the 44px
   *  iOS minimum for the mobile sheet — this control decides whether the desk
   *  places real orders unattended, so a mis-tap between MANUAL and AUTOPILOT
   *  is the one fat-finger this UI must not allow. */
  size?: "compact" | "touch";
  /** Mode requested but not yet confirmed by the server. Rendered distinctly
   *  from `mode` — never as selected — so an in-flight request can never be
   *  mistaken for an applied one. */
  pending?: "manual" | "copilot" | "autopilot" | null;
}) {
  const options: { key: "manual" | "copilot" | "autopilot"; label: string; onColor: string }[] = [
    { key: "manual", label: "MANUAL", onColor: "var(--ink-dim)" },
    { key: "copilot", label: "COPILOT", onColor: "var(--cyan)" },
    { key: "autopilot", label: "AUTOPILOT", onColor: "var(--amber)" },
  ];
  const touch = size === "touch";
  const geo = touch
    ? { group: 52, btn: 44, radius: 26, btnRadius: 22, pad: 4, gap: 6, font: 12, px: 14 }
    : { group: 22, btn: 18, radius: 11, btnRadius: 9, pad: 2, gap: 2, font: 9, px: 8 };

  return (
    <div style={{
      display: touch ? "flex" : "inline-flex",
      flexDirection: "column",
      alignItems: touch ? "stretch" : "flex-end",
      gap: touch ? 8 : 2,
      width: touch ? "100%" : undefined,
    }}>
      <div
        role="group"
        aria-label="Execution mode"
        title="Execution mode: Manual (signals only) → Copilot (approve each trade) → Autopilot (auto within guardrails). Leaving Autopilot returns to Copilot; choose Manual to stop approvals."
        style={{
          display: touch ? "flex" : "inline-flex", alignItems: "center", gap: geo.gap,
          height: geo.group, padding: geo.pad, borderRadius: geo.radius,
          width: touch ? "100%" : undefined,
          border: `1px solid ${error ? "rgba(239,68,68,0.55)" : "var(--line-dim)"}`,
          background: "var(--bg-3)",
          opacity: busy ? 0.7 : 1,
        }}
      >
        {options.map((opt) => {
          // `on` tracks the server-confirmed mode only. A requested-but-
          // unconfirmed mode gets its own dashed treatment and never borrows
          // the selected styling, so the control cannot imply a change that
          // has not happened.
          const on = mode === opt.key;
          const isPending = pending === opt.key;
          return (
            <button
              key={opt.key}
              type="button"
              onClick={() => onChange(opt.key)}
              aria-pressed={on}
              aria-busy={isPending || undefined}
              disabled={busy}
              style={{
                display: touch ? "flex" : "inline-flex",
                alignItems: "center", justifyContent: touch ? "center" : undefined, gap: 4,
                flex: touch ? 1 : undefined,
                height: geo.btn, padding: `0 ${geo.px}px`, borderRadius: geo.btnRadius,
                background: on ? "var(--cyan-dim)" : "transparent",
                border: isPending
                  ? "1px dashed var(--ink-dim)"
                  : `1px solid ${on ? opt.onColor : "transparent"}`,
                color: on ? opt.onColor : "var(--ink-faint)",
                fontFamily: "var(--mono)", fontSize: geo.font, letterSpacing: "0.08em",
                cursor: busy ? "wait" : "pointer", whiteSpace: "nowrap", transition: "all 0.12s",
              }}
            >
              <span className={`dot ${on ? "live" : "dead"}`} style={{ background: on ? opt.onColor : undefined }} />
              {opt.label}
            </button>
          );
        })}
      </div>
      {error && (
        // role="alert" (assertive), not "status" (polite): a refused change to
        // the control that decides whether the desk trades unattended has to
        // interrupt, not wait for a pause in screen-reader output. The block
        // treatment replaces a 9px right-aligned span that was easy to miss
        // entirely — which is how a 403 went unnoticed in production.
        <div
          role="alert"
          data-testid="exec-mode-error"
          style={{
            fontFamily: "var(--sans)", fontSize: 11, fontWeight: 600,
            color: "var(--red)",
            background: "rgba(239,68,68,0.12)",
            border: "1px solid rgba(239,68,68,0.55)",
            borderRadius: 4,
            padding: "5px 8px",
            maxWidth: 340, textAlign: "left", lineHeight: 1.35,
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}

function TickerStrip({ onToggle, sidebarExpanded, isMobile }: {
  onToggle: () => void; sidebarExpanded: boolean; isMobile: boolean;
}) {
  // The header's hamburger+logo block width must track the sidebar's actual
  // rendered width so their right-side dividers stay aligned as it expands/
  // collapses. On mobile the sidebar is an overlay (doesn't reserve layout
  // space) so the header block keeps its natural, unconstrained width.
  const showFullLogo = isMobile || sidebarExpanded;
  const headerLeftWidth = isMobile ? undefined : (sidebarExpanded ? 232 : 48);

  const [time, setTime] = useState(new Date());
  const [spy,  setSpy]  = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  const [qqq,  setQqq]  = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  const [nvda, setNvda] = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  const [iwm,  setIwm]  = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  const [tlt,  setTlt]  = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  const [gld,  setGld]  = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  const [uso,  setUso]  = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  // DXY = ICE US Dollar Index (~98–105). Previously this fed the UUP ETF (~$28),
  // which made the "DXY" ticker read a wrong ~28 value.
  const [dxy,  setDxy]  = useState<SnapShot>({ last_close: null, prev_close: null, change_pct: null });
  const [vix,  setVix]  = useState<number | null>(null);
  const [ivr,  setIvr]  = useState<number | null>(null);
  const [mode, setMode] = useState("balanced");
  // Execution mode (manual / copilot / autopilot) — the real backend tri-state.
  // Header shows a single three-way control so the state machine is visible.
  const [execMode, setExecMode] = useState<"manual" | "copilot" | "autopilot">("manual");
  // Mobile-only: the sheet that hosts the execution-mode control at touch size.
  const [mobileSheetOpen, setMobileSheetOpen] = useState(false);
  const [execBusy, setExecBusy] = useState(false);
  const [execError, setExecError] = useState<string | null>(null);

  // The mode being requested, if any. Kept separate from execMode so the
  // control can show that a change is in flight without ever claiming it
  // happened.
  const [execPending, setExecPending] = useState<"manual" | "copilot" | "autopilot" | null>(null);

  const setExec = (m: "manual" | "copilot" | "autopilot") => {
    if (m === execMode || execBusy) return;

    // No optimistic update. This control decides whether the desk trades on
    // its own, and the previous version flipped to the requested mode
    // immediately, then silently reverted if the server refused. Against a
    // 403 (the route is api-key gated and the browser holds no key) the
    // toggle visibly moved to MANUAL and snapped back — an operator could
    // reasonably read that as "trading is paused" while autopilot kept
    // running. For a safety control, showing an unconfirmed state is the
    // worst available failure, so execMode only ever moves on a server answer.
    setExecPending(m);
    setExecBusy(true);
    setExecError(null);
    api.setExecutionMode(m)
      .then((d: any) => {
        if (d?.mode) {
          setExecMode(d.mode);
        } else {
          // 2xx with no mode in the body: the change may or may not have
          // applied. Say exactly that rather than assuming either way.
          setExecError(
            `Mode change returned no mode — current state unconfirmed. ` +
            `Reload to re-read it from the server before acting.`,
          );
        }
      })
      .catch((err: any) => {
        const msg = String(err?.message || err || "");
        // Prefer ApiError.status; the substring check stays as a fallback for
        // anything that is not an ApiError (a network failure, a throw from
        // outside the client).
        const denied = err?.status === 403
          || msg.includes("403")
          || msg.toLowerCase().includes("forbidden");
        // Lead with the mode still in force. "Change failed" alone leaves the
        // operator to infer the current state, which is the thing they most
        // need to be certain about.
        setExecError(
          denied
            ? `REFUSED — still ${execMode.toUpperCase()}. Needs Operator API Key: Risk → paste SECRET_KEY → Save.`
            : `FAILED — still ${execMode.toUpperCase()}. ${msg || "Unknown error"}`,
        );
      })
      .finally(() => {
        setExecPending(null);
        setExecBusy(false);
      });
  };
  const [regime, setRegime] = useState<{regime: string; equity_allowed: boolean; options_allowed: boolean; equity_strategies: string[]; options_strategies: string[]} | null>(null);

  const fetchSnapshot = (sym: string, setter: (s: SnapShot) => void) => {
    fetch(`/api/market/snapshot/${sym}`)
      .then(r => r.json())
      .then(d => setter({ last_close: d.last_close, prev_close: d.prev_close, change_pct: d.change_pct }))
      .catch(() => {});
  };

  useEffect(() => {
    // Initial fetch
    fetchSnapshot("SPY",  setSpy);
    fetchSnapshot("QQQ",  setQqq);
    fetchSnapshot("NVDA", setNvda);
    fetchSnapshot("IWM",  setIwm);
    fetchSnapshot("TLT",  setTlt);
    fetchSnapshot("GLD",  setGld);
    fetchSnapshot("USO",  setUso);
    fetchSnapshot("DX-Y.NYB", setDxy);

    fetch("/api/market/regime")
      .then(r => r.json())
      .then(d => {
        setRegime(d);
        if (d.vix    !== undefined && d.vix    !== null) setVix(d.vix);
        if (d.iv_rank !== undefined && d.iv_rank !== null) setIvr(Math.round(d.iv_rank));
      })
      .catch(() => {});

    const fetchMode = () =>
      fetch("/api/mode/current")
        .then(r => r.json())
        .then(d => setMode(d.mode || "balanced"))
        .catch(() => {});
    fetchMode();

    const fetchExec = () =>
      fetch("/api/trade-desk/execution-mode")
        .then(r => r.json())
        .then(d => { if (d.mode) setExecMode(d.mode); })
        .catch(() => {});
    fetchExec();

    // Refresh every 5 minutes
    const si = setInterval(() => {
      fetchSnapshot("SPY",  setSpy);
      fetchSnapshot("QQQ",  setQqq);
      fetchSnapshot("NVDA", setNvda);
      fetchSnapshot("IWM",  setIwm);
      fetchSnapshot("TLT",  setTlt);
      fetchSnapshot("GLD",  setGld);
      fetchSnapshot("USO",  setUso);
      fetchSnapshot("DX-Y.NYB", setDxy);
    }, 5 * 60 * 1000);

    const ri = setInterval(() => {
      fetch("/api/market/regime").then(r => r.json()).then(d => {
        setRegime(d);
        if (d.vix    !== undefined && d.vix    !== null) setVix(d.vix);
        if (d.iv_rank !== undefined && d.iv_rank !== null) setIvr(Math.round(d.iv_rank));
      }).catch(() => {});
    }, 60000);

    // Poll mode every 15 seconds so it updates immediately after user changes it
    const mi = setInterval(fetchMode, 15000);
    const ei = setInterval(fetchExec, 15000);

    return () => { clearInterval(si); clearInterval(ri); clearInterval(mi); clearInterval(ei); };
  }, []);

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const mktOpen = () => {
    const h = time.getHours(), m = time.getMinutes();
    const day = time.getDay();
    if (day === 0 || day === 6) return false;
    const mins = h * 60 + m;
    return mins >= 570 && mins < 960; // 9:30–4:00 ET
  };

  const etTime = time.toLocaleTimeString("en-US", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    timeZone: "America/New_York", hour12: false,
  });

  // Build the repeating marquee content
  const sep = <span style={{ color: "var(--line-dim)", margin: "0 16px" }}>·</span>;
  const marqueeContent = (
    <span style={{ display: "inline-flex", alignItems: "center", whiteSpace: "nowrap" }}>
      {/* Equities */}
      <TickerCell label="SPY"  snap={spy}  />
      {sep}
      <TickerCell label="QQQ"  snap={qqq}  />
      {sep}
      <TickerCell label="IWM"  snap={iwm}  />
      {sep}
      <TickerCell label="NVDA" snap={nvda} />
      {sep}
      {/* Bonds */}
      <TickerCell label="TLT"  snap={tlt}  />
      {sep}
      {/* Commodities */}
      <TickerCell label="GOLD" snap={gld}  />
      {sep}
      <TickerCell label="OIL"  snap={uso}  />
      {sep}
      {/* Dollar */}
      <TickerCell label="DXY"  snap={dxy}  />
      {sep}
      {vix !== null && <>
        <span style={{ color: "var(--ink-dim)", marginRight: 6 }}>VIX</span>
        <span style={{ color: vix > 25 ? "var(--amber)" : "var(--ink)", fontWeight: 600, marginRight: 20 }}>
          {vix.toFixed(1)}
        </span>
        {sep}
      </>}
      {ivr !== null && <>
        <span style={{ color: "var(--ink-dim)", marginRight: 6 }}>IV RANK</span>
        <span style={{ color: ivr > 50 ? "var(--cyan)" : "var(--ink)", fontWeight: 600, marginRight: 20 }}>
          {ivr}
        </span>
        {sep}
      </>}
      <span style={{ color: "var(--ink-dim)", marginRight: 6 }}>MODE</span>
      <Badge kind="mode" tone={mode as "conservative" | "balanced" | "aggressive" | "scalper"} style={{ marginRight: 20 }}>{mode}</Badge>
      {/* Guard on regime.regime specifically, not just the object: a partial
          or error payload (e.g. `{}`) is still a truthy object but has no
          `.regime` string, and `.includes()`/`.replace()` on `undefined`
          crashed the whole ticker strip with no error boundary catching it
          (see AUDIT_2026-06.md re: market-data failures being a real
          condition, and the identical bug fixed in TickerCell above). */}
      {regime?.regime && <>
        {sep}
        <span style={{ color: "var(--ink-dim)", marginRight: 6 }}>REGIME</span>
        <span style={{
          fontFamily: "var(--mono)", fontSize: 10, marginRight: 20,
          color: regime.regime === "crisis" ? "var(--red)" :
                 regime.regime.includes("high_vol") ? "var(--amber)" : "var(--cyan)",
          fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em",
        }}>
          {regime.regime.replace(/_/g, " ")}
        </span>
      </>}
      {sep}
    </span>
  );

  // ── Mobile header ────────────────────────────────────────────────────────
  // The desktop header packs hamburger + wordmark + an 8-symbol marquee + the
  // execution-mode control + two clocks into ONE overflow-x:hidden row. At
  // 390px that is ~568px of content in a 390px viewport, and because the
  // overflow is hidden rather than auto, everything past the fold is
  // unreachable — including AUTOPILOT on the control that decides whether the
  // desk trades unattended, whose buttons were also 18px tall against a 44px
  // touch minimum.
  //
  // Phone gets its own layout rather than a squeezed desktop one: a 48px row
  // of real tap targets, the marquee on its own line (a marquee is meant to
  // scroll, so clipping it costs nothing), and the mode control moved into a
  // sheet at 44px where a mis-tap between MANUAL and AUTOPILOT is not one
  // stray thumb away. State stays here so there is still a single owner of
  // execMode — no parallel mobile component holding its own copy.
  if (isMobile) {
    const modeTone =
      execMode === "autopilot" ? "var(--amber)"
      : execMode === "copilot" ? "var(--cyan)"
      : "var(--ink-dim)";

    return (
      <div style={{ display: "flex", flexDirection: "column", flexShrink: 0 }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          height: 48, padding: "0 4px 0 0",
          background: "var(--bg-2)", borderBottom: "1px solid var(--line-dim)",
          fontFamily: "var(--mono)", fontSize: 11,
        }}>
          <button
            onClick={onToggle}
            aria-label="Toggle navigation"
            style={{
              width: 48, height: 48, flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: "transparent", border: "none", color: "var(--ink-dim)", cursor: "pointer",
            }}
          >
            <svg width="18" height="18" viewBox="0 0 16 16" fill="none">
              <rect x="2" y="3.5"  width="12" height="1.5" rx="0.75" fill="currentColor"/>
              <rect x="2" y="7.25" width="12" height="1.5" rx="0.75" fill="currentColor"/>
              <rect x="2" y="11"   width="12" height="1.5" rx="0.75" fill="currentColor"/>
            </svg>
          </button>

          <img src="/favicon-32x32.png" alt="" width={20} height={20} style={{ flexShrink: 0 }} />
          <span className="brand-wordmark" style={{ fontSize: 15, lineHeight: 1, whiteSpace: "nowrap" }}>
            OLBOS
          </span>

          <div style={{ flex: 1 }} />

          {/* Current mode, tappable. Shows state at a glance and is the only
              way into the control on phone — so it carries the live tone
              rather than a neutral chip that would hide AUTOPILOT being on. */}
          <button
            type="button"
            onClick={() => setMobileSheetOpen(true)}
            aria-haspopup="dialog"
            aria-expanded={mobileSheetOpen}
            style={{
              display: "flex", alignItems: "center", gap: 6,
              height: 44, padding: "0 14px", marginRight: 2, borderRadius: 22,
              // tint(), not `${modeTone}66` — modeTone is a var() reference, and
              // concatenating an alpha onto one drops the whole declaration, so
              // this border has never rendered. See utils/tint.ts.
              background: "var(--bg-3)", border: `1px solid ${tint(modeTone, 0.4)}`,
              color: modeTone, fontFamily: "var(--mono)", fontSize: 10,
              letterSpacing: "0.08em", cursor: "pointer", whiteSpace: "nowrap",
            }}
          >
            <span className={`dot ${mktOpen() ? "live" : "dead"}`} style={{ background: modeTone }} />
            {execMode.toUpperCase()}
          </button>
        </div>

        <div style={{
          height: 26, overflow: "hidden", position: "relative",
          background: "var(--bg-2)", borderBottom: "1px solid var(--line-dim)",
          fontFamily: "var(--mono)", fontSize: 10, display: "flex", alignItems: "center",
        }}>
          <div className="ticker-strip-marquee" style={{ display: "inline-flex", animation: "ticker-scroll 55s linear infinite" }}>
            {marqueeContent}{marqueeContent}
          </div>
        </div>

        <BottomSheet
          open={mobileSheetOpen}
          onClose={() => setMobileSheetOpen(false)}
          title="Execution mode"
          subtitle={mktOpen() ? `Market open · ${etTime} ET` : `Market closed · ${etTime} ET`}
        >
          {/* BottomSheet deliberately ships no horizontal padding — the gutter
              is the caller's, so content can go full-bleed where it wants to. */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "2px 16px 12px" }}>
            <ExecutionModeControl
              mode={execMode}
              onChange={setExec}
              busy={execBusy}
              error={execError}
              pending={execPending}
              size="touch"
            />
            <p style={{
              margin: 0, fontFamily: "var(--sans)", fontSize: 12.5,
              lineHeight: 1.6, color: "var(--ink-dim)",
            }}>
              <strong style={{ color: "var(--ink)" }}>Manual</strong> generates signals only.{" "}
              <strong style={{ color: "var(--ink)" }}>Copilot</strong> waits for your approval on
              every trade. <strong style={{ color: "var(--ink)" }}>Autopilot</strong> executes
              without asking, within the guardrails.
            </p>
          </div>
        </BottomSheet>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", flexShrink: 0 }}>
    <div className="instrument-ticker" style={{
      display: "flex",
      alignItems: "center",
      height: 38,
      fontFamily: "var(--mono)",
      fontSize: 11,
      flexShrink: 0,
      overflow: "hidden",
    }}>
      {/* Hamburger + logo — pinned left, width tracks the sidebar's own width
          so this block's right divider stays aligned with the sidebar's. */}
      <div style={{
        display: "flex", alignItems: "center", flexShrink: 0,
        width: headerLeftWidth, height: "100%",
        borderRight: "1px solid var(--line-dim)",
        transition: "width 0.18s ease",
      }}>
        <button
          onClick={onToggle}
          style={{
            width: 48, height: "100%", flexShrink: 0,
            display: "flex", alignItems: "center", justifyContent: "center",
            background: "transparent", border: "none",
            color: "var(--ink-faint)", cursor: "pointer",
            transition: "color 0.12s",
          }}
          onMouseEnter={e => (e.currentTarget.style.color = "var(--cyan)")}
          onMouseLeave={e => (e.currentTarget.style.color = "var(--ink-faint)")}
          title="Toggle sidebar"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <rect x="2" y="3.5"  width="12" height="1.5" rx="0.75" fill="currentColor"/>
            <rect x="2" y="7.25" width="12" height="1.5" rx="0.75" fill="currentColor"/>
            <rect x="2" y="11"   width="12" height="1.5" rx="0.75" fill="currentColor"/>
          </svg>
        </button>

        {showFullLogo && (
          <div style={{ display: "flex", alignItems: "center", gap: 7, overflow: "hidden", paddingRight: 12 }}>
            <img
              src="/favicon-32x32.png"
              alt=""
              width={20}
              height={20}
              style={{ flexShrink: 0, filter: "drop-shadow(0 0 4px rgba(212,175,55,.35))" }}
            />
            <div style={{ display: "flex", flexDirection: "column", gap: 2, overflow: "hidden" }}>
              <span
                className="brand-wordmark"
                style={{ fontSize: 17, lineHeight: 1, whiteSpace: "nowrap" }}
              >
                OLBOS
              </span>
              <span style={{
                fontFamily: "var(--mono)", fontSize: 8, fontWeight: 500,
                letterSpacing: "0.276em", color: "var(--ink-faint)", lineHeight: 1, paddingLeft: 1, whiteSpace: "nowrap",
              }}>TERMINAL</span>
            </div>
          </div>
        )}
      </div>

      {/* Marquee strip — scrolls continuously */}
      <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
        <div className="ticker-strip-marquee" style={{ display: "inline-flex", animation: "ticker-scroll 55s linear infinite" }}>
          {marqueeContent}{marqueeContent}
        </div>
      </div>

      {/* Execution mode (trading style lives in Desk Settings) */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, flexShrink: 0,
        padding: "0 12px", borderLeft: "1px solid var(--line-dim)",
      }}>
        <ExecutionModeControl mode={execMode} onChange={setExec} busy={execBusy} error={execError} pending={execPending} />
      </div>

      {/* Market status + clock — pinned right */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10, flexShrink: 0,
        padding: "0 14px", borderLeft: "1px solid var(--line-dim)",
      }}>
        <span className={`dot ${mktOpen() ? "live" : "dead"}`} />
        <span style={{ color: "var(--ink-dim)", fontSize: 10 }}>
          {mktOpen() ? "OPEN" : "CLOSED"}
        </span>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", lineHeight: 1.25 }}>
          <div>
            <span style={{ color: "var(--ink-faint)", marginRight: 3, fontSize: 9 }}>ET</span>
            <span style={{ color: "var(--ink)", fontWeight: 500, fontSize: 11 }}>{etTime}</span>
          </div>
          <div style={{ fontSize: 9, color: "var(--ink-faint)" }}>
            {time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}
            {" "}{Intl.DateTimeFormat().resolvedOptions().timeZone.split("/").pop()?.replace("_", " ")}
          </div>
        </div>
      </div>
    </div>
    </div>
  );
}

// ── Sidebar nav ───────────────────────────────────────────────────────────────
function Sidebar({ active, onNav, expanded, isMobile = false }: {
  active: string;
  onNav: (k: string) => void;
  expanded: boolean;
  isMobile?: boolean;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  // Accordion state: which group section is expanded. Auto-opens the group that
  // owns the active page.
  const navModel = activeNavModel();
  const [openGroup, setOpenGroup] = useState<string | null>(() => groupIdForKey(active, navModel));
  const [showAdvanced, setShowAdvanced] = useState(() => {
    try {
      return localStorage.getItem(NAV_ADVANCED_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    const g = groupIdForKey(active, navModel);
    if (g) setOpenGroup(g);
  }, [active, navModel]);

  const toggleAdvanced = () => {
    setShowAdvanced((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(NAV_ADVANCED_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  // Account is hidden unless somebody is actually signed in. Same predicate
  // UserMenu uses to hide itself, so the two cannot disagree about whether
  // this install has accounts at all.
  const navAuth = useAuthOptional();
  const visibleNav = filterNavForDisplay(
    navModel, showAdvanced, active,
    navAuth?.phase === "signed-in" && !!navAuth?.user,
  );

  // On mobile, labels always show (it's a full overlay panel); on desktop they
  // appear only when expanded (icon rail otherwise).
  const showLabels = expanded || isMobile;
  const W = isMobile ? 240 : (expanded ? 232 : 48);

  const containerStyle: React.CSSProperties = isMobile
    ? {
        position: "absolute", top: 0, bottom: 0, left: 0, zIndex: 50,
        width: W, minWidth: W,
        transform: expanded ? "translateX(0)" : "translateX(-100%)",
        transition: "transform 0.2s ease",
        background: "var(--bg-2)", borderRight: "1px solid var(--line-dim)",
        display: "flex", flexDirection: "column", alignItems: "stretch",
        paddingTop: 0, paddingBottom: 8, overflowY: "auto", overflowX: "hidden",
        boxShadow: expanded ? "4px 0 24px rgba(0,0,0,0.5)" : "none",
      }
    : {
        width: W, minWidth: W,
        background: "var(--bg-2)", borderRight: "1px solid var(--line-dim)",
        display: "flex", flexDirection: "column", alignItems: "stretch",
        paddingTop: 0, paddingBottom: 8, flexShrink: 0, position: "relative",
        transition: "width 0.18s ease, min-width 0.18s ease",
        overflowY: expanded ? "auto" : "hidden", overflowX: "hidden",
      };

  // A group header is highlighted when it's a leaf on the active page, or it owns
  // the active sub-item.
  const groupActive = (g: NavGroup) =>
    g.key === active || !!g.children?.some(c => c.key === active);

  const renderGroup = (g: NavGroup) => {
    const isLeaf    = !g.children;
    const isOpen    = openGroup === g.id;
    const isActive  = groupActive(g);
    const isHovered = hovered === g.id;

    const onHeaderClick = () => {
      if (isLeaf) { onNav(g.key!); return; }
      if (!showLabels) { onNav(g.children![0].key); return; } // icon rail: jump to first item
      setOpenGroup(prev => (prev === g.id ? null : g.id));
    };

    return (
      <div key={g.id} style={{ width: "100%" }}>
        <div
          style={{ position: "relative", width: "100%" }}
          onMouseEnter={() => setHovered(g.id)}
          onMouseLeave={() => setHovered(null)}
        >
          <button
            onClick={onHeaderClick}
            aria-label={g.label}
            aria-current={isActive ? "page" : undefined}
            style={{
              width: "100%", height: 38, display: "flex", alignItems: "center",
              justifyContent: showLabels ? "flex-start" : "center",
              paddingLeft: showLabels ? 14 : 0, gap: showLabels ? 10 : 0,
              background: isActive ? "var(--cyan-dim)" : isHovered ? "var(--bg-3)" : "transparent",
              border: "none",
              borderLeft: isActive ? "2px solid var(--cyan)" : "2px solid transparent",
              color: isActive ? "var(--cyan)" : isHovered ? "var(--ink)" : "var(--ink-faint)",
              cursor: "pointer", transition: "all 0.1s", overflow: "hidden", whiteSpace: "nowrap",
            }}
          >
            <span style={{ flexShrink: 0 }}><Icon d={ICONS[g.icon]} size={15} /></span>
            {showLabels && (
              <span style={{
                flex: 1, textAlign: "left", fontFamily: "var(--mono)", fontSize: 11,
                letterSpacing: "0.08em", textTransform: "uppercase",
              }}>
                {g.label}
              </span>
            )}
            {showLabels && !isLeaf && (
              <span style={{ flexShrink: 0, paddingRight: 12, opacity: 0.6, transition: "transform 0.15s", transform: isOpen ? "rotate(90deg)" : "none" }}>
                <Icon d="M9 18l6-6-6-6" size={12} />
              </span>
            )}
          </button>

          {/* Tooltip — only on the collapsed desktop icon rail */}
          {!showLabels && isHovered && (
            <div style={{
              position: "absolute", left: 52, top: "50%", transform: "translateY(-50%)",
              background: "var(--bg-4)", border: "1px solid var(--line-dim)", padding: "4px 10px",
              whiteSpace: "nowrap", fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em",
              textTransform: "uppercase", color: "var(--ink)", zIndex: 100, pointerEvents: "none",
            }}>
              {g.label}
            </div>
          )}
        </div>

        {/* Sub-items — only when expanded and open */}
        {showLabels && !isLeaf && isOpen && g.children!.map(c => {
          const subActive  = active === c.key;
          const subHovered = hovered === c.key;
          return (
            <button
              key={c.key}
              onClick={() => onNav(c.key)}
              onMouseEnter={() => setHovered(c.key)}
              onMouseLeave={() => setHovered(null)}
              aria-label={c.label}
              aria-current={subActive ? "page" : undefined}
              style={{
                width: "100%", height: 32, display: "flex", alignItems: "center",
                paddingLeft: 40, gap: 0,
                background: subActive ? "var(--cyan-dim)" : subHovered ? "var(--bg-3)" : "transparent",
                border: "none",
                borderLeft: subActive ? "2px solid var(--cyan)" : "2px solid transparent",
                color: subActive ? "var(--cyan)" : subHovered ? "var(--ink)" : "var(--ink-dim)",
                cursor: "pointer", transition: "all 0.1s", overflow: "hidden", whiteSpace: "nowrap",
                textAlign: "left",
              }}
            >
              <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, letterSpacing: "0.04em" }}>
                {c.label}
              </span>
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div style={containerStyle}>

      {/* Grouped nav — Core by default; Advanced leaves/groups behind toggle */}
      {visibleNav.map(renderGroup)}

      {showLabels && (
        <button
          type="button"
          onClick={toggleAdvanced}
          aria-expanded={showAdvanced}
          style={{
            width: "100%", height: 32, display: "flex", alignItems: "center",
            paddingLeft: 14, gap: 8, marginTop: 4,
            background: "transparent", border: "none",
            borderTop: "1px solid var(--line-dim)",
            color: "var(--ink-dim)", cursor: "pointer",
            fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          <span style={{ transform: showAdvanced ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>
            <Icon d="M9 18l6-6-6-6" size={11} />
          </span>
          {showAdvanced ? "Hide advanced" : "Show advanced"}
        </button>
      )}

      {/* Operational system access — pinned above the kill switch. */}
      <div style={{ flex: 1 }} />

      {/* No account/auth controls are shown until a real identity backend exists. */}
      <div
        onMouseEnter={() => setHovered("account")}
        onMouseLeave={() => setHovered(null)}
        style={{ position: "relative", width: "100%" }}
      >
        <button
          onClick={() => setAccountMenuOpen(o => !o)}
          title="Open system status"
          style={{
            width: "100%", height: 44, display: "flex", alignItems: "center",
            justifyContent: showLabels ? "flex-start" : "center",
            paddingLeft: showLabels ? 14 : 0, gap: showLabels ? 10 : 0,
            background: accountMenuOpen || hovered === "account" ? "var(--bg-3)" : "transparent",
            border: "none", borderTop: "1px solid var(--line-dim)",
            cursor: "pointer", overflow: "hidden", whiteSpace: "nowrap", transition: "all 0.1s",
          }}
        >
          <span style={{
            flexShrink: 0, width: 24, height: 24, borderRadius: "50%",
            background: "var(--cyan-dim)", color: "var(--cyan)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontFamily: "var(--mono)", fontSize: 10, fontWeight: 700,
          }}>
            SYS
          </span>
          {showLabels && (
            <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", lineHeight: 1.3, overflow: "hidden" }}>
              <span style={{ fontFamily: "var(--sans)", fontSize: 12, color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                System
              </span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink-faint)", letterSpacing: "0.06em" }}>
                OPERATIONS
              </span>
            </span>
          )}
        </button>
        {!showLabels && !accountMenuOpen && hovered === "account" && (
          <div style={{
            position: "absolute", left: 52, top: "50%", transform: "translateY(-50%)",
            background: "var(--bg-4)", border: "1px solid var(--line-dim)", padding: "4px 10px",
            whiteSpace: "nowrap", fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em",
            textTransform: "uppercase", color: "var(--ink)", zIndex: 100, pointerEvents: "none",
          }}>
            System
          </div>
        )}

        {/* Account menu — opens upward, anchored to the plate. On the collapsed
            icon rail it opens to the right instead, like the hover tooltips. */}
        {accountMenuOpen && (
          <>
            <div
              onClick={() => setAccountMenuOpen(false)}
              style={{ position: "fixed", inset: 0, zIndex: 90 }}
            />
            <div className="glass-surface" style={{
              position: "absolute",
              ...(showLabels
                ? { bottom: "100%", left: 8, right: 8, marginBottom: 4 }
                : { bottom: 0, left: 52 }),
              width: showLabels ? undefined : 200,
              border: "1px solid var(--line-dim)",
              borderRadius: 6, boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
              zIndex: 100, overflow: "hidden",
            }}>
              <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--line-dim)" }}>
                <div style={{ fontFamily: "var(--sans)", fontSize: 12, color: "var(--ink)" }}>System Operations</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", marginTop: 2 }}>
                  broker · data · health
                </div>
              </div>
              <AccountMenuItem icon="settings" label="System"
                onClick={() => { setAccountMenuOpen(false); onNav("settings"); }} />
            </div>
          </>
        )}
      </div>

      {/* Kill switch at bottom — engages confirm modal; does not navigate away */}
      <div
        style={{
          position: "relative", width: "100%",
          borderTop: "1px solid var(--line-dim)",
          paddingTop: 4, paddingBottom: 4,
        }}
      >
        <KillSwitchButton variant="sidebar" expanded={showLabels} />
      </div>
    </div>
  );
}

// ── Status bar ────────────────────────────────────────────────────────────────
function StatusLamp({
  label,
  on,
  warn,
  unknown,
  className,
}: {
  label: string;
  on: boolean;
  warn?: boolean;
  /** Lets a caller mark a lamp as duplicated elsewhere on the page, so the
   *  stylesheet can drop it in that one context. See app-shell--desk. */
  className?: string;
  /** The read failed or returned nothing. Rendered as amber "?" rather than
   *  as "off": a dim lamp is indistinguishable from a successful read saying
   *  the thing is off, which is the one reading this row must never imply
   *  about a kill switch or a live account. Mirrors GlobalRiskStatus, whose
   *  chips already treat unknown as attention-worthy. */
  unknown?: boolean;
}) {
  const color = unknown ? "var(--amber)" : warn ? "var(--amber)" : on ? "var(--green)" : "var(--ink-faint)";
  return (
    <span
      className={className}
      title={`${label}: ${unknown ? "could not be read" : warn ? "warn" : on ? "on" : "off"}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        color,
        textTransform: "uppercase",
      }}
    >
      <span
        className={`dot ${on || warn || unknown ? "live" : "dead"}`}
        style={{ background: color, width: 6, height: 6 }}
      />
      {unknown ? `${label} ?` : label}
    </span>
  );
}

function StatusBar({ page }: { page: string }) {
  const label = statusLabelForPage(page, activeNavModel());
  // null = not read yet, or the read failed. These three answer "is the desk
  // about to move real money unattended", so each defaulted to its reassuring
  // value on failure: killOn=false rendered a failed read as NOT ARMED,
  // paper=true rendered an unreadable broker as green PAPER even on a live
  // account, and execMode="manual" rendered a failed read as not auto-trading.
  // GlobalRiskStatus already refuses exactly this ("a missing kill-switch read
  // never renders as 'not armed'"); the two rows could therefore disagree,
  // with this one being the optimistic of the pair.
  const [killOn, setKillOn] = useState<boolean | null>(null);
  const [execMode, setExecMode] = useState<string | null>(null);
  const [styleMode, setStyleMode] = useState("balanced");
  const [rotationOn, setRotationOn] = useState(false);
  const [paper, setPaper] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.all([
        api.getTradeDeskKillSwitch().catch(() =>
          api.getKillSwitchStatus().catch(() => ({})),
        ),
        api.getExecutionMode().catch(() => ({})),
        api.getCurrentMode().catch(() => ({})),
        api.getGuardrailStatus().catch(() => ({})),
        fetch("/api/market/broker").then((r) => r.json()).catch(() => ({})),
      ]).then(([kill, exec, mode, guard, broker]) => {
        if (!alive) return;
        // Only assert a value the payload actually carried; a failed fetch
        // resolves to {} above, which must read as unknown, not as safe.
        const engaged = (kill as any).engaged ?? (kill as any).is_engaged;
        setKillOn(typeof engaged === "boolean" ? engaged : null);

        const em = (exec as any).mode;
        setExecMode(typeof em === "string" && em ? em.toLowerCase() : null);

        setStyleMode(((mode as any).mode || "balanced").toLowerCase());
        setRotationOn(!!(guard as any).position_rotation_on_max);

        if ((broker as any).paper_mode === false) setPaper(false);
        else if ((broker as any).paper_mode === true) setPaper(true);
        else if (typeof (guard as any).paper_mode === "boolean") {
          setPaper(!!(guard as any).paper_mode);
        } else setPaper(null);
      });
    };
    load();
    const id = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="instrument-status" style={{
      height: 28,
      display: "flex",
      alignItems: "center",
      padding: "0 16px",
      gap: 16,
      fontFamily: "var(--mono)",
      fontSize: 10,
      color: "var(--ink-faint)",
      letterSpacing: "0.08em",
      flexShrink: 0,
    }}>
      <span style={{ color: "var(--brand)", textTransform: "uppercase" }}>{label}</span>
      <span
        className="status-dup"
        style={{
          color: paper === null ? "var(--amber)" : paper ? "var(--green)" : "var(--red)",
          fontWeight: 700,
          textTransform: "uppercase",
        }}
        title={
          paper === null
            ? "Paper/live state could not be read from the broker or guardrails"
            : paper ? "Paper trading" : "Live trading — real capital"
        }
      >
        {paper === null ? "ENV ?" : paper ? "PAPER" : "LIVE"}
      </span>
      {/* status-dup: repeated by the Trade Desk rail, hidden there on phones
          only. See app-shell--desk. */}
      <StatusLamp className="status-dup" label="Kill" on={killOn === true} warn={killOn === true} unknown={killOn === null} />
      <StatusLamp
        className="status-dup"
        label={execMode ? `Exec ${execMode}` : "Exec"}
        on={execMode !== null && execMode !== "manual"}
        warn={execMode === "autopilot"}
        unknown={execMode === null}
      />
      <StatusLamp className="status-dup" label={`Style ${styleMode}`} on />
      <StatusLamp label="Rotation" on={rotationOn} />
      <div style={{ flex: 1 }} />
      <span id="broker-status-bar" className="status-dup">IBKR GATEWAY</span>
      {/* Renders nothing when auth is disabled — see UserMenu. */}
      <UserMenu />
      <span style={{ color: "var(--brand)", fontWeight: 700 }}>Olbos v5.0</span>
    </div>
  );
}

// ── Layout shell ──────────────────────────────────────────────────────────────
export default function TerminalLayout({ children, activePage, onNav, isDeskV2Shell = false }: {
  children: React.ReactNode;
  activePage: string;
  onNav: (key: string) => void;
  isDeskV2Shell?: boolean;
}) {
  const isMobile = useIsMobile();
  // On desktop the sidebar starts collapsed (icon rail); on mobile it starts
  // hidden and slides in as an overlay.
  const [sidebarExpanded, setSidebarExpanded] = useState(false);

  // On mobile, navigating closes the overlay.
  const handleNav = (key: string) => {
    onNav(key);
    if (isMobile) setSidebarExpanded(false);
  };

  return (
    <TerminalNavProvider onNav={handleNav}>
    {/* .app-shell carries the height: 100dvh fallback — 100vh on mobile
        Safari counts the URL bar, which pushes the status bar off screen. */}
    <div
      /*
        app-shell--desk marks only the V2 Trade Desk shell pages (including the
        /terminal/paper alias). Legacy TradeDesk routes share the same keys but
        do not render TradeDeskHeader, so deduping there would hide the only
        risk/status bands.
      */
      className={`app-shell${isDeskV2Shell ? " app-shell--desk" : ""}`}
      style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}
    >
      <ErrorBoundary label="Ticker strip">
        <TickerStrip onToggle={() => setSidebarExpanded(p => !p)} sidebarExpanded={sidebarExpanded} isMobile={isMobile} />
      </ErrorBoundary>
      <ErrorBoundary label="Capital-at-risk status">
        <GlobalRiskStatus />
      </ErrorBoundary>
      <div style={{ display: "flex", flex: 1, overflow: "hidden", position: "relative" }}>
        <ErrorBoundary label="Navigation">
          <Sidebar
            active={activePage}
            onNav={handleNav}
            expanded={sidebarExpanded}
            isMobile={isMobile}
          />
        </ErrorBoundary>
        {/* Tap-away backdrop when the overlay sidebar is open on mobile */}
        {isMobile && sidebarExpanded && (
          <div
            onClick={() => setSidebarExpanded(false)}
            style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 45 }}
          />
        )}
        <main style={{
          flex: 1,
          overflow: "auto",
          background: "var(--bg)",
          minWidth: 0,   // allow children to shrink instead of forcing overflow
        }}>
          <ErrorBoundary label="Page content">
            {children}
          </ErrorBoundary>
        </main>
      </div>
      <StatusBar page={activePage} />
      {/* Phones navigate from the bottom. The sidebar stays as the overflow,
          reached through "More", rather than becoming a second nav surface. */}
      {isMobile && (
        <ErrorBoundary label="Bottom navigation">
          <MobileBottomNav
            active={activePage}
            onNav={handleNav}
            onOpenMore={() => setSidebarExpanded(p => !p)}
            moreOpen={sidebarExpanded}
          />
        </ErrorBoundary>
      )}
    </div>
    </TerminalNavProvider>
  );
}
