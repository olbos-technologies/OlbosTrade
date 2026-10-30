import React from "react";
import {BOTTOM_NAV_ITEMS} from "./MobileBottomNav";
import KillSwitchButton from "./KillSwitchButton";
export default function CompactWorkspaceNav({active,onNav,onMore}:{active:string;onNav:(key:string)=>void;onMore:()=>void}){
 const isActive=(key:string,matches?:string[])=>active===key||!!matches?.includes(active);
 const core=BOTTOM_NAV_ITEMS.some(i=>isActive(i.key,i.matches));
 const risk=active==="risk"||active.startsWith("risk:")||active==="guardrails";
 return <nav className="compact-workspace-nav" aria-label="Workspace navigation">
  <div>{BOTTOM_NAV_ITEMS.map(i=><button key={i.key} type="button" aria-label={i.label} aria-current={isActive(i.key,i.matches)?"page":undefined} onClick={()=>onNav(i.key)}>{i.icon}<span>{i.label}</span></button>)}
   <button type="button" aria-label="Risk" aria-current={risk?"page":undefined} onClick={()=>onNav('risk:heat')}><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M12 3 22 21H2L12 3Z M12 9v5 M12 17h.01"/></svg><span>Risk</span></button>
   <button type="button" aria-label="More" aria-current={!core&&!risk?"page":undefined} aria-expanded={false} onClick={onMore}><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M4 6h16 M4 12h16 M4 18h16"/></svg><span>More</span></button>
  </div>
  <div className="compact-workspace-nav__halt"><KillSwitchButton variant="sidebar" expanded={false}/></div>
 </nav>;
}
