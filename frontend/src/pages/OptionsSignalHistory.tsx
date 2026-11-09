/**
 * Options Signal History — persisted log of every qualifying options spread
 * signal (GET /api/options/signals/history), unlike the live 60s-polled
 * feed in OptionsSignals.tsx which is wiped on every backend restart.
 *
 * This is a signal log, not a win-rate study: options spreads have no
 * entry/stop/target shape to resolve a forward outcome against the way
 * equity signals do (see SignalResearch.tsx for that model) — so there is
 * deliberately no status/hit-rate column here.
 */
import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Panel } from "../components/ui";
import type { Spread, SignalEvidence } from "./OptionsSignals";

interface HistoryRow {
  id: string;
  ticker: string;
  strategy: string;
  action: string;
  confidence: number;
  pop: number | null;
  kelly_fraction: number | null;
  signal_score: number;
  quantity: number;
  iv_rank: number;
  regime: string;
  spread: Spread;
  sigma: number;
  vix_used: number;
  credit_source: string;
  evidence: SignalEvidence | null;
  intelligence: Record<string, number> | null;
  generated_at: string | null;
}

function formatStrikePair(spread: Spread): string {
  if (spread.short_strike == null || spread.long_strike == null) return "—";
  const ot = (spread.option_type || "").toUpperCase().slice(0, 1) || "?";
  return `${ot} ${spread.short_strike}/${spread.long_strike}`;
}

function HistoryRowView({ row }: { row: HistoryRow }) {
  const genDate = row.generated_at
    ? new Date(row.generated_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "—";
  const isDebit = row.action === "BUY_SPREAD";
  return (
    <tr>
      <td className="mono" style={{ fontWeight: 600 }}>{row.ticker}</td>
      <td className="mono" style={{ color: "var(--cyan)" }}>{row.strategy.replace(/_/g, " ")}</td>
      <td className="mono" style={{ color: isDebit ? "var(--green)" : "var(--red)" }}>{row.action.replace("_", " ")}</td>
      <td className="mono">{(row.confidence * 100).toFixed(0)}</td>
      <td className="mono">{formatStrikePair(row.spread)}</td>
      <td className="mono" style={{ color: "var(--ink)" }}>
        {row.spread.net_credit != null ? `$${Math.abs(row.spread.net_credit).toFixed(2)}` : "—"}
      </td>
      <td className="mono" style={{ color: "var(--red)" }}>
        {row.spread.max_loss != null ? `$${row.spread.max_loss.toFixed(2)}` : "—"}
      </td>
      <td className="mono" style={{ color: "var(--green)" }}>
        {row.spread.breakeven != null ? `$${row.spread.breakeven.toFixed(2)}` : "—"}
      </td>
      <td className="mono">{row.spread.dte ?? "—"}</td>
      <td className="mono" style={{ color: "var(--ink-dim)" }}>{genDate}</td>
    </tr>
  );
}

export default function OptionsSignalHistory() {
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [strategyFilter, setStrategyFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    (api.getOptionsSignalHistory({ limit: 200, strategy: strategyFilter || undefined }) as Promise<{ signals: HistoryRow[]; total: number }>)
      .then(d => { setRows(d.signals || []); setTotal(d.total || 0); })
      .catch(e => setError(e?.message || "Failed to load options signal history"))
      .finally(() => setLoading(false));
  };

  useEffect(load, [strategyFilter]);

  const strategies = Array.from(new Set(rows.map(r => r.strategy))).sort();

  if (loading && rows.length === 0 && !error) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 200,
      fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
      LOADING OPTIONS SIGNAL HISTORY...
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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ padding: "10px 14px", background: "var(--bg-2)", border: "1px solid color-mix(in srgb, var(--cyan) 19%, transparent)", borderLeft: "2px solid var(--cyan)" }}>
        <span className="panel-title" style={{ marginRight: 12 }}>OPTIONS SIGNAL HISTORY</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink)" }}>
          Every qualifying options spread signal is persisted here permanently, {total} tracked so far.
          Forward P&amp;L resolution against real spread outcomes at expiration isn't built yet — this is
          a signal log, not a win-rate study (see the Equities view for that model).
        </span>
      </div>

      <Panel
        padding={0}
        title="Persisted Signals"
        action={
          strategies.length > 1 ? (
            <div style={{ display: "flex", gap: 0 }}>
              {["", ...strategies].map(s => (
                <button
                  key={s || "all"}
                  onClick={() => setStrategyFilter(s)}
                  style={{
                    background: strategyFilter === s ? "var(--bg-4)" : "transparent",
                    color: strategyFilter === s ? "var(--ink)" : "var(--ink-faint)",
                    border: "1px solid var(--line-dim)", padding: "3px 10px",
                    fontFamily: "var(--mono)", fontSize: 10, cursor: "pointer",
                  }}
                >
                  {s ? s.replace(/_/g, " ") : "ALL"}
                </button>
              ))}
            </div>
          ) : undefined
        }
      >
        {rows.length === 0 ? (
          <div style={{ padding: 20, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
            No options signals persisted yet. They accumulate as the options scanner runs.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="t-table">
              <thead><tr>
                {["Ticker", "Strategy", "Action", "Confidence", "Type & Strikes", "Net Credit", "Max Loss", "Breakeven", "DTE", "Generated"].map(h => <th key={h}>{h}</th>)}
              </tr></thead>
              <tbody>
                {rows.map(r => <HistoryRowView key={r.id} row={r} />)}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
