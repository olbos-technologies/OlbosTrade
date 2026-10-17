import React, { useState } from "react";
import { UNIVERSE } from "./universe";
import { useMarketSnapshots } from "./useMarketSnapshots";
import { MarketToolbar, chartHref } from "./MarketToolbar";

export function tileBg(pct: number | null): string {
  if (pct === null || pct === 0) return "var(--bg-3)";
  const alpha = .15 + Math.min(Math.abs(pct),3)/3*.35;
  return pct > 0 ? `rgba(34,197,94,${alpha.toFixed(2)})` : `rgba(239,68,68,${alpha.toFixed(2)})`;
}
export default function Heatmap() {
  const { snaps, loading, updatedAt, stale, failed, refresh } = useMarketSnapshots();
  const [group, setGroup] = useState("all");
  return <div className="markets-page">
    <MarketToolbar title="Watchlist heatmap" subtitle="Daily close-to-close change · equal-sized tiles · refreshes every 60s" {...{ updatedAt, loading, onRefresh:refresh }}>
      <select aria-label="Heatmap group" value={group} onChange={e=>setGroup(e.target.value)}><option value="all">All groups</option>{UNIVERSE.map(g=><option key={g.label}>{g.label}</option>)}</select>
    </MarketToolbar>
    <div className="heatmap-legend"><span>−3% or less</span><i aria-hidden="true" /><span>+3% or more</span><span>Neutral / unavailable shown without a directional tint</span></div>
    {(stale || failed > 0) && <p className="markets-notice">{stale ? "Refresh overdue. Values withheld." : `${failed} symbols unavailable.`}</p>}
    {UNIVERSE.filter(g=>group==="all" || group===g.label).map(g=><section key={g.label}><h2 className="markets-group-title">{g.label}</h2>
      <div className="market-tiles">{g.symbols.map(s=>{
        const snap=stale ? undefined : snaps[s.sym], pct=snap?.change_pct ?? null;
        return <a className="market-tile" href={chartHref(s.api ?? s.sym)} key={s.sym} style={{background:tileBg(pct)}} aria-label={`Open ${s.sym} chart`}>
          <div className="market-tile-top"><strong>{s.sym}</strong><span>{snap?.last_close?.toFixed(2) ?? "—"}</span></div>
          <div className="market-tile-name">{s.name}</div>
          <div className="market-tile-change">{pct===null ? "Unavailable" : `${pct===0 ? "" : pct>0 ? "▲ " : "▼ "}${Math.abs(pct).toFixed(2)}%`}</div>
        </a>;
      })}</div>
    </section>)}
  </div>;
}
