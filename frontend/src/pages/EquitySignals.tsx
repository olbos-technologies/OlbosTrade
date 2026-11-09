/**
 * EquitySignals page — live signal feed with EQUITIES | OPTIONS toggle.
 * Equity cards: confidence, orderflow, IV boost, entry/stop/target.
 * Options cards: OptionsSignals (spread premium, Greeks, contracts, DTE).
 */

import React, { useEffect, useState } from "react";
import SignalDetailDrawer from "../components/SignalDetailDrawer";
import BrokerStatus from "../components/BrokerStatus";
import PortfolioGreeks from "../components/PortfolioGreeks";
import SignalAttribution from "../components/SignalAttribution";
import SignalDirectionBadge from "../components/SignalDirectionBadge";
import ConfidenceFloorLabel from "../components/ConfidenceFloorLabel";
import WhyBlockedChip from "../components/WhyBlockedChip";
import AlphaEdgeInline, { OpportunityScorePill } from "../components/AlphaEdgeInline";

import BacktestButtons from "../components/BacktestButtons";
import type { SignalAttributionData } from "../types/signal";
import { useDeskBlockContext } from "../hooks/useDeskBlockContext";
import { deriveSignalBlockReason } from "../utils/signalBlockReason";
import { compareLedger, groupLedger, stopDistancePct, type DirectionFilter, type LedgerStatus } from "../utils/signalLedger";
import OptionsSignals from "./OptionsSignals";
import { equitySignalRationale } from "../utils/equitySignalRationale";

export type AssetTab = "equities" | "options";

interface TradePlan {
  entry_price?: number;
  stop_price?: number;
  target_price?: number;
  target_move_pct?: number;
  shares?: number;
  risk_reward?: number;
  risk_dollars?: number;
}

interface Signal {
  id: string;
  ticker: string;
  generated_at: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  orderflow_score: number;
  iv_overlay_boost: number;
  earnings_gated: boolean;
  /** Unused by the live scanner; kept only as a last-resort UI fallback. Prefer `reasons`. */
  reason?: string;
  /** Scanner contributing factors — map of factor → weight (see equity_signal_engine). */
  reasons?: Record<string, number> | string[] | string;
  source?: string;
  trade_plan?: TradePlan;
  indicators?: {
    rsi?: number;
    macd?: number;
    bb_pct_b?: number;
    atr?: number;
    volume_ratio?: number;
  };
  opportunity_score?: { score: number; components: Record<string, number> } | null;
}

/** Deterministic color from the ticker string — used for the fallback
 * initials avatar so a given ticker always gets the same color, not a
 * random one on every render. */
function tickerColor(ticker: string): string {
  let hash = 0;
  for (let i = 0; i < ticker.length; i++) {
    hash = ticker.charCodeAt(i) + ((hash << 5) - hash);
  }
  return `hsl(${Math.abs(hash) % 360}, 55%, 42%)`;
}

function InitialsAvatar({ ticker, size }: { ticker: string; size: number }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 6, background: tickerColor(ticker),
      display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
      fontFamily: "var(--mono)", fontSize: size * 0.36, fontWeight: 700, color: "#fff",
    }}>
      {ticker.slice(0, 2)}
    </div>
  );
}

/** Best-effort real company logo, falling back to a generated initials
 * avatar on load failure — never leaves a broken image icon, and works for
 * any ticker without needing a hand-maintained name/domain lookup table. */
function TickerLogo({ ticker, size = 28 }: { ticker: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <InitialsAvatar ticker={ticker} size={size} />;
  return (
    <img
      src={`https://companiesmarketcap.com/img/company-logos/64/${ticker}.png`}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{ borderRadius: 6, objectFit: "contain", background: "var(--bg-3)", flexShrink: 0 }}
    />
  );
}

function ConfidenceBar({ value, minConfidence }: { value: number; minConfidence: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 75 ? "var(--green)" : pct >= 62 ? "var(--cyan)" : "var(--amber)";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{
          flex: 1, height: 4, background: "var(--bg-4)", borderRadius: 2, overflow: "hidden",
        }}>
          <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 2 }} />
        </div>
        <span style={{ color, fontFamily: "var(--mono)", fontSize: 11, fontWeight: 600, minWidth: 36 }}>
          {pct}%
        </span>
      </div>
      <ConfidenceFloorLabel confidence={value} minConfidence={minConfidence} />
    </div>
  );
}

function toAttribution(sig: Signal): SignalAttributionData {
  return {
    direction: sig.action,
    // These signals come from the background scanner (main.py), not the
    // equity_scan_engine.py-backed scan panel — read the producer's own
    // "source" field rather than guessing which engine generated it.
    source: sig.source ?? "unknown",
    // Repository verified: /api/equity/signals does not return a bar-timeframe
    // field on the signal payload — do not fabricate one.
    timeframe: null,
    confidence: sig.action !== "HOLD" ? sig.confidence : null,
    updatedAt: sig.generated_at,
    // This page has no execute/approve control of its own (unlike the scan
    // panels), and there's no verified lineage from this payload to
    // AUTOPILOT here — authority is left unknown rather than guessed.
    authority: "unknown",
  };
}

function confidenceTone(pct: number): string {
  return pct >= 75 ? "var(--green)" : pct >= 62 ? "var(--cyan)" : "var(--amber)";
}

/** The one-word read on a row: could Autopilot take this now. Always a word plus
 * a mark, never colour alone. "Unknown" means the desk state could not be read,
 * which is not the same as clear. */
function StatusCell({ status }: { status: LedgerStatus }) {
  if (status.kind === "blocked") {
    return (
      <span className="signal-status signal-status--blocked" title={`Likely Autopilot block: ${status.reason.code}. Run Evaluate for the authoritative gate list.`}>
        <span aria-hidden="true">⊘ </span>Blocked · {status.reason.label}
      </span>
    );
  }
  if (status.kind === "unknown") {
    return (
      <span className="signal-status signal-status--unknown" title="The desk state could not be read, so blocks cannot be checked. Not the same as clear.">
        <span aria-hidden="true">? </span>Not checked
      </span>
    );
  }
  return (
    <span className="signal-status signal-status--clear" title="No block found by the preview check. Evaluate is authoritative.">
      <span aria-hidden="true">✓ </span>Clear
    </span>
  );
}

function SignalCard({
  sig,
  minConfidence,
  blockCtx,
  status,
}: {
  sig: Signal;
  minConfidence: number;
  blockCtx: ReturnType<typeof useDeskBlockContext>;
  status: LedgerStatus;
}) {
  const [reviewing, setReviewing] = useState(false);
  const tp = sig.trade_plan || {};
  const ind = sig.indicators || {};
  const block = deriveSignalBlockReason(sig, blockCtx);
  const confPct = Math.round(sig.confidence * 100);
  const stopPct = stopDistancePct(tp);
  const directionClass = sig.action === "BUY" ? "mission-card--buy" : sig.action === "SELL" ? "mission-card--sell" : "";
  const opportunityScore = sig.opportunity_score?.score;
  const hasSignalScore = typeof opportunityScore === "number" && Number.isFinite(opportunityScore) && opportunityScore >= 0 && opportunityScore <= 100;
  const hasConfidence = sig.action !== "HOLD" && Number.isFinite(sig.confidence) && sig.confidence >= 0 && sig.confidence <= 1;
  // These are distinct producer metrics. Missing opportunity scores must not
  // silently turn confidence into a composite signal score.
  const reward = hasSignalScore
    ? { label: "Signal score", value: `${Math.round(opportunityScore)}/100` }
    : hasConfidence
      ? { label: "Confidence score", value: `${confPct}/100` }
      : { label: "Unavailable", value: "—" };
  const factorLabels: Record<string, string> = {
    confidence: "Confidence factor", ev: "Expected-value factor",
    reward_risk: "Reward / risk", liquidity: "Liquidity", regime: "Regime alignment",
  };

  return (
    <>
    <details className={`design-signal-card signal-ledger-row ${directionClass}`}>
      <summary className="signal-ledger-summary">
        <span className="signal-ledger-symbol"><TickerLogo ticker={sig.ticker} size={24} /><strong>{sig.ticker}</strong></span>
        <span className="signal-ledger-score" title={reward.label}>
          <small className="mission-card__reward-prefix">{reward.label}</small>
          <strong className="mission-card__reward-value">{reward.value}</strong>
        </span>
        <SignalDirectionBadge action={sig.action} size="sm" />
        <span className="signal-ledger-move tnum" title="Distance from entry to target">
          <small>Move</small>{tp.target_move_pct != null ? `+${tp.target_move_pct.toFixed(1)}%` : "—"}
        </span>
        <span className="signal-ledger-stop tnum" title="Distance from entry to stop">
          <small>Stop</small>{stopPct != null ? `−${stopPct.toFixed(1)}%` : "—"}
        </span>
        {sig.action === "HOLD" || sig.earnings_gated
          ? <span className="signal-status signal-status--parked">{sig.earnings_gated ? "Earnings gate" : "Hold"}</span>
          : <StatusCell status={status} />}
        <span className="signal-ledger-chevron" aria-hidden="true">⌄</span>
      </summary>
      <div className="signal-ledger-content">
      <div className="signal-ledger-warnings"><WhyBlockedChip reason={block} />{sig.earnings_gated && <span>EARNINGS GATE</span>}</div>
      <div className="signal-rationale"><span>Why it appeared</span><p>{equitySignalRationale(sig)}</p><small>Research only · Model scores are not probabilities of profit.</small></div>
      {tp.entry_price != null && (
        <div className="trade-plan-well signal-trade-plan" style={{ marginTop: 10 }}>
          <PriceCell label="ENTRY" value={tp.entry_price} color="var(--ink)" />
          <PriceCell label="STOP"  value={tp.stop_price}  color="var(--red)" />
          <PriceCell label="TARGET" value={tp.target_price} color="var(--green)" />
          <PriceCell label="MOVE" value={tp.target_move_pct} color="var(--amber)" suffix="%" />
          {tp.shares !== undefined && (
            <div className="signal-sizing" style={{ gridColumn: "1/-1", display: "flex", gap: 16,
              fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginTop: 4 }}>
              <span>SHARES: <b style={{color:"var(--ink)"}}>{tp.shares}</b></span>
              {tp.risk_reward !== undefined && (
                <span>R:R <b style={{color:"var(--cyan)"}}>{tp.risk_reward.toFixed(2)}x</b></span>
              )}
              {tp.risk_dollars !== undefined && (
                <span>RISK <b style={{color:"var(--amber)"}}>${tp.risk_dollars.toFixed(0)}</b></span>
              )}
            </div>
          )}
        </div>
      )}
      <time className="signal-generated" dateTime={sig.generated_at}>Generated {new Date(sig.generated_at).toLocaleString()}</time>
      <button className="design-review" onClick={() => setReviewing(true)}>Review idea <span aria-hidden="true">→</span></button>
      <details className="signal-score-explainer"><summary>About this score</summary>
        <p><strong>{reward.label}: {reward.value}</strong></p>
        <p>{hasSignalScore
          ? "Composite scanner ranking from the available signal factors. It helps compare research ideas; it does not authorize a trade."
          : hasConfidence
            ? "A separate scanner confidence measure. A composite signal score is not available for this idea."
            : "No valid signal score or scanner confidence is available for this idea."}</p>
        <p>Not a probability of profit.</p>
        {hasSignalScore && <>
          <p className="kicker">Factor scores · normalized inputs</p>
          {Object.keys(sig.opportunity_score?.components ?? {}).length ? <dl className="signal-score-factors">
            {Object.entries(sig.opportunity_score?.components ?? {}).map(([key, value]) => <div key={key}>
              <dt>{factorLabels[key] ?? key.replace(/_/g, " ")}</dt>
              <dd>{Number.isFinite(value) && value >= 0 && value <= 1 ? `${Math.round(value * 100)}/100` : "Unavailable"}</dd>
            </div>)}
          </dl> : <p>Factor breakdown unavailable.</p>}
          <p>Factor scores are inputs, not percentage contributions to the final score.</p>
        </>}
      </details>
      <details className="signal-evidence"><summary>Technical evidence</summary>
      <div className="mission-card__details">
        <SignalAttribution data={toAttribution(sig)} size="sm" />
        {sig.action !== "HOLD" && (
          <ConfidenceFloorLabel confidence={sig.confidence} minConfidence={minConfidence} />
        )}

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {sig.opportunity_score != null && (
            <OpportunityScorePill value={sig.opportunity_score.score} />
          )}
          <ScorePill label="ORDERFLOW" value={sig.orderflow_score} />
          <ScorePill label="IV BOOST" value={sig.iv_overlay_boost} />
          {ind.rsi !== undefined && <StatPill label="RSI" value={ind.rsi.toFixed(1)} />}
          {ind.volume_ratio !== undefined && <StatPill label="VOL×" value={ind.volume_ratio.toFixed(1)} />}
          {ind.bb_pct_b !== undefined && <StatPill label="BB%B" value={ind.bb_pct_b.toFixed(2)} />}
          <span style={{ flex: 1 }} />
          <AlphaEdgeInline ticker={sig.ticker} assetType="equity" />
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <BacktestButtons ticker={sig.ticker} assetType="equity" />
        </div>
      </div>
      </details>
      </div>
    </details>
    <SignalDetailDrawer
      open={reviewing}
      onClose={() => setReviewing(false)}
      signal={sig}
      symbol={sig.ticker}
      timeframe="unknown"
      latestBar={null}
    />
    </>
  );
}

function ScorePill({ label, value }: { label: string; value: number }) {
  const color = value > 0.1 ? "var(--green)" : value < -0.1 ? "var(--red)" : "var(--ink-faint)";
  return (
    <div style={{
      background: "var(--bg-3)", borderRadius: 3, padding: "3px 8px",
      fontFamily: "var(--mono)", fontSize: 9, display: "flex", gap: 5, alignItems: "center",
    }}>
      <span style={{ color: "var(--ink-dim)", letterSpacing: "0.08em" }}>{label}</span>
      <span style={{ color, fontWeight: 600 }}>
        {value >= 0 ? "+" : ""}{value.toFixed(3)}
      </span>
    </div>
  );
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

function PriceCell({ label, value, color, suffix }: { label: string; value?: number; color: string; suffix?: "%" }) {
  return (
    <div className="signal-price-cell" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ color: "var(--ink-dim)", fontFamily: "var(--mono)", fontSize: 9, letterSpacing: "0.08em" }}>
        {label}
      </span>
      <span className="financial-value" style={{ color, fontFamily: "var(--mono)", fontSize: 13, fontWeight: 600 }}>
        {value === undefined ? "—" : suffix === "%" ? `${value.toFixed(1)}%` : `$${value.toFixed(2)}`}
      </span>
    </div>
  );
}

export function AssetToggle({ tab, onChange }: { tab: AssetTab; onChange: (t: AssetTab) => void }) {
  const btn = (id: AssetTab, label: string) => (
    <button
      type="button"
      onClick={() => onChange(id)}
      className={`asset-toggle__btn${tab === id ? " asset-toggle__btn--active" : ""}`}
    >
      {label}
    </button>
  );
  return (
    <div className="asset-toggle">
      {btn("options", "OPTIONS")}
      {btn("equities", "EQUITIES")}
    </div>
  );
}

function SignalTable({
  title,
  rows,
  minConfidence,
  blockCtx,
  collapsible,
  defaultOpen,
  hint,
}: {
  title: string;
  rows: Array<{ sig: Signal; status: LedgerStatus }>;
  minConfidence: number;
  blockCtx: ReturnType<typeof useDeskBlockContext>;
  collapsible?: boolean;
  /** Folded groups open by themselves when there is nothing above them to read. */
  defaultOpen?: boolean;
  hint?: string;
}) {
  if (rows.length === 0) return null;
  const body = (
    <>
      {hint && <p className="signal-group-hint">{hint}</p>}
      <div className="signal-ledger-columns" aria-hidden="true">
        <span>Symbol</span><span>Score</span><span>Direction</span><span>Move</span><span>Stop</span><span>Status</span>
      </div>
      <div className="mission-list signal-ledger">
        {rows.map(({ sig, status }) => (
          <SignalCard key={sig.id} sig={sig} status={status} minConfidence={minConfidence} blockCtx={blockCtx} />
        ))}
      </div>
    </>
  );
  const heading = <><span className="signal-group-title">{title}</span><span className="signal-group-count">({rows.length})</span></>;
  return collapsible ? (
    <details className="signal-group signal-group--collapsed" open={defaultOpen || undefined}>
      <summary className="signal-group-head">{heading}</summary>
      {body}
    </details>
  ) : (
    <section className="signal-group" aria-label={title}>
      <div className="signal-group-head">{heading}</div>
      {body}
    </section>
  );
}

function buildShareText(top: Signal[], label: string): string {
  const lines = top.map(sig => {
    const emoji = sig.action === "BUY" ? "🟢" : "🔴";
    const pct = Math.round(sig.confidence * 100);
    const move = sig.trade_plan?.target_move_pct;
    const moveStr = move != null ? ` · ${move.toFixed(1)}% target move` : "";
    return `${emoji} ${sig.ticker} — ${sig.action} · confidence score ${pct}/100${moveStr}`;
  });
  const when = new Date().toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
  return [
    `📊 Top ${label} Signals — OlbosTrade (${when})`,
    "",
    ...lines,
    "",
    "Paper trading signals — not investment advice.",
  ].join("\n");
}

function TopSignals({
  top,
  label,
  minConfidence,
  blockCtx,
}: {
  top: Signal[];
  label: string;
  minConfidence: number;
  blockCtx: ReturnType<typeof useDeskBlockContext>;
}) {
  const [copied, setCopied] = useState(false);
  if (top.length === 0) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(buildShareText(top, label));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can fail (permissions, insecure context) — the
      // button just won't flip to "Copied!"; no error worth surfacing.
    }
  };

  return (
    <div className="instrument-card" style={{
      padding: "14px 16px", display: "flex",
      flexDirection: "column", gap: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          color: "var(--ink)", fontFamily: "var(--mono)", fontSize: 12,
          fontWeight: 700, letterSpacing: "0.1em",
        }}>
          TOP {top.length} {label}
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={handleCopy}
          style={{
            background: copied ? "var(--green)" : "var(--bg-3)",
            color: copied ? "var(--bg)" : "var(--ink)",
            border: "1px solid var(--line-dim)", borderRadius: 4,
            padding: "5px 12px", fontFamily: "var(--mono)", fontSize: 10,
            fontWeight: 600, letterSpacing: "0.06em", cursor: "pointer",
          }}
        >
          {copied ? "COPIED ✓" : "COPY TO SHARE"}
        </button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {top.map(sig => {
          const move = sig.trade_plan?.target_move_pct;
          const block = deriveSignalBlockReason(sig, blockCtx);
          return (
            <div key={sig.id} style={{
              display: "flex", alignItems: "center", gap: 10,
              background: "var(--bg-3)", borderRadius: 4, padding: "8px 12px",
              flexWrap: "wrap",
            }}>
              <TickerLogo ticker={sig.ticker} size={24} />
              <span style={{ color: "var(--ink)", fontFamily: "var(--mono)", fontSize: 13, fontWeight: 700, minWidth: 56 }}>
                {sig.ticker}
              </span>
              <span style={{
                color: sig.action === "BUY" ? "var(--green)" : "var(--red)",
                fontFamily: "var(--mono)", fontSize: 11, fontWeight: 700, minWidth: 36,
              }}>
                {sig.action}
              </span>
              <div style={{ flex: 1, maxWidth: 180, minWidth: 120 }}>
                <ConfidenceBar value={sig.confidence} minConfidence={minConfidence} />
              </div>
              <WhyBlockedChip reason={block} />
              {move != null && (
                <span style={{ color: "var(--amber)", fontFamily: "var(--mono)", fontSize: 11, fontWeight: 600 }}>
                  {move.toFixed(1)}% target
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EquitySignalsGrid({ onTabChange }: { onTabChange: (tab: AssetTab) => void }) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minMovePct, setMinMovePct] = useState(0);
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState<DirectionFilter>("all");
  const [loading, setLoading] = useState(true);
  const blockCtx = useDeskBlockContext();
  const { minConfidence } = blockCtx;

  const loadSignals = () => {
    // Explicit limit — a single scan cycle across the equity watchlist (102
    // tickers as of the Nasdaq-100 switch) can produce one entry per
    // ticker; relying on the backend's implicit default silently truncated
    // a full cycle in the past. 150 covers the current watchlist with room
    // to grow.
    fetch("/api/equity/signals?limit=150")
      .then(r => { if (!r.ok) throw new Error("Signal feed unavailable. Refresh to retry."); return r.json(); })
      .then(d => { if (!Array.isArray(d.signals)) throw new Error("Signal feed unavailable."); setSignals(d.signals); setError(null); })
      .catch(e => { setSignals([]); setError(String(e.message || e)); })
      .finally(() => setLoading(false));
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
      const response = await fetch("/api/equity/scan", { method: "POST" });
      if (!response.ok) throw new Error("Scan could not be started.");
      loadSignals();
    } catch (e) {
      setError(String(e));
    } finally {
      setScanning(false);
    }
  };

  // Min move % only makes sense for BUY/SELL: HOLD has no trade_plan to measure.
  // The split into candidates / blocked / parked lives in utils/signalLedger.
  const filteredSignals = signals.filter(s => `${s.ticker} ${equitySignalRationale(s)}`.toLowerCase().includes(query.toLowerCase()));
  const desk = { guardrailAvailable: blockCtx.guardrailAvailable, killStatusAvailable: blockCtx.killStatusAvailable };
  const groups = groupLedger(filteredSignals, blockCtx, desk, { direction, minMovePct });
  const buySignals = filteredSignals.filter(s => s.action === "BUY" && !s.earnings_gated && (s.trade_plan?.target_move_pct ?? 0) >= minMovePct).sort(compareLedger);
  const sellSignals = filteredSignals.filter(s => s.action === "SELL" && !s.earnings_gated && (s.trade_plan?.target_move_pct ?? 0) >= minMovePct).sort(compareLedger);
  const actionableCount = buySignals.length + sellSignals.length;
  // Split rather than merged: a mixed top-3 can get crowded out entirely by
  // one side (e.g. three high-confidence BUYs hiding a strong SELL), so BUY
  // and SELL each get their own top-3-by-confidence ranking.
  const topBuySignals = buySignals.slice(0, 3);
  const topSellSignals = sellSignals.slice(0, 3);

  const unavailable = Boolean(error) || loading;
  const latest = signals.length ? new Date(Math.max(...signals.map(x => new Date(x.generated_at).getTime()))) : null;
  const latestText = latest ? latest.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : null;
  const dirButton = (id: DirectionFilter, label: string, count: number | null) => (
    <button
      type="button"
      key={id}
      className={`signal-dir-filter__btn${direction === id ? " signal-dir-filter__btn--active" : ""}`}
      aria-pressed={direction === id}
      onClick={() => setDirection(id)}
    >
      {label}{count != null && <span className="tnum"> {count}</span>}
    </button>
  );

  return (
    <div className="design-signal-feed signals-ledger-page" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="instrument-card page-header">
        <div>
          <h1 className="page-header__title">Signals</h1>
          <p className="page-header__sub">
            Research ideas for your review · no orders submitted here
          </p>
          <p className="signals-summary tnum" aria-live="polite">
            {unavailable
              ? "—"
              : <>
                  <b>{actionableCount}</b> ideas · <b>{groups.candidates.length}</b> candidates
                  · <b>{groups.blocked.length}</b> blocked
                  {latestText && <span title="Latest signal generation time, in your local timezone"> · latest {latestText}</span>}
                </>}
          </p>
        </div>
      </div>

      <AssetToggle tab="equities" onChange={onTabChange} />
      <div className="design-search-row">
        <input aria-label="Search signals" placeholder="Search symbol or explanation" value={query} onChange={e => setQuery(e.target.value)} />
        <button className="design-review signal-refresh" aria-label="Refresh signals" title="Refresh signals" onClick={loadSignals}>↻</button>
        <details className="signal-filter-panel"><summary>Filter</summary><div className="signal-scan-controls">
        <label className="kicker" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          Min move %
          <input
            type="number"
            min={0}
            step={0.5}
            value={minMovePct || ""}
            onChange={(e) => setMinMovePct(e.target.value ? parseFloat(e.target.value) : 0)}
            placeholder="0"
            title="Only show signals whose target price implies at least this % move from entry"
            className="control-input"
            style={{ width: 56 }}
          />
        </label>
        <button onClick={runScan} disabled={scanning} className="btn-primary">
          {scanning ? "SCANNING…" : "RUN SCAN"}
        </button>
        </div></details>
      </div>
      <div className="signal-dir-filter" role="group" aria-label="Direction">
        {dirButton("all", "All", unavailable ? null : actionableCount)}
        {dirButton("buy", "Buy", unavailable ? null : buySignals.length)}
        {dirButton("sell", "Sell", unavailable ? null : sellSignals.length)}
      </div>

      {error && (
        <div style={{
          background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: "var(--radius-control)", padding: "10px 14px",
          fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)",
        }}>
          {error}
        </div>
      )}

      {!blockCtx.guardrailAvailable || !blockCtx.killStatusAvailable ? (
        <p className="signal-desk-note" role="status">
          Desk state could not be read, so blocks cannot be checked. Ideas show “Not checked”, which is not the same as clear.
        </p>
      ) : null}

      {loading ? <p role="status">Loading signals…</p> : error ? null : filteredSignals.length === 0 ? (
        <div className="instrument-card instrument-card--flat empty-chassis">
          <p className="empty-chassis__title">{query ? "No signals match your search" : "No equity signals yet"}</p>
          <p className="empty-chassis__hint">Click <strong style={{ color: "var(--ink)" }}>RUN SCAN</strong> to score the watchlist.</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {groups.candidates.length === 0 && groups.blocked.length > 0 && (
            <p className="signal-desk-note" role="status">
              None of the {groups.blocked.length} ideas can be taken right now. They are listed under Blocked, with the reason.
            </p>
          )}
          <SignalTable title="Candidates" rows={groups.candidates} minConfidence={minConfidence} blockCtx={blockCtx} />
          <SignalTable
            title="Blocked" collapsible defaultOpen={groups.candidates.length === 0} rows={groups.blocked} minConfidence={minConfidence} blockCtx={blockCtx}
            hint="Preview of what Autopilot would refuse right now. Evaluate is authoritative."
          />
          <SignalTable
            title="Not actionable" collapsible minConfidence={minConfidence} blockCtx={blockCtx}
            rows={groups.parked.map(sig => ({ sig, status: { kind: "unknown" as const } }))}
            hint="HOLD ideas and ideas held back by an earnings gate."
          />
          <details className="signal-ranking"><summary>Ranked scanner summary</summary><div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 12 }}>
            <TopSignals top={topBuySignals} label="BUY" minConfidence={minConfidence} blockCtx={blockCtx} />
            <TopSignals top={topSellSignals} label="SELL" minConfidence={minConfidence} blockCtx={blockCtx} />
          </div>
          </details>
        </div>
      )}
    </div>
  );
}

export default function EquitySignals() {
  const [tab, setTab] = useState<AssetTab>("equities");

  return (
    <div className="page-shell signals-workspace" style={{ maxWidth: 1200 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        {tab === "options" && <AssetToggle tab={tab} onChange={setTab} />}
        <div className="signal-feed-context" style={{ flex: 1, minWidth: 220 }}><BrokerStatus /></div>
        <div className="signal-feed-context" style={{ flex: 1, minWidth: 220 }}><PortfolioGreeks compact /></div>
      </div>

      {tab === "equities" ? <EquitySignalsGrid onTabChange={setTab} /> : <OptionsSignals />}
      <p className="signals-score-note">Scores are not probabilities of profit.</p>
    </div>
  );
}
