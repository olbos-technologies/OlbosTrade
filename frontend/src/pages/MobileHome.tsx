import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { useTerminalNav } from "../components/TerminalNavContext";
import { riskWarnings } from "../components/CommandAttention";
import KillSwitchButton from "../components/KillSwitchButton";
import "./MobileHome.css";

type Mode = { mode: string; persistence?: string; detail?: string };
type Summary = { total_equity: number | null; total_pnl: number; day_pnl: number };
type Position = { symbol?: string; strategy?: string; option_type?: string; quantity?: number; avg_cost?: number; unrealized_pnl?: number | null; tracked?: boolean };
type Pending = { id: string; queued_at?: string | null };
type Broker = { broker: string; paper_mode: boolean; status: string };
type Coverage = { available?: boolean; source?: string; unprotected_is_reliable?: boolean; positions_error?: string; positions_without_stop?: { ticker: string }[] };
type Resource<T> = { data: T | null; at: number | null; error: boolean; loading: boolean };
const FRESH_MS = 45000;

export async function json<T>(path: string): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin" });
  if (!r.ok) throw new Error("Read failed");
  const d = await r.json();
  if (!d || d.error) throw new Error("Read unavailable");
  return d as T;
}

// Independent reads: one failed panel must not turn the others into zeroes.
// Serial polling prevents an older response from overwriting a newer one.
export function useResource<T>(read: () => Promise<T>, now: number) {
  const [state, setState] = useState<Resource<T>>({ data: null, at: null, error: false, loading: true });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(n => n + 1), []);
  useEffect(() => {
    let alive = true;
    let busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const data = await read();
        if (alive) setState({ data, at: Date.now(), error: false, loading: false });
      } catch {
        if (alive) setState(s => ({ ...s, error: true, loading: false }));
      } finally { busy = false; }
    };
    load();
    const timer = setInterval(load, 15000);
    return () => { alive = false; clearInterval(timer); };
  }, [read, revision]);
  const fresh = !state.error && state.at !== null && now - state.at < FRESH_MS;
  return { ...state, value: fresh ? state.data : null, fresh, refresh };
}

export const readSummary = async () => {
  const d = await json<Summary>("/api/dashboard/summary");
  if (!(d.total_equity === null || (typeof d.total_equity === "number" && Number.isFinite(d.total_equity)))) throw new Error("Account value unavailable");
  return d;
};
export const readBroker = async () => {
  const d = await json<Broker>("/api/market/broker");
  if (typeof d.broker !== "string" || typeof d.paper_mode !== "boolean" || typeof d.status !== "string") throw new Error("Broker unavailable");
  return d;
};
type Guard = { trading_allowed: boolean; reason?: string; daily_loss_pct?: number; max_daily_loss_pct?: number; weekly_loss_pct?: number; max_weekly_loss_pct?: number };
export const readGuard = async () => {
  const value = await json<Guard>("/api/guardrails/status");
  if (typeof value.trading_allowed !== "boolean") throw new Error("Risk checks unavailable");
  return value;
};
export const readRiskState = () => json<{ state?: Record<string, unknown> }>("/api/risk/portfolio-state");
export const readCoverage = () => json<Coverage>("/api/portfolio/open-orders");
export const readMode = async () => {
  const d = await api.getExecutionMode() as Mode;
  if (!["manual", "copilot", "autopilot"].includes(d?.mode)) throw new Error("Unknown mode");
  return d;
};
export const readKill = async () => {
  const d = await api.getTradeDeskKillSwitch() as { engaged: boolean };
  if (typeof d?.engaged !== "boolean") throw new Error("Unknown kill switch");
  return d;
};
export const readPositions = async () => {
  const d = await api.getPositions() as { positions: Position[] };
  if (!Array.isArray(d?.positions)) throw new Error("Positions unavailable");
  return d.positions;
};
export const readPending = async () => {
  const d = await api.getPendingApprovals() as { pending: Pending[] };
  if (!Array.isArray(d?.pending)) throw new Error("Queue unavailable");
  return d.pending;
};

export function splitPending(rows: Pending[], now: number) {
  return rows.reduce((out, row) => {
    const at = Date.parse(row.queued_at || "");
    if (!Number.isFinite(at) || at > now) out.unknown++;
    else if (now - at >= 86400000) out.older++;
    else out.recent++;
    return out;
  }, { recent: 0, older: 0, unknown: 0 });
}
export const money = (n: unknown, signed = false) =>
  typeof n === "number" && Number.isFinite(n)
    ? (signed ? (n < 0 ? "−" : "+") : n < 0 ? "−" : "") +
      Math.abs(n).toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 })
    : "—";

export default function MobileHome() {
  const nav = useTerminalNav();
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t); }, []);
  const summary = useResource(readSummary, now);
  const mode = useResource(readMode, now);
  const broker = useResource(readBroker, now);
  const positions = useResource(readPositions, now);
  const pending = useResource(readPending, now);
  const coverage = useResource(readCoverage, now);
  const kill = useResource(readKill, now);
  const guard = useResource(readGuard, now);
  const riskState = useResource(readRiskState, now);
  const warnings = riskWarnings(guard.value, riskState.value?.state);
  const [stopping, setStopping] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [stopError, setStopError] = useState(false);
  const counts = pending.value ? splitPending(pending.value, now) : null;
  const halted = kill.value?.engaged === true;
  const modeLabel = halted ? "Trading halted" : mode.value ? mode.value.mode.toUpperCase() : "MODE UNVERIFIED";
  const persisted = mode.value?.persistence === "confirmed";
  const reliable = coverage.value?.available === true &&
    coverage.value.source === "refreshed" &&
    coverage.value.unprotected_is_reliable === true &&
    !coverage.value.positions_error &&
    Array.isArray(coverage.value.positions_without_stop);
  const uncovered = reliable ? coverage.value!.positions_without_stop! : null;
  const stop = async () => {
    setStopping(true); setNotice(null); setStopError(false);
    try {
      const result = await api.setExecutionMode("manual") as Mode;
      if (result?.mode !== "manual") throw new Error("Manual mode was not confirmed. Check execution controls.");
      setNotice(result.persistence === "confirmed"
        ? "Manual mode confirmed. New automated entries stopped."
        : "Manual is in force, but its saved state is unconfirmed. Verify before a restart.");
      mode.refresh();
    } catch (e) {
      setStopError(true);
      setNotice(e instanceof Error ? e.message : "Could not confirm Manual mode.");
      mode.refresh();
    } finally { setStopping(false); }
  };
  const refresh = () => {
    [summary, mode, broker, positions, pending, coverage, kill, guard, riskState].forEach(r => r.refresh());
    setNow(Date.now());
  };

  return (
    <div className="mobile-home" data-testid="mobile-home">
      <header className="mh-heading">
        <div><div className="mh-eyebrow">YOUR TRADING DAY</div><h1>Overview</h1></div>
        <button className="mh-icon-button" onClick={refresh} aria-label="Refresh home">↻</button>
      </header>

      <section className="mh-session-intro" aria-label="Session overview">
        <span className="mh-eyebrow">COMMAND CENTER / PORTFOLIO OVERSIGHT</span>
        <h2>Control the session.<br /><span>Protect the capital.</span></h2>
        <p>One operational view for portfolio state, developing signals and exceptions requiring your attention.</p>
        <button className="mh-primary" onClick={() => nav("trade:positions")}>Review positions <span aria-hidden="true">→</span></button>
        <button className="mh-link" onClick={() => nav("risk:heat")}>Open Risk Center <span aria-hidden="true">↗</span></button>
      </section>

      {warnings.length > 0 && <section className="mh-card mh-risk-alert" aria-label="Needs attention">
        <div className="mh-section-title">Risk needs attention</div>
        {warnings.map(warning => <p className="mh-warning" key={warning}>{warning}</p>)}
        <button className="mh-link" onClick={() => nav("risk")}>Review portfolio &amp; risk <span aria-hidden="true">↗</span></button>
      </section>}
      <section className={`mh-status mh-card${halted ? " mh-status-halted" : ""}${!mode.value || !kill.value || !broker.value || broker.value.status !== "connected" || !guard.value ? " mh-status-unverified" : ""}`} aria-label="Execution status">
        <div className="mh-section-title">Trading status</div>
        <div className="mh-line">
          <strong className={halted ? "mh-negative" : mode.value?.mode === "autopilot" ? "mh-warning" : ""}>{modeLabel}</strong>
          <span className="mh-environment">{broker.value
            ? `${broker.value.broker.toUpperCase()} · ${broker.value.paper_mode === true ? "PAPER" : broker.value.paper_mode === false ? "LIVE" : "ENV UNKNOWN"}`
            : "BROKER UNAVAILABLE"}</span>
        </div>
        <p>{halted ? "Kill switch engaged. Verify broker exposure."
          : !mode.value ? "Execution mode could not be confirmed."
          : !kill.value ? "Kill switch status unverified. Do not assume automatic execution is available."
          : !broker.value || broker.value.status !== "connected" ? "Broker connection unverified. Execution availability is unknown."
          : mode.value.mode === "autopilot" ? "Automatic execution enabled."
          : mode.value.mode === "copilot" ? "Every new trade needs your approval."
          : "Signals only. No automated entries."}</p>
        {mode.value && <p className={!persisted ? "mh-warning" : "mh-muted"}>
          {persisted ? `Mode: ${mode.value.mode.charAt(0).toUpperCase() + mode.value.mode.slice(1)} · saved`
            : `Saved state ${mode.value.persistence || "unverified"} — verify execution controls`}
        </p>}
        {broker.value && broker.value.status !== "connected" && <p className="mh-warning">Broker disconnected — account data may be unavailable.</p>}
        {!kill.value && <p className="mh-warning">Kill switch status unverified.</p>}
        {!guard.value && !guard.loading && <p className="mh-warning">Entry-check status unavailable. Review Risk before submitting new orders.</p>}
        <details className="mh-system-details">
          <summary>System details</summary>
          <p className="mh-muted">Mode last checked: {mode.at ? `${Math.max(0, Math.floor((now - mode.at) / 1000))}s ago${mode.fresh ? "" : " · unavailable or stale"}` : "unavailable"}. Account and protection data refresh independently.</p>
          <button className="mh-link" onClick={() => nav("system:broker")}>View connection details <span aria-hidden="true">↗</span></button>
        </details>
      </section>

      <section className={`mh-card mh-account${!summary.loading && !summary.value ? " mh-account-unavailable" : ""}`} aria-label="Account overview">
        <div className="mh-eyebrow">{broker.value?.paper_mode === true ? "Paper account value"
          : broker.value?.paper_mode === false ? "Live account value" : "Broker account value"}</div>
        <div className="mh-balance">{summary.loading ? "Loading…" : money(summary.value?.total_equity)}</div>
        {!summary.value && !summary.loading && <p className="mh-warning">Account data unavailable. Refresh to retry.</p>}
        {summary.value?.total_equity == null && summary.value && <p className="mh-warning">Broker account value unavailable.</p>}
        <div className="mh-pnl-grid">
          <div><span className="mh-muted">Olbos day P&amp;L</span><strong className={typeof summary.value?.day_pnl === "number" ? summary.value.day_pnl < 0 ? "mh-negative" : "mh-positive" : ""}>{money(summary.value?.day_pnl, true)}</strong></div>
          <div><span className="mh-muted">Olbos total P&amp;L</span><strong>{money(summary.value?.total_pnl, true)}</strong></div>
        </div>
        <p className="mh-muted">Trade P&amp;L and broker balance may cover different holdings.</p>
      </section>

      <section className="mh-card mh-protection" aria-label="Stop coverage">
        <div className="mh-section-title">Protection</div>
        <p className={uncovered === null || uncovered.length ? "mh-warning" : ""}>
          {uncovered === null ? "Stop coverage unverified"
            : uncovered.length ? `${uncovered.length} position${uncovered.length === 1 ? "" : "s"} without a protective stop`
            : "No missing stops reported"}
        </p>
        <p className="mh-muted">{uncovered === null ? "A refreshed broker order book is required."
          : uncovered.length ? uncovered.map(p => p.ticker).join(" · ") : "Based on the latest refreshed order book."}</p>
        <button className="mh-link" onClick={() => nav("trade:positions")}>Review positions <span aria-hidden="true">↗</span></button>
      </section>

      <section className="mh-card mh-attention" aria-label="Pending signals">
        <div className="mh-section-title">Needs your attention</div>
        {counts ? <>
          <div className="mh-counts" aria-label="Signals awaiting review">
            <div><strong>{counts.recent}</strong><span>Recent · under 24h</span></div>
            <div><strong>{counts.older}</strong><span>Older · 24h or more</span></div>
          </div>
          {counts.unknown > 0 && <p className="mh-warning">{counts.unknown} with unknown age</p>}
          <p className="mh-muted">{pending.value!.length === 0 ? "Your approval queue is clear." : "Age groups only. Every approval is checked again before execution."}</p>
        </> : <p className="mh-warning">{pending.loading ? "Loading signals…" : "Signal queue unavailable."}</p>}
        <button className="mh-primary" onClick={() => nav("trade:copilot")}>Review signal queue <span aria-hidden="true">→</span></button>
        <button className="mh-link" onClick={() => nav("markets:chart")}>Explore markets <span aria-hidden="true">↗</span></button>
      </section>

      <section className="mh-card mh-holdings" aria-label="Open positions">
        <div className="mh-line"><div className="mh-section-title">Open positions</div><span className="mh-muted">{positions.value ? positions.value.length : "—"}</span></div>
        {!positions.value ? <p className="mh-warning">{positions.loading ? "Loading positions…" : "Positions unavailable."}</p>
          : positions.value.length === 0 ? <p className="mh-muted">No open positions reported.</p>
          : positions.value.slice(0, 3).map((p, i) => <div className="mh-position" key={`${p.symbol}-${i}`}>
              <div><strong>{p.symbol || "Unknown symbol"}</strong><span>{p.tracked === false ? "Untracked broker holding" : p.strategy || p.option_type || "Position"}{p.quantity != null ? ` · ${p.quantity}` : ""}</span></div>
              <strong className={typeof p.unrealized_pnl === "number" ? p.unrealized_pnl < 0 ? "mh-negative" : "mh-positive" : ""}>{money(p.unrealized_pnl, true)}</strong>
            </div>)}
        <button className="mh-link" onClick={() => nav("trade:positions")}>All positions <span aria-hidden="true">↗</span></button>
      </section>

      <section className="mh-card mh-controls" aria-label="Automation controls">
        <div className="mh-section-title">Execution controls</div>
        <button className="mh-secondary" disabled={stopping || mode.value?.mode === "manual" || halted} onClick={stop}>
          {stopping ? "Stopping…" : mode.value?.mode === "manual" ? "Manual mode" : "Stop automation"}
        </button>
        <p className="mh-muted">Switches to Manual. Existing positions, protective stops and exit management continue.</p>
        {notice && <p role={stopError ? "alert" : "status"} className="mh-warning">{notice}</p>}
        <button className="mh-link" onClick={() => nav("trade:overview")}>Open Trade Desk <span aria-hidden="true">↗</span></button>
        <details className="mh-emergency">
          <summary>Emergency controls</summary>
          <p>Requests cancellation of working orders and closure of positions at market. Broker execution must be verified.</p>
          <KillSwitchButton onEngaged={() => { kill.refresh(); positions.refresh(); }} />
        </details>
      </section>
    </div>
  );
}

