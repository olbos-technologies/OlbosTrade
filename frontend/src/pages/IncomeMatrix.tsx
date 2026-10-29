/**
 * IncomeMatrix — Options-Matrix-Pro-style premium-income screener.
 *
 * Per-watchlist grid of the best cash-secured-put / covered-call setups with
 * standardized yield (per-trade / monthly / annualized %), capital required,
 * breakeven, and probability the option expires OTM. Free yfinance snapshot.
 */
import React, { useEffect, useState } from "react";
import { Button } from "../components/ui";

interface Row {
  ticker: string; strategy: string; type: "PUT" | "CALL"; strike: number;
  expiry: string; dte: number; spot: number | null; premium: number;
  capital_required: number; breakeven: number; prob_otm: number | null;
  return_pct: number; monthly_return_pct: number; annualized_return_pct: number;
}

const usd = (n: number) => "$" + Math.round(n).toLocaleString("en-US");

export default function IncomeMatrix({ initialFilter = "all" }: { initialFilter?: "all" | "csp" | "cc" }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "csp" | "cc">(initialFilter);

  const load = () => {
    fetch("/api/income-matrix?min_prob_otm=0.6&top=200")
      .then(r => r.json())
      .then(d => { if (d.error) setError(d.error); else { setRows(d.results || []); setError(null); } })
      .catch(() => setError("Failed to load income matrix"))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, []);

  const shown = rows.filter(r =>
    filter === "all" ? true : filter === "csp" ? r.strategy === "cash_secured_put" : r.strategy === "covered_call");

  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14, flexWrap: "wrap" }}>
        <span className="panel-title">Income Matrix</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)" }}>
          cash-secured puts &amp; covered calls · sorted by annualized yield · yfinance snapshot
        </span>
        <div style={{ display: "flex", gap: 6, marginLeft: "auto" }}>
          {(["all","csp","cc"] as const).map(f => (
            <Button key={f} active={f === filter}
              style={{ padding: "2px 10px", fontSize: 10 }} onClick={() => setFilter(f)}>
              {f === "all" ? "ALL" : f === "csp" ? "PUTS" : "CALLS"}
            </Button>
          ))}
        </div>
      </div>

      {error ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--red)", padding: 24 }}>⚠ {error}</div>
      ) : loading ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-faint)", padding: 24 }}>
          Scanning option chains… (first load can take a few seconds)
        </div>
      ) : shown.length === 0 ? (
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-faint)", padding: 24 }}>
          No qualifying income setups right now (markets may be closed, or nothing above threshold).
        </div>
      ) : (
        <div className="workspace-table-scroll"><table className="t-table">
          <thead>
            <tr>
              {["Ticker","Strategy","Strike","Exp","DTE","Spot","Premium","Capital","Return","Monthly","Annual","P(OTM)","Breakeven"].map(h => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={i}>
                <td className="mono" style={{ color: "var(--cyan)" }}>{r.ticker}</td>
                <td className="mono" style={{ color: r.type === "PUT" ? "var(--green)" : "var(--amber)" }}>
                  {r.strategy === "cash_secured_put" ? "CSP" : "COVERED CALL"}
                </td>
                <td className="mono">{r.strike}</td>
                <td className="mono">{r.expiry}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{r.dte}d</td>
                <td className="mono">{r.spot != null ? r.spot.toFixed(2) : "—"}</td>
                <td className="mono">${r.premium.toFixed(2)}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{usd(r.capital_required)}</td>
                <td className="mono">{r.return_pct}%</td>
                <td className="mono" style={{ color: "var(--cyan)" }}>{r.monthly_return_pct}%</td>
                <td className="mono" style={{ color: "var(--green)" }}>{r.annualized_return_pct}%</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{r.prob_otm != null ? `${(r.prob_otm * 100).toFixed(0)}%` : "—"}</td>
                <td className="mono" style={{ color: "var(--ink-dim)" }}>{r.breakeven}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
