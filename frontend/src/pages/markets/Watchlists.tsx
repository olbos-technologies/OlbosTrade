import React, { useState } from "react";
import { UNIVERSE, changeColor } from "./universe";
import { useMarketSnapshots } from "./useMarketSnapshots";
import { MarketToolbar, chartHref } from "./MarketToolbar";

export default function Watchlists() {
  const { snaps, loading, updatedAt, stale, failed, refresh } = useMarketSnapshots();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("group");
  const [onlySaved, setOnlySaved] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [saved, setSaved] = useState<string[]>(() => {
    try { const v = JSON.parse(localStorage.getItem("olbos.market.favorites") || "[]"); return Array.isArray(v) ? v.filter(x => typeof x === "string") : []; } catch { return []; }
  });
  const toggle = (sym: string) => {
    const next = saved.includes(sym) ? saved.filter(s => s !== sym) : [...saved, sym];
    setSaved(next);
    try { localStorage.setItem("olbos.market.favorites", JSON.stringify(next)); setSaveError(false); } catch { setSaveError(true); }
  };
  const groups = UNIVERSE.map(g => ({ ...g, symbols: g.symbols.filter(s =>
    (!onlySaved || saved.includes(s.sym)) && (s.sym + " " + s.name).toLowerCase().includes(query.toLowerCase())) }));
  const rows = groups.flatMap(g => g.symbols);
  if (sort !== "group") rows.sort((a,b) => {
    if (sort === "symbol" || stale) return a.sym.localeCompare(b.sym);
    const av = snaps[a.sym]?.change_pct, bv = snaps[b.sym]?.change_pct;
    if (av == null) return bv == null ? a.sym.localeCompare(b.sym) : 1;
    if (bv == null) return -1;
    return sort === "gainers" ? bv-av : av-bv;
  });
  const renderRow = (s: typeof rows[number]) => {
    const d = stale ? undefined : snaps[s.sym], pct = d?.change_pct;
    const delta = d?.last_close != null && d.prev_close != null ? d.last_close-d.prev_close : null;
    return <tr key={s.sym}>
      <td><button className="watchlist-favorite" aria-label={`Save ${s.sym}`} aria-pressed={saved.includes(s.sym)} onClick={() => toggle(s.sym)}>{saved.includes(s.sym) ? "★" : "☆"}</button></td>
      <td><a className="ticker-link" href={chartHref(s.api ?? s.sym)}>{s.sym}</a></td><td>{s.name}</td>
      <td className="markets-number">{d?.last_close?.toFixed(2) ?? "—"}</td>
      <td className="markets-number">{d?.prev_close?.toFixed(2) ?? "—"}</td>
      <td className="markets-number">{delta == null ? "—" : `${delta >= 0 ? "+" : ""}${delta.toFixed(2)}`}</td>
      <td className="markets-number" style={{ color:changeColor(pct ?? null) }}>{pct == null ? "—" : `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(2)}%`}</td>
    </tr>;
  };
  return <div className="markets-page">
    <MarketToolbar title="Watchlists" subtitle="Closing-price snapshots · ETF proxies are labelled · refreshes every 60s" {...{ updatedAt, loading, onRefresh:refresh }}>
      <input aria-label="Search watchlist" placeholder="Search symbol or name" value={query} onChange={e=>setQuery(e.target.value)} />
      <select aria-label="Sort watchlist" value={sort} onChange={e=>setSort(e.target.value)}><option value="group">By group</option><option value="symbol">Symbol A–Z</option><option value="gainers">Biggest gainers</option><option value="decliners">Biggest decliners</option></select>
      <button aria-pressed={onlySaved} onClick={()=>setOnlySaved(v=>!v)}>{onlySaved ? "Saved only" : "All symbols"}</button>
    </MarketToolbar>
    {(stale || failed > 0) && <p className="markets-notice" role="status">{stale ? "Snapshot refresh overdue. Values withheld until refreshed." : `${failed} symbols unavailable. Missing prices are not zero returns.`}</p>}
    {stale && (sort === "gainers" || sort === "decliners") && <p className="markets-notice">Return sorting is unavailable while snapshots are stale; symbols are shown alphabetically.</p>}
    {saveError && <p className="markets-notice">Favorites apply for this visit but could not be saved on this device.</p>}
    <div className="markets-table-wrap"><table className="markets-table"><thead><tr><th>Save</th><th>Symbol</th><th>Name</th><th className="markets-number">Last close</th><th className="markets-number">Previous close</th><th className="markets-number">Change $</th><th className="markets-number">Change %</th></tr></thead><tbody>
      {rows.length === 0 ? <tr><td colSpan={7} className="markets-empty">No symbols match your filters.</td></tr>
        : sort === "group" ? groups.filter(g=>g.symbols.length).map(g=><React.Fragment key={g.label}><tr className="markets-group"><td colSpan={7}>{g.label}</td></tr>{g.symbols.map(renderRow)}</React.Fragment>) : rows.map(renderRow)}
    </tbody></table></div>
  </div>;
}
