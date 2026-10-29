import React, { useState } from "react";
import { useBacktest } from "../hooks/useBacktest";
import { Panel, Button } from "../components/ui";

export default function Backtest() {
  const { results, loading, error, runBacktest } = useBacktest();
  const [form, setForm] = useState({
    strategy: "bull_put_spread", start_date: "2022-01-01",
    end_date: "2024-12-31", mode: "balanced",
  });

  // Only treat results as displayable metrics once the async run completes.
  const done = results?.status === "completed";
  const status: string | undefined = results?.status;
  const pct = (v: any) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");
  const num = (v: any, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

  const Field = ({ label, children }: any) => (
    <div style={{ marginBottom: 14 }}>
      <div className="kicker" style={{ marginBottom: 6 }}>{label}</div>
      {children}
    </div>
  );

  const inputStyle = {
    width: "100%", background: "var(--bg-3)", border: "1px solid var(--line-dim)",
    color: "var(--ink)", fontFamily: "var(--mono)", fontSize: 12,
    padding: "7px 10px", outline: "none",
  };

  const selectStyle = { ...inputStyle };

  // Backend returns fractions named *_pct (e.g. total_return_pct = 0.12 = 12%).
  const metrics = [
    { label: "Total Return",   val: done ? pct(results.total_return_pct) : "—",  color: (results?.total_return_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)" },
    { label: "Sharpe Ratio",   val: done ? num(results.sharpe_ratio) : "—",      color: (results?.sharpe_ratio ?? 0) >= 1 ? "var(--cyan)" : "var(--ink)" },
    { label: "Win Rate",       val: done ? pct(results.win_rate) : "—",          color: "var(--ink)" },
    { label: "Max Drawdown",   val: done ? pct(results.max_drawdown_pct) : "—",  color: "var(--red)" },
    { label: "Profit Factor",  val: done ? num(results.profit_factor) : "—",     color: "var(--ink)" },
    { label: "Total Trades",   val: done ? (results.total_trades ?? 0) : "—",    color: "var(--ink)" },
  ];

  return (
    <div className="workspace-backtest-layout" style={{ display: "grid", gridTemplateColumns: "280px minmax(0,1fr)", height: "100%", overflow: "hidden" }}>
      {/* Left: config */}
      <div className="instrument-card" style={{ borderRight: "1px solid var(--line-dim)", padding: 16, overflowY: "auto" }}>
        <div className="panel-title" style={{ marginBottom: 16 }}>Configuration</div>
        <Field label="Strategy">
          <select style={selectStyle} value={form.strategy}
            onChange={e => setForm({ ...form, strategy: e.target.value })}>
            <option value="bull_put_spread">Bull Put Spread</option>
            <option value="bear_call_spread">Bear Call Spread</option>
            <option value="iron_condor">Iron Condor</option>
            <option value="bull_call_debit_spread">Bull Call Debit</option>
          </select>
        </Field>
        <Field label="Mode">
          <select style={selectStyle} value={form.mode}
            onChange={e => setForm({ ...form, mode: e.target.value })}>
            {["conservative","balanced","aggressive","scalper"].map(m => (
              <option key={m} value={m}>{m.charAt(0).toUpperCase() + m.slice(1)}</option>
            ))}
          </select>
        </Field>
        <Field label="Start Date">
          <input type="date" style={inputStyle} value={form.start_date}
            onChange={e => setForm({ ...form, start_date: e.target.value })} />
        </Field>
        <Field label="End Date">
          <input type="date" style={inputStyle} value={form.end_date}
            onChange={e => setForm({ ...form, end_date: e.target.value })} />
        </Field>
        <Button onClick={() => runBacktest(form)}
          style={{ width: "100%", marginTop: 8, padding: "10px", justifyContent: "center", display: "flex" }}>
          {loading ? "Running…" : "Run backtest ↗"}
        </Button>
      </div>

      {/* Right: results */}
      <div style={{ overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>
        {/* Metrics ribbon — tight single row to maximise chart space below. */}
        <div className="instrument-stat-strip workspace-stat-grid" style={{
          display: "grid", gridTemplateColumns: "repeat(6, 1fr)",
          overflow: "hidden",
        }}>
          {metrics.map((m, i) => (
            <div key={m.label} style={{
              padding: "8px 12px",
              borderRight: i < 5 ? "1px solid var(--line-dim)" : "none",
              display: "flex", flexDirection: "column", gap: 3,
            }}>
              <div className="kicker">{m.label}</div>
              <div className="data-val sm" style={{ color: m.color }}>{m.val}</div>
            </div>
          ))}
        </div>

        {/* Status / equity curve placeholder */}
        <Panel title="Equity Curve" padding={16}>
          <div style={{
            height: 200, background: "var(--bg-3)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            {error
              ? <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>
                  ⚠ {error}
                </span>
              : loading || status === "queued" || status === "running"
              ? <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--amber)" }}>
                  RUNNING BACKTEST… ({status || "starting"})
                </span>
              : status === "failed"
              ? <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>
                  ⚠ BACKTEST FAILED — {results?.error || "see server logs"}
                </span>
              : done
              ? <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--cyan)" }}>
                  BACKTEST COMPLETE — {results.total_trades ?? 0} TRADES
                </span>
              : <span style={{ fontSize: 12, color: "var(--ink-dim)" }}>
                  Configure and run a backtest
                </span>
            }
          </div>
        </Panel>

        {/* Trade log */}
        {done && results?.trades?.length > 0 && (
          <Panel title="Trade Log" padding={16}>
            <div className="workspace-table-scroll"><table className="t-table">
              <thead><tr>
                {["Entry","Exit","Strategy","Credit","P&L","Exit Reason"].map(h => <th key={h}>{h}</th>)}
              </tr></thead>
              <tbody>
                {results.trades.slice(0, 20).map((t: any, i: number) => (
                  <tr key={i}>
                    <td className="mono">{t.entry_date}</td>
                    <td className="mono">{t.exit_date || "—"}</td>
                    <td className="mono" style={{ color: "var(--ink)" }}>{t.strategy}</td>
                    <td className="mono">${t.credit_received?.toFixed(2) || "—"}</td>
                    <td className="mono" style={{ color: t.pnl >= 0 ? "var(--green)" : "var(--red)" }}>
                      {t.pnl >= 0 ? "+" : ""}${t.pnl?.toFixed(0) || 0}
                    </td>
                    <td className="mono" style={{ color: "var(--ink-dim)" }}>{t.exit_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          </Panel>
        )}
      </div>
    </div>
  );
}
