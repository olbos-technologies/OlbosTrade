/** Read-only professional insight cards sourced exclusively from the positions hook. */
import React from "react";

export type PositionInsight = {
  symbol?: string | null;
  unrealized_pnl?: number | null;
  tracked?: boolean | null;
  quantity?: number | null;
};

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
  const contributors = portfolioUnavailable ? [] : pnlContributors(positions);
  const known = contributors.length;
  const untracked = positions.filter(p => p.tracked === false).length;
  const top = contributors.slice(0, 6);
  const max = Math.max(1, ...top.map(p => Math.abs(p.pnl)));
  const usd = (value: number) => `${value < 0 ? "−" : "+"}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

  return (
    <section aria-label="Pro portfolio analytics" style={{ margin: "0 12px 12px" }}>
      <div style={{ marginBottom: 8, display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, color: "var(--ink)", fontFamily: "var(--sans)" }}>Portfolio Intelligence</h2>
        <span style={{ ...muted, fontSize: 10 }}>READ-ONLY · POSITION DATA</span>
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
          <p style={muted}>A reliable correlation heatmap requires aligned historical return observations for each holding. Current position snapshots cannot supply those correlations.</p>
          <div role="status" style={{ fontSize: 11, color: "var(--amber)" }}>Historical returns required</div>
        </Panel>
      </div>
    </section>
  );
}
