import React,{useEffect,useRef,useState} from "react";
import {useTerminalNav} from "./TerminalNavContext";
const destinations=[['Command Center','dashboard'],['Markets','markets:chart'],['Signals','equity'],['Positions','trade:positions'],['Risk','risk:heat'],['Journal','journal'],['Performance','analytics'],['Data & Integrations','system:broker']] as const;
export default function WorkspaceSearch(){
 const nav=useTerminalNav(),input=useRef<HTMLInputElement>(null);
 const [query,setQuery]=useState('');
 const matches=query.trim()?destinations.filter(([label])=>label.toLowerCase().includes(query.trim().toLowerCase())):[];
 useEffect(()=>{const key=(e:KeyboardEvent)=>{const el=e.target as HTMLElement|null;if(((e.key==='/'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!el?.closest('input,textarea,select,[contenteditable]'))||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'))){e.preventDefault();input.current?.focus();}};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);},[]);
 const choose=(key:string)=>{nav(key);setQuery('');input.current?.blur();};
 return <form className="workspace-command-search" role="search" aria-label="Workspace commands" onSubmit={e=>{e.preventDefault();if(matches.length===1)choose(matches[0][1]);}}><label className="sr-only" htmlFor="workspace-command">Find a workspace</label><input ref={input} id="workspace-command" value={query} placeholder="Find workspace…  /" autoComplete="off" onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key==='Escape'){setQuery('');input.current?.blur();}}}/>{query.trim()&&<div className="workspace-command-results" aria-label="Workspace results">{matches.length?matches.map(([label,key])=><button type="button" key={key} onClick={()=>choose(key)}>{label} →</button>):<span>No matching workspace. Try Markets, Signals, Positions or Risk.</span>}</div>}</form>;
}
