import React, {useEffect, useState} from "react";
import {AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer} from "recharts";
import {api} from "../api/client";
import {useTerminalNav} from "../components/TerminalNavContext";
import {riskWarnings} from "../components/CommandAttention";
import {useResource, json, readSummary, readBroker, readGuard, readRiskState, readCoverage, readMode, readKill, readPositions, readPending, money} from "./MobileHome";
import "./DesktopCommandCenter.css";

export const finiteNumber = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
type ClosedTrade = {status?: string; exit_date?: string; pnl?: number};
export function closedPnlPoints(trades: ClosedTrade[]) {
  if(trades.some(t => t.status !== "closed" || !finiteNumber(t.pnl) || !Number.isFinite(Date.parse(t.exit_date || "")))) throw new Error("Incomplete trade history");
  let cumulative=0;
  return [...trades].sort((a,b)=>Date.parse(a.exit_date!)-Date.parse(b.exit_date!)).map(t=>{
    cumulative+=t.pnl!;
    if(!Number.isFinite(cumulative)) throw new Error("Invalid cumulative P&L");
    return {at:Date.parse(t.exit_date!),value:cumulative};
  });
}
const readHistory = async()=>{
  const d=await api.getTradeHistory({limit:500,status:"closed"}) as {trades:ClosedTrade[]};
  if(!Array.isArray(d?.trades)) throw new Error("History unavailable");
  return closedPnlPoints(d.trades);
};
const readBuyingPower = async()=>{
  const d=await json<{portfolio?:{buying_power?:number}}>("/api/paper-trade/portfolio");
  return finiteNumber(d.portfolio?.buying_power) ? d.portfolio!.buying_power! : null;
};
type Snapshot = {symbol:string;price:number|null;change:number|null};
const readPulse=()=>Promise.all(['SPY','QQQ','IWM','NVDA','TLT'].map(async (symbol):Promise<Snapshot>=>{
  try {const d=await json<{last_close?:number;change_pct?:number}>(`/api/market/snapshot/${symbol}`);return {symbol,price:finiteNumber(d.last_close)?d.last_close:null,change:finiteNumber(d.change_pct)?d.change_pct:null};}
  catch{return {symbol,price:null,change:null};}
}));
type CalendarEvent = {name:string;date:string;severity?:string;source?:string;kind?:string};
const readEvents=async()=>{
 const d=await json<{events:CalendarEvent[]}>('/api/intel/calendar?days_ahead=45');
 if(!Array.isArray(d.events)||d.events.some(e=>!e||typeof e.name!=='string'||typeof e.date!=='string'))throw new Error('Calendar unavailable');
 return d.events;
};
function Metric({label,value,note,tone}:{label:string;value:string;note:string;tone?:string}) {
  return <div className="dc-metric"><span>{label}</span><strong style={{color:tone}}>{value}</strong><small>{note}</small></div>;
}
export default function DesktopCommandCenter() {
  const nav=useTerminalNav();
  const [now,setNow]=useState(Date.now());
  const [range,setRange]=useState("ALL");
  useEffect(()=>{const t=setInterval(()=>setNow(Date.now()),5000);return()=>clearInterval(t);},[]);
  const summary=useResource(readSummary,now), broker=useResource(readBroker,now), mode=useResource(readMode,now);
  const positions=useResource(readPositions,now), pending=useResource(readPending,now), coverage=useResource(readCoverage,now);
  const kill=useResource(readKill,now), guard=useResource(readGuard,now), risk=useResource(readRiskState,now);
  const history=useResource(readHistory,now), buyingPower=useResource(readBuyingPower,now);
  const pulse=useResource(readPulse,now), events=useResource(readEvents,now);
  const resources=[summary,broker,mode,positions,pending,coverage,kill,guard,risk,history,buyingPower,pulse,events];
  const refresh=()=>{resources.forEach(r=>r.refresh());setNow(Date.now());};
  const reliableCoverage=coverage.value?.available===true && coverage.value.source==="refreshed" && coverage.value.unprotected_is_reliable===true && !coverage.value.positions_error && Array.isArray(coverage.value.positions_without_stop);
  const missingStops=reliableCoverage ? coverage.value!.positions_without_stop! : null;
  const state=risk.value?.state;
  const loss=state?.daily_loss_pct, limit=state?.max_daily_loss_pct;
  const used=!state?.broker_error && finiteNumber(loss)&&loss>=0&&finiteNumber(limit)&&limit>0 ? loss/limit*100 : null;
  const warnings=riskWarnings(guard.value,state);
  const latest=history.value?.[history.value.length-1]?.at;
  const days:Record<string,number>={"1W":7,"1M":30,"3M":90};
  const points=history.value?.filter(p=>range==="ALL" || (latest!==undefined&&p.at>=latest-days[range]*86400000))??[];
  const halted=kill.value?.engaged===true;
  const checked=(at:number|null,fresh:boolean)=>at&&fresh?`Read ${Math.max(0,Math.floor((now-at)/1000))}s ago`:"Unavailable or stale";
  const modeText=halted?"HALTED":mode.value?.mode.toUpperCase()||"UNVERIFIED";
  const pnlTone=(n:unknown)=>finiteNumber(n)?n<0?"var(--red)":"var(--green)":"var(--ink-faint)";
  return <div className="desktop-command-center" data-testid="desktop-command-center">
    <header className="dc-heading"><div><span className="design-eyebrow">YOUR TRADING DAY</span><h1>Command Center</h1></div><div className="dc-actions"><button onClick={refresh} aria-label="Refresh command center">↻ Refresh</button><button className="dc-primary" onClick={()=>nav("trade:overview")}>Open Trade Desk →</button></div></header>
    {(halted||warnings.length>0)&&<div className="dc-warning" role="status">{halted&&<strong>Kill switch engaged. Verify broker exposure. </strong>}{warnings.join(" · ")} <button onClick={()=>nav("risk")}>Review risk →</button></div>}
    <section className="dc-metrics" aria-label="Account overview">
      <Metric label={broker.value?.paper_mode===true?"Paper account value":broker.value?.paper_mode===false?"Live account value":"Broker account value"} value={money(summary.value?.total_equity)} note={checked(summary.at,summary.fresh)}/>
      <Metric label="Olbos day P&L" value={money(summary.value?.day_pnl,true)} tone={pnlTone(summary.value?.day_pnl)} note="Olbos trade results"/>
      <Metric label="Olbos total P&L" value={money(summary.value?.total_pnl,true)} tone={pnlTone(summary.value?.total_pnl)} note="May differ from broker holdings"/>
      <Metric label="Buying power" value={money(buyingPower.value)} note={checked(buyingPower.at,buyingPower.fresh)}/>
      <div className="dc-metric"><span>Daily loss budget used</span><strong>{used===null?"—":`${used.toFixed(1)}%`}</strong>{used!==null&&<progress aria-label="Daily loss budget used" value={Math.min(used,100)} max={100}/>}<small>{used===null?"Unavailable":`${((loss as number)*100).toFixed(2)}% loss / ${((limit as number)*100).toFixed(2)}% limit`}</small></div>
    </section>
    <div className="dc-main-grid">
      <section className="dc-panel dc-chart" aria-label="Olbos closed-trade performance">
        <header><h2><i>01</i> Olbos closed-trade P&L</h2><div className="dc-ranges" role="group" aria-label="Performance range">{["1W","1M","3M","ALL"].map(r=><button key={r} aria-pressed={range===r} onClick={()=>setRange(r)}>{r}</button>)}</div></header>
        <div className="dc-chart-caption"><span>Cumulative realized P&L · up to 500 returned trades</span><strong style={{color:pnlTone(points[points.length-1]?.value)}}>{money(points[points.length-1]?.value,true)}</strong></div>
        <div className="dc-chart-body">{history.value===null?<div className="dc-empty" role="status">{history.loading?"Loading trade history…":"Trade performance unavailable. Refresh to retry."}</div>:points.length===0?<div className="dc-empty">No closed trades in this range.</div>:<ResponsiveContainer width="100%" height="100%"><AreaChart data={points} margin={{top:16,right:24,bottom:8,left:16}}><CartesianGrid stroke="#23374e" strokeDasharray="3 5"/><XAxis dataKey="at" type="number" domain={["dataMin","dataMax"]} tickFormatter={n=>new Date(n).toLocaleDateString(undefined,{month:"short",day:"numeric"})} tick={{fill:"#adbed4",fontSize:11}}/><YAxis tick={{fill:"#adbed4",fontSize:11}} tickFormatter={n=>`$${n}`} width={70}/><Tooltip labelFormatter={n=>new Date(Number(n)).toLocaleString()} formatter={(v:number)=>[money(v,true),"Cumulative realized P&L"]} contentStyle={{background:"#101b29",border:"1px solid #2b4059",color:"#eef3fa"}}/><Area type="linear" dataKey="value" stroke="#39ce9a" fill="#39ce9a" fillOpacity={.1} isAnimationActive={false} dot={points.length===1}/></AreaChart></ResponsiveContainer>}</div>
        <p className="dc-footnote">Trade history reconstruction, not broker account equity or intraday marks. Range buttons show recent dates within the returned history; cumulative values retain their history baseline.</p>
      </section>
      <section className="dc-panel dc-execution" aria-label="Execution status"><header><h2><i>02</i> Execution status</h2></header><div className="dc-execution-body"><small>ACTIVE MODE</small><div className="dc-mode"><strong>{modeText}</strong><span>{broker.value?`${broker.value.broker.toUpperCase()} · ${broker.value.paper_mode?"PAPER":"LIVE"}`:"BROKER UNAVAILABLE"}</span></div><p>{halted?"Trading halted. Verify broker positions and working orders.":!mode.value?"Execution mode could not be confirmed.":mode.value.mode==="manual"?"Signals only. No automated entries.":mode.value.mode==="copilot"?"Trades require approval before submission.":"Automatic execution enabled within existing controls."}</p><dl>
        <div><dt>Broker connection</dt><dd>{broker.value?.status.toUpperCase()||"UNVERIFIED"}</dd></div>
        <div><dt>Stop coverage</dt><dd>{missingStops===null?"UNVERIFIED":missingStops.length?`${missingStops.length} MISSING`:"No missing stops reported"}</dd></div>
        <div><dt>Kill switch</dt><dd>{kill.value?kill.value.engaged?"ENGAGED":"CLEAR":"UNVERIFIED"}</dd></div>
        <div><dt>Pending approvals</dt><dd>{pending.value?pending.value.length:"UNAVAILABLE"}</dd></div>
        <div><dt>Entry checks</dt><dd>{guard.value?guard.value.trading_allowed?"ALLOWED":"BLOCKED":"UNVERIFIED"}</dd></div>
      </dl><small>{checked(mode.at,mode.fresh)} · {mode.value?.persistence==="confirmed"?"mode saved":"saved mode unconfirmed"}</small><button className="dc-primary" onClick={()=>nav("trade:overview")}>Manage execution →</button><button onClick={()=>nav("risk")}>Review protection →</button></div></section>
    </div>
    <section className="dc-panel dc-positions" aria-label="Open positions"><header><h2><i>03</i> Open positions <small>{positions.value?positions.value.length:"—"}</small></h2><button onClick={()=>nav("trade:positions")}>View all →</button></header><div className="dc-table-scroll" role="region" aria-label="Holdings table" tabIndex={0}><table><thead><tr>{["Symbol / strategy","Quantity","Average cost","Unrealized P&L","Tracking","Stop coverage"].map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>{positions.value===null?<tr><td colSpan={6}>{positions.loading?"Loading positions…":"Positions unavailable. Refresh to retry."}</td></tr>:positions.value.length===0?<tr><td colSpan={6}>No open positions reported.</td></tr>:positions.value.map((p,i)=><tr key={`${p.symbol}-${i}`}><td><strong>{p.symbol||"Unknown symbol"}</strong><small>{p.strategy||p.option_type||"Position"}</small></td><td>{finiteNumber(p.quantity)?p.quantity:"—"}</td><td>{money(p.avg_cost)}</td><td style={{color:pnlTone(p.unrealized_pnl)}}>{money(p.unrealized_pnl,true)}</td><td>{p.tracked===false?"Untracked broker holding":p.tracked===true?"Olbos tracked":"Unknown"}</td><td>{missingStops===null||!p.symbol?"Unverified":missingStops.some(s=>s.ticker===p.symbol)?"Missing stop":"No missing stop reported"}</td></tr>)}</tbody></table></div><p className="dc-footnote">{checked(positions.at,positions.fresh)} · Broker holdings and Olbos trade P&L may cover different positions. Protection is a separate read.</p></section>
    <div className="dc-lower-grid">
      <section className="dc-panel" aria-label="Market pulse"><header><h2><i>04</i> Market pulse</h2><button onClick={()=>nav("markets:chart")}>Explore →</button></header><div className="dc-pulse">{pulse.value?pulse.value.map(p=><div key={p.symbol}><strong>{p.symbol}</strong><span>{money(p.price)}</span><b style={{color:pnlTone(p.change)}}>{p.change===null?"Unavailable":`${p.change>=0?"+":""}${p.change.toFixed(2)}%`}</b></div>):<p>Market snapshots unavailable.</p>}</div><p className="dc-footnote">Last-close snapshots, not streaming quotes. Missing prices remain unavailable.</p></section>
      <section className="dc-panel" aria-label="Event risk"><header><h2><i>05</i> Event risk</h2><button onClick={()=>nav("lab:intel")}>Calendar →</button></header>{events.value===null?<p className="dc-calendar-state" role="status">{events.loading?"Loading event calendar…":"Event calendar unavailable. No event count confirmed."}</p>:events.value.length===0?<p className="dc-calendar-state">No events returned for the next 45 days.</p>:<div className="dc-events">{events.value.slice(0,5).map((e,i)=><div key={`${e.date}-${e.name}-${i}`}><time>{e.date}</time><span><strong>{e.name}</strong><small>{e.source||"Source unavailable"}</small></span><b>{e.severity?.replace(/_/g," ")||"Unclassified"}</b></div>)}</div>}<p className="dc-footnote">{checked(events.at,events.fresh)} · 45-day calendar window. Event times are shown only when supplied.</p></section>
    </div>
    <section className="dc-review-strip" aria-label="Pending signals"><span><strong>{pending.value?`${pending.value.length} approvals awaiting review`:"Approval queue unavailable"}</strong><small>Each approval is checked again before execution.</small></span><button className="dc-primary" onClick={()=>nav("trade:copilot")}>Review queue →</button></section>
  </div>;
}
