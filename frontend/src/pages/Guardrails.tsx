import React, { useState, useEffect } from "react";
import { useRisk } from "../hooks/useRisk";
import { api } from "../api/client";
import { Panel, Button } from "../components/ui";
import { tint } from "../utils/tint";

// daily_loss_pct/weekly_loss_pct from the API are signed P&L ratios (positive
// on a gain day, negative on a loss day) — not always a loss despite the field
// name. Format with an explicit sign and color so a gain doesn't read as red.
function pnlPct(value: number | null | undefined): { text: string; color: string } {
  const v = (value || 0) * 100;
  const sign = v >= 0 ? "+" : "";
  return { text: `${sign}${v.toFixed(2)}%`, color: v >= 0 ? "var(--green)" : "var(--red)" };
}

interface GuardrailEvent {
  id: string;
  timestamp: string | null;
  event_type: string;
  trigger_value: number | null;
  limit_value: number | null;
  trading_suspended_until: string | null;
  portfolio_value: number | null;
  notes: string | null;
}

// Matches the exact flag strings GuardrailEngine.check_all() appends to
// GuardrailStatus.flags (backend/app/services/guardrails.py), plus the two
// kill-switch event types kill_switch.py already writes into this same table.
const EVENT_TYPE_LABEL: Record<string, string> = {
  normal: "Recovered — normal",
  daily_loss_limit: "Daily loss limit",
  weekly_loss_limit: "Weekly loss limit",
  monthly_loss_limit: "Monthly loss limit",
  max_drawdown_limit: "Max drawdown",
  consecutive_loss_limit: "Consecutive losses",
  cooling_off_active: "Cooling off",
  capital_preservation_mode: "Capital preservation",
  daily_trade_cap: "Daily trade cap",
  kill_switch: "Kill switch engaged",
  kill_switch_reset: "Kill switch reset",
};

// Loss/capital events are fractional ratios (0.023 = 2.3%); trade-count
// events (daily_trade_cap, consecutive_loss_limit) are plain integers.
const COUNT_EVENT_TYPES = ["daily_trade_cap", "consecutive_loss_limit"];

function formatMetric(value: number | null, eventType: string): string {
  if (value == null) return "—";
  return COUNT_EVENT_TYPES.includes(eventType) ? String(value) : `${(value * 100).toFixed(2)}%`;
}

export default function Guardrails() {
  const { guardrailStatus, reconciliation, refresh } = useRisk();
  const [tab, setTab] = useState<"status"|"history">("status");
  const [activeMode, setActiveMode] = useState<any>(null);
  const [reconRunning, setReconRunning] = useState(false);
  const [reconMessage, setReconMessage] = useState<string | null>(null);
  const [history, setHistory] = useState<GuardrailEvent[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  useEffect(() => {
    if (tab !== "history" || history !== null) return;
    (api.getGuardrailHistory() as Promise<{ events: GuardrailEvent[] }>)
      .then(d => setHistory(d.events || []))
      .catch(e => setHistoryError(e?.message || "Failed to load guardrail history"));
  }, [tab, history]);

  // Poll the active trading mode independently — it lives in a different endpoint
  useEffect(() => {
    const load = () =>
      (api.getCurrentMode() as any)
        .then((d: any) => setActiveMode(d))
        .catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const modeName   = activeMode?.mode            ?? "—";
  const modeDte    = activeMode?.dte_range       ?? "—";
  const modeSignal = activeMode?.signal_threshold != null
    ? activeMode.signal_threshold.toFixed(2)
    : guardrailStatus?.signal_threshold?.toFixed(2) ?? "0.65";

  const activeLimits = {
    dailyLoss: guardrailStatus?.max_daily_loss_pct ?? 0.02,
    weeklyLoss: guardrailStatus?.max_weekly_loss_pct ?? 0.05,
    monthlyLoss: guardrailStatus?.max_monthly_loss_pct ?? 0.10,
    drawdown: guardrailStatus?.max_drawdown_pct ?? 0.15,
    tradesPerDay: guardrailStatus?.max_trades_per_day ?? 3,
    consecutiveLosses: guardrailStatus?.max_consecutive_losses ?? 3,
    capitalThreshold: guardrailStatus?.capital_preservation_threshold ?? 0.85,
  };

  // Color the mode badge
  const modeColor: Record<string, string> = {
    conservative: "var(--green)",
    balanced:     "var(--accent)",
    aggressive:   "var(--amber)",
    scalper:      "var(--red)",
  };
  const badgeColor = modeColor[modeName] ?? "var(--accent)";
  const reconColor =
    reconciliation?.status === "clean" ? "var(--green)"
    : reconciliation?.status === "warning" ? "var(--amber)"
    : reconciliation?.status === "error" ? "var(--red)"
    : "var(--ink-dim)";
  const reconLabel = reconciliation?.status?.toUpperCase() || "NO SNAPSHOT";

  const runReconciliation = async () => {
    setReconRunning(true);
    setReconMessage("Running reconciliation...");
    try {
      const res: any = await api.runReconciliation();
      setReconMessage(
        res.status === "error"
          ? (res.error || "Reconciliation failed")
          : res.status === "warning"
            ? "Reconciliation completed with warnings."
            : "Reconciliation clean."
      );
      await refresh();
    } catch (err: any) {
      setReconMessage(err.message || "Could not run reconciliation");
    } finally {
      setReconRunning(false);
    }
  };

  const rules = [
    {
      label: "Max Daily Loss",
      val: `${(activeLimits.dailyLoss * 100).toFixed(1)}%`,
      status: Math.abs(guardrailStatus?.daily_loss_pct || 0) < activeLimits.dailyLoss,
    },
    {
      label: "Max Weekly Loss",
      val: `${(activeLimits.weeklyLoss * 100).toFixed(1)}%`,
      status: Math.abs(guardrailStatus?.weekly_loss_pct || 0) < activeLimits.weeklyLoss,
    },
    {
      label: "Max Monthly Loss",
      val: `${(activeLimits.monthlyLoss * 100).toFixed(1)}%`,
      status: Math.abs(guardrailStatus?.monthly_loss_pct || 0) < activeLimits.monthlyLoss,
    },
    {
      label: "Max Drawdown",
      val: `${(activeLimits.drawdown * 100).toFixed(1)}%`,
      status: (guardrailStatus?.drawdown_pct || 0) < activeLimits.drawdown,
    },
    {
      label: "Max Trades Per Day",
      val: String(activeLimits.tradesPerDay),
      status: (guardrailStatus?.trades_today || 0) < activeLimits.tradesPerDay,
    },
    {
      label: "Consecutive Losses",
      val: String(activeLimits.consecutiveLosses),
      status: (guardrailStatus?.consecutive_losses || 0) < activeLimits.consecutiveLosses,
    },
    {
      label: "Capital Threshold",
      val: `${(activeLimits.capitalThreshold * 100).toFixed(0)}%`,
      status: (guardrailStatus?.capital_pct_remaining ?? 1) > activeLimits.capitalThreshold,
    },
    { label: "Kill Switch",         val: "OFF",   status: !guardrailStatus?.kill_switch_engaged },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--line-dim)", background: "var(--bg-2)", display: "flex", gap: 1 }}>
        {(["status","history"] as const).map(t => (
          <Button key={t} active={tab === t} onClick={() => setTab(t)}>
            {t.toUpperCase()}
          </Button>
        ))}
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
        {tab === "status" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

            {/* Active trading style banner */}
            <div className="instrument-card" style={{
              border: `1px solid ${badgeColor}`,
              padding: "14px 20px",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div>
                <div className="kicker" style={{ marginBottom: 4 }}>Active Trading Style</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 20, fontWeight: 700, color: badgeColor, textTransform: "uppercase" }}>
                  {modeName}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div className="kicker" style={{ marginBottom: 4 }}>DTE Range</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 14, color: "var(--ink)" }}>{modeDte}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div className="kicker" style={{ marginBottom: 4 }}>Signal Threshold</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 14, color: "var(--ink)" }}>{modeSignal}</div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div className="kicker" style={{ marginBottom: 4 }}>Risk State</div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 14,
                  color: guardrailStatus?.trading_mode === "suspended" ? "var(--red)"
                       : guardrailStatus?.trading_mode === "capital_preservation" ? "var(--amber)"
                       : "var(--green)" }}>
                  {(guardrailStatus?.trading_mode || "normal").toUpperCase()}
                </div>
              </div>
            </div>

            {guardrailStatus?.paper_visibility_mode && (
              <div style={{
                border: "1px solid rgba(249,115,22,0.42)",
                background: "rgba(249,115,22,0.08)",
                padding: "12px 16px",
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--amber)",
                lineHeight: 1.7,
              }}>
                PAPER VISIBILITY MODE ACTIVE
                <div style={{ color: "var(--ink-dim)", marginTop: 4 }}>
                  Paper-only rules are relaxed so the app can generate more signals, executions, and trade history for testing. Live guardrails remain unchanged.
                </div>
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              {/* Rule status */}
              <Panel padding={0} title="Active Rules">
                {rules.map((r, i) => (
                  <div key={r.label} style={{
                    padding: "8px 14px",
                    borderBottom: i < rules.length - 1 ? "1px solid var(--line-dim)" : "none",
                    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
                  }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
                      <span style={{ fontSize: 13, color: "var(--ink)" }}>{r.label}</span>
                      <span className="tnum" style={{ fontSize: 11, color: "var(--ink-dim)" }}>{r.val}</span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, flexShrink: 0 }}>
                      <span className={`dot ${r.status ? "live" : "dead"}`} />
                      <span style={{ fontSize: 11, color: r.status ? "var(--green)" : "var(--red)" }}>
                        {r.status ? "OK" : "Breach"}
                      </span>
                    </div>
                  </div>
                ))}
              </Panel>

              {/* State snapshot */}
              <Panel padding={12} title="Current State">
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 12px" }}>
                  {[
                    { label: "Trades Today",       val: String(guardrailStatus?.trades_today || 0) },
                    { label: "Consecutive Losses", val: String(guardrailStatus?.consecutive_losses || 0) },
                    { label: "Cooling Off Until",  val: guardrailStatus?.cooling_off_until || "—" },
                    { label: "Capital Remaining",  val: `${((guardrailStatus?.capital_pct_remaining || 1) * 100).toFixed(1)}%` },
                    // Signed P&L %, not always a loss — label and color follow the
                    // actual sign instead of always reading "Loss" on a gain day.
                    { label: "Daily P&L",  val: pnlPct(guardrailStatus?.daily_loss_pct) },
                    { label: "Weekly P&L", val: pnlPct(guardrailStatus?.weekly_loss_pct) },
                  ].map(s => (
                    <div key={s.label} className="instrument-card--flat" style={{
                      padding: "7px 10px",
                      display: "flex", flexDirection: "column", gap: 3,
                    }}>
                      <span className="kicker">{s.label}</span>
                      <span className="tnum" style={{
                        fontSize: 14, fontWeight: 600,
                        color: typeof s.val === "object" ? s.val.color : "var(--ink)",
                      }}>
                        {typeof s.val === "object" ? s.val.text : s.val}
                      </span>
                    </div>
                  ))}
                </div>
              </Panel>
            </div>

            <Panel
              padding={16}
              title="Reconciliation Status"
              action={<Button onClick={runReconciliation} disabled={reconRunning}>{reconRunning ? "Running..." : "Run Check"}</Button>}
            >
              <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 16 }}>
                <div style={{
                  border: `1px solid ${tint(reconColor, 0.333)}`,
                  background: "var(--bg-1)",
                  padding: 14,
                }}>
                  <div className="kicker" style={{ marginBottom: 6 }}>Latest Result</div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 18, color: reconColor, marginBottom: 10 }}>
                    {reconLabel}
                  </div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", lineHeight: 1.7 }}>
                    <div>BROKER {reconciliation?.broker_name?.toUpperCase() || "—"}</div>
                    <div>CHECKED {reconciliation?.checked_at ? new Date(reconciliation.checked_at).toLocaleString("en-US") : "—"}</div>
                    <div>SOURCE {reconciliation?.source?.replace(/_/g, " ").toUpperCase() || "—"}</div>
                  </div>
                </div>

                <div style={{ display: "grid", gap: 12 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
                    {[
                      { label: "Broker Positions", value: reconciliation?.broker_position_count ?? "—", color: "var(--ink)" },
                      { label: "DB Open Trades", value: reconciliation?.db_open_trade_count ?? "—", color: "var(--ink)" },
                      { label: "Untracked", value: reconciliation?.untracked_at_broker?.length ?? 0, color: "var(--red)" },
                      { label: "Quantity Drift", value: reconciliation?.quantity_mismatches?.length ?? 0, color: "var(--amber)" },
                    ].map((item) => (
                      <div key={item.label} className="instrument-card" style={{ padding: 12 }}>
                        <div className="kicker" style={{ marginBottom: 6 }}>{item.label}</div>
                        <div className="tnum" style={{ fontSize: 16, color: item.color }}>{item.value}</div>
                      </div>
                    ))}
                  </div>

                  {reconMessage && (
                    <div style={{ fontSize: 11, color: "var(--ink-dim)" }}>
                      {reconMessage}
                    </div>
                  )}

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    {[
                      { title: "Warnings", items: reconciliation?.warnings || [], empty: "No warnings recorded." },
                      { title: "Drift Details", items: [
                        ...(reconciliation?.untracked_at_broker || []).map((s: string) => `Untracked broker position: ${s}`),
                        ...(reconciliation?.phantom_in_db || []).map((s: string) => `Open DB trade without broker position: ${s}`),
                        ...(reconciliation?.quantity_mismatches || []),
                        ...(reconciliation?.error_message ? [reconciliation.error_message] : []),
                      ], empty: "No drift detected." },
                    ].map((group) => (
                      <div key={group.title} className="instrument-card" style={{ padding: 12 }}>
                        <div className="kicker" style={{ marginBottom: 8 }}>{group.title}</div>
                        {group.items.length === 0 ? (
                          <div style={{ color: "var(--ink-faint)", fontSize: 12 }}>{group.empty}</div>
                        ) : (
                          <div style={{ display: "grid", gap: 8 }}>
                            {group.items.map((item: string, index: number) => (
                              <div key={`${group.title}-${index}`} style={{ fontSize: 12, color: "var(--ink-dim)", lineHeight: 1.6 }}>
                                {item}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </Panel>
          </div>
        )}

        {tab === "history" && (
          <Panel padding={0} title="Guardrail Event Log">
            <table className="t-table">
              <thead><tr>
                {["Timestamp","Event","Rule","Value","Action"].map(h => <th key={h}>{h}</th>)}
              </tr></thead>
              <tbody>
                {historyError ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", padding: 40, color: "var(--red)", fontFamily: "var(--mono)", fontSize: 11 }}>
                      {historyError}
                    </td>
                  </tr>
                ) : history === null ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", padding: 40, color: "var(--ink-faint)", fontFamily: "var(--mono)", fontSize: 11 }}>
                      LOADING…
                    </td>
                  </tr>
                ) : history.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: "center", padding: 40, color: "var(--ink-faint)", fontFamily: "var(--mono)", fontSize: 11 }}>
                      NO GUARDRAIL EVENTS RECORDED YET
                    </td>
                  </tr>
                ) : history.map(e => (
                  <tr key={e.id}>
                    <td className="mono" style={{ color: "var(--ink-dim)" }}>
                      {e.timestamp ? new Date(e.timestamp).toLocaleString() : "—"}
                    </td>
                    <td className="mono" style={{
                      color: e.event_type === "normal" || e.event_type === "kill_switch_reset" ? "var(--green)" : "var(--red)",
                    }}>
                      {EVENT_TYPE_LABEL[e.event_type] || e.event_type}
                    </td>
                    <td className="mono" style={{ color: "var(--ink-dim)" }}>
                      {formatMetric(e.limit_value, e.event_type)}
                    </td>
                    <td className="mono">{formatMetric(e.trigger_value, e.event_type)}</td>
                    <td className="mono" style={{ color: "var(--ink-dim)" }}>
                      {e.notes ||
                        (e.trading_suspended_until
                          ? `Suspended until ${new Date(e.trading_suspended_until).toLocaleString()}`
                          : "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
      </div>
    </div>
  );
}
