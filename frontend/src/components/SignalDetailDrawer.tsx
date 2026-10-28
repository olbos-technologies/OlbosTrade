/**
 * Signal Detail Drawer — Chart Intelligence Phase 1.
 *
 * Shows transparent signal evidence and lifecycle labels. This drawer is
 * informational only and has no execution controls.
 */

import React, { useEffect, useRef } from "react";
import { Panel, StatTile, Button } from "./ui";
import BottomSheet from "./BottomSheet";
import { useIsMobile } from "../hooks/useIsMobile";

interface SignalLike {
  id?: string;
  ticker?: string;
  action?: string;
  confidence?: number;
  generated_at?: string;
  status?: string;
  reasons?: unknown;
  trade_plan?: {
    entry_price?: number;
    stop_price?: number;
    target_price?: number;
    target_move_pct?: number;
    shares?: number;
    risk_reward?: number;
    risk_dollars?: number;
  };
  indicators?: {
    rsi?: number;
    macd?: number;
    bb_pct_b?: number;
    volume_ratio?: number;
  };
}

interface LatestBar {
  timestamp: string;
  close: number;
  volume: number;
}

function normalizeReasons(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string");
  if (typeof value === "string" && value.trim()) return [value];
  return [];
}

function fmtMoney(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return "—";
  return `$${value.toFixed(2)}`;
}

function fmtNum(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function fmtDate(value: string | undefined) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export default function SignalDetailDrawer({
  open,
  onClose,
  signal,
  symbol,
  timeframe,
  latestBar,
}: {
  open: boolean;
  onClose: () => void;
  signal: SignalLike | null;
  symbol: string;
  timeframe: string;
  latestBar: LatestBar | null;
}) {
  const isMobile = useIsMobile();
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open || isMobile) return;
    const previous = document.activeElement as HTMLElement | null;
    const root = dialogRef.current;
    const focusable = () => Array.from(root?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, summary, [tabindex="0"]') || []);
    focusable()[0]?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      if (event.key !== "Tab") return;
      const nodes = focusable(); const first = nodes[0]; const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); previous?.focus(); };
  }, [open, isMobile, onClose]);


  if (!open) return null;

  const lifecycle = signal?.status || "preliminary";
  const reasons = normalizeReasons(signal?.reasons);

  const heading = `Signal Detail · ${signal?.ticker || symbol}`;
  const subheading =
    `${timeframe.toUpperCase()} · ${lifecycle.toUpperCase()} · evidence only`;

  // One body, two shells. The content is identical; only the surface it
  // arrives on differs, because a side drawer and a bottom sheet are what
  // their respective platforms expect rather than two sizes of one thing.
  const body = (
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
          {!signal ? (
            <div className="empty-state">No signal payload is selected for this symbol yet.</div>
          ) : (
            <>
              <Panel>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
                  <StatTile variant="boxed" label="Action" value={signal.action || "HOLD"} tone={signal.action === "BUY" ? "var(--green)" : signal.action === "SELL" ? "var(--red)" : "var(--ink-dim)"} />
                  <StatTile variant="boxed" label="Confidence" value={signal.confidence != null ? `${Math.round(signal.confidence * 100)}%` : "—"} tone="var(--amber)" />
                  <StatTile variant="boxed" label="Generated" value={fmtDate(signal.generated_at)} />
                </div>
              </Panel>

              <Panel title="Closed-Bar Lifecycle">
                <div style={{ color: lifecycle === "confirmed" ? "var(--green)" : "var(--amber)", fontWeight: 700, textTransform: "uppercase", marginBottom: 8 }}>
                  {lifecycle}
                </div>
                <div style={{ color: "var(--ink-dim)", fontSize: 12, lineHeight: 1.7 }}>
                  Live equity signal payloads are treated as preliminary unless the backend marks them confirmed with candle-close metadata.
                  Preliminary evidence is not eligible for backtest performance claims or direct execution.
                </div>
                <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <StatTile variant="boxed" label="Bar timestamp" value={latestBar ? fmtDate(latestBar.timestamp) : "—"} />
                  <StatTile variant="boxed" label="Bar close" value={fmtMoney(latestBar?.close)} />
                </div>
              </Panel>

              <Panel title="Technical Evidence">
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
                  <StatTile variant="boxed" label="RSI" value={fmtNum(signal.indicators?.rsi, 1)} />
                  <StatTile variant="boxed" label="MACD" value={fmtNum(signal.indicators?.macd, 3)} />
                  <StatTile variant="boxed" label="BB %B" value={fmtNum(signal.indicators?.bb_pct_b, 2)} />
                  <StatTile variant="boxed" label="Volume x" value={fmtNum(signal.indicators?.volume_ratio, 2)} />
                </div>
              </Panel>

              <Panel title="Trade Plan Evidence">
                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
                  <StatTile variant="boxed" label="Entry" value={fmtMoney(signal.trade_plan?.entry_price)} />
                  <StatTile variant="boxed" label="Stop" value={fmtMoney(signal.trade_plan?.stop_price)} tone="var(--red)" />
                  <StatTile variant="boxed" label="Target" value={fmtMoney(signal.trade_plan?.target_price)} tone="var(--green)" />
                  <StatTile variant="boxed" label="Target Move %" value={signal.trade_plan?.target_move_pct != null ? `${signal.trade_plan.target_move_pct.toFixed(1)}%` : "—"} tone="var(--amber)" />
                  <StatTile variant="boxed" label="Risk / Reward" value={signal.trade_plan?.risk_reward != null ? `${signal.trade_plan.risk_reward.toFixed(2)}x` : "—"} tone="var(--amber)" />
                </div>
              </Panel>

              <Panel title="Narrative">
                {reasons.length ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {reasons.map((reason, index) => (
                      <div key={`${reason}-${index}`} style={{ color: "var(--ink-dim)", fontSize: 12, lineHeight: 1.65 }}>
                        {index + 1}. {reason}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ color: "var(--ink-faint)", fontSize: 12 }}>No signal narrative available yet.</div>
                )}
              </Panel>

              <div style={{ color: "var(--ink-faint)", fontSize: 11, lineHeight: 1.7 }}>
                No chart signal or drawer action can submit a broker order. Candidates still require macro, risk, portfolio, guardrail, and execution-mode gates.
              </div>
            </>
          )}
        </div>
  );

  if (isMobile) {
    return (
      <BottomSheet
        open={open}
        onClose={onClose}
        title={heading}
        subtitle={subheading}
        labelledBy="signal-detail-title"
      >
        {body}
      </BottomSheet>
    );
  }

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="signal-detail-title"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 260,
        background: "rgba(0,0,0,0.58)",
        display: "flex",
        justifyContent: "flex-end",
      }}
    >
      <aside
        className="glass-surface"
        style={{
          width: "min(520px, 100vw)",
          height: "100%",
          borderLeft: "1px solid var(--line)",
          boxShadow: "-20px 0 60px rgba(0,0,0,0.45)",
          overflowY: "auto",
        }}
      >
        <div className="panel-head" style={{ position: "sticky", top: 0, background: "var(--bg-2)", zIndex: 1 }}>
          <div>
            <div id="signal-detail-title" className="panel-title">{heading}</div>
            <div className="kicker" style={{ marginTop: 4 }}>{subheading}</div>
          </div>
          <Button onClick={onClose}>Close</Button>
        </div>
        {body}
      </aside>
    </div>
  );
}
