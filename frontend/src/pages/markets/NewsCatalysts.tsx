import React, { useEffect, useState } from "react";
import { UNIVERSE } from "./universe";
import { MarketToolbar, chartHref } from "./MarketToolbar";

export type NewsItem = {
  symbol: string; type: "news" | "filing"; payload: {title?: string; link?: string; published?: string};
  source: string; as_of: string; classification?: { impact?: string };
};
export function safeNewsLink(link?: string): string | null {
  try { const u=new URL(link || ""); return ["http:","https:"].includes(u.protocol) ? u.href : null; } catch { return null; }
}
export function groupNews(items: NewsItem[]) {
  const map=new Map<string,NewsItem & { symbols:string[] }>();
  for(const item of items) {
    let key=safeNewsLink(item.payload?.link);
    if(key) { const u=new URL(key); u.hash=""; [...u.searchParams.keys()].filter(k=>k.startsWith("utm_")).forEach(k=>u.searchParams.delete(k)); key=u.href; }
    else key=`${item.type}:${item.payload?.title || ""}:${item.as_of}`;
    const existing=map.get(key);
    if(existing) {
      if(!existing.symbols.includes(item.symbol)) existing.symbols.push(item.symbol);
      const rank:Record<string,number>={ low:1, moderate:2, high:3 };
      if((rank[item.classification?.impact || ""] || 0)>(rank[existing.classification?.impact || ""] || 0)) existing.classification=item.classification;
    } else map.set(key,{...item,symbols:[item.symbol]});
  }
  return [...map.values()].sort((a,b)=>(Date.parse(b.as_of)||0)-(Date.parse(a.as_of)||0));
}
export default function NewsCatalysts() {
  const [items,setItems]=useState<NewsItem[]>([]);
  const [loading,setLoading]=useState(true), [failed,setFailed]=useState(0);
  const [updatedAt,setUpdatedAt]=useState<number|null>(null), [revision,setRevision]=useState(0);
  const [query,setQuery]=useState(""), [kind,setKind]=useState("all");
  useEffect(()=>{
    let alive=true, busy=false;
    const load=async()=>{
      if(busy)return; busy=true; setLoading(true);
      const results=await Promise.all(UNIVERSE.flatMap(g=>g.symbols).map(async s=>{
        const symbol=s.api ?? s.sym;
        try {
          const r=await fetch(`/api/intel/classify/${encodeURIComponent(symbol)}?limit=5`,{signal:AbortSignal.timeout(12000)});
          if(!r.ok)throw new Error();
          const d=await r.json();
          if(d.error || !Array.isArray(d.items))throw new Error();
          return {ok:true,items:d.items.map((it:Omit<NewsItem,"symbol">)=>({...it,symbol:s.sym})) as NewsItem[]};
        }catch{return {ok:false,items:[] as NewsItem[]};}
      }));
      if(alive){setItems(results.flatMap(r=>r.items));setFailed(results.filter(r=>!r.ok).length);setUpdatedAt(results.some(r=>r.ok)?Date.now():null);setLoading(false);}
      busy=false;
    };
    load();const t=setInterval(load,60000);
    return()=>{alive=false;clearInterval(t);};
  },[revision]);
  const grouped=groupNews(items).filter(i=>(kind==="all" || i.type===kind) &&
    (i.payload?.title+" "+i.symbols.join(" ")+" "+i.source).toLowerCase().includes(query.toLowerCase())).slice(0,40);
  return <div className="markets-page">
    <MarketToolbar note="event times below" title="News & events" subtitle="Watchlist headlines and filings · importance classifies event type, not expected price direction" {...{updatedAt,loading,onRefresh:()=>setRevision(n=>n+1)}}>
      <input aria-label="Search news" placeholder="Search headline or symbol" value={query} onChange={e=>setQuery(e.target.value)} />
      <select aria-label="News type" value={kind} onChange={e=>setKind(e.target.value)}><option value="all">All types</option><option value="news">News</option><option value="filing">Filings</option></select>
    </MarketToolbar>
    {failed>0 && <p className="markets-notice" role="status">{failed} symbol feeds unavailable. Results may be incomplete.</p>}
    <p className="markets-meta" style={{color:"var(--ink-dim)",fontSize:12}}>Duplicates grouped across symbols · event times shown in your local timezone · up to 40 matching items</p>
    <div className="markets-table-wrap"><table className="markets-table"><thead><tr><th>Time</th><th>Symbols</th><th>Headline / source</th><th>Type</th><th>Event importance</th></tr></thead><tbody>
      {!loading && grouped.length===0 && <tr><td colSpan={5} className="markets-empty">{failed ? "News unavailable or no matching results from responding feeds." : "No matching recent items."}</td></tr>}
      {grouped.map((it,i)=>{
        const link=safeNewsLink(it.payload?.link), time=Date.parse(it.as_of);
        const importance=it.classification?.impact;
        return <tr key={link || i}><td>{Number.isFinite(time)?new Date(time).toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",timeZoneName:"short"}):"Time unavailable"}</td>
          <td><div className="news-tickers">{it.symbols.map(s=><a href={chartHref(s)} key={s}>{s}</a>)}</div></td>
          <td className="news-headline">{link ? <a href={link} target="_blank" rel="noopener noreferrer">{it.payload.title || "Untitled item"}</a> : it.payload?.title || "Untitled item"}
            <span className="news-source">{it.source || (link ? new URL(link).hostname : "Source unavailable")}</span></td>
          <td>{it.type}</td><td style={{color:importance==="high"?"var(--amber)":"var(--ink-dim)"}}>{importance || "Unclassified"}</td>
        </tr>;
      })}
    </tbody></table></div>
  </div>;
}
