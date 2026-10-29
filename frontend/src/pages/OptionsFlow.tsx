/**
 * OptionsFlow — "Unusual Options Activity" tape (free yfinance approximation).
 *
 * A real-time OPRA flow feed needs a paid subscription; this shows a periodic
 * snapshot of contracts trading at unusually high volume vs. open interest across
 * the watchlist. Polls /api/options-flow (server caches ~5 min).
 *
 * Card feed is the default view (dense info-card per contract); Table remains
 * available as a toggle for a denser scan. Every field rendered here maps to a
 * real backend field (see unusual_activity.py::flow_row) — there is no per-row
 * timestamp, sweep/block classification, or position P/L in the source data, so
 * none of those are shown or invented.
 */
import React, { useEffect, useState } from "react";
import { Badge, Button } from "../components/ui";
import { tint } from "../utils/tint";

interface FlowRow {
  ticker: string; type: "CALL" | "PUT"; strike: number; expiry: string;
  dte: number | null; spot: number | null; volume: number;
  open_interest: number; vol_oi_ratio: number | null; last_price: number;
  iv: number | null; premium: number; sentiment: string;
}

type ViewMode = "cards" | "table";

const usd = (n: number) =>
  "$" + Math.round(n).toLocaleString("en-US");

function Cell({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontFamily: "var(--sans)", fontSize: 9.5, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--ink-faint)", marginBottom: 2 }}>
        {label}
      </div>
      <div className="mono" style={{ fontSize: 12.5, fontWeight: 600, color: tone || "var(--ink)" }}>
        {value}
      </div>
    </div>
  );
}

function FlowCard({ row, onPickTicker }: { row: FlowRow; onPickTicker: (t: string) => void }) {
  const isCall = row.type === "CALL";
  const tone = isCall ? "var(--green)" : "var(--red)";
  // "Why is this flagged" signal — the real basis for inclusion (volume well
  // above open interest, or brand-new open interest). Deliberately not
  // labeled SWEEP/BLOCK: the source data has no true order-type/side field
  // (see unusual_activity.py — "no true buy/sell side").
  const unusualTag = row.vol_oi_ratio != null ? `${row.vol_oi_ratio}× VOL/OI` : "NEW OI";

  return (
    <div
      className="instrument-card"
      style={{
        borderLeft: `3px solid ${tone}`,
        padding: "12px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      {/* Header: ticker + type + DTE, premium at right */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <button
            className="mono"
            onClick={() => onPickTicker(row.ticker)}
            title={`Filter to ${row.ticker}`}
            style={{
              background: "transparent", border: "none", cursor: "pointer", padding: 0,
              color: "var(--ink)", fontSize: 16, fontWeight: 700,
            }}
          >
            {row.ticker}
          </button>
          <span style={{
            fontFamily: "var(--sans)", fontSize: 10.5, fontWeight: 600, letterSpacing: "0.04em",
            padding: "2px 7px", borderRadius: 3, color: tone,
            background: isCall ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)",
            border: `1px solid ${tint(tone, 0.333)}`,
          }}>
            {row.type}
          </span>
          {row.dte != null && (
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>{row.dte}d</span>
          )}
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div className="mono" style={{ fontSize: 16, fontWeight: 700, color: tone }}>{usd(row.premium)}</div>
          <div style={{ fontFamily: "var(--sans)", fontSize: 9, color: "var(--ink-faint)" }}>est. premium</div>
        </div>
      </div>

      {/* Signal row: sentiment + the unusual-activity basis */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: "var(--sans)", fontSize: 11, color: "var(--ink-dim)", textTransform: "capitalize" }}>
          {row.sentiment}
        </span>
        <Badge kind="tag" tone="var(--amber)" style={{
          fontSize: 9.5, letterSpacing: "0.04em",
          border: "1px solid rgba(245,158,11,0.4)",
          borderRadius: 2, padding: "1px 6px", opacity: 1,
        }}>
          {unusualTag}
        </Badge>
      </div>

      {/* Stat grid — every value is a real field from the scan row */}
      <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: "8px 10px" }}>
        <Cell label="Strike" value={row.strike} />
        <Cell label="Exp" value={row.expiry} />
        <Cell label="Spot" value={row.spot != null ? row.spot.toFixed(2) : "—"} />
        <Cell label="Last" value={`$${row.last_price.toFixed(2)}`} />
        <Cell label="Volume" value={row.volume.toLocaleString("en-US")} />
        <Cell label="Open Int." value={row.open_interest.toLocaleString("en-US")} />
        <Cell label="Vol/OI" value={row.vol_oi_ratio != null ? `${row.vol_oi_ratio}×` : "NEW"} tone="var(--amber)" />
        <Cell label="IV" value={row.iv != null ? `${(row.iv * 100).toFixed(0)}%` : "—"} />
      </div>
    </div>
  );
}

export default function OptionsFlow() {
  const [rows, setRows] = useState<FlowRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ticker, setTicker] = useState("");
  const [tickerInput, setTickerInput] = useState("");
  const [typeFilter, setTypeFilter] = useState<"all" | "call" | "put">("all");
  const [view, setView] = useState<ViewMode>("cards");

  const load = () => {
    fetch("/api/options-flow?min_volume=200&ratio=2&top=150")
      .then(r => r.json())
      .then(d => {
        if (d.error) setError(d.error);
        else { setRows(d.results || []); setError(null); }
      })
      .catch(() => setError("Failed to load options flow"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const submitTicker = (e: React.FormEvent) => {
    e.preventDefault();
    setTicker(tickerInput.trim().toUpperCase());
  };

  const pickTicker = (t: string) => { setTicker(t); setTickerInput(t); };

  const shown = rows.filter(r =>
    (ticker === "" || r.ticker === ticker) &&
    (typeFilter === "all" || r.type === typeFilter.toUpperCase()));

  // Newest-premium-first is already the backend's sort; cards read best in
  // that same order (highest-conviction prints first).

  // Stats reflect the current filtered view, not the whole watchlist.
  const callCount = shown.filter(r => r.type === "CALL").length;
  const putCount  = shown.filter(r => r.type === "PUT").length;
  const totalCount = callCount + putCount;
  const callPct = totalCount > 0 ? Math.round((callCount / totalCount) * 100) : null;
  const putPct  = totalCount > 0 ? 100 - (callPct ?? 0) : null;
  const callPremium = shown.filter(r => r.type === "CALL").reduce((s, r) => s + r.premium, 0);
  const putPremium  = shown.filter(r => r.type === "PUT").reduce((s, r) => s + r.premium, 0);

  // Top 3 tickers by total premium, computed separately for calls and puts
  // (a ticker can appear in both lists — e.g. NVDA heavy on calls, light on puts).
  const topByTicker = (type: "CALL" | "PUT") => {
    const totals = new Map<string, number>();
    for (const r of shown) {
      if (r.type !== type) continue;
      totals.set(r.ticker, (totals.get(r.ticker) ?? 0) + r.premium);
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3);
  };
  const topCalls = topByTicker("CALL");
  const topPuts  = topByTicker("PUT");

  return (
    <div className="page-shell" style={{ height: "100%", overflowY: "auto" }}>
      <div className="instrument-card page-header">
        <div>
          <div className="page-header__title">Unusual Options Activity</div>
          <p className="page-header__sub">Free snapshot · volume ≫ open interest · not a real-time OPRA tape</p>
        </div>
        {totalCount > 0 && (
          <div style={{ display: "flex", gap: 16, fontFamily: "var(--mono)", fontSize: 11 }}>
            <span style={{ color: "var(--green)" }}>CALLS {callCount} · {callPct}% · {usd(callPremium)}</span>
            <span style={{ color: "var(--red)" }}>PUTS {putCount} · {putPct}% · {usd(putPremium)}</span>
          </div>
        )}
        <span style={{ flex: 1 }} />
        <div className="asset-toggle" role="group" aria-label="View mode">
          <button
            type="button"
            className={`asset-toggle__btn${view === "cards" ? " asset-toggle__btn--active" : ""}`}
            aria-pressed={view === "cards"}
            onClick={() => setView("cards")}
          >
            CARDS
          </button>
          <button
            type="button"
            className={`asset-toggle__btn${view === "table" ? " asset-toggle__btn--active" : ""}`}
            aria-pressed={view === "table"}
            onClick={() => setView("table")}
          >
            TABLE
          </button>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <form onSubmit={submitTicker} style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            value={tickerInput}
            onChange={e => setTickerInput(e.target.value)}
            placeholder="Ticker"
            aria-label="Filter by ticker"
            className="control-input"
            style={{ width: 80, textTransform: "uppercase" }}
          />
          <button type="submit" className="btn-primary" style={{ padding: "6px 12px" }}>Go</button>
          {ticker && (
            <button type="button" className="btn-ghost" style={{ padding: "6px 12px" }}
              onClick={() => { setTicker(""); setTickerInput(""); }}>
              Clear
            </button>
          )}
        </form>

        <div style={{ display: "flex", gap: 6 }}>
          {(["all", "call", "put"] as const).map(f => (
            <Button key={f} active={f === typeFilter}
              style={{ padding: "2px 10px", fontSize: 10 }} onClick={() => setTypeFilter(f)}>
              {f === "all" ? "ALL" : f === "call" ? "CALLS" : "PUTS"}
            </Button>
          ))}
        </div>
      </div>

      {(topCalls.length > 0 || topPuts.length > 0) && (
        <div style={{ display: "flex", gap: 28, marginBottom: 14, flexWrap: "wrap", fontFamily: "var(--mono)", fontSize: 11 }}>
          {topCalls.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--ink-faint)", fontSize: 9.5, letterSpacing: "0.08em" }}>TOP CALLS</span>
              {topCalls.map(([tk, premium], i) => (
                <Button key={tk} style={{ padding: "2px 8px", fontSize: 10.5, color: "var(--green)" }}
                  onClick={() => pickTicker(tk)}>
                  {i + 1}. {tk} {usd(premium)}
                </Button>
              ))}
            </div>
          )}
          {topPuts.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--ink-faint)", fontSize: 9.5, letterSpacing: "0.08em" }}>TOP PUTS</span>
              {topPuts.map(([tk, premium], i) => (
                <Button key={tk} style={{ padding: "2px 8px", fontSize: 10.5, color: "var(--red)" }}
                  onClick={() => pickTicker(tk)}>
                  {i + 1}. {tk} {usd(premium)}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}

      {error ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--red)", padding: 24 }}>
          ⚠ {error}
        </div>
      ) : loading ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-faint)", padding: 24 }}>
          Scanning option chains… (first load can take a few seconds)
        </div>
      ) : shown.length === 0 ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-faint)", padding: 24 }}>
          {rows.length === 0
            ? "No unusual options activity right now (markets may be closed, or nothing above threshold)."
            : "No rows match the current ticker/type filter."}
        </div>
      ) : view === "cards" ? (
        <div className="workspace-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%,320px), 1fr))", gap: 10 }}>
          {shown.map((r, i) => (
            <FlowCard key={`${r.ticker}-${r.strike}-${r.expiry}-${i}`} row={r} onPickTicker={pickTicker} />
          ))}
        </div>
      ) : (
        <div className="workspace-table-scroll"><table className="t-table">
          <thead>
            <tr>
              {["Ticker","P/C","Strike","Exp","DTE","Spot","Volume","OI","Vol/OI","Last","IV","Premium"].map(h => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>
                <td className="mono" style={{ color: "var(--cyan)" }}>{r.ticker}</td>
                <td className="mono" style={{ color: r.type === "CALL" ? "var(--green)" : "var(--red)", fontWeight: 600 }}>
                  {r.type}
                </td>
                <td className="mono">{r.strike}</td>
                <td className="mono">{r.expiry}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{r.dte != null ? `${r.dte}d` : "—"}</td>
                <td className="mono">{r.spot != null ? r.spot.toFixed(2) : "—"}</td>
                <td className="mono">{r.volume.toLocaleString("en-US")}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{r.open_interest.toLocaleString("en-US")}</td>
                <td className="mono" style={{ color: "var(--amber)" }}>{r.vol_oi_ratio != null ? `${r.vol_oi_ratio}×` : "NEW"}</td>
                <td className="mono">${r.last_price.toFixed(2)}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{r.iv != null ? `${(r.iv * 100).toFixed(0)}%` : "—"}</td>
                <td className="mono" style={{ color: r.type === "CALL" ? "var(--green)" : "var(--red)" }}>{usd(r.premium)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
