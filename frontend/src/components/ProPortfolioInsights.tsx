/** Read-only professional insight cards sourced exclusively from the positions hook. */
import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export type PositionInsight = {
  symbol?: string | null;
  unrealized_pnl?: number | null;
  tracked?: boolean | null;
  quantity?: number | null;
};

export function portfolioDataCoverage(positions: PositionInsight[]) {
  const total = positions.length;
  const pnlKnown = positions.filter(p => p.unrealized_pnl != null && Number.isFinite(p.unrealized_pnl)).length;
  const untracked = positions.filter(p => p.tracked === false).length;
  return { total, pnlKnown, missingPnl: total - pnlKnown, untracked };
}

export function pnlContributors(positions: PositionInsight[]): { symbol: string; pnl: number; tracked: boolean }[] {
  return positions
    .filter(p => p.unrealized_pnl != null && Number.isFinite(p.unrealized_pnl))
    .map(p => ({ symbol: p.symbol || "Unspecified", pnl: Number(p.unrealized_pnl), tracked: p.tracked !== false }))
    .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl));
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="instrument-card" aria-label={title} style={{ minWidth: 0, padding: "14px 16px", borderRadius: 8 }}>
      <div className="panel-title" style={{ marginBottom: 10 }}>{title}</div>
      {children}
    </section>
  );
}

const muted: React.CSSProperties = { color: "var(--ink-dim)", fontFamily: "var(--sans)", fontSize: 12, lineHeight: 1.6 };

export default function ProPortfolioInsights({
  positions, portfolioUnavailable,
}: {
  positions: PositionInsight[];
  portfolioUnavailable: boolean;
}) {
  const [correlations, setCorrelations] = useState<any>(null);
  const [correlationState, setCorrelationState] = useState<"loading" | "ready" | "error">("loading");
  useEffect(() => {
    let mounted = true;
    api.getPortfolioCorrelation()
      .then((response: any) => { if (mounted) { setCorrelations(response); setCorrelationState("ready"); } })
      .catch(() => { if (mounted) setCorrelationState("error"); });
    return () => { mounted = false; };
  }, []);
  const matrix: unknown = correlations?.correlation_matrix;
  const symbols: unknown = correlations?.tickers;
  const validMatrix = correlationState === "ready" && correlations?.status === "ok" && Array.isArray(symbols) && Array.isArray(matrix)
    && symbols.length >= 2 && matrix.length === symbols.length
    && matrix.every((row: unknown) => Array.isArray(row) && row.length === symbols.length && row.every((value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= -1 && value <= 1));
  const contributors = portfolioUnavailable ? [] : pnlContributors(positions);
  const known = contributors.length;
  const coverage = portfolioUnavailable ? null : portfolioDataCoverage(positions);
  const untracked = coverage?.untracked ?? 0;
  const top = contributors.slice(0, 6);
  const max = Math.max(1, ...top.map(p => Math.abs(p.pnl)));
  const usd = (value: number) => `${value < 0 ? "−" : "+"}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

  return (
    <section aria-label="Pro portfolio analytics" style={{ margin: "0 12px 12px" }}>
      <div style={{ marginBottom: 8, display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, color: "var(--ink)", fontFamily: "var(--sans)" }}>Portfolio Intelligence</h2>
        <span style={{ ...muted, fontSize: 10 }}>READ-ONLY · POSITION DATA</span>
      </div>
      <div role="status" style={{ ...muted, marginBottom: 10, padding: "8px 10px", border: "1px solid var(--line-dim)", borderRadius: 6, background: "var(--bg-3)" }}>
        {coverage === null ? "Position coverage unavailable — portfolio source could not be verified."
          : coverage.total === 0 ? "No open positions reported by the current portfolio source."
          : `Position data coverage: ${coverage.pnlKnown}/${coverage.total} with observed unrealized P&L · ${coverage.missingPnl} missing P&L values · ${coverage.untracked} untracked holdings.`}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 270px), 1fr))", gap: 12 }}>
        <Panel title="Unrealized P&L contributors">
          {portfolioUnavailable ? (
            <p role="status" style={muted}>Portfolio data unavailable. No P&L attribution can be calculated.</p>
          ) : known === 0 ? (
            <p style={muted}>No positions with verified unrealized P&L values. No chart is shown.</p>
          ) : (
            <>
              <p style={{ ...muted, marginTop: 0 }}>Largest absolute position-level unrealized P&L; not a realized P&L waterfall.</p>
              {top.map((p, index) => (
                <div key={`${p.symbol}-${index}`} style={{ marginBottom: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 11, color: "var(--ink)" }}>
                    <span>{p.symbol}{!p.tracked ? " · Untracked" : ""}</span>
                    <span style={{ color: p.pnl >= 0 ? "var(--green)" : "var(--red)", fontVariantNumeric: "tabular-nums" }}>{usd(p.pnl)}</span>
                  </div>
                  <div aria-hidden="true" style={{ background: "var(--bg-3)", height: 6, marginTop: 5, borderRadius: 4 }}>
                    <div style={{ width: `${Math.abs(p.pnl) / max * 100}%`, height: "100%", background: p.pnl >= 0 ? "var(--green)" : "var(--red)", borderRadius: 4 }} />
                  </div>
                </div>
              ))}
              <p style={{ ...muted, marginBottom: 0 }}>{known} position{known === 1 ? "" : "s"} with P&L data · {untracked} untracked broker holding{untracked === 1 ? "" : "s"}</p>
            </>
          )}
        </Panel>
        <Panel title="Sector allocation">
          <p style={muted}>Sector exposure isn't provided by the current positions summary. A percentage chart requires authoritative sector classification and consistent exposure values.</p>
          <div role="status" style={{ fontSize: 11, color: "var(--amber)" }}>Awaiting verified allocation dataset</div>
        </Panel>
        <Panel title="Position correlations">
          {correlationState === "loading" ? <p role="status" style={muted}>Loading historical correlations…</p>
            : correlationState === "error" ? <p role="status" style={muted}>Correlation data unavailable. No values are inferred.</p>
            : !validMatrix ? <p role="status" style={muted}>Insufficient verified overlapping price history for a correlation matrix.</p>
            : <>
              <p style={{ ...muted, marginTop: 0 }}>Historical return correlations · {correlations.lookback_days ?? "—"}-day lookback</p>
              <div style={{ overflowX: "auto" }}>
                <table aria-label="Portfolio correlation matrix" style={{ borderCollapse: "separate", borderSpacing: 3, fontSize: 10, fontVariantNumeric: "tabular-nums" }}>
                  <thead><tr><th scope="col"></th>{(symbols as string[]).map((s, i) => <th scope="col" key={i} style={{ padding: 5 }}>{s}</th>)}</tr></thead>
                  <tbody>{(matrix as number[][]).map((row, i) => <tr key={i}><th scope="row" style={{ padding: 5 }}>{(symbols as string[])[i]}</th>{row.map((v, j) => <td key={j} title={`${(symbols as string[])[i]} / ${(symbols as string[])[j]}: ${v.toFixed(2)}`} style={{ textAlign: "center", minWidth: 34, padding: 6, background: v > 0.5 ? "rgba(245,158,11,0.34)" : v < -0.5 ? "rgba(34,211,238,0.24)" : "var(--bg-3)", color: "var(--ink)", borderRadius: 3 }}>{v.toFixed(2)}</td>)}</tr>)}</tbody>
                </table>
              </div>
              <p style={{ ...muted, fontSize: 10 }}>{correlations.excluded_symbols?.length || 0} symbols excluded from analysis. Correlation is not a forecast.</p>
            </>}
        </Panel>
      </div>
    </section>
  );
}
