/**
 * Execution Monitor — timeline over execution_events (kind=execution).
 * Visibility only; no submit path changes.
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

export default function ExecutionMonitor() {
  const [log, setLog] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");

  const refresh = useCallback(() => {
    setLoading(true);
    (api.getExecutionLog() as any)
      .catch(() => ({ log: [], total: 0 }))
      .then((d: any) => {
        setLog(d.log || []);
        setTotal(d.total ?? (d.log || []).length);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
    return () => clearInterval(id);
  }, [refresh]);

  const enriched = useMemo(
    () =>
      log.map((e: any, i: number) => ({
        key: e.signal_id || String(i),
        life: lifecycleFromExecution(e),
        entry: e,
      })),
    [log],
  );

  const filtered = useMemo(
    () => (filter === "all" ? enriched : enriched.filter((r) => r.life === filter)),
    [enriched, filter],
  );

  const stats = useMemo(() => {
    const s = { submitted: 0, blocked: 0, skipped: 0, rejected: 0, error: 0 };
    for (const r of enriched) {
      if (r.life in s) (s as any)[r.life] += 1;
    }
    return s;
  }, [enriched]);

  const filters: { key: Filter; label: string }[] = [
    { key: "all", label: "ALL" },
    { key: "submitted", label: "SUBMITTED" },
    { key: "blocked", label: "BLOCKED" },
    { key: "skipped", label: "SKIPPED" },
    { key: "rejected", label: "REJECTED" },
    { key: "error", label: "ERROR" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="desk-tool-rail" style={{ padding: "10px 16px", flexDirection: "column", alignItems: "stretch", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span className="panel-title">Execution Monitor</span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>
            {total} events persisted · showing last {log.length}
          </span>
          <div style={{ flex: 1 }} />
          <button type="button" className="btn-ghost" onClick={refresh}>
            Refresh
          </button>
        </div>
        <div className="instrument-stat-strip" style={{ gridTemplateColumns: "repeat(5, minmax(0, 1fr))" }}>
          {(
            [
              ["Submitted", stats.submitted, "var(--green)"],
              ["Blocked", stats.blocked, "var(--red)"],
              ["Skipped", stats.skipped, "var(--amber)"],
              ["Rejected", stats.rejected, "var(--ink-dim)"],
              ["Error", stats.error, "var(--red)"],
            ] as const
          ).map(([label, n, color]) => (
            <div key={label} style={{ padding: "10px 14px" }}>
              <div className="kicker" style={{ marginBottom: 4 }}>
                {label}
              </div>
              <div className="data-val sm" style={{ color }}>
                {n}
              </div>
            </div>
          ))}
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
            <p className="empty-chassis__title">No execution events</p>
            <p className="empty-chassis__hint">Submitted and blocked trades will show up here.</p>
          </div>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {filtered.map(({ key, life, entry: e }) => {
              const ts = e.executed_at || e.rejected_at;
              return (
                <li
                  key={key}
                  className="instrument-card"
                  style={{
                    display: "grid",
                    gridTemplateColumns: "16px 140px 1fr auto",
                    gap: 12,
                    alignItems: "start",
                    padding: "10px 14px",
                    marginBottom: 6,
                  }}
                >
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      marginTop: 5,
                      borderRadius: 0,
                      background: lifecycleColor(life),
                      justifySelf: "center",
                    }}
                  />
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                    {ts ? new Date(ts).toLocaleString() : "—"}
                  </div>
                  <div>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
                        {e.ticker || "—"}
                      </span>
                      {(e.action || e.strategy) && (
                        <SignalDirectionBadge action={e.action || e.strategy} size="sm" />
                      )}
                      <Badge kind="tag" tone={lifecycleColor(life)}>{lifecycleLabel(life)}</Badge>
                      <Badge kind="tag" tone="var(--ink-dim)">{(e.asset_type || "eq").toUpperCase()}</Badge>
                    </div>
                    {e.reason && (
                      <div
                        style={{
                          marginTop: 4,
                          fontFamily: "var(--mono)",
                          fontSize: 10,
                          color: "var(--ink-faint)",
                        }}
                      >
                        {e.reason}
                      </div>
                    )}
                  </div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--amber)", textAlign: "right" }}>
                    {e.executed_by || e.approved_by || e.rejected_by || "—"}
                    {typeof e.confidence === "number" && (
                      <div style={{ color: "var(--ink-faint)" }}>
                        score {(e.confidence * 100).toFixed(0)}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
