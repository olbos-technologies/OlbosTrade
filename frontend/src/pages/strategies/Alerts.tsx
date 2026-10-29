/**
 * Alerts — Smart Alert rules + Notification Center.
 * Backed by /api/alerts/rules (CRUD) and /api/notifications (list/read).
 * Alerts only ever create notifications — never orders (see backend/app/
 * services/alerts/service.py).
 */
import React, { useEffect, useState } from "react";
import { Button } from "../../components/ui";

// Only metrics the background equity scan actually populates in the alert
// snapshot (app/main.py's _scan_one, via app/services/alerts/service.py).
// iv_rank/iv_percentile/vix need options/vol-surface data this scan doesn't
// fetch — a separate, larger options-desk wiring slice, not silently faked
// here. Existing rules referencing them keep safely never firing; this only
// affects new rule creation.
const METRICS = [
  "price", "change_pct", "rsi_14", "volume",
  "alpha_edge_entry_score", "alpha_edge_risk_score", "opportunity_score",
  "iv_rank", "vix_used",
];
const OPS = ["gt", "gte", "lt", "lte", "eq", "ne"] as const;

interface Predicate { metric: string; op: string; value: string }
interface Rule {
  id: string; name: string; symbol: string; predicates: Predicate[];
  mode: string; enabled: boolean; cooldown_min: number; last_fired_at: string | null;
}
interface Notification {
  id: string; title: string; body: string; severity: string; read: boolean; created_at: string;
}

const inp: React.CSSProperties = {
  padding: "7px 9px", fontSize: 12, color: "var(--ink)",
  background: "var(--bg-2)", border: "1px solid var(--line-dim)", borderRadius: 4,
};

export default function Alerts() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [notifs, setNotifs] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);

  const [name, setName] = useState("");
  const [symbol, setSymbol] = useState("");
  const [metric, setMetric] = useState(METRICS[0]);
  const [op, setOp] = useState<typeof OPS[number]>("gt");
  const [value, setValue] = useState("");
  const [cooldown, setCooldown] = useState(60);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const loadRules = () => fetch("/api/alerts/rules").then(r => r.json()).then(d => setRules(d.rules || [])).catch(() => {});
  const loadNotifs = () => fetch("/api/notifications?limit=20").then(r => r.json())
    .then(d => { setNotifs(d.notifications || []); setUnread(d.unread || 0); }).catch(() => {});

  useEffect(() => {
    loadRules(); loadNotifs();
    const t = setInterval(loadNotifs, 30000);
    return () => clearInterval(t);
  }, []);

  const create = async () => {
    if (!name.trim() || !symbol.trim() || !value.trim()) { setMsg("Name, symbol, and value are required."); return; }
    setBusy(true); setMsg(null);
    const r = await fetch("/api/alerts/rules", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, symbol: symbol.toUpperCase(),
        predicates: [{ metric, op, value: isNaN(+value) ? value : +value }],
        mode: "manual", cooldown_min: cooldown,
      }),
    }).then(r => r.json()).catch(() => null);
    setBusy(false);
    if (!r || r.error) { setMsg(r?.error || "Request failed."); return; }
    setName(""); setSymbol(""); setValue("");
    loadRules();
  };

  const toggle = async (rule: Rule) => {
    await fetch(`/api/alerts/rules/${rule.id}/toggle?enabled=${!rule.enabled}`, { method: "POST" }).catch(() => {});
    loadRules();
  };
  const remove = async (id: string) => {
    await fetch(`/api/alerts/rules/${id}`, { method: "DELETE" }).catch(() => {});
    loadRules();
  };
  const markAllRead = async () => {
    await fetch("/api/notifications/read-all", { method: "POST" }).catch(() => {});
    loadNotifs();
  };

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 14, flexWrap: "wrap" }}>
        <span className="panel-title">Alerts</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>
          rules fire notifications only — never orders
        </span>
      </div>

      {/* New rule */}
      <div className="exec-card" style={{ padding: 14, marginBottom: 16, maxWidth: 760 }}>
        <div className="panel-title" style={{ marginBottom: 10, fontSize: 12 }}>New rule</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <input style={{ ...inp, width: 140 }} value={name} onChange={e => setName(e.target.value)} placeholder="Rule name" />
          <input style={{ ...inp, width: 80 }} value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="Symbol" />
          <select style={inp} value={metric} onChange={e => setMetric(e.target.value)}>
            {METRICS.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <select style={inp} value={op} onChange={e => setOp(e.target.value as typeof op)}>
            {OPS.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
          <input style={{ ...inp, width: 90 }} value={value} onChange={e => setValue(e.target.value)} placeholder="Value" />
          <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>cooldown (min)</span>
            <input style={{ ...inp, width: 60 }} type="number" value={cooldown} onChange={e => setCooldown(+e.target.value)} />
          </label>
          <Button onClick={create} disabled={busy} active>{busy ? "…" : "Create"}</Button>
        </div>
        {msg && <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--amber)", marginTop: 8 }}>{msg}</div>}
      </div>

      {/* Rules list */}
      <div style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--ink-dim)", marginBottom: 8, textTransform: "uppercase" }}>
        Rules ({rules.length})
      </div>
      {rules.length === 0 ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)", marginBottom: 20 }}>No rules yet.</div>
      ) : (
        <div className="workspace-table-scroll"><table className="t-table" style={{ marginBottom: 20, maxWidth: 760 }}>
          <thead>
            <tr><th>Name</th><th>Symbol</th><th>Condition</th><th>Cooldown</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {rules.map(r => (
              <tr key={r.id}>
                <td className="mono">{r.name}</td>
                <td className="mono" style={{ color: "var(--cyan)" }}>{r.symbol}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>
                  {r.predicates.map(p => `${p.metric} ${p.op} ${p.value}`).join(", ")}
                </td>
                <td className="mono">{r.cooldown_min}m</td>
                <td>
                  <button onClick={() => toggle(r)} className="mono" style={{
                    background: "none", border: "1px solid var(--line-dim)", cursor: "pointer",
                    padding: "1px 8px", fontSize: 10,
                    color: r.enabled ? "var(--green)" : "var(--ink-faint)",
                  }}>
                    {r.enabled ? "ENABLED" : "DISABLED"}
                  </button>
                </td>
                <td>
                  <button onClick={() => remove(r.id)} className="mono" style={{
                    background: "none", border: "none", cursor: "pointer", color: "var(--red)", fontSize: 10,
                  }}>
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      {/* Notifications */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.1em", color: "var(--ink-dim)", textTransform: "uppercase" }}>
          Notifications {unread > 0 && `(${unread} unread)`}
        </span>
        {unread > 0 && (
          <button onClick={markAllRead} className="mono" style={{ background: "none", border: "none", cursor: "pointer", color: "var(--cyan)", fontSize: 10 }}>
            mark all read
          </button>
        )}
      </div>
      {notifs.length === 0 ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>No notifications.</div>
      ) : (
        <div style={{ maxWidth: 760 }}>
          {notifs.map(n => (
            <div key={n.id} style={{
              display: "flex", justifyContent: "space-between", gap: 12,
              padding: "8px 0", borderBottom: "1px solid var(--line-dim)",
              opacity: n.read ? 0.6 : 1,
            }}>
              <div>
                <div style={{ fontSize: 12, color: n.severity === "critical" ? "var(--red)" : n.severity === "warning" ? "var(--amber)" : "var(--ink)" }}>
                  {n.title}
                </div>
                <div style={{ fontSize: 11, color: "var(--ink-dim)", marginTop: 2 }}>{n.body}</div>
              </div>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", flexShrink: 0 }}>
                {new Date(n.created_at).toLocaleTimeString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
