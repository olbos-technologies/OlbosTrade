/**
 * OptionsSignals — options spread signal cards (mirrors EquitySignals layout).
 * Data from GET /api/options/signals (background Options Signal Scanner).
 */

import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import SignalAttribution from "../components/SignalAttribution";
import SignalDirectionBadge from "../components/SignalDirectionBadge";
import AlphaEdgeInline, { OpportunityScorePill } from "../components/AlphaEdgeInline";
import SignalDecisionCard from "../components/SignalDecisionCard";
import BacktestButtons from "../components/BacktestButtons";
import ScanButton from "../components/ScanButton";
import { StatTile } from "../components/ui";
import type { SignalAttributionData } from "../types/signal";

export interface Spread {
  option_type?: string;
  short_strike?: number;
  long_strike?: number;
  expiration?: string;
  dte?: number;
  net_credit?: number;
  max_loss?: number;
  breakeven?: number;
}

interface OptionsIntelligence {
  pop?: number;
  delta_short?: number;
  theta_short?: number;
  vega_short?: number;
  kelly_fraction?: number;
  reward_risk?: number;
}

export interface SignalEvidence {
  top_positive_factors: { feature: string; value: number; impact: number }[];
  top_negative_factors: { feature: string; value: number; impact: number }[];
}

interface OptionsSignal {
  id: string;
  ticker: string;
  generated_at: string;
  action: "BUY_SPREAD" | "SELL_SPREAD" | "HOLD" | string;
  confidence: number;
  pop?: number | null;
  kelly_fraction?: number | null;
  signal_score?: number;
  quantity?: number;
  iv_rank?: number;
  regime?: string;
  strategy?: string;
  source?: string;
  spread?: Spread;
  intelligence?: OptionsIntelligence | null;
  // Only set on HOLD entries — a scanned-but-not-qualified ticker, with why.
  reason?: string;
  // SHAP attribution from the AI scorer — present on both approved and
  // AI-scorer-rejected entries, absent on entries rejected for other
  // reasons (insufficient history, zero-sized position, etc.).
  evidence?: SignalEvidence | null;
  opportunity_score?: { score: number; components: Record<string, number> } | null;
}

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      background: "var(--bg-3)", borderRadius: 3, padding: "3px 8px",
      fontFamily: "var(--mono)", fontSize: 9, display: "flex", gap: 5, alignItems: "center",
    }}>
      <span style={{ color: "var(--ink-dim)", letterSpacing: "0.08em" }}>{label}</span>
      <span style={{ color: "var(--ink)", fontWeight: 600 }}>{value}</span>
    </div>
  );
}

function PriceCell({ label, value, color, prefix = "$" }: {
  label: string; value?: number | string; color: string; prefix?: string;
}) {
  const display = typeof value === "number"
    ? `${prefix}${value.toFixed(2)}`
    : (value ?? "—");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ color: "var(--ink-dim)", fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.08em" }}>
        {label}
      </span>
      <span style={{ color, fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600 }}>
        {display}
      </span>
    </div>
  );
}

function formatStrikePair(spread?: Spread): string {
  if (!spread?.short_strike || !spread?.long_strike) return "";
  const ot = (spread.option_type || "").toUpperCase().slice(0, 1) || "?";
  return `${spread.short_strike}/${spread.long_strike}${ot}`;
}

function formatExp(expiration?: string): string {
  if (!expiration) return "";
  const d = new Date(expiration);
  if (Number.isNaN(d.getTime())) return expiration.slice(0, 10);
  return d.toLocaleDateString(undefined, { month: "2-digit", day: "2-digit" });
}

function toAttribution(sig: OptionsSignal): SignalAttributionData {
  const dte = sig.spread?.dte;
  return {
    direction: sig.action,
    source: sig.source ?? "unknown",
    timeframe: typeof dte === "number" ? `DTE ${dte}` : null,
    confidence: sig.confidence,
    updatedAt: sig.generated_at,
    authority: "unknown",
    topPositiveFactors: sig.evidence?.top_positive_factors,
    topNegativeFactors: sig.evidence?.top_negative_factors,
  };
}

const ACTION_GROUP_COLOR: Record<string, string> = {
  BUY_SPREAD: "var(--green)",
  SELL_SPREAD: "var(--red)",
};

function confidenceTone(pct: number): string {
  return pct >= 75 ? "var(--green)" : pct >= 62 ? "var(--cyan)" : "var(--amber)";
}

function OptionsSignalGroup({
  action,
  signals,
}: {
  action: "BUY_SPREAD" | "SELL_SPREAD";
  signals: OptionsSignal[];
}) {
  if (signals.length === 0) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          color: ACTION_GROUP_COLOR[action], fontFamily: "var(--mono)", fontSize: 12,
          fontWeight: 700, letterSpacing: "0.1em",
        }}>
          {action.replace("_", " ")}
        </span>
        <span style={{ color: "var(--ink-faint)", fontFamily: "var(--mono)", fontSize: 11 }}>
          ({signals.length})
        </span>
        <div style={{ flex: 1, height: 1, background: "var(--line-dim)" }} />
      </div>
      <div className="mission-list">
        {signals.map(sig => <OptionsSignalCard key={sig.id} sig={sig} />)}
      </div>
    </div>
  );
}

function OptionsSignalCard({ sig }: { sig: OptionsSignal }) {
  const spread = sig.spread || {};
  const intel = sig.intelligence || {};
  const isDebit = sig.action === "BUY_SPREAD";
  const net = spread.net_credit;
  const directionClass = isDebit ? "mission-card--buy" : sig.action === "SELL_SPREAD" ? "mission-card--sell" : "";

  const pop = sig.pop ?? intel.pop ?? sig.confidence;
  const popPct = Math.round(pop * 100);
  const popTone = confidenceTone(popPct);
  const confPct = Math.round(sig.confidence * 100);

  const reward = sig.opportunity_score != null
    ? { prefix: "OPP", value: String(sig.opportunity_score.score), tone: confidenceTone(sig.opportunity_score.score) }
    : net != null
      ? { prefix: isDebit ? "DB" : "CR", value: `$${Math.abs(net).toFixed(2)}`, tone: "var(--green)" }
      : { prefix: sig.pop != null ? "POP" : "CONF", value: `${popPct}%`, tone: popTone };

  const subtitle = [
    sig.strategy ? sig.strategy.replace(/_/g, " ") : null,
    formatExp(spread.expiration) || null,
    formatStrikePair(spread) || null,
  ].filter(Boolean).join(" · ");

  return (
    <SignalDecisionCard
      className={directionClass}
      reward={reward}
      title={(
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className="mono" style={{ fontWeight: 700, fontSize: 15 }}>{sig.ticker}</span>
          {formatStrikePair(spread) && (
            <span className="mono" style={{ color: "var(--ink-dim)", fontWeight: 500, fontSize: 12 }}>
              {formatStrikePair(spread)}
            </span>
          )}
          <SignalDirectionBadge action={sig.action} size="sm" />
          <SignalAttribution data={toAttribution(sig)} size="sm" />
        </span>
      )}
      subtitle={subtitle || new Date(sig.generated_at).toLocaleTimeString()}
      meta={{
        label: spread.dte != null ? `${spread.dte}D` : `${confPct}%`,
        tone: spread.dte != null ? "var(--cyan)" : popTone,
        icon: "⏳",
      }}
      progress={{
        value: popPct,
        tone: popTone,
        label: `${sig.ticker} probability`,
      }}
    >
      <>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {sig.opportunity_score != null && (
            <OpportunityScorePill value={sig.opportunity_score.score} />
          )}
          {sig.iv_rank !== undefined && <StatPill label="IV RANK" value={sig.iv_rank.toFixed(0)} />}
          {sig.signal_score !== undefined && <StatPill label="SCORE" value={sig.signal_score.toFixed(3)} />}
          {intel.delta_short !== undefined && <StatPill label="Δ" value={intel.delta_short.toFixed(2)} />}
          {intel.vega_short !== undefined && <StatPill label="V" value={intel.vega_short.toFixed(3)} />}
          {intel.theta_short !== undefined && <StatPill label="θ" value={intel.theta_short.toFixed(3)} />}
          {sig.kelly_fraction != null && <StatPill label="KELLY" value={`${(sig.kelly_fraction * 100).toFixed(0)}%`} />}
          <span style={{ flex: 1 }} />
          <AlphaEdgeInline ticker={sig.ticker} assetType="options" />
        </div>

        <div style={{
          display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
          gap: 8, background: "var(--bg-3)", borderRadius: 4, padding: "8px 12px",
        }}>
          <PriceCell
            label={isDebit ? "DEBIT" : "CREDIT"}
            value={net !== undefined ? Math.abs(net) : undefined}
            color="var(--ink)"
          />
          <PriceCell
            label="MAX LOSS"
            value={spread.max_loss}
            color="var(--red)"
          />
          <PriceCell
            label="BREAKEVEN"
            value={spread.breakeven}
            color="var(--green)"
          />
          <div style={{
            gridColumn: "1/-1", display: "flex", gap: 16, flexWrap: "wrap",
            fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginTop: 4,
          }}>
            {sig.quantity !== undefined && (
              <span>CONTRACTS: <b style={{ color: "var(--ink)" }}>{sig.quantity}</b></span>
            )}
            {spread.dte !== undefined && (
              <span>DTE <b style={{ color: "var(--cyan)" }}>{spread.dte}</b></span>
            )}
            {intel.reward_risk !== undefined && (
              <span>R:R <b style={{ color: "var(--cyan)" }}>{intel.reward_risk.toFixed(2)}x</b></span>
            )}
            {spread.short_strike !== undefined && spread.long_strike !== undefined && (
              <span>
                STRIKES <b style={{ color: "var(--ink)" }}>
                  {spread.short_strike}/{spread.long_strike}
                </b>
              </span>
            )}
          </div>
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <BacktestButtons ticker={sig.ticker} assetType="options" strategy={sig.strategy} />
        </div>
      </>
    </SignalDecisionCard>
  );
}

function RejectionRow({ sig }: { sig: OptionsSignal }) {
  return (
    <div
      className="instrument-card"
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "7px 12px", fontFamily: "var(--mono)", fontSize: 11,
      }}
    >
      <span style={{ color: "var(--ink)", fontWeight: 700, minWidth: 52 }}>{sig.ticker}</span>
      <span style={{
        color: "var(--cyan)", fontSize: 9, letterSpacing: "0.06em",
        textTransform: "uppercase", minWidth: 130,
      }}>
        {sig.strategy ? sig.strategy.replace(/_/g, " ") : "—"}
      </span>
      <span style={{ color: "var(--ink-dim)", flex: 1 }}>{sig.reason || "no signal"}</span>
      <span style={{ color: "var(--ink-faint)", fontSize: 9, flexShrink: 0 }}>
        {new Date(sig.generated_at).toLocaleTimeString()}
      </span>
    </div>
  );
}

export default function OptionsSignals() {
  const [signals, setSignals] = useState<OptionsSignal[]>([]);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [showReasons, setShowReasons] = useState(false);

  const loadSignals = () => {
    // Explicit limit — see EquitySignals.tsx's loadSignals for why (same
    // watchlist-size vs. implicit-default truncation issue).
    (api.getOptionsSignals(150) as Promise<{ signals?: OptionsSignal[] }>)
      .then(d => { setSignals(d.signals || []); setLastUpdated(new Date()); setError(null); })
      .catch(() => setError("Signal feed unavailable. Check the service connection, then retry."));
  };

  useEffect(() => {
    loadSignals();
    const t = setInterval(loadSignals, 60000);
    return () => clearInterval(t);
  }, []);

  const runScan = async () => {
    setScanning(true);
    setError(null);
    try {
      const d = await (api.scanOptionsSignals() as Promise<{ signals?: OptionsSignal[] }>);
      setSignals(d.signals || []);
    } catch (e) {
      setError(String(e));
    } finally {
      setScanning(false);
    }
  };

  const buySpreads = signals.filter(s => s.action === "BUY_SPREAD");
  const sellSpreads = signals.filter(s => s.action === "SELL_SPREAD");
  const actionable = [...buySpreads, ...sellSpreads];
  // Scanned-but-not-qualified tickers — previously these reasons only ever
  // reached a server log line, so "0 total" told you nothing about whether
  // the scanner was broken or genuinely found nothing, and why.
  const notQualified = signals.filter(s => s.action === "HOLD");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="instrument-card page-header">
        <div>
          <div className="page-header__title">Options Signals</div>
          <p className="page-header__sub">{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()} · stale after 60s` : "Awaiting first verified update"} · POP, credit, and eligibility</p>
        </div>
        <span style={{ flex: 1 }} />
        {notQualified.length > 0 && (
          <button onClick={() => setShowReasons(v => !v)} className="btn-ghost">
            {showReasons ? "Hide reasons" : "Show reasons"} ({notQualified.length})
          </button>
        )}
        <ScanButton scanning={scanning} onScan={runScan} />
      </div>

      <div className="instrument-stat-strip" style={{ gridTemplateColumns: "repeat(5, minmax(0, 1fr))" }}>
        <StatTile variant="divider" size="sm" label="Actionable" value={actionable.length} tone="var(--accent)" />
        <StatTile variant="divider" size="sm" label="Buy spread" value={buySpreads.length} tone="var(--green)" />
        <StatTile variant="divider" size="sm" label="Sell spread" value={sellSpreads.length} tone="var(--red)" />
        <StatTile variant="divider" size="sm" label="Not qualified" value={notQualified.length} tone="var(--amber)" />
        <StatTile variant="divider" size="sm" label="Total" value={signals.length} />
      </div>

      {error && (
        <div className="signal-feed-error" role="alert">
          <span>{error}</span>
          <button type="button" className="btn-ghost" onClick={loadSignals}>Retry</button>
        </div>
      )}

      {signals.length === 0 ? (
        <div className="instrument-card instrument-card--flat empty-chassis">
          <p className="empty-chassis__title">{scanning ? "Options scan in progress" : error ? "Signal feed is unavailable" : "No options signals yet"}</p>
          <p className="empty-chassis__hint">{scanning ? "The watchlist is being scored. This view will update when the scan completes." : error ? "Retry after restoring the service connection." : "Run a scan to score spreads across the watchlist, or wait for the scheduled scanner."}</p>
        </div>
      ) : (
        <>
          {actionable.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              <OptionsSignalGroup action="BUY_SPREAD" signals={buySpreads} />
              <OptionsSignalGroup action="SELL_SPREAD" signals={sellSpreads} />
            </div>
          )}
          {actionable.length === 0 && (
            <div className="instrument-card instrument-card--flat empty-chassis" style={{ padding: 24 }}>
              <p className="empty-chassis__title">Nothing qualified this scan</p>
              <p className="empty-chassis__hint">
                {notQualified.length} ticker{notQualified.length === 1 ? "" : "s"} scanned — use Show reasons for why.
              </p>
            </div>
          )}
          {showReasons && notQualified.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="panel-title">Not qualified</span>
                <div style={{ flex: 1, height: 1, background: "var(--line-dim)" }} />
              </div>
              {notQualified.map(sig => <RejectionRow key={sig.id} sig={sig} />)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
