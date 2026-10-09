import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Panel, Sparkline } from "../components/ui";
import { tint } from "../utils/tint";

interface ModeStats {
  display_name: string; trade_count: number; win_rate: number;
  win_count: number; loss_count: number; avg_win: number; avg_loss: number;
  expectancy: number; profit_factor: number; total_pnl: number;
  avg_pnl_per_trade: number; max_drawdown_pct: number; sharpe_ratio: number;
  avg_hold_days: number; score_vs_outcome: number;
  exit_breakdown: { profit_target: number; stop_loss: number; dte_exit: number; other: number };
  last_5_pnl: number[]; is_improving: boolean; ui_color: string; recommendation: string;
}

interface AnalyticsData {
  total_trades: number; best_mode: string; recommendation: string;
  modes: Record<string, ModeStats>;
}

const ORDER = ["conservative","balanced","aggressive","scalper"];
const MODE_COLOR: Record<string, string> = {
  conservative: "var(--green)", balanced: "var(--cyan)",
  aggressive: "var(--orange)", scalper: "var(--red)",
};
const fmt = (n: number) => (n >= 0 ? "+$" : "-$") + Math.abs(n).toFixed(0);
const pct = (n: number) => (n * 100).toFixed(1) + "%";

function ModeCard({ modeKey, s, isBest }: { modeKey: string; s: ModeStats; isBest: boolean }) {
  const color = MODE_COLOR[modeKey] || "var(--cyan)";
  return (
    <div style={{
      border: `1px solid ${isBest ? tint(color, 0.376) : "var(--line-dim)"}`,
      background: isBest ? tint(color, 0.031) : "var(--bg-2)",
      display: "flex", flexDirection: "column",
    }}>
      {/* Header */}
      <div style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--line-dim)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        borderLeft: `2px solid ${color}`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 600, color }}>
            {s.display_name.toUpperCase()}
          </span>
          {isBest && (
            <span style={{ fontFamily: "var(--mono)", fontSize: 9, padding: "1px 6px",
              background: tint(color, 0.125), color, border: `1px solid ${tint(color, 0.251)}` }}>
              BEST SHARPE
            </span>
          )}
          {s.is_improving && (
            <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--green)" }}>↑</span>
          )}
        </div>
        <Sparkline values={s.last_5_pnl} formatTitle={fmt} />
      </div>

      {/* Stats grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)" }}>
        {[
          { label: "WIN RATE",   val: pct(s.win_rate),                color: s.win_rate >= 0.55 ? "var(--green)" : "var(--red)" },
          { label: "EXPECTANCY", val: fmt(s.expectancy),               color: s.expectancy >= 0 ? "var(--green)" : "var(--red)" },
          { label: "SHARPE",     val: s.sharpe_ratio.toFixed(2),       color: s.sharpe_ratio >= 0.5 ? "var(--cyan)" : "var(--ink)" },
          { label: "TOTAL P&L",  val: fmt(s.total_pnl),                color: s.total_pnl >= 0 ? "var(--green)" : "var(--red)" },
          { label: "AVG/TRADE",  val: fmt(s.avg_pnl_per_trade),        color: s.avg_pnl_per_trade >= 0 ? "var(--green)" : "var(--red)" },
          { label: "MAX DD",     val: pct(s.max_drawdown_pct),         color: s.max_drawdown_pct < 0.15 ? "var(--green)" : "var(--red)" },
        ].map((m, i) => (
          <div key={m.label} style={{
            padding: "10px 12px",
            borderRight: i % 3 < 2 ? "1px solid var(--line-dim)" : "none",
            borderBottom: i < 3 ? "1px solid var(--line-dim)" : "none",
          }}>
            <div className="kicker" style={{ marginBottom: 4 }}>{m.label}</div>
            <div className="data-val sm" style={{ color: m.color }}>{m.val}</div>
          </div>
        ))}
      </div>

      {/* Win bar */}
      <div style={{ padding: "10px 14px", borderTop: "1px solid var(--line-dim)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
          <span className="kicker">{s.win_count}W · {s.loss_count}L · {s.trade_count} TOTAL</span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
            Avg hold {s.avg_hold_days.toFixed(0)}d
          </span>
        </div>
        <div style={{ height: 3, background: "var(--bg-4)", position: "relative" }}>
          <div style={{ position: "absolute", left: 0, top: 0, height: "100%",
            width: `${s.win_rate * 100}%`, background: "var(--green)" }} />
        </div>
      </div>

      {/* Exit breakdown */}
      <div style={{ padding: "10px 14px", borderTop: "1px solid var(--line-dim)" }}>
        <div className="kicker" style={{ marginBottom: 8 }}>Exit Reasons</div>
        <div style={{ display: "flex", gap: 8 }}>
          {[
            { label: "PT",    val: s.exit_breakdown.profit_target, color: "var(--green)" },
            { label: "SL",    val: s.exit_breakdown.stop_loss,     color: "var(--red)" },
            { label: "DTE",   val: s.exit_breakdown.dte_exit,      color: "var(--amber)" },
            { label: "OTHER", val: s.exit_breakdown.other,         color: "var(--ink-faint)" },
          ].map(e => (
            <div key={e.label} style={{ fontFamily: "var(--mono)", fontSize: 10, textAlign: "center" }}>
              <div style={{ color: e.color, fontWeight: 600 }}>{e.val}</div>
              <div style={{ color: "var(--ink-faint)" }}>{e.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Recommendation */}
      {s.recommendation && (
        <div style={{ padding: "8px 14px", borderTop: "1px solid var(--line-dim)" }}>
          <p style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", lineHeight: 1.7 }}>
            {s.recommendation}
          </p>
        </div>
      )}
    </div>
  );
}

export default function ModeAnalytics() {
  const [data, setData]     = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [scoreData, setScore] = useState<any>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    Promise.all([
      (api as any).getModeAnalytics(),
      (api as any).getSignalScoreImpact(),
    ]).then(([a, s]: any) => { setData(a); setScore(s); })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%",
      fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
      LOADING ANALYTICS...
    </div>
  );

  if (error) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%",
      flexDirection: "column", gap: 12 }}>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>FAILED TO LOAD ANALYTICS</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>
        Could not reach the analytics API. Try reloading.
      </span>
    </div>
  );

  if (!data || data.total_trades === 0) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%",
      flexDirection: "column", gap: 12 }}>
      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--cyan)" }}>NO TRADE DATA</span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>
        Start paper trading. Every trade is tagged by mode automatically.
      </span>
    </div>
  );

  const active = ORDER.filter(k => data.modes[k]);

  return (
    <div style={{ overflowY: "auto", height: "100%", padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>

      {/* Portfolio rec */}
      {data.recommendation && (
        <div style={{ padding: "10px 14px", background: "var(--bg-2)", border: "1px solid color-mix(in srgb, var(--cyan) 19%, transparent)", borderLeft: "2px solid var(--cyan)" }}>
          <span className="panel-title" style={{ marginRight: 12 }}>SYSTEM RECOMMENDATION</span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink)" }}>{data.recommendation}</span>
        </div>
      )}

      {/* Trading style cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 16 }}>
        {active.map(key => (
          <ModeCard key={key} modeKey={key} s={data.modes[key]} isBest={key === data.best_mode} />
        ))}
      </div>

      {/* Comparison table */}
      <Panel padding={0} title="Head-to-Head Comparison">
        <table className="t-table">
          <thead><tr>
            {["Trading style","Trades","Win %","Avg/Trade","Total P&L","Sharpe","Expectancy","Hold"].map(h => <th key={h}>{h}</th>)}
          </tr></thead>
          <tbody>
            {active.map(key => {
              const s = data.modes[key];
              const color = MODE_COLOR[key] || "var(--cyan)";
              return (
                <tr key={key}>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div style={{ width: 2, height: 16, background: color, flexShrink: 0 }} />
                      <span className="mono" style={{ color }}>
                        {s.display_name.toUpperCase()}
                      </span>
                      {key === data.best_mode && <span style={{ color: "var(--cyan)", fontSize: 10 }}>★</span>}
                    </div>
                  </td>
                  <td className="mono">{s.trade_count}</td>
                  <td className="mono" style={{ color: s.win_rate >= 0.55 ? "var(--green)" : "var(--red)" }}>{pct(s.win_rate)}</td>
                  <td className="mono" style={{ color: s.avg_pnl_per_trade >= 0 ? "var(--green)" : "var(--red)" }}>{fmt(s.avg_pnl_per_trade)}</td>
                  <td className="mono" style={{ color: s.total_pnl >= 0 ? "var(--green)" : "var(--red)" }}>{fmt(s.total_pnl)}</td>
                  <td className="mono" style={{ color: s.sharpe_ratio >= 1 ? "var(--cyan)" : "var(--ink)" }}>{s.sharpe_ratio.toFixed(2)}</td>
                  <td className="mono" style={{ color: s.expectancy >= 0 ? "var(--green)" : "var(--red)" }}>{fmt(s.expectancy)}</td>
                  <td className="mono" style={{ color: "var(--ink-dim)" }}>{s.avg_hold_days.toFixed(0)}d</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>

      {/* Signal score impact */}
      {scoreData?.by_score_bucket && (
        <Panel
          padding={16}
          title="Signal Score → Outcome"
          action={
            <span style={{
              fontFamily: "var(--mono)", fontSize: 10, padding: "2px 8px",
              background: scoreData.overall_correlation > 0.2 ? "rgba(34,197,94,0.1)" : "rgba(245,158,11,0.1)",
              color: scoreData.overall_correlation > 0.2 ? "var(--green)" : "var(--amber)",
              border: `1px solid ${scoreData.overall_correlation > 0.2 ? "rgba(34,197,94,0.3)" : "rgba(245,158,11,0.3)"}`,
            }}>{scoreData.verdict}</span>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {Object.entries(scoreData.by_score_bucket).map(([label, stats]: any) => (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", width: 180, flexShrink: 0 }}>{label}</span>
                <div style={{ flex: 1, height: 3, background: "var(--bg-4)" }}>
                  <div style={{
                    height: "100%",
                    background: stats.avg_pnl >= 0 ? "var(--green)" : "var(--red)",
                    width: `${Math.min(Math.abs(stats.avg_pnl) / 200 * 100, 100)}%`,
                  }} />
                </div>
                <span className="mono" style={{ fontSize: 10, color: "var(--ink-dim)", width: 50 }}>{stats.trade_count}t</span>
                <span className="mono" style={{ fontSize: 11, width: 60, textAlign: "right", color: stats.avg_pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                  {fmt(stats.avg_pnl)}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
