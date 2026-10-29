/**
 * Signal Research — forward-return study over every tracked equity signal,
 * not just the handful that got traded. Answers: of the signals this system
 * generates, how many actually move to target vs stop, and how many trading
 * days does that take. See backend/app/services/signal_outcome_tracker.py.
 */
import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Panel, StatTile } from "../components/ui";
import { tint } from "../utils/tint";

interface ConfidenceBucket { count: number; hit_rate: number | null }
interface TickerRow { ticker: string; total: number; hit_rate: number | null }
interface OutcomeStats {
  total: number; pending: number; target_hit: number; stop_hit: number; expired: number;
  hit_rate: number | null; avg_days_to_target: number | null; avg_days_to_stop: number | null;
  by_confidence_bucket: Record<string, ConfidenceBucket>;
  by_ticker: TickerRow[];
  message?: string;
}
interface RawOutcome {
  id: string; ticker: string; action: string; confidence: number; status: string;
  generated_at: string | null; resolved_at: string | null; days_to_resolve: number | null;
  entry_price: number; target_price: number; stop_price: number; exit_price: number | null;
  target_move_pct: number | null; max_favorable_pct: number | null; max_adverse_pct: number | null;
}

const STATUS_COLOR: Record<string, string> = {
  target_hit: "var(--green)",
  stop_hit:   "var(--red)",
  expired:    "var(--ink-faint)",
  pending:    "var(--amber)",
};
const STATUS_LABEL: Record<string, string> = {
  target_hit: "TARGET HIT",
  stop_hit:   "STOP HIT",
  expired:    "EXPIRED",
  pending:    "PENDING",
};
const pct = (n: number | null) => n == null ? "—" : (n * 100).toFixed(1) + "%";
const days = (n: number | null) => n == null ? "—" : `${n.toFixed(1)}d`;

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ flex: 1, minWidth: 130 }}>
      <StatTile variant="boxed" label={label} value={value} tone={color} />
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLOR[status] || "var(--ink-dim)";
  return (
    <span style={{
      fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600,
      padding: "2px 8px", color, background: tint(color, 0.082),
      border: `1px solid ${tint(color, 0.251)}`,
    }}>
      {STATUS_LABEL[status] || status.toUpperCase()}
    </span>
  );
}

function RawRow({ o }: { o: RawOutcome }) {
  const genDate = o.generated_at ? new Date(o.generated_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—";
  return (
    <tr>
      <td className="mono" style={{ fontWeight: 600 }}>{o.ticker}</td>
      <td className="mono" style={{ color: o.action === "BUY" ? "var(--green)" : "var(--red)" }}>{o.action}</td>
      <td className="mono">{(o.confidence * 100).toFixed(0)}%</td>
      <td><StatusPill status={o.status} /></td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>{genDate}</td>
      <td className="mono">{days(o.days_to_resolve)}</td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>${o.entry_price.toFixed(2)}</td>
      <td className="mono" style={{ color: "var(--green)" }}>${o.target_price.toFixed(2)}</td>
      <td className="mono" style={{ color: "var(--red)" }}>${o.stop_price.toFixed(2)}</td>
      <td className="mono">{o.max_favorable_pct != null ? pct(o.max_favorable_pct) : "—"}</td>
    </tr>
  );
}

export default function SignalResearch() {
  const [stats, setStats] = useState<OutcomeStats | null>(null);
  const [raw, setRaw] = useState<RawOutcome[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    Promise.all([
      (api as any).getSignalOutcomes(),
      (api as any).getSignalOutcomesRaw({ limit: 200, status: statusFilter || undefined }),
    ])
      .then(([s, r]: any) => { setStats(s); setRaw(r.outcomes || []); })
      .catch((e: any) => setError(e?.message || "Failed to load signal outcomes"))
      .finally(() => setLoading(false));
  };

  useEffect(load, [statusFilter]);

  if (loading && !stats) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%",
      fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
      LOADING SIGNAL OUTCOMES...
    </div>
  );

  if (error) return (
    <div style={{ padding: 16 }}>
      <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
        padding: "10px 14px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>
        {error}
      </div>
    </div>
  );

  if (!stats || stats.total === 0) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%",
      flexDirection: "column", gap: 12 }}>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--cyan)" }}>NO SIGNALS TRACKED YET</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", maxWidth: 420, textAlign: "center", lineHeight: 1.6 }}>
        {stats?.message || "Outcomes are recorded automatically as the equity scanner runs, and resolved every 6 hours against fresh daily bars."}
      </span>
    </div>
  );

  const resolvedCount = stats.target_hit + stats.stop_hit;

  return (
    <div style={{ overflowY: "auto", height: "100%", padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>

      {/* Header / explainer */}
      <div className="instrument-card" style={{ padding: "10px 14px", border: "1px solid color-mix(in srgb, var(--cyan) 19%, transparent)", borderLeft: "2px solid var(--cyan)" }}>
        <span className="panel-title" style={{ marginRight: 12 }}>SIGNAL RESEARCH</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink)" }}>
          Every routable equity signal is tracked from generation to real forward outcome — target hit, stop hit,
          or expired after ~20 trading days. {stats.total} tracked so far, {stats.pending} still pending.
        </span>
      </div>

      {/* Top stats */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <StatCard label="TOTAL TRACKED" value={String(stats.total)} />
        <StatCard label="PENDING" value={String(stats.pending)} color="var(--amber)" />
        <StatCard label="TARGET HIT" value={String(stats.target_hit)} color="var(--green)" />
        <StatCard label="STOP HIT" value={String(stats.stop_hit)} color="var(--red)" />
        <StatCard label="EXPIRED" value={String(stats.expired)} color="var(--ink-faint)" />
        <StatCard
          label="HIT RATE"
          value={resolvedCount > 0 ? pct(stats.hit_rate) : "— (0 resolved)"}
          color={stats.hit_rate != null ? (stats.hit_rate >= 0.5 ? "var(--green)" : "var(--red)") : "var(--ink-faint)"}
        />
        <StatCard label="AVG DAYS → TARGET" value={days(stats.avg_days_to_target)} />
        <StatCard label="AVG DAYS → STOP" value={days(stats.avg_days_to_stop)} />
      </div>

      {resolvedCount === 0 && (
        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", padding: "0 4px" }}>
          Hit rate and days-to-resolve fill in as tracked signals actually resolve — price needs real trading days
          to move, so this is expected to stay empty right after signals are first recorded.
        </div>
      )}

      {/* Confidence bucket breakdown */}
      <div className="instrument-card">
        <div style={{ padding: "9px 14px", borderBottom: "1px solid var(--line-dim)" }}>
          <span className="panel-title">Hit Rate by Confidence Bucket</span>
        </div>
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          {Object.entries(stats.by_confidence_bucket).map(([label, b]) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", width: 100, flexShrink: 0 }}>{label}</span>
              <div style={{ flex: 1, height: 3, background: "var(--bg-4)" }}>
                <div style={{
                  height: "100%",
                  background: b.hit_rate != null && b.hit_rate >= 0.5 ? "var(--green)" : "var(--red)",
                  width: `${(b.hit_rate ?? 0) * 100}%`,
                }} />
              </div>
              <span className="mono" style={{ fontSize: 10, color: "var(--ink-dim)", width: 60 }}>{b.count} sig.</span>
              <span className="mono" style={{ fontSize: 11, width: 50, textAlign: "right" }}>{pct(b.hit_rate)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Ticker breakdown */}
      {stats.by_ticker.length > 0 && (
        <div className="instrument-card">
          <div style={{ padding: "9px 14px", borderBottom: "1px solid var(--line-dim)" }}>
            <span className="panel-title">By Ticker</span>
          </div>
          <div className="workspace-table-scroll"><table className="t-table">
            <thead><tr>{["Ticker", "Signals", "Hit Rate"].map(h => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>
              {stats.by_ticker.map(t => (
                <tr key={t.ticker}>
                  <td className="mono" style={{ fontWeight: 600 }}>{t.ticker}</td>
                  <td className="mono">{t.total}</td>
                  <td className="mono" style={{ color: t.hit_rate != null ? (t.hit_rate >= 0.5 ? "var(--green)" : "var(--red)") : "var(--ink-faint)" }}>
                    {pct(t.hit_rate)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      )}

      {/* Raw signal list */}
      <Panel
        padding={0}
        title="Tracked Signals"
        action={
          <div style={{ display: "flex", gap: 0 }}>
            {["", "pending", "target_hit", "stop_hit", "expired"].map(s => (
              <button
                key={s || "all"}
                onClick={() => setStatusFilter(s)}
                style={{
                  background: statusFilter === s ? "var(--bg-4)" : "transparent",
                  color: statusFilter === s ? "var(--ink)" : "var(--ink-faint)",
                  border: "1px solid var(--line-dim)", padding: "3px 10px",
                  fontFamily: "var(--mono)", fontSize: 10, cursor: "pointer",
                }}
              >
                {s ? STATUS_LABEL[s] : "ALL"}
              </button>
            ))}
          </div>
        }
      >
        {raw.length === 0 ? (
          <div style={{ padding: 20, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
            No signals match this filter.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <div className="workspace-table-scroll"><table className="t-table">
              <thead><tr>
                {["Ticker", "Action", "Confidence", "Status", "Generated", "Days", "Entry", "Target", "Stop", "Best Move"].map(h => <th key={h}>{h}</th>)}
              </tr></thead>
              <tbody>
                {raw.map(o => <RawRow key={o.id} o={o} />)}
              </tbody>
            </table></div>
          </div>
        )}
      </Panel>
    </div>
  );
}
