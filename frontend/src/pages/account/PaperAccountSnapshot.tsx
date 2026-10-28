import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, type BrokerPaperSnapshot, type PaperOrder } from "../../api/client";
import { Button } from "../../components/ui";

const actionStyle = { minHeight: 44 };
const grid: React.CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 12 };
const card: React.CSSProperties = { padding: 12, border: "1px solid var(--line-dim)", borderRadius: 8, minWidth: 0, overflowWrap: "anywhere" };
const numeric = (v: unknown): v is string => typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v));
const optionalNumber = (v: unknown) => v === null || numeric(v);

function valid(value: BrokerPaperSnapshot, id: string): boolean {
  if (!value || value.connection_id !== id || value.broker !== "alpaca" || value.environment !== "paper"
      || value.execution_enabled !== false || value.positions_reconciled !== false || value.snapshot_atomic !== false
      || typeof value.account_last4 !== "string" || !/^[a-f0-9]{4}$/i.test(value.account_last4)
      || ![value.collection_started_at,value.collection_completed_at,value.last_checked_at].every(v => Number.isFinite(Date.parse(v)))
      || !["verified","restricted","unavailable","identity_mismatch","reconnect_required"].includes(value.health)
      || !["active","reconnect_required"].includes(value.status)) return false;
  const sections = [value.balances, value.positions, value.open_orders];
  if (sections.some(s => !s || !["available", "incomplete", "unavailable"].includes(s.state)
      || (s.state === "unavailable" ? s.data !== null : s.data === null))) return false;
  if (value.positions.state === "incomplete" || value.balances.state === "incomplete") return false;
  const b = value.balances.data;
  if (value.balances.state !== "unavailable" && (!b || !/^[A-Z]{3}$/.test(b.currency)
      || ![b.equity,b.cash,b.buying_power].every(numeric))) return false;
  const p = value.positions.data;
  if (value.positions.state !== "unavailable" && (!Array.isArray(p) || !p.every(row => row && typeof row.symbol === "string"
      && numeric(row.qty) && ["long","short"].includes(row.side) && [row.avg_entry_price,row.current_price,row.market_value,row.unrealized_pl].every(optionalNumber)))) return false;
  const order = (row: PaperOrder, depth = 0): boolean => !!row && depth <= 2 && typeof row.id === "string"
    && (row.symbol === null || typeof row.symbol === "string") && typeof row.status === "string" && typeof row.type === "string"
    && (row.side === null || typeof row.side === "string")
    && [row.qty,row.filled_qty,row.limit_price,row.stop_price,row.notional].every(optionalNumber)
    && (row.legs === null || (Array.isArray(row.legs) && row.legs.every(leg => order(leg,depth+1))));
  return value.open_orders.state === "unavailable" || (Array.isArray(value.open_orders.data)
    && (value.open_orders.state !== "incomplete" || value.open_orders.data.length > 0)
    && value.open_orders.data.every(row => order(row)));
}

function money(value: string | null, currency: string | undefined): string {
  if (value === null || !currency) return "Unavailable";
  return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 }).format(Number(value));
}

function OrderCard({order}: {order: PaperOrder}) {
  return <article style={card}>
    <strong>{order.symbol || "Multi-leg / unspecified symbol"}</strong>
    <div>{order.side || "Side not supplied"} · {order.type} · {order.status}</div>
    <div>Quantity: {order.qty ?? "Not supplied"} · Filled: {order.filled_qty ?? "Unavailable"}</div>
    <div>Limit: {order.limit_price ?? "Not supplied"} · Stop: {order.stop_price ?? "Not supplied"}</div>
    {order.legs && order.legs.length > 0 && <details><summary>Order legs ({order.legs.length})</summary>
      {order.legs.map(leg => <OrderCard key={leg.id} order={leg}/>)}</details>}
  </article>;
}

export default function PaperAccountSnapshot({connectionId, onClose, onHealth}: {
  connectionId: string; onClose: () => void; onHealth: (snapshot: BrokerPaperSnapshot) => void;
}) {
  const [data, setData] = useState<BrokerPaperSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(Date.now());
  const generation = useRef(0);
  const healthCallback = useRef(onHealth);
  healthCallback.current = onHealth;
  const load = useCallback(async () => {
    const request = ++generation.current;
    setData(null); setError(""); setLoading(true);
    try {
      const result = await api.getBrokerPaperSnapshot(connectionId);
      if (generation.current !== request) return;
      if (!valid(result, connectionId)) throw new Error();
      setData(result); setClock(Date.now()); healthCallback.current(result);
    } catch {
      if (generation.current === request) setError("Account data unavailable. Refresh your connections and retry. Previous values have been cleared.");
    } finally { if (generation.current === request) setLoading(false); }
  }, [connectionId]);
  useEffect(() => { void load(); return () => { generation.current++; }; }, [load]);
  useEffect(() => { const timer = window.setInterval(() => setClock(Date.now()),5000); return () => window.clearInterval(timer); }, []);
  const currency = data?.balances.data?.currency;
  const stale = data ? Math.abs(clock - Date.parse(data.collection_started_at)) >= 60000 : false;

  return <section className="instrument-card" aria-label="Paper account data" style={{padding:16,maxWidth:920,lineHeight:1.65,marginTop:16}}>
    <h3 style={{marginTop:0}}>Paper account data · Read-only</h3>
    <p>These broker reads happen at different times. They do not confirm reconciliation, stop coverage, or permission to trade.</p>
    <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
      <Button style={actionStyle} onClick={() => void load()} disabled={loading}>Refresh account data</Button>
      <Button style={actionStyle} onClick={onClose}>Close account data</Button>
    </div>
    {loading && <p role="status">Reading the selected paper account…</p>}
    {error && <p role="alert" style={{color:"var(--amber)"}}>{error}</p>}
    {data && <>
      <p style={{color:stale?"var(--amber)":"var(--ink-dim)"}}>
        Account ••••{data.account_last4} · Retrieved {new Date(data.collection_completed_at).toLocaleString()}
        {stale && " · Stale — refresh before relying on these values"}
      </p>
      <h4>Balances</h4>
      {data.balances.state === "unavailable" || !data.balances.data ? <p role="status">Balances unavailable.</p> :
        <div style={grid}>{([ ["Equity",data.balances.data.equity], ["Cash",data.balances.data.cash], ["Buying power",data.balances.data.buying_power] ] as const).map(([label,value]) =>
          <div key={label} style={card}>{label}<br/><strong title={value}>{money(value,currency)}</strong></div>)}</div>}
      <h4>Positions</h4>
      {data.positions.state === "unavailable" || !data.positions.data ? <p role="status">Positions unavailable — holdings are unknown.</p> : data.positions.data.length === 0 ? <p>No open positions reported in this read.</p> : <>
        <p>Showing {Math.min(100,data.positions.data.length)} of {data.positions.data.length} reported positions.</p>
        <div style={grid}>{data.positions.data.slice(0,100).map(p => <article key={p.asset_id} style={card}>
          <strong>{p.symbol}</strong> · {p.side}<div>Quantity: {p.qty}</div>
          <div>Market value: {money(p.market_value,currency)}</div><div>Unrealized P/L: {money(p.unrealized_pl,currency)}</div>
          <div>Average entry: {p.avg_entry_price ?? "Unavailable"} · Current price: {p.current_price ?? "Unavailable"}</div>
        </article>)}</div>
      </>}
      <h4>Open-order query</h4>
      {data.open_orders.state === "incomplete" && <p role="status" style={{color:"var(--amber)"}}>Order limit reached. This list may be incomplete.</p>}
      {data.open_orders.state === "unavailable" || !data.open_orders.data ? <p role="status">Open orders unavailable — order coverage is unknown.</p> : data.open_orders.data.length === 0 ? <p>No open orders reported in this read.</p> : <>
        <p>Showing {Math.min(100,data.open_orders.data.length)} of {data.open_orders.data.length} returned orders. Nested legs may include other order statuses.</p>
        <div style={grid}>{data.open_orders.data.slice(0,100).map(o => <OrderCard key={o.id} order={o}/>)}</div>
      </>}
      <p style={{color:"var(--amber)"}}>Trading remains off for this connection.</p>
    </>}
  </section>;
}
