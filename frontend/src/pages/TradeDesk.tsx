/**
 * Trade Desk — unified execution hub covering paper + live trading.
 * Tabs: DESK SIGNALS | POSITIONS | APPROVALS | P&L BREAKDOWN | TRADING STYLE
 * Execution modes: MANUAL (signals only) · COPILOT (you approve) · AUTOPILOT (auto-execute)
 */
import React, { useState, useEffect, useCallback } from "react";
import { usePaperTrade } from "../hooks/usePaperTrade";
import TradingModeSelector from "../components/TradingModeSelector";
import SignalAttribution from "../components/SignalAttribution";
import MetricHint, { resolveMetricHint } from "../components/MetricHint";
import type { SignalAttributionData } from "../types/signal";
import { api } from "../api/client";
import CommandOverview from "../trade-desk/CommandOverview";
import ExecutionMonitor from "../trade-desk/execution/ExecutionMonitor";
import type { TradeDeskTab } from "../trade-desk/TradeDeskTabs";
import { useTerminalNav } from "../components/TerminalNavContext";
import HoldToConfirmButton from "../components/HoldToConfirmButton";
import { canClosePosition, isCloseableType, isDbOnly } from "../utils/closeablePosition";
import { Button } from "../components/ui";
import ManualTradePanel from "../trade-desk/orders/ManualTradePanel";
import { tint } from "../utils/tint";
import { useTabRoute } from "../hooks/useTabRoute";

function HintedTh({ label }: { label: string }) {
  return (
    <th>
      {resolveMetricHint(label) ? <MetricHint id={label} /> : label}
    </th>
  );
}
type ExecMode = "manual" | "copilot" | "autopilot";
type Tab = "overview" | "signals" | "positions" | "approvals" | "execution" | "pnl" | "mode" | "manual";

const Badge = ({ text, color }: { text: string; color: string }) => (
  <span style={{
    fontFamily: "var(--mono)", fontSize: 10, padding: "2px 8px",
    border: `1px solid ${tint(color, 0.251)}`, background: tint(color, 0.082),
    color, letterSpacing: "0.08em",
  }}>{text}</span>
);

const fmtDollars = (value: number | null | undefined, digits = 0) =>
  value == null ? "—" : `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(digits)}`;

const fmtCapture = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(0)}%`;

// ── Execution Mode Selector ───────────────────────────────────────────────────
function ExecModeBar() {
  const [mode, setMode]     = useState<ExecMode>("manual");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (api.getExecutionMode() as any)
      .then((d: any) => setMode(d.mode || "manual"))
      .catch(() => {});
  }, []);

  const select = async (m: ExecMode) => {
    if (m === mode) return;
    setSaving(true);
    try {
      await (api.setExecutionMode(m) as any);
      setMode(m);
    } finally {
      setSaving(false);
    }
  };

  const modes: { key: ExecMode; label: string; desc: string; color: string }[] = [
    { key: "manual",    label: "Manual",    desc: "Signals displayed only — you decide when to trade",  color: "var(--ink-dim)" },
    { key: "copilot",   label: "Copilot",   desc: "System queues trades — you approve each one first",   color: "var(--accent)" },
    { key: "autopilot", label: "Autopilot", desc: "Fully automatic — executes within guardrail limits",  color: "var(--orange)" },
  ];

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "6px 16px", background: "var(--bg-3)",
      borderBottom: "1px solid var(--line-dim)",
    }}>
      <span className="kicker" style={{ marginRight: 4 }}>Execution</span>
      {modes.map(m => (
        <Button
          key={m.key}
          active={mode === m.key}
          style={{
            fontSize: 12,
            ...(mode === m.key ? { borderColor: m.color, color: m.color, background: tint(m.color, 0.082) } : {}),
          }}
          onClick={() => select(m.key)}
          disabled={saving}
          title={m.desc}
        >
          {m.label}
          {mode === m.key && <span style={{ marginLeft: 4, opacity: 0.6 }}>●</span>}
        </Button>
      ))}
      {mode === "autopilot" && (
        <span style={{
          fontSize: 11, color: "var(--orange)",
          padding: "2px 8px", borderRadius: 3, border: "1px solid rgba(249,115,22,0.4)",
          background: "rgba(249,115,22,0.08)", marginLeft: 8,
        }}>
          Auto-executing within guardrails
        </span>
      )}
      {mode === "copilot" && (
        <span style={{
          fontSize: 11, color: "var(--accent)",
          padding: "2px 8px", borderRadius: 3, border: "1px solid rgba(59,130,246,0.4)",
          background: "rgba(59,130,246,0.08)", marginLeft: 8,
        }}>
          Approval required per trade
        </span>
      )}
      {mode === "manual" && (
        <span style={{ fontSize: 11, color: "var(--ink-dim)", marginLeft: 8 }}>
          Signals only — no auto execution
        </span>
      )}
      <div style={{ flex: 1 }} />
      {saving && <span className="kicker">Saving…</span>}
    </div>
  );
}

// ── Approvals Queue (Copilot mode) ────────────────────────────────────────────
function ApprovalsQueue() {
  const [pending, setPending]   = useState<any[]>([]);
  const [log, setLog]           = useState<any[]>([]);
  const [loading, setLoading]   = useState(true);
  const [acting, setActing]     = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    Promise.all([
      (api.getPendingApprovals() as any).catch(() => ({ pending: [] })),
      (api.getExecutionLog()    as any).catch(() => ({ log: [] })),
    ]).then(([p, l]) => {
      setPending((p as any).pending || []);
      setLog((l as any).log || []);
    }).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  const act = async (id: string, action: "approve" | "reject") => {
    setActing(id);
    try {
      if (action === "approve") await (api.approveSignal(id) as any);
      else                      await (api.rejectSignal(id) as any);
      refresh();
    } finally {
      setActing(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Pending approvals */}
      <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--line-dim)", background: "var(--bg-3)" }}>
        <span className="panel-title">
          PENDING APPROVAL
          {pending.length > 0 && (
            <span style={{
              marginLeft: 8, fontFamily: "var(--mono)", fontSize: 10,
              padding: "1px 6px", background: "rgba(249,115,22,0.2)",
              color: "var(--orange)", border: "1px solid rgba(249,115,22,0.4)",
            }}>{pending.length}</span>
          )}
        </span>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: "center", fontFamily: "var(--mono)", color: "var(--ink-faint)", fontSize: 11 }}>Loading…</div>
      ) : pending.length === 0 ? (
        <div style={{ padding: 40, textAlign: "center", fontFamily: "var(--mono)", color: "var(--ink-faint)", fontSize: 11 }}>
          No pending approvals — queue is clear
          <div style={{ marginTop: 8, color: "var(--ink-faint)", fontSize: 10 }}>
            Switch to Copilot mode to route new signals here for approval.
          </div>
        </div>
      ) : (
        <div style={{ overflowY: "auto", flex: "0 0 auto" }}>
          {pending.map((s: any) => (
            <div key={s.id} style={{
              display: "flex", alignItems: "center", gap: 16,
              padding: "12px 16px", borderBottom: "1px solid var(--line-dim)",
              background: "var(--bg-2)",
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 4 }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
                    {s.ticker}
                  </span>
                  <Badge text={s.asset_type?.toUpperCase() || "EQUITY"} color="var(--ink-dim)" />
                  <SignalAttribution
                    data={{
                      direction: s.action || s.strategy?.toUpperCase() || "BUY",
                      // Pending-approval payloads now carry their own "source"
                      // field from whichever producer queued them (background
                      // scanner or scan panel) — read it directly instead of
                      // guessing equity-vs-options from spread presence.
                      source: s.source ?? "unknown",
                      // Repository verified: pending-approval payloads carry no
                      // bar-timeframe field — left unknown rather than guessed.
                      timeframe: null,
                      confidence: typeof s.confidence === "number" ? s.confidence : null,
                      updatedAt: s.queued_at ?? null,
                      // This row is, by construction, in the Copilot approval
                      // queue — it cannot reach the broker without a human
                      // APPROVE click, so "advisory" is verified from this
                      // page's own gating, not assumed.
                      authority: "advisory",
                    } as SignalAttributionData}
                    size="sm"
                  />
                  <Badge text={s.regime?.toUpperCase() || "—"} color="var(--amber)" />
                </div>
                {s.spread && (
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                    {s.spread.option_type?.toUpperCase()} spread &nbsp;
                    {s.spread.short_strike}/{s.spread.long_strike} exp {s.spread.expiration} &nbsp;
                    credit: <span style={{ color: "var(--green)" }}>${s.spread.net_credit?.toFixed(2)}</span> &nbsp;
                    max loss: <span style={{ color: "var(--red)" }}>${s.spread.max_loss?.toFixed(2)}</span>
                  </div>
                )}
                {s.intelligence && (
                  <div style={{ display: "flex", gap: 14, marginTop: 6, flexWrap: "wrap",
                    fontFamily: "var(--mono)", fontSize: 10 }}>
                    <span style={{ color: "var(--ink-dim)" }}>
                      <MetricHint id="POP" />{" "}
                      <b style={{
                      color: (s.intelligence.pop ?? 0) >= 0.7 ? "var(--green)" : "var(--amber)" }}>
                      {((s.intelligence.pop ?? 0) * 100).toFixed(0)}%</b></span>
                    <span style={{ color: "var(--ink-dim)" }}>
                      <MetricHint id="EV" />{" "}
                      <b style={{
                      color: (s.intelligence.expected_value ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      ${(s.intelligence.expected_value ?? 0).toFixed(0)}</b></span>
                    <span style={{ color: "var(--ink-dim)" }}>
                      <MetricHint id="Kelly" />{" "}
                      <b style={{ color: "var(--cyan)" }}>
                      {((s.intelligence.kelly_fraction ?? 0) * 100).toFixed(1)}%</b></span>
                    <span style={{ color: "var(--ink-dim)" }}>
                      <MetricHint id="P(touch)" />{" "}
                      <b style={{ color: "var(--ink)" }}>
                      {((s.intelligence.prob_touch_short ?? 0) * 100).toFixed(0)}%</b></span>
                    <span style={{ color: "var(--ink-dim)" }}>
                      <MetricHint id="Δ" />{" "}
                      <b style={{ color: "var(--ink)" }}>
                      {(s.intelligence.delta_short ?? 0).toFixed(2)}</b></span>
                  </div>
                )}
                {!s.spread && (
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                    Equity signal · queued at {s.queued_at ? new Date(s.queued_at).toLocaleTimeString() : "—"}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <Button
                  disabled={acting === s.id}
                  onClick={() => act(s.id, "approve")}
                  style={{ color: "var(--green)", borderColor: "rgba(34,197,94,0.5)", fontSize: 11 }}
                >
                  APPROVE
                </Button>
                <Button
                  danger
                  disabled={acting === s.id}
                  onClick={() => act(s.id, "reject")}
                  style={{ fontSize: 11 }}
                >
                  REJECT
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Execution log */}
      <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--line-dim)", borderTop: "1px solid var(--line-dim)", background: "var(--bg-3)" }}>
        <span className="panel-title">EXECUTION LOG</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {log.length === 0 ? (
          <div style={{ padding: 32, textAlign: "center", fontFamily: "var(--mono)", color: "var(--ink-faint)", fontSize: 11 }}>
            NO EXECUTION HISTORY
          </div>
        ) : (
          <table className="t-table">
            <thead><tr>
              {["Time","Ticker","Type","Action","Confidence","Executed By","Status"].map(h => <th key={h}>{h}</th>)}
            </tr></thead>
            <tbody>
              {log.map((e: any, i: number) => (
                <tr key={i}>
                  <td className="mono" style={{ fontSize: 10, color: "var(--ink-dim)" }}>
                    {e.executed_at ? new Date(e.executed_at).toLocaleTimeString() : "—"}
                  </td>
                  <td className="mono" style={{ color: "var(--ink)" }}>{e.ticker || "—"}</td>
                  <td><Badge text={e.asset_type?.toUpperCase() || "EQ"} color="var(--ink-dim)" /></td>
                  <td className="mono" style={{ fontSize: 10 }}>{e.action || e.strategy || "—"}</td>
                  <td className="mono">{((e.confidence || 0) * 100).toFixed(1)}%</td>
                  <td className="mono" style={{ fontSize: 10, color: "var(--amber)" }}>{e.executed_by || "—"}</td>
                  <td>
                    <Badge
                      text={e.status?.toUpperCase() || "SENT"}
                      color={e.status === "rejected" ? "var(--red)" : "var(--green)"}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── P&L Breakdown ─────────────────────────────────────────────────────────────
function PnLBreakdown() {
  const [trades, setTrades]   = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [sort, setSort]       = useState<"date"|"pnl"|"commission">("date");

  useEffect(() => {
    setLoading(true);
    (api.getTradeHistory() as any)
      .then((h: any) => setTrades(h.trades || []))
      .finally(() => setLoading(false));
  }, []);

  const closed    = trades.filter((t: any) => t.status === "closed");
  const totalPnl  = closed.reduce((s: number, t: any) => s + (t.pnl || 0), 0);
  const totalComm = closed.reduce((s: number, t: any) => s + (t.commission_paid || 1.30), 0);
  const totalSlip = closed.reduce((s: number, t: any) => s + Math.abs((t.credit_received || 0) * 100 * 0.15), 0);
  const grossPnl  = totalPnl + totalComm + totalSlip;
  const wins      = closed.filter((t: any) => (t.pnl || 0) > 0);
  const losses    = closed.filter((t: any) => (t.pnl || 0) < 0);

  const sorted = [...trades].sort((a: any, b: any) => {
    if (sort === "pnl")        return (b.pnl || 0) - (a.pnl || 0);
    if (sort === "commission") return (b.commission_paid || 0) - (a.commission_paid || 0);
    return new Date(b.entry_date || 0).getTime() - new Date(a.entry_date || 0).getTime();
  });

  const StatBox = ({ label, value, color }: any) => (
    <div style={{ padding: "14px 18px", flex: 1, borderRight: "1px solid var(--line-dim)" }}>
      <div className="kicker" style={{ marginBottom: 6 }}>{label}</div>
      <div className="data-val sm" style={{ color: color || "var(--ink)" }}>{value}</div>
    </div>
  );

  return (
    <div style={{ padding: 16, overflowY: "auto", height: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <span className="panel-title">Trade Execution Summary</span>
        <Badge text="COMMISSIONS + SLIPPAGE INCLUDED" color="var(--amber)" />
      </div>

      <div className="instrument-stat-strip" style={{ display: "flex", gap: 0, marginBottom: 16 }}>
        <StatBox label="Net P&L"         value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`}  color={totalPnl >= 0 ? "var(--green)" : "var(--red)"} />
        <StatBox label="Gross P&L"       value={`$${grossPnl.toFixed(2)}`}    color="var(--ink)" />
        <StatBox label="Commission Drag" value={`-$${totalComm.toFixed(2)}`}  color="var(--amber)" />
        <StatBox label="Est. Slippage"   value={`-$${totalSlip.toFixed(2)}`}  color="var(--amber)" />
        <StatBox label="Closed Trades"   value={String(closed.length)}        color="var(--ink)" />
        <StatBox label="Win / Loss"      value={`${wins.length} / ${losses.length}`} color="var(--ink)" />
      </div>

      <div className="instrument-card" style={{
        fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)",
        padding: "8px 12px", marginBottom: 14,
        lineHeight: 1.7,
      }}>
        Commission: $0.65/contract · $1.30 per spread (IBKR standard) ·
        Slippage: VIX-adjusted est. ~15% of bid-ask spread at VIX 17, widens at higher VIX ·
        Switch broker to LIVE when ready to trade real capital.
      </div>

      <div style={{ display: "flex", gap: 1, marginBottom: 12 }}>
        {(["date","pnl","commission"] as const).map(s => (
          <Button key={s} active={sort === s}
            style={{ borderRadius: 0, fontSize: 10 }} onClick={() => setSort(s)}>
            SORT: {s === "commission" ? "COMMISSION" : s.toUpperCase()}
          </Button>
        ))}
      </div>

      {loading ? (
        <div style={{ textAlign: "center", padding: 40, fontFamily: "var(--mono)", color: "var(--ink-faint)", fontSize: 11 }}>LOADING...</div>
      ) : sorted.length === 0 ? (
        <div style={{ textAlign: "center", padding: 40, fontFamily: "var(--mono)", color: "var(--ink-faint)", fontSize: 11 }}>
          No closed trades yet — P&L breakdown appears after the first trade closes
        </div>
      ) : (
        <table className="t-table">
          <thead><tr>
            {["Symbol","Strategy","Entry","Exit","Hold","Credit","Net P&L","MFE","MAE","Capture","Commission","Slip Est.","Exit Reason"].map(h => (
              <HintedTh key={h} label={h} />
            ))}
          </tr></thead>
          <tbody>
            {sorted.map((t: any, i: number) => {
              const pnl  = t.pnl || 0;
              const comm = t.commission_paid || 1.30;
              const slip = Math.abs((t.credit_received || 0) * 100 * 0.15);
              return (
                <tr key={i}>
                  <td className="mono" style={{ color: "var(--ink)" }}>{t.underlying || t.symbol || "—"}</td>
                  <td className="mono" style={{ fontSize: 10 }}>{t.strategy?.replace(/_/g," ").toUpperCase() || "—"}</td>
                  <td className="mono" style={{ color: "var(--ink-dim)", fontSize: 10 }}>{t.entry_date?.slice(0,10) || "—"}</td>
                  <td className="mono" style={{ color: "var(--ink-dim)", fontSize: 10 }}>{t.exit_date?.slice(0,10) || "—"}</td>
                  <td className="mono">{t.hold_days != null ? `${t.hold_days}d` : "—"}</td>
                  <td className="mono">${(t.credit_received || 0).toFixed(2)}</td>
                  <td className="mono" style={{ color: pnl >= 0 ? "var(--green)" : "var(--red)", fontWeight: 600 }}>
                    {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
                  </td>
                  <td className="mono" style={{ color: "var(--green)" }}>{fmtDollars(t.mfe_pnl, 2)}</td>
                  <td className="mono" style={{ color: "var(--red)" }}>{fmtDollars(t.mae_pnl, 2)}</td>
                  <td className="mono" style={{ color: "var(--amber)" }}>{fmtCapture(t.pnl_capture_pct)}</td>
                  <td className="mono" style={{ color: "var(--amber)" }}>-${comm.toFixed(2)}</td>
                  <td className="mono" style={{ color: "var(--amber)" }}>~-${slip.toFixed(2)}</td>
                  <td className="mono" style={{ color: "var(--ink-dim)", fontSize: 10 }}>{t.exit_reason?.replace(/_/g," ").toUpperCase() || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Main Trade Desk ────────────────────────────────────────────────────────────
/**
 * Tab -> the page key that renders it.
 *
 * "mode" (Trading style) and "manual" (Manual Trade) have no page key in
 * App.tsx, so they switch without changing the URL — the address bar then
 * names the desk but not the tab. Refusing to open a tab because it has no URL
 * would be the worse trade.
 */
/** Exported so the route table can be checked against the page registry — see src/hooks/__tests__/tabRouteTable.test.tsx. */
/** The tab the page opens on when the URL names it without one. */
export const DEFAULT_TAB: Tab = "overview";

export const TAB_PAGE_KEYS = {
  overview:  "trade:overview",
  signals:   "trade:orders",
  positions: "trade:positions",
  approvals: "trade:copilot",
  execution: "trade:execlog",
  pnl:       "trade:logs",
} as const;

/** Stable identity: a fresh `{}` each render would re-run effects downstream. */
const EMPTY_TAB_KEYS: Readonly<Partial<Record<Tab, string>>> = Object.freeze({});

/**
 * `routeTabsToUrl` exists because this component is mounted in two very
 * different roles.
 *
 * As a PAGE (trade_desk_v2 off) it owns the workspace, its tabs have their own
 * registry keys, and putting the tab in the URL is the whole point.
 *
 * NESTED inside TradeDeskV2's Positions tab (TradeDeskV2.tsx) it owns only a
 * panel, and the same keys mean something else entirely: with v2 enabled
 * `trade:orders` mounts V2's OrdersWorkspace, not this component's signals tab.
 * URL-routing the nested tabs would therefore navigate the user out of the
 * page they are on — a regression, and a subtle one, because it only happens
 * with the flag in its shipping position.
 *
 * Passing an empty table rather than adding a branch keeps this on the path
 * already covered by tests: a tab with no page key switches locally and does
 * not touch the URL.
 */
export default function TradeDesk({
  initialTab = DEFAULT_TAB,
  routeTabsToUrl = true,
}: { initialTab?: Tab; routeTabsToUrl?: boolean }) {
  const { positions, lastSignal, cycleLog, loading, runCycle, refresh } = usePaperTrade();
  const [tab, setTab] = useTabRoute<Tab>(
    initialTab,
    routeTabsToUrl ? TAB_PAGE_KEYS : EMPTY_TAB_KEYS,
  );
  const onNav = useTerminalNav();
  const [closingId, setClosingId] = useState<string | null>(null);
  const [closeMsg, setCloseMsg] = useState<string | null>(null);
  const [closeFailed, setCloseFailed] = useState(false);

  /**
   * Turn a close failure into something the operator can act on.
   *
   * The 403 is the one worth naming. /api/trade-desk/close-position requires
   * X-Api-Key, the browser only sends it when the operator key is in
   * sessionStorage, and sessionStorage dies with the tab — so the common way
   * to hit this is simply to have reopened the terminal. The hold-to-confirm
   * animation still completes normally, which makes a rejected close look
   * exactly like a dead button.
   */
  const closeFailureMessage = (e: any): string => {
    if (e?.status === 403) {
      return "operator API key missing or wrong — re-enter it on Risk Monitor " +
             "(it is session-scoped and is cleared when the tab closes)";
    }
    if (e?.status === 429) return "rate limited — wait a moment and hold again";
    return e?.message || "close failed";
  };

  const closePosition = async (id: string, symbol: string) => {
    setClosingId(id);
    setCloseMsg(null);
    setCloseFailed(false);
    try {
      const res: any = await api.closePosition(id);
      setCloseFailed(false);
      setCloseMsg(`${symbol}: ${res.status === "filled" ? "closed" : `order ${res.status}`}`);
      refresh();
    } catch (e: any) {
      setCloseFailed(true);
      setCloseMsg(`${symbol}: ${closeFailureMessage(e)}`);
    } finally {
      setClosingId(null);
    }
  };

  // Same action, but for a broker position with no matching DB Trade row
  // (e.g. a fill the app lost track of after an order-placement timeout) —
  // keyed by symbol instead of a trade id, since there is no id.
  const closeUntrackedPosition = async (symbol: string) => {
    setClosingId(symbol);
    setCloseMsg(null);
    setCloseFailed(false);
    try {
      const res: any = await api.closeUntrackedPosition(symbol);
      setCloseFailed(false);
      setCloseMsg(`${symbol}: ${res.status === "filled" ? "closed" : `order ${res.status}`}`);
      refresh();
    } catch (e: any) {
      setCloseFailed(true);
      setCloseMsg(`${symbol}: ${closeFailureMessage(e)}`);
    } finally {
      setClosingId(null);
    }
  };

  // Command Overview's queue cards link out to specific workspaces — map its
  // V2-shaped tab keys onto this page's own tab set (no V2 shell involved).
  // Options has no in-page tab here (unlike V2's own Options Desk), so that
  // one jumps to the real Spread Scanner page via the terminal nav context
  // instead of silently landing on the equity-only Desk signals table.
  const handleOverviewNavigate = (t: TradeDeskTab) => {
    if (t === "copilot") setTab("approvals");
    else if (t === "execution") setTab("execution");
    else if (t === "positions") setTab("positions");
    else if (t === "options") onNav("scan");
    else setTab("signals");
  };
  const [trades, setTrades]       = useState<any[]>([]);
  const [tradesLoading, setTradesLoading] = useState(true);

  useEffect(() => {
    const load = () => {
      setTradesLoading(true);
      (api.getTradeHistory() as any)
        .then((h: any) => setTrades(h.trades || []))
        .finally(() => setTradesLoading(false));
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const holdDays = (entry: string | null, exit: string | null) => {
    if (!entry) return null;
    const from = new Date(entry);
    const to   = exit ? new Date(exit) : new Date();
    return Math.floor((to.getTime() - from.getTime()) / 86400000);
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview",  label: "Overview" },
    { key: "signals",   label: "Desk signals" },
    { key: "positions", label: "Positions" },
    { key: "approvals", label: "Approvals" },
    { key: "execution", label: "Execution Monitor" },
    { key: "pnl",       label: "P&L breakdown" },
    { key: "mode",      label: "Trading style" },
    { key: "manual",    label: "Manual Trade" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>

      {/* Execution mode bar — always visible */}
      <ExecModeBar />

      {/* Tab bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "6px 16px", borderBottom: "1px solid var(--line-dim)",
        background: "var(--bg-2)",
      }}>
        <div style={{ display: "flex", gap: 4 }}>
          {tabs.map(t => (
            <Button key={t.key} active={tab === t.key}
              onClick={() => setTab(t.key)}>
              {t.label}
            </Button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <Button active onClick={runCycle}>
          {loading ? "Running…" : "▶ Run signal cycle"}
        </Button>
      </div>

      <div style={{ flex: 1, overflow: "auto" }}>
        {/* OVERVIEW */}
        {tab === "overview" && <CommandOverview onNavigateTab={handleOverviewNavigate} />}

        {/* EXECUTION MONITOR */}
        {tab === "execution" && <ExecutionMonitor />}

        {/* SIGNALS */}
        {tab === "signals" && (
          <div>
            {lastSignal && (
              <div className="instrument-card" style={{
                padding: "12px 16px", borderBottom: "1px solid var(--line-dim)",
                display: "flex", gap: 24, alignItems: "center",
              }}>
                <span className="panel-title">LAST SIGNAL</span>
                <Badge text={lastSignal.strategy?.toUpperCase() || "—"} color="var(--ink-dim)" />
                <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)" }}>
                  Score: <span style={{ color: "var(--ink)" }}>{lastSignal.signal_score?.toFixed(3) || "—"}</span>
                </span>
                <Badge text={lastSignal.approved ? "APPROVED" : "PENDING"}
                  color={lastSignal.approved ? "var(--green)" : "var(--amber)"} />
              </div>
            )}
            <table className="t-table">
              <thead><tr>
                {["Entry Date","Symbol","Qty","Strategy","Status","Entry Price","P&L","MFE","MAE","Capture","Hold Days","Exit Reason"].map(h => (
                  <HintedTh key={h} label={h} />
                ))}
              </tr></thead>
              <tbody>
                {tradesLoading ? (
                  <tr><td colSpan={12} style={{ textAlign: "center", padding: 40, color: "var(--ink-faint)", fontFamily: "var(--mono)", fontSize: 11 }}>
                    LOADING…
                  </td></tr>
                ) : trades.length === 0 ? (
                  <tr><td colSpan={12} style={{ textAlign: "center", padding: 40, color: "var(--ink-faint)", fontFamily: "var(--mono)", fontSize: 11 }}>
                    No trade history — run a cycle or wait for the next scan
                  </td></tr>
                ) : trades.map((t: any, i: number) => {
                  const pnl  = t.pnl || 0;
                  const days = holdDays(t.entry_date, t.exit_date);
                  return (
                    <tr key={i}>
                      <td className="mono" style={{ color: "var(--ink-dim)", fontSize: 10 }}>{t.entry_date?.slice(0,10) || "—"}</td>
                      <td className="mono" style={{ color: "var(--ink)" }}>{t.underlying || t.symbol || "—"}</td>
                      <td className="mono" style={{ color: t.quantity > 0 ? "var(--green)" : t.quantity < 0 ? "var(--red)" : "var(--ink-dim)", fontWeight: 600 }}>
                        {t.quantity > 0 ? `+${t.quantity}` : t.quantity < 0 ? `${t.quantity}` : "—"}
                        <span style={{ fontSize: 9, marginLeft: 3, opacity: 0.7 }}>
                          {t.quantity > 0 ? "LONG" : t.quantity < 0 ? "SHORT" : ""}
                        </span>
                      </td>
                      <td className="mono" style={{ fontSize: 10 }}>{t.strategy?.replace(/_/g," ").toUpperCase() || "—"}</td>
                      <td><Badge text={t.status?.toUpperCase() || "OPEN"}
                        color={t.status === "closed" ? "var(--green)" : "var(--cyan)"} /></td>
                      <td className="mono">${(t.credit_received || t.short_strike || 0).toFixed(2)}</td>
                      <td className="mono" style={{ color: t.status === "open" ? "var(--ink-dim)" : pnl >= 0 ? "var(--green)" : "var(--red)", fontWeight: pnl !== 0 ? 600 : 400 }}>
                        {t.status === "open" ? "—" : pnl !== 0 ? `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}` : "$0.00"}
                      </td>
                      <td className="mono" style={{ color: "var(--green)" }}>{fmtDollars(t.mfe_pnl, t.status === "open" ? 0 : 2)}</td>
                      <td className="mono" style={{ color: "var(--red)" }}>{fmtDollars(t.mae_pnl, t.status === "open" ? 0 : 2)}</td>
                      <td className="mono" style={{ color: "var(--amber)" }}>{t.status === "closed" ? fmtCapture(t.pnl_capture_pct) : "—"}</td>
                      <td className="mono">{days != null ? `${days}d` : "—"}</td>
                      <td className="mono" style={{ color: "var(--ink-dim)", fontSize: 10 }}>
                        {t.exit_reason?.replace(/_/g," ").toUpperCase() || (t.status === "open" ? "OPEN" : "—")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* POSITIONS */}
        {tab === "positions" && (
          <div>
            {closeMsg && (
              // Sticky, not static: this sits above the table, so a result for
              // a row further down used to render off-screen. A rejected close
              // then looked identical to a button that did nothing — the hold
              // animation completes either way.
              <div
                role={closeFailed ? "alert" : "status"}
                style={{
                  position: "sticky", top: 0, zIndex: 2,
                  padding: "8px 16px", fontFamily: "var(--mono)", fontSize: 11,
                  color: closeFailed ? "var(--red)" : "var(--green)",
                  background: "var(--bg)",
                  borderBottom: `1px solid ${closeFailed ? "var(--red)" : "var(--line-dim)"}`,
                }}
              >
                {closeMsg}
              </div>
            )}
          {/*
            t-table--cards: on a phone this eleven-column table gets about
            35px per column at 390px, which is unreadable — the reason the
            positions list could not be used on mobile at all.

            The reflow is done in CSS (index.css, the 760px block) rather than
            by rendering a different component. Every cell keeps its place in
            the DOM and every piece of close-position logic — canClose,
            canCloseUntracked, the hold-to-confirm buttons, the "not at
            broker" fallbacks — is untouched, so a presentation change cannot
            alter which positions can be closed or how.
          */}
          <table className="t-table t-table--cards">
            <thead><tr>
              {["Symbol","Type","Strategy","Entry Credit","Unreal P&L","MFE","MAE","Hold Days","Status","Mode","Action"].map(h => (
                <HintedTh key={h} label={h} />
              ))}
            </tr></thead>
            <tbody>
              {(positions || []).length === 0 ? (
                <tr><td colSpan={11} style={{ textAlign: "center", padding: 40, color: "var(--ink-dim)", fontSize: 12 }}>
                  No open positions
                </td></tr>
              ) : (positions || []).map((p: any, i: number) => {
                // unrealized_pnl requires a live broker mark (paper_trade.py
                // only sets it when the IBKR position was matched to a
                // current price) — absent whenever the broker is
                // disconnected or the position is DB-only. `|| 0` here used
                // to silently show a fabricated "+$0" instead of the real
                // "unavailable" state MFE/MAE already show correctly below.
                const pnl: number | null | undefined = p.unrealized_pnl;
                // Both halves live in utils/closeablePosition.ts, which is
                // pinned to close_position()'s allowlist by a test. This gate
                // was equity-only and had not moved when the backend gained
                // options support, so every options row rendered the "close
                // via broker" placeholder and offered no button at all — which
                // an operator reads as a button that does nothing.
                const canClose = canClosePosition(p);
                // A broker position with no matching DB Trade row (e.g. an
                // order-placement timeout that filled after the app already
                // gave up on it) — asset_type now comes from the broker's
                // own contract type for these, not a DB field, so it's
                // reliable even with no DB row to read.
                const canCloseUntracked = p.tracked === false && !p.id && p.asset_type === "equity";
                return (
                  <tr key={i}>
                    <td data-col="symbol" className="mono" style={{ color: "var(--ink)" }}>{p.symbol || p.underlying || "—"}</td>
                    <td data-col="type"><Badge text={p.asset_type?.toUpperCase() || "OPTIONS"} color="var(--ink-dim)" /></td>
                    <td data-col="strategy" className="mono" style={{ fontSize: 10 }}>{p.strategy?.replace(/_/g," ").toUpperCase() || "—"}</td>
                    <td data-col="credit" data-label="Entry" className="mono">${(p.credit_received ?? p.entry_credit ?? p.avg_cost ?? 0).toFixed(2)}</td>
                    <td data-col="pnl" className="mono" style={{ color: pnl == null ? "var(--ink-faint)" : pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                      {fmtDollars(pnl)}
                    </td>
                    <td data-col="mfe" data-label="MFE" className="mono" style={{ color: "var(--green)" }}>{fmtDollars(p.mfe_pnl)}</td>
                    <td data-col="mae" data-label="MAE" className="mono" style={{ color: "var(--red)" }}>{fmtDollars(p.mae_pnl)}</td>
                    <td data-col="hold" data-label="Held" className="mono">{p.hold_days != null ? `${p.hold_days}d` : "—"}</td>
                    <td data-col="status"><Badge text="OPEN" color="var(--ink-dim)" /></td>
                    <td data-col="mode"><span className={`mode-badge ${p.trading_mode || "balanced"}`}>{p.trading_mode || "balanced"}</span></td>
                    <td data-col="action" style={{ minWidth: 90 }}>
                      {canClose ? (
                        <HoldToConfirmButton
                          label="Hold to close"
                          confirmingLabel="Closing"
                          holdMs={1200}
                          disabled={closingId === p.id}
                          onConfirm={() => closePosition(p.id, p.symbol || p.underlying || "?")}
                        />
                      ) : canCloseUntracked ? (
                        <HoldToConfirmButton
                          label="Hold to close"
                          confirmingLabel="Closing"
                          holdMs={1200}
                          disabled={closingId === (p.symbol || p.underlying)}
                          onConfirm={() => closeUntrackedPosition(p.symbol || p.underlying)}
                        />
                      ) : (
                        <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink-faint)" }}>
                          {isDbOnly(p)
                            ? "not at broker"
                            : isCloseableType(p) ? "no trade id" : "close via broker"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}

        {/* APPROVALS */}
        {tab === "approvals" && <ApprovalsQueue />}

        {/* P&L BREAKDOWN */}
        {tab === "pnl" && <PnLBreakdown />}

        {/* RISK PROFILE */}
        {tab === "mode" && (
          <div style={{ padding: 20, maxWidth: 720 }}>
            <div className="panel-title" style={{ marginBottom: 16 }}>Market Regime & Trading Style</div>
            <div className="instrument-card" style={{
              fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)",
              padding: "8px 12px", marginBottom: 20,
              lineHeight: 1.7,
            }}>
              Risk Profile controls position sizing, strategy selection, and risk budget.<br />
              Execution Mode (bar above) controls whether trades need your approval.<br />
              In AUTOPILOT, both must be set — the system will trade within your guardrail limits.
            </div>
            <TradingModeSelector />
          </div>
        )}

        {/* MANUAL TRADE */}
        {tab === "manual" && <ManualTradePanel />}
      </div>
    </div>
  );
}
