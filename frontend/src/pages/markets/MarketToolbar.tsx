import React from "react";
import "./Markets.css";
export function MarketToolbar({ title, subtitle, updatedAt, loading, onRefresh, children, note = "quote time not supplied" }: {
  title: string; subtitle: string; updatedAt?: number | null; loading: boolean;
  onRefresh: () => void; children?: React.ReactNode; note?: string;
}) {
  return <header className="markets-toolbar">
    <div><div className="markets-kicker">MARKET INTELLIGENCE</div><h1>{title}</h1><p>{subtitle}</p>
      <p className="markets-meta">{loading ? "Loading…" : updatedAt ? `Fetched ${new Date(updatedAt).toLocaleTimeString([], { timeZoneName: "short" })} · ${note}` : "No successful read yet"}</p></div>
    <div className="markets-controls">{children}<button onClick={onRefresh} disabled={loading}>Refresh</button></div>
  </header>;
}
export const chartHref = (symbol: string) => "/terminal/markets/chart?symbol=" + encodeURIComponent(symbol);
