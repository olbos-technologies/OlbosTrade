/**
 * Orders workspace — thin lifecycle view from pending + execution log.
 * Not a full OMS order table (no broker ack/partial/cancel entity yet).
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import SignalDirectionBadge from "../../components/SignalDirectionBadge";
import { Badge } from "../../components/ui";
import {
  type DeskLifecycle,
  lifecycleColor,
  lifecycleFromExecution,
  lifecycleLabel,
} from "../executionStatus";

type Filter = "all" | DeskLifecycle;

interface OrderRow {
  id: string;
  time: string | null;
  ticker: string;
  asset: string;
  action: string;
  lifecycle: DeskLifecycle;
  by: string;
  reason: string;
  source: "queue" | "execution";
}

export default function OrdersWorkspace() {
  const [rows, setRows] = useState<OrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");

  const refresh = useCallback(() => {
    setLoading(true);
    Promise.all([
      (api.getPendingApprovals() as any).catch(() => ({ pending: [] })),
      (api.getExecutionLog() as any).catch(() => ({ log: [] })),
    ])
      .then(([p, l]) => {
        const pending: OrderRow[] = ((p as any).pending || []).map((s: any) => ({
          id: `p-${s.id}`,
          time: s.queued_at || null,
          ticker: s.ticker || "—",
          asset: (s.asset_type || "equity").toUpperCase(),
          action: s.action || s.strategy || "—",
          lifecycle: "pending_approval" as DeskLifecycle,
          by: "copilot",
          reason: "awaiting operator approve/reject",
          source: "queue" as const,
        }));
        const exec: OrderRow[] = ((l as any).log || []).map((e: any, i: number) => ({
          id: `e-${e.signal_id || i}`,
          time: e.executed_at || e.rejected_at || null,
          ticker: e.ticker || "—",
          asset: (e.asset_type || "equity").toUpperCase(),
          action: e.action || e.strategy || "—",
          lifecycle: lifecycleFromExecution(e),
          by: e.executed_by || e.approved_by || e.rejected_by || "—",
          reason: e.reason || "—",
          source: "execution" as const,
        }));
        const merged = [...pending, ...exec].sort((a, b) => {
          const ta = a.time ? new Date(a.time).getTime() : 0;
          const tb = b.time ? new Date(b.time).getTime() : 0;
          return tb - ta;
        });
        setRows(merged);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 12000);
    return () => clearInterval(id);
  }, [refresh]);

  const filtered = useMemo(
    () => (filter === "all" ? rows : rows.filter((r) => r.lifecycle === filter)),
    [rows, filter],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows.length };
    for (const r of rows) c[r.lifecycle] = (c[r.lifecycle] || 0) + 1;
    return c;
  }, [rows]);

  const filters: { key: Filter; label: string }[] = [
    { key: "all", label: "ALL" },
    { key: "pending_approval", label: "PENDING" },
    { key: "submitted", label: "SUBMITTED" },
    { key: "blocked", label: "BLOCKED" },
    { key: "skipped", label: "SKIPPED" },
    { key: "rejected", label: "REJECTED" },
    { key: "error", label: "ERROR" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="desk-tool-rail" style={{ padding: "10px 16px", flexDirection: "column", alignItems: "stretch", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="panel-title">Orders</span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", flex: 1 }}>
            Derived from Copilot queue + execution log. Broker ack / partial / cancel not modeled yet.
          </span>
          <button type="button" className="btn-ghost" onClick={refresh}>
            Refresh
          </button>
        </div>
        <div className="asset-toggle" style={{ flexWrap: "wrap", alignSelf: "flex-start" }}>
          {filters.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`asset-toggle__btn${filter === f.key ? " asset-toggle__btn--active" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
              {counts[f.key] != null ? ` (${counts[f.key]})` : ""}
            </button>
          ))}
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "8px 16px" }}>
        {loading ? (
          <div className="instrument-card instrument-card--flat empty-chassis empty-chassis--compact">
            <p className="empty-chassis__title">Loading…</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="instrument-card instrument-card--flat empty-chassis empty-chassis--compact">
            <p className="empty-chassis__title">No orders in this filter</p>
            <p className="empty-chassis__hint">Approve a Copilot signal or wait for an execution event.</p>
          </div>
        ) : (
          <table className="t-table">
            <thead>
              <tr>
                {["Time", "Ticker", "Asset", "Action", "Lifecycle", "By", "Detail"].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id}>
                  <td className="mono" style={{ fontSize: 10, color: "var(--ink-dim)" }}>
                    {r.time ? new Date(r.time).toLocaleString() : "—"}
                  </td>
                  <td className="mono" style={{ color: "var(--ink)", fontWeight: 600 }}>
                    {r.ticker}
                  </td>
                  <td>
                    <Badge kind="tag" tone="var(--ink-dim)">{r.asset}</Badge>
                  </td>
                  <td>
                    {r.action && r.action !== "—" ? (
                      <SignalDirectionBadge action={r.action} size="sm" />
                    ) : (
                      <span className="mono" style={{ fontSize: 10, color: "var(--ink-faint)" }}>—</span>
                    )}
                  </td>
                  <td>
                    <Badge kind="tag" tone={lifecycleColor(r.lifecycle)}>{lifecycleLabel(r.lifecycle)}</Badge>
                  </td>
                  <td className="mono" style={{ fontSize: 10, color: "var(--amber)" }}>
                    {r.by}
                  </td>
                  <td className="mono" style={{ fontSize: 10, color: "var(--ink-dim)", maxWidth: 280 }}>
                    {r.reason}
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
