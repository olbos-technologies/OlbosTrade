/**
 * Alerts Manager — list and create Smart Alert rules.
 *
 * Rules produce notifications or gated candidates only — never orders. The mode
 * (manual/copilot/autopilot) controls how a fire is handled downstream.
 */

import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Button } from "./ui";

interface Rule {
  id: string; name: string; symbol: string; mode: string; enabled: boolean;
  cooldown_min: number; predicates: { metric: string; op: string; value: any }[];
}

const METRICS = ["relative_volume", "confirmation_score", "macro_event_within_min", "portfolio_overlap_pct"];
const OPS = ["gt", "gte", "lt", "lte", "eq", "within"];
const MODE_CLASS: Record<string, string> = { manual: "conservative", copilot: "balanced", autopilot: "aggressive" };

export default function AlertsManager() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [name, setName] = useState("");
  const [symbol, setSymbol] = useState("QQQ");
  const [metric, setMetric] = useState(METRICS[0]);
  const [op, setOp] = useState("gt");
  const [value, setValue] = useState("1.5");
  const [mode, setMode] = useState("manual");

  const load = () => (api.getAlertRules() as Promise<{ rules: Rule[] }>).then(d => setRules(d.rules || [])).catch(() => {});
  useEffect(() => { load(); }, []);

  const create = () => {
    if (!name.trim()) return;
    api.createAlertRule({
      name, symbol, mode,
      predicates: [{ metric, op, value: isNaN(Number(value)) ? value : Number(value) }],
    }).then(() => { setName(""); load(); });
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 14, alignItems: "start" }}>
      <div style={{ border: "1px solid var(--line-dim)", borderRadius: 6, overflow: "hidden" }}>
        <table className="t-table">
          <thead><tr><th>Rule</th><th>Symbol</th><th>Condition</th><th>Mode</th><th>On</th><th></th></tr></thead>
          <tbody>
            {rules.map(r => (
              <tr key={r.id}>
                <td style={{ color: "var(--ink)" }}>{r.name}</td>
                <td className="mono">{r.symbol}</td>
                <td style={{ color: "var(--ink-dim)", fontSize: 12 }}>
                  {r.predicates.map(p => `${p.metric} ${p.op} ${p.value}`).join(" and ")}
                </td>
                <td><Badge kind="mode" tone={(MODE_CLASS[r.mode] || "conservative") as any}>{r.mode}</Badge></td>
                <td>
                  <input type="checkbox" checked={r.enabled}
                    onChange={e => api.toggleAlertRule(r.id, e.target.checked).then(load)} />
                </td>
                <td><Button danger style={{ fontSize: 11, padding: "2px 8px" }}
                  onClick={() => api.deleteAlertRule(r.id).then(load)}>Delete</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {rules.length === 0 && <div style={{ padding: 24, textAlign: "center", color: "var(--ink-dim)", fontSize: 12 }}>No alert rules yet.</div>}
      </div>

      <div style={{ border: "1px solid var(--line-dim)", borderRadius: 6, background: "var(--bg-2)", padding: 14 }}>
        <div className="panel-title" style={{ marginBottom: 12 }}>New alert</div>
        <Field label="Name"><input value={name} onChange={e => setName(e.target.value)} placeholder="QQQ volume spike" style={inp} /></Field>
        <Field label="Symbol"><input value={symbol} onChange={e => setSymbol(e.target.value.toUpperCase())} style={inp} /></Field>
        <Field label="Condition">
          <div style={{ display: "flex", gap: 6 }}>
            <select value={metric} onChange={e => setMetric(e.target.value)} style={{ ...inp, flex: 2 }}>
              {METRICS.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <select value={op} onChange={e => setOp(e.target.value)} style={{ ...inp, flex: 1 }}>
              {OPS.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
            <input value={value} onChange={e => setValue(e.target.value)} style={{ ...inp, flex: 1 }} />
          </div>
        </Field>
        <Field label="Mode">
          <div style={{ display: "flex", gap: 6 }}>
            {["manual", "copilot", "autopilot"].map(m => (
              <Button selector key={m} active={mode === m} style={{ fontSize: 12, textTransform: "capitalize" }} onClick={() => setMode(m)}>{m}</Button>
            ))}
          </div>
        </Field>
        <Button active style={{ marginTop: 4 }} onClick={create}>Create alert</Button>
        <div style={{ fontSize: 11, color: "var(--ink-faint)", marginTop: 10, lineHeight: 1.6 }}>
          Alerts create notifications or gated candidates only — never orders. Copilot/Autopilot candidates still pass every risk, portfolio, and macro gate.
        </div>
      </div>
    </div>
  );
}

const inp: React.CSSProperties = {
  width: "100%", background: "var(--bg-3)", border: "1px solid var(--line-dim)",
  color: "var(--ink)", fontSize: 13, padding: "6px 9px", borderRadius: 4, outline: "none", boxSizing: "border-box",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="kicker" style={{ marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  );
}
