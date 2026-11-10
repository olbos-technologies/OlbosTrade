/**
 * Strategy Health — full per-strategy diagnosis from GET /api/strategy/health
 * (backend/app/services/strategy_health.py). The Dashboard's compact StratRow
 * (ExecutiveSummary.tsx) and Strategy Cards' health block both only surface
 * status/score/sample_size — action, profit_factor, drawdown_pct,
 * losing_streak, and reasons (the actual explanation for degradation) were
 * computed but never shown anywhere. This is where they finally are.
 */
import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Panel } from "../components/ui";
import { tint } from "../utils/tint";

interface StrategyHealthRow {
  strategy: string;
  status: "healthy" | "watch" | "degraded" | "insufficient_data";
  action: "none" | "reduce" | "suspend";
  score: number;
  volatility: number;
  sample_size: number;
  win_rate: number;
  expectancy: number;
  profit_factor: number;
  drawdown_pct: number;
  losing_streak: number;
  expectancy_ratio: number | null;
  reasons: string[];
}

interface StrategyHealthResponse {
  strategies: StrategyHealthRow[];
  suspended?: string[];
  total_strategies?: number;
  error?: string;
}

const HEALTH_COLOR: Record<string, string> = {
  healthy: "var(--green)",
  watch: "var(--amber)",
  degraded: "var(--red)",
  insufficient_data: "var(--ink-faint)",
};

const STATUS_LABEL: Record<string, string> = {
  healthy: "HEALTHY",
  watch: "WATCH",
  degraded: "SUSPENDED",
  insufficient_data: "NO DATA",
};

const ACTION_LABEL: Record<string, string> = {
  suspend: "SUSPEND NEW ENTRIES",
  reduce: "REDUCE SIZE",
};

function scoreColor(score: number): string {
  return score >= 70 ? "var(--green)" : score >= 45 ? "var(--amber)" : "var(--red)";
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ color: "var(--ink-dim)", fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.08em" }}>
        {label}
      </span>
      <span style={{ color: color || "var(--ink)", fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600 }}>
        {value}
      </span>
    </div>
  );
}

function StrategyHealthRowView({ row }: { row: StrategyHealthRow }) {
  const color = HEALTH_COLOR[row.status] || "var(--ink-faint)";
  const ratio = row.expectancy_ratio != null ? `${(row.expectancy_ratio * 100).toFixed(0)}% of base` : "—";

  return (
    <div className="instrument-card" style={{
      border: `1px solid ${tint(color, 0.251)}`, borderLeft: `2px solid ${color}`,
      padding: "14px 16px", display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ color: "var(--ink)", fontFamily: "var(--mono)", fontSize: 15, fontWeight: 700 }}>
          {row.strategy.replace(/_/g, " ")}
        </span>
        <span style={{
          fontFamily: "var(--mono)", fontSize: 9, fontWeight: 700, letterSpacing: "0.08em",
          padding: "2px 8px", color, border: `1px solid ${tint(color, 0.376)}`,
        }}>
          {STATUS_LABEL[row.status] || row.status.toUpperCase()}
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink-faint)" }}>
          σ {row.volatility.toFixed(3)}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(90px, 1fr))", gap: 10 }}>
        <Metric label="SCORE" value={row.score.toFixed(0)} color={scoreColor(row.score)} />
        <Metric label="SAMPLE" value={String(row.sample_size)} />
        <Metric label="WIN RATE" value={`${(row.win_rate * 100).toFixed(0)}%`} />
        <Metric label="EXPECTANCY" value={`${row.expectancy >= 0 ? "+" : ""}${row.expectancy.toFixed(0)}`}
          color={row.expectancy >= 0 ? "var(--green)" : "var(--red)"} />
        <Metric label="PROFIT FACTOR" value={row.profit_factor.toFixed(2)} />
        <Metric label="DRAWDOWN" value={`${row.drawdown_pct.toFixed(1)}%`} color="var(--red)" />
        <Metric label="LOSING STREAK" value={String(row.losing_streak)} />
        <Metric label="VS BASELINE" value={ratio} />
      </div>

      {row.action !== "none" && (
        <div style={{
          fontFamily: "var(--mono)", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
          color: row.action === "suspend" ? "var(--red)" : "var(--amber)",
          border: `1px solid ${tint(row.action === "suspend" ? "var(--red)" : "var(--amber)", 0.376)}`,
          padding: "6px 10px", width: "fit-content",
        }}>
          ACTION: {ACTION_LABEL[row.action] || row.action.toUpperCase()}
        </div>
      )}

      {row.reasons.length > 0 && (
        <ul style={{ margin: 0, padding: "0 0 0 18px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.7 }}>
          {row.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function StrategyHealthPanel() {
  const [data, setData] = useState<StrategyHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    setError(null);
    (api.getStrategyHealth() as Promise<StrategyHealthResponse>)
      .then(d => {
        if (d.error) { setError(d.error); return; }
        setData(d);
      })
      .catch(e => setError(e?.message || "Failed to load strategy health"))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  if (loading && !data && !error) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 200,
      fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
      LOADING STRATEGY HEALTH...
    </div>
  );

  if (error) return (
    <div className="workspace-page-root" style={{ padding: 16 }}>
      <div style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
        padding: "10px 14px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>
        {error}
      </div>
    </div>
  );

  const strategies = data?.strategies || [];
  const suspended = data?.suspended || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: 16 }}>
      <div className="instrument-card" style={{ padding: "10px 14px", border: "1px solid color-mix(in srgb, var(--cyan) 19%, transparent)", borderLeft: "2px solid var(--cyan)" }}>
        <span className="panel-title" style={{ marginRight: 12 }}>STRATEGY HEALTH</span>
        <span className="mono" style={{ fontSize: 11, color: suspended.length === 0 ? "var(--green)" : "var(--red)" }}>
          {data?.total_strategies ?? strategies.length} tracked
          {suspended.length === 0 ? " · none suspended" : ` · ${suspended.length} suspended (${suspended.join(", ")})`}
        </span>
      </div>

      <Panel padding={0} title="Strategies">
        {strategies.length === 0 ? (
          <div style={{ padding: 20, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
            No strategies tracked yet — collecting data.
          </div>
        ) : (
          <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
            {strategies.map(row => <StrategyHealthRowView key={row.strategy} row={row} />)}
          </div>
        )}
      </Panel>
    </div>
  );
}
