/**
 * Sector Rotation — the 11 GICS sector ETFs ranked by trailing return
 * (1D/1W/1M/3M), with a rank-change indicator derived server-side from the
 * same bars fetch (see backend/app/services/sector_rotation_engine.py).
 * MASTER_SPEC.md lists this page under Markets with no further design
 * detail — this ranking-table shape was confirmed with the user.
 */
import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import { changeColor } from "./universe";
import { MarketToolbar, chartHref } from "./MarketToolbar";

const TIMEFRAME_LABELS = ["1D", "1W", "1M", "3M"] as const;

interface SectorRow {
  ticker: string;
  name: string;
  returns: Record<string, number | null>;
  rank: number | null;
  prior_rank: number | null;
  rank_change: number | null;
}

interface SectorRotationResp {
  as_of?: string;
  rank_basis?: string;
  sectors?: SectorRow[];
  excluded?: { ticker: string; name: string; reason: string }[];
  data_source?: string;
  error?: string;
}

function pctCell(v: number | null): React.ReactNode {
  if (v === null) return <span style={{ color: "var(--ink-faint)" }}>—</span>;
  const pct = v * 100;
  return (
    <span style={{ color: changeColor(pct) }}>
      {pct >= 0 ? "+" : ""}{pct.toFixed(2)}%
    </span>
  );
}

function rankChangeCell(rc: number | null): React.ReactNode {
  if (rc === null) return <span style={{ color: "var(--ink-faint)" }}>—</span>;
  if (rc === 0) return <span style={{ color: "var(--ink-dim)" }}>—</span>;
  const color = rc > 0 ? "var(--green)" : "var(--red)";
  const glyph = rc > 0 ? "▲" : "▼";
  return <span style={{ color }}>{glyph} {Math.abs(rc)}</span>;
}

export default function SectorRotation() {
  const [basis, setBasis] = useState("1M");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [data, setData] = useState<SectorRotationResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);

  const load = () => {
    (api.getSectorRotation() as Promise<SectorRotationResp>)
      .then((d) => {
        setData(d);
        setUpdatedAt(Date.now());
        setFetchError(false);
        setLoading(false);
      })
      .catch(() => {
        setFetchError(true);
        setLoading(false);
      });
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 300000); // 5 min — sector returns don't need sub-minute refresh
    return () => clearInterval(t);
  }, []);

  const sectors = [...(data?.sectors ?? [])].sort((a,b) => {
    const av=a.returns?.[basis], bv=b.returns?.[basis];
    if(av == null) return bv == null ? a.ticker.localeCompare(b.ticker) : 1;
    if(bv == null) return -1;
    return bv-av;
  });
  const excluded = data?.excluded ?? [];

  return (
    <div className="markets-page">
      <MarketToolbar note="daily closing bars" title="Sector rotation" subtitle={`Ranked by ${basis} trailing return · refreshes every 5m`} updatedAt={updatedAt} loading={loading} onRefresh={load}>
        <select aria-label="Ranking period" value={basis} onChange={e=>setBasis(e.target.value)}>
          {TIMEFRAME_LABELS.map(tf=><option key={tf}>{tf}</option>)}
        </select>
      </MarketToolbar>
      <p style={{fontSize:12,color:"var(--ink-dim)",marginBottom:16}}>Rank change compares the server's {data?.rank_basis || "1M"} ranking with five trading days earlier. Shown only when that ranking period is selected. Return bars cap at 10%.</p>
      {fetchError || data?.error ? (
        <div style={{ padding: "14px 16px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>
          {data?.error ? `Failed to load sector rotation: ${data.error}` : "Failed to load sector rotation."}
        </div>
      ) : !loading && sectors.length === 0 ? (
        <div style={{ padding: "14px 16px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
          No sector data available.
        </div>
      ) : (
        <div className="markets-table-wrap">
          <table className="markets-table">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line-dim)" }}>
                <th style={{ textAlign: "left", padding: "8px 12px", color: "var(--ink-dim)", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase" }}>Rank</th>
                <th style={{ textAlign: "left", padding: "8px 12px", color: "var(--ink-dim)", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase" }}>Sector</th>
                {TIMEFRAME_LABELS.map(tf => (
                  <th key={tf} style={{ textAlign: "right", padding: "8px 12px", color: "var(--ink-dim)", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                    {tf}
                  </th>
                ))}
                <th style={{ textAlign: "right", padding: "8px 12px", color: "var(--ink-dim)", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase" }}>Rank Δ</th>
              </tr>
            </thead>
            <tbody>
              {sectors.map((s,index) => (
                <tr key={s.ticker} style={{ borderBottom: "1px solid var(--line-dim)" }}>
                  <td style={{ padding: "8px 12px", color: "var(--ink-faint)" }}>
                    {s.returns?.[basis] == null ? "—" : index+1}
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <a className="ticker-link" href={chartHref(s.ticker)}>{s.ticker}</a>{" "}
                    <span style={{ fontSize: 10, color: "var(--ink-dim)" }}>{s.name}</span>
                  </td>
                  {TIMEFRAME_LABELS.map(tf => (
                    <td key={tf} style={{ padding: "8px 12px", textAlign: "right" }}>
                      {pctCell(s.returns?.[tf] ?? null)}
                      {tf===basis && s.returns?.[tf]!=null && <div className="sector-meter"><div style={{height:"100%",width:`${Math.min(Math.abs(s.returns[tf]!) * 1000,100)}%`,background:changeColor(s.returns[tf]! * 100)}} /></div>}
                    </td>
                  ))}
                  <td style={{ padding: "8px 12px", textAlign: "right" }}>
                    {rankChangeCell(basis===(data?.rank_basis || "1M") ? s.rank_change : null)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {excluded.length > 0 && (
        <div style={{ padding: "8px 4px", display: "flex", flexDirection: "column", gap: 3 }}>
          {excluded.map(x => (
            <div key={x.ticker} className="mono" style={{ fontSize: 10, color: "var(--amber)" }}>
              {x.ticker} ({x.name}) excluded — {x.reason}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
