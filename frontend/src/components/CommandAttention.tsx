import React from "react";
import { useTerminalNav } from "./TerminalNavContext";
import "../pages/markets/Markets.css";

export function riskWarnings(guard: any, state: any): string[] {
  const result:string[]=[];
  for(const period of ["daily","weekly"]) {
    const raw=guard?.[period+"_loss_pct"] ?? state?.[period+"_loss_pct"];
    const limit=guard?.["max_"+period+"_loss_pct"] ?? state?.["max_"+period+"_loss_pct"];
    if(typeof raw==="number" && Number.isFinite(raw) && typeof limit==="number" && limit>0 &&
       Math.max(0,-raw)>=limit) result.push(`${period==="daily"?"Daily":"Weekly"} loss limit exceeded: ${(Math.max(0,-raw)*100).toFixed(1)}% / ${(limit*100).toFixed(1)}%.`);
  }
  if(guard?.trading_allowed===false) result.push(guard.reason || "New entries are suspended by guardrails.");
  return result;
}
export default function CommandAttention({ guard, state, untracked, portfolioUnavailable }: {
  guard:any; state:any; untracked:number; portfolioUnavailable:boolean;
}) {
  const nav=useTerminalNav();
  const warnings=riskWarnings(guard,state);
  return <section className="command-attention" aria-label="Needs attention">
    <h2>Needs attention</h2>
    {warnings.map((w,i)=><p className="attention-alert" key={i}>{w}</p>)}
    {!guard && <p className="attention-alert">Entry-check status unavailable. Execution mode alone does not confirm permission to trade.</p>}
    {portfolioUnavailable && <p className="attention-alert">Portfolio read unavailable. Position counts may be incomplete.</p>}
    {untracked>0 && <p className="attention-alert">{untracked} untracked broker holdings require review. Managed-position metrics do not describe all broker exposure.</p>}
    {warnings.length===0 && guard?.trading_allowed===true && <p>Guardrails currently report entries allowed. Each order still requires its own execution checks.</p>}
    <button onClick={()=>nav("risk")}>Review portfolio &amp; risk →</button>
  </section>;
}
