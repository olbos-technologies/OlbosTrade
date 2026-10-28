/**
 * PortfolioGreeks — real-time display of net delta, vega, theta.
 */

import React, { useEffect, useState } from "react";
import { Badge } from "./ui";

interface Greeks {
  net_delta: number;
  net_vega: number;
  net_theta: number;
  equity_position_count: number;
  options_position_count: number;
  total_position_count: number;
  is_delta_neutral: boolean;
  needs_hedge: boolean;
}

function GreekCell({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  const color = warn ? "var(--amber)" : Math.abs(value) < 0.001 ? "var(--ink-faint)" :
                value > 0 ? "var(--green)" : "var(--red)";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
      <span style={{ color: "var(--ink-dim)", fontSize: 9, letterSpacing: "0.1em" }}>
        {label}
      </span>
      <span style={{ color, fontWeight: 600, fontSize: 14, fontFamily: "var(--mono)" }}>
        {value >= 0 ? "+" : ""}{value.toFixed(4)}
      </span>
    </div>
  );
}

export default function PortfolioGreeks() {
  const [unavailable, setUnavailable] = useState(false);
  const [greeks, setGreeks] = useState<Greeks | null>(null);

  useEffect(() => {
    const load = () =>
      fetch("/api/market/greeks")
        .then(r => { if (!r.ok) throw new Error("Greeks unavailable"); return r.json(); })
        .then(d => { if (![d?.net_delta, d?.net_vega, d?.net_theta].every(v => typeof v === "number" && Number.isFinite(v))) throw new Error("Greeks unavailable"); setGreeks(d); setUnavailable(false); })
        .catch(() => { setGreeks(null); setUnavailable(true); });
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  if (!greeks) {
    return (
      <div className="instrument-card" style={{
        padding: "12px 16px",
        fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)",
      }}>
        {unavailable ? "Portfolio Greeks unavailable" : "Loading Greeks…"}
      </div>
    );
  }

  return (
    <div
      className="instrument-card"
      style={{
        border: `1px solid ${greeks.needs_hedge ? "var(--amber)" : "var(--line-dim)"}`,
        padding: "12px 20px",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 4,
        marginBottom: 10, fontFamily: "var(--mono)", fontSize: 10,
        color: "var(--ink-dim)", letterSpacing: "0.08em",
      }}>
        PORTFOLIO GREEKS
        {greeks.needs_hedge && (
          <Badge kind="tag" tone="var(--amber)" style={{ borderRadius: 2, padding: "1px 5px", fontSize: 9, marginLeft: 8, opacity: 1 }}>
            HEDGE NEEDED
          </Badge>
        )}
        {greeks.is_delta_neutral && (
          <Badge kind="tag" tone="var(--green)" style={{ borderRadius: 2, padding: "1px 5px", fontSize: 9, marginLeft: 8, opacity: 1 }}>
            DELTA NEUTRAL
          </Badge>
        )}
      </div>

      <div style={{ display: "flex", gap: 32, marginBottom: 10 }}>
        <GreekCell label="NET Δ" value={greeks.net_delta} warn={greeks.needs_hedge} />
        <GreekCell label="NET V" value={greeks.net_vega} />
        <GreekCell label="NET Θ/DAY" value={greeks.net_theta} />
      </div>

      <div style={{
        display: "flex", gap: 16, fontFamily: "var(--mono)", fontSize: 10,
        color: "var(--ink-faint)",
      }}>
        <span>EQ: {greeks.equity_position_count}</span>
        <span>OPT: {greeks.options_position_count}</span>
        <span>TOTAL: {greeks.total_position_count}</span>
      </div>
    </div>
  );
}
