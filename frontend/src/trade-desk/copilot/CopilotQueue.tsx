/**
 * Copilot Queue v2 — pending approvals + recent audit.
 * Approve/reject use existing trade-desk APIs → _execute_signal on approve.
 * No TradeIntent modify / re-eval in this thin phase.
 */

import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import SignalAttribution from "../../components/SignalAttribution";
import SignalDirectionBadge from "../../components/SignalDirectionBadge";
import type { SignalAttributionData } from "../../types/signal";
import MissionCard from "../../components/MissionCard";
import { Badge } from "../../components/ui";
import {
  lifecycleColor,
  lifecycleFromExecution,
  lifecycleLabel,
} from "../executionStatus";

function signalAge(queuedAt: string | null | undefined): string {
  if (!queuedAt) return "";
  const deltaMs = Date.now() - Date.parse(queuedAt);
  if (Number.isNaN(deltaMs) || deltaMs < 0) return "";
  const s = Math.floor(deltaMs / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function CopilotQueue() {
  const [pending, setPending] = useState<any[]>([]);
  const [log, setLog] = useState<any[]>([]);
  const [mode, setMode] = useState<string>("—");
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState<string | null>(null);
  const [rejectingAll, setRejectingAll] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // `showSkeleton` is only ever true for the very first load. A background
  // poll must not blank the list: setting loading on every 10s tick tore down
  // all 174 rendered rows and replaced them with skeletons until the refetch
  // landed, so the queue spent a visible slice of every cycle looking empty —
  // and with this many cards the re-render made that window long enough to
  // look permanent. Later fetches update in place instead.
  const refresh = useCallback((showSkeleton = false) => {
    if (showSkeleton) setLoading(true);
    Promise.all([
      (api.getPendingApprovals() as any).catch(() => ({ pending: [], mode: "—" })),
      (api.getExecutionLog() as any).catch(() => ({ log: [] })),
      (api.getExecutionMode() as any).catch(() => ({ mode: "—" })),
    ])
      .then(([p, l, m]) => {
        setPending((p as any).pending || []);
        setLog((l as any).log || []);
        setMode(
          String((p as any).mode || (m as any).mode || "—").toUpperCase(),
        );
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh(true);                                    // first paint: skeletons are honest
    const id = setInterval(() => refresh(), 10000);   // polls: update in place
    return () => clearInterval(id);
  }, [refresh]);

  const act = async (id: string, action: "approve" | "reject") => {
    setActing(id);
    setError(null);
    try {
      if (action === "approve") await (api.approveSignal(id) as any);
      else await (api.rejectSignal(id) as any);
      refresh();
    } catch (e: any) {
      setError(e?.message || `${action} failed`);
    } finally {
      setActing(null);
    }
  };

  const rejectAll = async () => {
    setConfirmClear(false);
    setRejectingAll(true);
    setError(null);
    try {
      await (api.rejectAllPending() as any);
      refresh();
    } catch (e: any) {
      setError(e?.message || "Bulk reject failed");
    } finally {
      setRejectingAll(false);
    }
  };

  const recent = log.slice(0, 40);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="desk-tool-rail" style={{ padding: "10px 16px", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <span className="panel-title">Copilot Queue</span>
        <Badge kind="tag" tone="var(--amber)">{`MODE ${mode}`}</Badge>
        {pending.length > 0 && (
          <Badge kind="tag" tone="var(--orange)">{`${pending.length} PENDING`}</Badge>
        )}
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", flex: 1 }}>
          Approve routes through existing OMS. Size edits require a new signal (no modify → re-eval yet).
        </span>
        {pending.length > 1 && !confirmClear && (
          <button
            type="button"
            className="btn-ghost"
            style={{ padding: "4px 10px", fontSize: 10, color: "var(--red)", borderColor: "rgba(239,68,68,0.4)" }}
            onClick={() => setConfirmClear(true)}
          >
            Reject All
          </button>
        )}
        {confirmClear && (
          <>
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--red)" }}>
              Reject all {pending.length} pending?
            </span>
            <button
              type="button"
              className="btn-ghost"
              disabled={rejectingAll}
              style={{ padding: "4px 10px", fontSize: 10, color: "var(--red)", borderColor: "rgba(239,68,68,0.5)" }}
              onClick={rejectAll}
            >
              {rejectingAll ? "Rejecting…" : "Confirm"}
            </button>
            <button
              type="button"
              className="btn-ghost"
              style={{ padding: "4px 10px", fontSize: 10 }}
              onClick={() => setConfirmClear(false)}
            >
              Cancel
            </button>
          </>
        )}
        {/* Manual refresh does show the skeleton: the operator asked for it
            and wants to see that something happened. Only the silent 10s poll
            leaves the rows alone. Passing `refresh` directly here would hand
            the MouseEvent in as `showSkeleton` — truthy by accident rather
            than by choice. */}
        <button type="button" className="btn-ghost" style={{ padding: "4px 10px", fontSize: 10 }}
                onClick={() => refresh(true)}>
          Refresh
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: "8px 16px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            color: "var(--red)",
            borderBottom: "1px solid var(--line-dim)",
          }}
        >
          {error}
        </div>
      )}

      {loading ? (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            fontFamily: "var(--mono)",
            color: "var(--ink-faint)",
            fontSize: 11,
          }}
        >
          Loading…
        </div>
      ) : pending.length === 0 ? (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            fontFamily: "var(--mono)",
            color: "var(--ink-faint)",
            fontSize: 11,
          }}
        >
          No pending approvals — queue is clear
          <div style={{ marginTop: 8, fontSize: 10 }}>
            Switch to Copilot mode to route new signals here for approval.
          </div>
        </div>
      ) : (
        <div className="mission-list" style={{ overflowY: "auto", flex: "0 1 auto", maxHeight: "55%", padding: "6px 8px" }}>
          {pending.map((s: any) => {
            // pop and expected_value may live at the top level (equity signals,
            // scan-panel submissions) OR nested under intelligence (options
            // scanner signals that went through SpreadIntelligence.as_dict()).
            // Read top-level first; fall back to the nested path.
            const pop = s.pop ?? s.intelligence?.pop ?? null;
            const ev = s.expected_value ?? s.intelligence?.expected_value ?? null;
            const confidence = typeof s.confidence === "number" ? s.confidence : null;
            const progressVal = pop != null ? pop * 100 : confidence != null ? confidence * 100 : null;
            const progressTone = pop != null
              ? (pop >= 0.7 ? "var(--green)" : "var(--amber)")
              : confidence != null && confidence >= 0.7
                ? "var(--green)"
                : "var(--amber)";

            const reward = s.spread?.net_credit != null
              ? { prefix: "$", value: s.spread.net_credit.toFixed(2), tone: "var(--green)" }
              : ev != null
                ? { prefix: "EV", value: `$${ev.toFixed(0)}`, tone: ev >= 0 ? "var(--green)" : "var(--red)" }
                : confidence != null
                  ? { prefix: "CONF", value: `${Math.round(confidence * 100)}%`, tone: progressTone }
                  : undefined;

            const age = signalAge(s.queued_at);
            const subtitle = s.spread
              ? `${s.spread.option_type?.toUpperCase()} ${s.spread.short_strike}/${s.spread.long_strike} · exp ${s.spread.expiration} · max loss $${s.spread.max_loss?.toFixed(2) ?? "—"}${age ? ` · ${age}` : ""}`
              : `Equity · queued ${s.queued_at ? new Date(s.queued_at).toLocaleString() : "—"}${age ? ` · ${age}` : ""}`;

            return (
              <MissionCard
                key={s.id}
                reward={reward}
                title={(
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span className="mono" style={{ fontWeight: 700 }}>{s.ticker}</span>
                    <Badge kind="tag" tone="var(--ink-dim)">{s.asset_type?.toUpperCase() || "EQUITY"}</Badge>
                    <SignalDirectionBadge action={s.action || s.strategy?.toUpperCase()} size="sm" />
                    <SignalAttribution
                      data={
                        {
                          direction: s.action || s.strategy?.toUpperCase() || "BUY",
                          source: s.source ?? "unknown",
                          timeframe: null,
                          confidence,
                          updatedAt: s.queued_at ?? null,
                          authority: "advisory",
                        } as SignalAttributionData
                      }
                      size="sm"
                    />
                  </span>
                )}
                subtitle={subtitle}
                meta={{
                  label: s.regime?.toUpperCase() || (pop != null ? `${Math.round(pop * 100)}% POP` : "PENDING"),
                  tone: s.regime ? "var(--amber)" : pop != null && pop >= 0.7 ? "var(--green)" : "var(--ink-dim)",
                  icon: "⏳",
                }}
                progress={progressVal != null ? {
                  value: progressVal,
                  tone: progressTone,
                  label: `${s.ticker} approval readiness`,
                } : undefined}
                actions={(
                  <>
                    <button
                      type="button"
                      className="btn-t"
                      disabled={acting === s.id}
                      onClick={() => act(s.id, "approve")}
                      style={{ color: "var(--green)", borderColor: "rgba(34,197,94,0.5)", fontSize: 11 }}
                    >
                      APPROVE
                    </button>
                    <button
                      type="button"
                      className="btn-t danger"
                      disabled={acting === s.id}
                      onClick={() => act(s.id, "reject")}
                      style={{ fontSize: 11 }}
                    >
                      REJECT
                    </button>
                  </>
                )}
              />
            );
          })}
        </div>
      )}

      <div
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid var(--line-dim)",
          borderTop: "1px solid var(--line-dim)",
          background: "var(--bg-3)",
        }}
      >
        <span className="panel-title">RECENT DECISIONS</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {recent.length === 0 ? (
          <div
            style={{
              padding: 32,
              textAlign: "center",
              fontFamily: "var(--mono)",
              color: "var(--ink-faint)",
              fontSize: 11,
            }}
          >
            NO AUDIT HISTORY
          </div>
        ) : (
          <table className="t-table">
            <thead>
              <tr>
                {["Time", "Ticker", "Type", "Action", "By", "Status", "Reason"].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recent.map((e: any, i: number) => {
                const life = lifecycleFromExecution(e);
                const ts = e.executed_at || e.rejected_at;
                return (
                  <tr key={e.signal_id || i}>
                    <td className="mono" style={{ fontSize: 10, color: "var(--ink-dim)" }}>
                      {ts ? new Date(ts).toLocaleString() : "—"}
                    </td>
                    <td className="mono" style={{ color: "var(--ink)" }}>
                      {e.ticker || "—"}
                    </td>
                    <td>
                      <Badge kind="tag" tone="var(--ink-dim)">{e.asset_type?.toUpperCase() || "EQ"}</Badge>
                    </td>
                    <td className="mono" style={{ fontSize: 10 }}>
                      {e.action || e.strategy || "—"}
                    </td>
                    <td className="mono" style={{ fontSize: 10, color: "var(--amber)" }}>
                      {e.executed_by || e.approved_by || e.rejected_by || "—"}
                    </td>
                    <td>
                      <Badge kind="tag" tone={lifecycleColor(life)}>{lifecycleLabel(life)}</Badge>
                    </td>
                    <td className="mono" style={{ fontSize: 10, color: "var(--ink-dim)", maxWidth: 220 }}>
                      {e.reason || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
