/**
 * Strategy Builder — configure a spread strategy's parameters and register it
 * as a Research Lab experiment. The promotion funnel (backtest → walk-forward →
 * paper → promoted) already exists; this exposes the `spec` dict that
 * POST /api/research/lab/experiments accepts but ResearchLab's quick-create
 * form doesn't surface.
 */
import React, { useEffect, useState } from "react";
import { Button } from "../../components/ui";

const STRATEGIES = [
  { key: "bull_put_spread", label: "Bull Put Spread" },
  { key: "bear_call_spread", label: "Bear Call Spread" },
  { key: "iron_condor", label: "Iron Condor" },
  { key: "bull_call_debit_spread", label: "Bull Call Debit Spread" },
];

interface Experiment {
  id: string; name: string; strategy: string; stage: string;
}

const inp: React.CSSProperties = {
  width: "100%", padding: "8px 10px", fontSize: 12, color: "var(--ink)",
  background: "var(--bg-2)", border: "1px solid var(--line-dim)", borderRadius: 4,
};
const label: React.CSSProperties = {
  fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)",
  textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4, display: "block",
};

export default function StrategyBuilder() {
  const [name, setName] = useState("");
  const [strategy, setStrategy] = useState(STRATEGIES[0].key);
  const [hypothesis, setHypothesis] = useState("");
  const [dteMin, setDteMin] = useState(21);
  const [dteMax, setDteMax] = useState(45);
  const [deltaMin, setDeltaMin] = useState(0.15);
  const [deltaMax, setDeltaMax] = useState(0.30);
  const [minCreditToWidth, setMinCreditToWidth] = useState(0.33);
  const [watchlist, setWatchlist] = useState("SPY, QQQ, IWM");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [recent, setRecent] = useState<Experiment[]>([]);

  const loadRecent = () => {
    fetch("/api/research/lab/experiments")
      .then(r => r.json())
      .then(d => setRecent((d.experiments || []).slice(0, 8)))
      .catch(() => {});
  };
  useEffect(() => { loadRecent(); }, []);

  const create = async () => {
    if (!name.trim()) { setMsg("Name required"); return; }
    setBusy(true); setMsg(null);
    const spec = {
      dte_min: dteMin, dte_max: dteMax,
      delta_min: deltaMin, delta_max: deltaMax,
      min_credit_to_width: minCreditToWidth,
      watchlist: watchlist.split(",").map(s => s.trim().toUpperCase()).filter(Boolean),
    };
    const r = await fetch("/api/research/lab/experiments", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, strategy, hypothesis, spec }),
    }).then(r => r.json()).catch(() => null);
    setBusy(false);
    if (!r) { setMsg("Request failed — the server didn't respond."); return; }
    if (r.error) { setMsg(r.error); return; }
    setMsg(`Created "${name}" as a draft experiment — track it in Research Lab → Strategy Lab.`);
    setName(""); setHypothesis("");
    loadRecent();
  };

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 14, flexWrap: "wrap" }}>
        <span className="panel-title">Strategy Builder</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>
          configure parameters · registers a draft experiment in Research Lab
        </span>
      </div>

      <div className="exec-card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
        <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <label>
            <span style={label}>Name</span>
            <input style={inp} value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. SPY 30-DTE bull put" />
          </label>
          <label>
            <span style={label}>Base strategy</span>
            <select style={inp} value={strategy} onChange={e => setStrategy(e.target.value)}>
              {STRATEGIES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>

          <label>
            <span style={label}>DTE min</span>
            <input style={inp} type="number" value={dteMin} onChange={e => setDteMin(+e.target.value)} />
          </label>
          <label>
            <span style={label}>DTE max</span>
            <input style={inp} type="number" value={dteMax} onChange={e => setDteMax(+e.target.value)} />
          </label>

          <label>
            <span style={label}>Short-leg delta min</span>
            <input style={inp} type="number" step="0.01" value={deltaMin} onChange={e => setDeltaMin(+e.target.value)} />
          </label>
          <label>
            <span style={label}>Short-leg delta max</span>
            <input style={inp} type="number" step="0.01" value={deltaMax} onChange={e => setDeltaMax(+e.target.value)} />
          </label>

          <label>
            <span style={label}>Min credit / width</span>
            <input style={inp} type="number" step="0.01" value={minCreditToWidth} onChange={e => setMinCreditToWidth(+e.target.value)} />
          </label>
          <label>
            <span style={label}>Watchlist</span>
            <input style={inp} value={watchlist} onChange={e => setWatchlist(e.target.value)} placeholder="SPY, QQQ, IWM" />
          </label>

          <label style={{ gridColumn: "1 / -1" }}>
            <span style={label}>Hypothesis</span>
            <input style={inp} value={hypothesis} onChange={e => setHypothesis(e.target.value)}
              placeholder="Why should this have an edge?" />
          </label>
        </div>

        <Button onClick={create} disabled={busy} active style={{ marginTop: 14 }}>
          {busy ? "…" : "Create draft experiment"}
        </Button>
        {msg && (
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: msg.startsWith("Created") ? "var(--green)" : "var(--amber)", marginTop: 10 }}>
            {msg}
          </div>
        )}
      </div>

      <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--ink-dim)", marginBottom: 8, textTransform: "uppercase" }}>
        Recent experiments
      </div>
      {recent.length === 0 ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>None yet.</div>
      ) : (
        <div className="workspace-table-scroll"><table className="t-table" style={{ maxWidth: 640 }}>
          <thead><tr><th>Name</th><th>Strategy</th><th>Stage</th></tr></thead>
          <tbody>
            {recent.map(e => (
              <tr key={e.id}>
                <td className="mono">{e.name}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{e.strategy}</td>
                <td className="mono" style={{ color: "var(--cyan)" }}>{e.stage.toUpperCase()}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
