import React,{useEffect,useState} from "react";
import {statusLabelForPage} from "../utils/navLabels";
import {NAV_MODEL_V2} from "../utils/navModels";
import {useTerminalNav} from "./TerminalNavContext";
import {useResource,readMode,readBroker,readKill,readPending,readCoverage,readGuard} from "../pages/MobileHome";
export default function WorkspaceContext({page}:{page:string}){
 const nav=useTerminalNav(),[now,setNow]=useState(Date.now());
 useEffect(()=>{const t=setInterval(()=>setNow(Date.now()),5000);return()=>clearInterval(t);},[]);
 const mode=useResource(readMode,now),broker=useResource(readBroker,now),kill=useResource(readKill,now),pending=useResource(readPending,now),coverage=useResource(readCoverage,now),guard=useResource(readGuard,now);
 const reliable=coverage.value?.available===true&&coverage.value.source==='refreshed'&&coverage.value.unprotected_is_reliable===true&&!coverage.value.positions_error&&Array.isArray(coverage.value.positions_without_stop);
 const missing=reliable?coverage.value!.positions_without_stop!.length:null;
 const checked=(at:number|null,fresh:boolean)=>fresh&&at?`${Math.max(0,Math.floor((now-at)/1000))}s ago`:'Unavailable';
 const exceptions:{label:string;detail:string;route:string}[]=[];
 if(!broker.value||broker.value.status!=='connected')exceptions.push({label:broker.value?'Broker disconnected':'Broker unverified',detail:'Review connection before relying on account data.',route:'system:broker'});
 if(!mode.value)exceptions.push({label:'Execution mode unverified',detail:'The current mode could not be confirmed.',route:'trade:overview'});
 if(!kill.value||kill.value.engaged)exceptions.push({label:kill.value?'Kill switch engaged':'Kill switch unverified',detail:'Review execution and broker exposure.',route:'risk:heat'});
 if(missing===null||missing>0)exceptions.push({label:missing===null?'Protection unverified':`${missing} positions without a reported stop`,detail:'Requires a refreshed order-book read.',route:'trade:positions'});
 if(!guard.value||!guard.value.trading_allowed)exceptions.push({label:guard.value?'Entry checks blocked':'Entry checks unverified',detail:'Review Risk before submitting new entries.',route:'risk:heat'});
 if(!pending.value||pending.value.length>0)exceptions.push({label:pending.value?`${pending.value.length} approvals awaiting review`:'Approval queue unavailable',detail:'Review the current queue before acting.',route:'trade:copilot'});
 return <aside className="workspace-context" aria-label="Workspace context">
  <section><span className="context-eyebrow">Current workspace</span><h2>Workspace brief</h2><span className="context-route">{page==='dashboard'?'Command Center':statusLabelForPage(page,NAV_MODEL_V2)}</span><p>Point-in-time readings. Trading permissions and risk limits remain enforced separately.</p></section>
  <section><span className="context-eyebrow">Session readings</span><dl><div><dt>Execution</dt><dd>{mode.value?.mode.toUpperCase()||'UNVERIFIED'}</dd></div><div><dt>Environment</dt><dd>{broker.value?(broker.value.paper_mode?'PAPER':'LIVE'):'UNVERIFIED'}</dd></div><div><dt>Broker</dt><dd>{broker.value?.status.toUpperCase()||'UNVERIFIED'}</dd></div><div><dt>Kill switch</dt><dd>{kill.value?(kill.value.engaged?'ENGAGED':'CLEAR'):'UNVERIFIED'}</dd></div><div><dt>Entry checks</dt><dd>{guard.value?(guard.value.trading_allowed?'ALLOWED':'BLOCKED'):'UNVERIFIED'}</dd></div><div><dt>Approvals</dt><dd>{pending.value?pending.value.length:'Unavailable'}</dd></div></dl></section>
  <section><span className="context-eyebrow">Needs attention</span>{exceptions.length?exceptions.map(e=><button className="context-exception" key={e.label} onClick={()=>nav(e.route)}><strong>{e.label} →</strong><small>{e.detail}</small></button>):<p>No exceptions reported by these reads. This does not verify every trading prerequisite.</p>}</section>
  <section><span className="context-eyebrow">Last checked</span><dl><div><dt>Mode</dt><dd>{checked(mode.at,mode.fresh)}</dd></div><div><dt>Broker</dt><dd>{checked(broker.at,broker.fresh)}</dd></div><div><dt>Protection</dt><dd>{checked(coverage.at,coverage.fresh)}</dd></div></dl><button className="context-link" onClick={()=>nav('trade:overview')}>Open Trade Desk →</button><br/><button className="context-link" onClick={()=>nav('risk:heat')}>Review risk →</button></section>
 </aside>;
}
