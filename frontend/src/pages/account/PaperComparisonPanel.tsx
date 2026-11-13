import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, type BrokerOAuthConnection, type PaperComparison, type PaperComparisonObservation } from "../../api/client";
import PaperAccountSnapshot, { validPaperSnapshot } from "./PaperAccountSnapshot";
import { Button } from "../../components/ui";
import "./paper-comparison.css";

const roles = ["system", "hybrid"] as const;
const title = {system: "A · System exits", hybrid: "B · Hybrid exits"};
const stamp = (s: string) => Number.isFinite(Date.parse(s));
function validPair(p: PaperComparison): boolean {
  return !!p && typeof p.revision === "string" && typeof p.system_grant_id === "string"
    && typeof p.hybrid_grant_id === "string" && p.system_grant_id !== p.hybrid_grant_id && stamp(p.updated_at);
}
function validObservation(r: PaperComparisonObservation, p: PaperComparison): boolean {
  return !!r && r.execution_enabled === false && r.snapshot_atomic === false && validPair(r.comparison)
    && r.comparison.revision === p.revision && stamp(r.collection_started_at) && stamp(r.collection_completed_at)
    && roles.every(role => {
      const id = p[`${role}_grant_id`];
      const row = r.accounts?.[role];
      return r.comparison[`${role}_grant_id`] === id && !!row && (row.state === "observed"
        ? !!row.snapshot && validPaperSnapshot(row.snapshot, id)
        : ["unavailable", "changed"].includes(row.state) && row.snapshot === null);
    });
}

export default function PaperComparisonPanel({connections}: {connections: BrokerOAuthConnection[]}) {
  const [pair, setPair] = useState<PaperComparison | null>(null);
  const [system, setSystem] = useState("");
  const [hybrid, setHybrid] = useState("");
  const [busy, setBusy] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [observed, setObserved] = useState<PaperComparisonObservation | null>(null);
  const [inspect, setInspect] = useState<string | null>(null);
  const [clock, setClock] = useState(Date.now());
  const generation = useRef(0);
  const eligible = connections.filter(c => c.broker === "alpaca" && c.environment === "paper" && c.status === "active");
  const load = useCallback(async () => {
    const seq = ++generation.current;
    setBusy("load"); setError(""); setObserved(null); setInspect(null); setLoaded(false); setPair(null);
    try {
      const r = await api.paperComparison();
      if (generation.current !== seq) return;
      if (r.execution_enabled !== false || (r.comparison !== null && !validPair(r.comparison))) throw new Error();
      setPair(r.comparison); setSystem(r.comparison?.system_grant_id ?? ""); setHybrid(r.comparison?.hybrid_grant_id ?? ""); setLoaded(true);
    } catch { if (generation.current === seq) setError("Saved pairing unavailable. Reload before making changes."); }
    finally { if (generation.current === seq) setBusy(""); }
  }, []);
  // Clear observations when another account is disconnected in the parent.
  const connectionVersion = connections.map(c => `${c.id}:${c.status}`).join("|");
  useEffect(() => { void load(); return () => { generation.current++; }; }, [load, connectionVersion]);
  useEffect(() => { const t = window.setInterval(() => setClock(Date.now()), 5000); return () => window.clearInterval(t); }, []);
  const eligiblePair = eligible.some(c => c.id === system) && eligible.some(c => c.id === hybrid) && system !== hybrid;
  const dirty = !pair || pair.system_grant_id !== system || pair.hybrid_grant_id !== hybrid;
  const change = (role: "system" | "hybrid", id: string) => {
    setObserved(null); setInspect(null); setError("");
    (role === "system" ? setSystem : setHybrid)(id);
  };
  const save = async () => {
    const seq = ++generation.current;
    setBusy("save"); setError(""); setObserved(null); setInspect(null);
    try {
      const r = await api.savePaperComparison({system_grant_id:system, hybrid_grant_id:hybrid, expected_revision:pair?.revision ?? null});
      if (generation.current !== seq) return;
      if (r.execution_enabled !== false || !validPair(r.comparison) || r.comparison.system_grant_id !== system || r.comparison.hybrid_grant_id !== hybrid) throw new Error();
      setPair(r.comparison);
    } catch {
      if (generation.current === seq) { setLoaded(false); setError("Pairing could not be saved. Reload to check the latest version; no test was started."); }
    } finally { if (generation.current === seq) setBusy(""); }
  };
  const refresh = async () => {
    if (!pair) return;
    const seq = ++generation.current;
    setBusy("observe"); setError(""); setObserved(null); setInspect(null);
    try {
      const r = await api.observePaperComparison(pair.revision);
      if (generation.current !== seq) return;
      if (!validObservation(r, pair)) throw new Error();
      setObserved(r); setClock(Date.now());
    } catch {
      if (generation.current === seq) { setLoaded(false); setError("Comparison data unavailable or pairing changed. Previous values cleared. Reload the pairing and retry."); }
    } finally { if (generation.current === seq) setBusy(""); }
  };
  return <section className="paper-comparison" aria-label="Paper comparison">
    <div className="paper-comparison-heading"><div><small>PAPER RESEARCH</small><h3>System vs Hybrid</h3></div><span className="paper-comparison-status">Setup only · Trading off</span></div>
    <p>Compare two independent Alpaca paper accounts. These roles save your intended exit policies; they do not start a test or change Autopilot.</p>
    <ol>
      <li>In Alpaca’s account menu, choose <strong>Open New Paper Account</strong> and create two accounts with the same starting balance.</li>
      <li>Sign in with Alpaca above once for each paper account. Select a different account on the second authorization.</li>
      <li>Assign the accounts below. Keep entries, sizing, and risk rules the same when testing exits.</li>
    </ol>
    <div className="paper-comparison-grid">
      {roles.map(role => <label key={role}>{title[role]}
        <span>{role === "system" ? "Intended: system TP and SL" : "Intended: manual profits; system TP and SL otherwise"}</span>
        <select aria-label={title[role]} disabled={!loaded || !!busy} value={role === "system" ? system : hybrid} onChange={e => change(role, e.target.value)}>
          <option value="">Select paper account</option>
          {eligible.map(c => <option key={c.id} value={c.id}>Alpaca · ••••{c.account_last4}</option>)}
          {(role === "system" ? system : hybrid) && !eligible.some(c => c.id === (role === "system" ? system : hybrid)) && <option value={role === "system" ? system : hybrid}>Saved account unavailable — reconnect or replace</option>}
        </select>
      </label>)}
    </div>
    {loaded && eligible.length < 2 && <p role="status">Connect two active paper accounts to save a pairing. The platform’s IBKR account is separate.</p>}
    {system && system === hybrid && <p role="status">Choose different accounts. Two views of one broker account are one portfolio.</p>}
    <div className="paper-comparison-actions">
      <Button disabled={!loaded || !!busy || !eligiblePair || !dirty} onClick={() => void save()}>Save pairing</Button>
      <Button disabled={!loaded || !!busy || !pair || dirty || !eligiblePair} onClick={() => void refresh()}>Refresh comparison data</Button>
      <Button disabled={!!busy} onClick={() => void load()}>Reload saved pairing</Button>
    </div>
    {busy && <p role="status">{busy === "observe" ? "Reading each account separately…" : busy === "save" ? "Saving account roles…" : "Loading saved pairing…"}</p>}
    {error && <p role="alert">{error}</p>}
    {loaded && pair && <p className="paper-comparison-note">Pairing saved {new Date(pair.updated_at).toLocaleString()}. No orders submitted.</p>}
    {observed && <>
      <p className="paper-comparison-note">Retrieved {new Date(observed.collection_completed_at).toLocaleString()} · Separate reads, not a simultaneous snapshot.
        {Math.abs(clock - Date.parse(observed.collection_started_at)) >= 60000 && <strong> Stale — refresh before using these values.</strong>}</p>
      <div className="paper-comparison-grid">{roles.map(role => {
        const row = observed.accounts[role]; const snap = row.snapshot; const b = snap?.balances.data;
        return <article key={role} className="paper-comparison-account">
          <h4>{title[role]}</h4><small>Alpaca paper {snap ? `· ••••${snap.account_last4}` : ""}</small>
          {!snap ? <p role="status">{row.state === "changed" ? "Connection changed. Refresh your connections." : "Account unavailable. Reconnect or replace it."} Balances, holdings, and orders are unknown.</p> : <>
            <dl>{([ ["Equity", b?.equity], ["Cash", b?.cash], ["Buying power", b?.buying_power] ] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value && b ? new Intl.NumberFormat(undefined, {style:"currency",currency:b.currency}).format(Number(value)) : "Unavailable"}</dd></div>)}
              <div><dt>Positions</dt><dd>{snap.positions.state === "available" ? snap.positions.data!.length : "Unknown"}</dd></div>
              <div><dt>Open-order query</dt><dd>{snap.open_orders.state === "unavailable" ? "Unknown" : `${snap.open_orders.data!.length}${snap.open_orders.state === "incomplete" ? "+ · incomplete" : ""}`}</dd></div>
            </dl>
            <Button onClick={() => setInspect(observed.comparison[`${role}_grant_id`])}>Inspect account {role === "system" ? "A" : "B"}</Button>
          </>}
        </article>;
      })}</div>
    </>}
    {inspect && <PaperAccountSnapshot key={inspect} connectionId={inspect} onClose={() => setInspect(null)} onHealth={() => setObserved(null)} />}
    <details className="paper-comparison-notes"><summary>What is needed to run a fair automated test?</summary>
      <p>Account-specific order routing, broker reconciliation, protective exits, matched entry records, and manual/system exit attribution are still required. Connected accounts are read-only today.</p>
      <p>Equity and cash are broker observations, not strategy returns. Deposits, resets, and unrelated holdings can change them. No winner, win rate, or performance difference is calculated here.</p>
      <p><a href="https://docs.alpaca.markets/us/docs/paper-trading" target="_blank" rel="noreferrer">Alpaca paper account guide ↗</a></p>
    </details>
  </section>;
}
