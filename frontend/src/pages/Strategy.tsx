import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import BottomSheet from "../components/BottomSheet";
import { useIsMobile } from "../hooks/useIsMobile";

// Payoff shape is a property of the strategy TYPE itself, not live system
// state — kept as a small static lookup rather than round-tripped through
// the backend. Everything else (eligibility, lifecycle, live health) comes
// from GET /api/strategy/registry (backend/app/api/routes/strategy.py).
const PAYOFF_SHAPE: Record<string, { type: string; bias: string; max_profit: string; max_loss: string; legs: number }> = {
  bull_put_spread:        { type: "CREDIT", bias: "BULLISH", max_profit: "Net credit",    max_loss: "Width − credit", legs: 2 },
  bear_call_spread:       { type: "CREDIT", bias: "BEARISH", max_profit: "Net credit",    max_loss: "Width − credit", legs: 2 },
  iron_condor:            { type: "CREDIT", bias: "NEUTRAL", max_profit: "Net credit",    max_loss: "Width − credit", legs: 4 },
  bull_call_debit_spread: { type: "DEBIT",  bias: "BULLISH", max_profit: "Width − debit", max_loss: "Net debit",      legs: 2 },
};

interface StrategyCard {
  strategy_id: string;
  name: string;
  lifecycle_status: string;
  enabled: boolean;
  manual_eligible: boolean;
  copilot_eligible: boolean;
  autopilot_supported: boolean;
  allocation_limit_pct: number | null;
  main_risk_warning: string | null;
  health_score: number | null;
  health_status: string | null;
  sample_size: number;
}

const HEALTH_COLOR: Record<string, string> = {
  healthy: "var(--green)",
  watch: "var(--amber)",
  degraded: "var(--red)",
  insufficient_data: "var(--ink-faint)",
};

function EligibilityBadge({ label, eligible }: { label: string; eligible: boolean }) {
  return (
    <span style={{
      fontFamily: "var(--mono)", fontSize: 9, padding: "2px 7px",
      color: eligible ? "var(--green)" : "var(--ink-faint)",
      border: `1px solid ${eligible ? "rgba(34,197,94,0.4)" : "var(--line-dim)"}`,
    }}>
      {eligible ? "✓" : "✕"} {label}
    </span>
  );
}

export default function Strategy() {
  const [cards, setCards] = useState<StrategyCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const isMobile = useIsMobile();
  // On a phone the detail is a sheet, so it opens on tap rather than sitting
  // permanently beside a list that has no room for it.
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api.getStrategyRegistry()
      .then((res: any) => {
        if (!alive) return;
        const list: StrategyCard[] = res?.strategies || [];
        setCards(list);
        setSelected(list[0]?.strategy_id ?? null);
      })
      .catch((e: any) => {
        if (alive) setError(e?.message || "Failed to load strategy registry");
      });
    return () => { alive = false; };
  }, []);

  if (error) {
    return (
      <div style={{ padding: 40, textAlign: "center", fontFamily: "var(--mono)", fontSize: 12, color: "var(--red)" }}>
        {error}
      </div>
    );
  }

  if (!cards) {
    return (
      <div style={{ padding: 40, textAlign: "center", fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-faint)" }}>
        Loading strategies…
      </div>
    );
  }

  const strat = cards.find(c => c.strategy_id === selected);
  const shape = strat ? PAYOFF_SHAPE[strat.strategy_id] : null;

  // One body, two shells. Extracted so an edit reaches both — the desktop
  // column and the mobile sheet must never drift apart.
  const detailBody = strat && shape ? (<>

          <div>
            <div className="kicker" style={{ marginBottom: 8 }}>Strategy Details</div>
            <h2 style={{ fontFamily: "var(--mono)", fontSize: 24, fontWeight: 600, color: "var(--cyan)", marginBottom: 6 }}>
              {strat.name}
            </h2>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              {[shape.type, shape.bias, `${shape.legs} LEGS`, strat.lifecycle_status.toUpperCase()].map(tag => (
                <span key={tag} style={{
                  fontFamily: "var(--mono)", fontSize: 10, padding: "2px 8px",
                  border: "1px solid var(--line-dim)", color: "var(--ink-dim)",
                }}>{tag}</span>
              ))}
            </div>
          </div>

          <div className="instrument-stat-strip" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
            {[
              { label: "Max Profit", val: shape.max_profit, color: "var(--green)" },
              { label: "Max Loss",   val: shape.max_loss,   color: "var(--red)" },
              { label: "Bias",       val: shape.bias,        color: "var(--amber)" },
            ].map(m => (
              <div key={m.label} style={{ padding: "18px 20px" }}>
                <div className="kicker" style={{ marginBottom: 6 }}>{m.label}</div>
                <div className="data-val sm" style={{ color: m.color }}>{m.val}</div>
              </div>
            ))}
          </div>

          {/* Live health — never fabricated. Explicit insufficient-data state
              rather than showing a 0/blank score as if it meant something. */}
          <div className="instrument-card" style={{ padding: 16 }}>
            <div className="panel-title" style={{ marginBottom: 12 }}>Live Health</div>
            {strat.health_status && strat.health_status !== "insufficient_data" ? (
              <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                <div>
                  <div className="kicker" style={{ marginBottom: 4 }}>Score</div>
                  <div className="data-val sm" style={{ color: HEALTH_COLOR[strat.health_status] || "var(--ink)" }}>
                    {strat.health_score != null ? strat.health_score.toFixed(1) : "—"}
                  </div>
                </div>
                <div>
                  <div className="kicker" style={{ marginBottom: 4 }}>Status</div>
                  <div className="data-val sm" style={{ color: HEALTH_COLOR[strat.health_status] || "var(--ink)" }}>
                    {strat.health_status.toUpperCase()}
                  </div>
                </div>
                <div>
                  <div className="kicker" style={{ marginBottom: 4 }}>Sample</div>
                  <div className="data-val sm">{strat.sample_size} trades</div>
                </div>
              </div>
            ) : (
              <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
                Insufficient closed-trade history to grade this strategy yet
                {strat.sample_size > 0 ? ` (${strat.sample_size} trades so far)` : ""}.
              </div>
            )}
          </div>

          <div className="instrument-card" style={{ padding: 16 }}>
            <div className="panel-title" style={{ marginBottom: 12 }}>Eligibility</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <EligibilityBadge label="Manual" eligible={strat.manual_eligible} />
              <EligibilityBadge label="Copilot" eligible={strat.copilot_eligible} />
              <EligibilityBadge label="Autopilot" eligible={strat.autopilot_supported} />
            </div>
            {strat.main_risk_warning && (
              <div style={{ marginTop: 12, fontFamily: "var(--mono)", fontSize: 11, color: "var(--amber)", lineHeight: 1.6 }}>
                {strat.main_risk_warning}
              </div>
            )}
          </div>

          <div className="instrument-card" style={{ padding: 16, flex: 1, display: "flex", flexDirection: "column", minHeight: 160 }}>
            <div className="panel-title" style={{ marginBottom: 12 }}>Current Signals</div>
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              NO SIGNALS — WAITING FOR 09:35 ET
            </div>
          </div>
  </>) : null;

  return (
    // 240px + 1fr left the detail ~150px on a 390px screen, which is why every
    // title wrapped and the list sat beside an empty column. Below the
    // breakpoint the list takes the full width and the detail becomes a sheet.
    <div style={{
      display: "grid",
      gridTemplateColumns: isMobile ? "1fr" : "240px 1fr",
      height: "100%", overflow: "hidden",
    }}>
      {/* List */}
      <div className="instrument-card" style={{
        borderRight: isMobile ? "none" : "1px solid var(--line-dim)",
        overflowY: "auto",
      }}>
        <div style={{ padding: "9px 14px", borderBottom: "1px solid var(--line-dim)" }}>
          <span className="panel-title">Strategies</span>
        </div>
        {cards.map(c => {
          const s = PAYOFF_SHAPE[c.strategy_id];
          return (
            <button key={c.strategy_id}
              onClick={() => { setSelected(c.strategy_id); if (isMobile) setSheetOpen(true); }}
              style={{
                width: "100%", padding: isMobile ? "15px 16px" : "13px 16px",
                minHeight: isMobile ? 60 : undefined, textAlign: "left",
                touchAction: "manipulation",
                background: selected === c.strategy_id ? "var(--cyan-dim)" : "transparent",
                border: "none", borderLeft: selected === c.strategy_id ? "2px solid var(--cyan)" : "2px solid transparent",
                borderBottom: "1px solid var(--line-dim)", cursor: "pointer",
                display: "flex", justifyContent: "space-between", alignItems: "center",
              }}>
              <div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: selected === c.strategy_id ? "var(--cyan)" : "var(--ink)" }}>
                  {c.name}
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", marginTop: 2 }}>
                  {s ? `${s.legs} LEGS` : "—"}
                </div>
              </div>
              <span style={{
                fontFamily: "var(--mono)", fontSize: 9, padding: "1px 6px",
                background: s?.type === "CREDIT" ? "rgba(212,175,55,0.1)" : "rgba(59,130,246,0.1)",
                color: s?.type === "CREDIT" ? "var(--cyan)" : "var(--blue)",
                border: `1px solid ${s?.type === "CREDIT" ? "rgba(212,175,55,0.3)" : "rgba(59,130,246,0.3)"}`,
              }}>{s?.type || "—"}</span>
            </button>
          );
        })}
      </div>

      {/* Beside the list on a desktop; in a sheet on a phone. */}
      {detailBody && !isMobile && (
        <div style={{ overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 20 }}>
          {detailBody}
        </div>
      )}
      {detailBody && isMobile && (
        <BottomSheet
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
          title={strat?.name ?? "Strategy"}
          subtitle="Strategy Details"
          labelledBy="strategy-detail-title"
        >
          <div className="workspace-page-root" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 20 }}>
            {detailBody}
          </div>
        </BottomSheet>
      )}
    </div>
  );
}
