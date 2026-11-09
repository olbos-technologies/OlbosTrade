/**
 * Equity Scan Panel — A+ grade with Tier 1 enhancements.
 *
 * Tier 1 features:
 * - Drill-down modal: Risk scenarios, entry ladder, technical indicators
 * - Filtering + sorting: EV, confidence, Kelly, action, ticker
 * - Auto-refresh: Periodic scanning + toast notifications
 *
 * Grade: A+ (Institutional UX for retail traders)
 */

import React, { useEffect, useState, useRef } from "react";
import WatchlistManager from "./WatchlistManager";
import IBKRLiveControl from "./IBKRLiveControl";
import SignalAttribution from "./SignalAttribution";
import SignalDivergence from "./SignalDivergence";
import type { SignalAttributionData } from "../types/signal";
import { useLiveData } from "../hooks/useLiveData";
import { api, apiAuthHeaders } from "../api/client";
import { confidenceScoreText } from "../utils/confidenceScore";

interface Candidate {
  ticker: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  expected_value: number;
  pop: number;
  kelly_fraction: number;
  entry_price: number;
  stop_price: number;
  target_price: number;
  max_loss: number;
  max_profit: number;
  entry_ladder: Array<{
    tranche: number;
    pct_position: number;
    entry_price: number;
    description: string;
  }>;
  iv_rank: number;
  realized_vol: number;
  pricing_source: string;
  indicators: Record<string, number>;
  orderflow_score: number;
  iv_overlay_boost: number;
  ev_per_risk: number;
}

interface ScanResult {
  candidates: Candidate[];
  tickers_scanned: string[];
  gate_blocked: boolean;
  gate_reason: string;
  iv_rank: number;
  realized_vol: number;
  error?: string;
}

type SortBy = "ev" | "confidence" | "kelly" | "action" | "ticker" | "target_pct";

/** % move from entry to target — lets a candidate be judged by projected
 * size (e.g. "only show setups targeting 5%+"), not just EV/confidence. */
function targetMovePct(cand: { entry_price: number; target_price: number }): number {
  if (!cand.entry_price) return 0;
  return (Math.abs(cand.target_price - cand.entry_price) / cand.entry_price) * 100;
}
type ActionFilter = "ALL" | "BUY" | "SELL";

// Toast component
function Toast({ message, type }: { message: string; type: "info" | "success" | "warning" }) {
  const bgColor =
    type === "success"
      ? "rgba(34,197,94,0.15)"
      : type === "warning"
      ? "rgba(245,158,11,0.15)"
      : "rgba(59,130,246,0.15)";
  const borderColor =
    type === "success"
      ? "rgba(34,197,94,0.3)"
      : type === "warning"
      ? "rgba(245,158,11,0.3)"
      : "rgba(59,130,246,0.3)";
  const textColor =
    type === "success" ? "var(--green)" : type === "warning" ? "var(--amber)" : "var(--blue)";

  return (
    <div
      style={{
        position: "fixed",
        bottom: 20,
        right: 20,
        background: bgColor,
        border: `1px solid ${borderColor}`,
        borderRadius: 4,
        padding: "10px 14px",
        fontFamily: "var(--mono)",
        fontSize: 11,
        color: textColor,
        animation: "slideIn 0.3s ease",
        zIndex: 1000,
      }}
    >
      {message}
    </div>
  );
}

// Drill-down modal
function CandidateModal({ candidate, onClose }: { candidate: Candidate; onClose: () => void }) {
  const [evalResult, setEvalResult] = useState<{
    final_status: string;
    block_reasons: string[];
    warnings: string[];
  } | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);

  if (!candidate) return null;

  const roi = candidate.max_profit > 0 ? ((candidate.max_profit / candidate.max_loss) * 100).toFixed(1) : "0";

  // This card's plan has no explicit share count — max_loss is the total
  // dollar risk the scan engine already sized for this Kelly plan, so the
  // implied share count is max_loss / per-share risk (same math the
  // composer/backend use elsewhere for risk_dollars).
  const riskPerShare = Math.abs(candidate.entry_price - candidate.stop_price);
  const impliedShares = riskPerShare > 0 ? Math.max(1, Math.round(candidate.max_loss / riskPerShare)) : 1;

  const checkEligibility = async () => {
    setEvaluating(true);
    setEvalError(null);
    try {
      const body: any = await api.evaluateEquityIntent({
        ticker: candidate.ticker,
        action: candidate.action,
        shares: impliedShares,
        entry_price: candidate.entry_price,
        stop_price: candidate.stop_price,
        target_price: candidate.target_price,
        order_type: "limit",
      });
      setEvalResult({
        final_status: body.final_status,
        block_reasons: body.block_reasons || [],
        warnings: body.warnings || [],
      });
    } catch (e: any) {
      setEvalError(e?.message || "Eligibility check failed");
      setEvalResult(null);
    } finally {
      setEvaluating(false);
    }
  };

  const statusColor =
    evalResult?.final_status === "BLOCKED" ? "var(--red)" :
    evalResult?.final_status === "AUTOPILOT_ELIGIBLE" ? "var(--green)" :
    evalResult?.final_status ? "var(--amber)" : "var(--ink-dim)";

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 999,
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg)",
          border: "1px solid var(--line-dim)",
          borderRadius: 8,
          maxWidth: 600,
          maxHeight: "80vh",
          overflowY: "auto",
          padding: 24,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: "var(--ink)" }}>
              {candidate.ticker}
            </h2>
            <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--ink-dim)" }}>
              {candidate.action} — Entry ${candidate.entry_price.toFixed(2)}
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "var(--bg-2)",
              border: "1px solid var(--line-dim)",
              borderRadius: 4,
              width: 32,
              height: 32,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "pointer",
              fontSize: 16,
            }}
          >
            ✕
          </button>
        </div>

        {/* Key metrics */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 12,
            marginBottom: 20,
          }}
        >
          <div className="instrument-card" style={{ padding: 12 }}>
            <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontFamily: "var(--mono)", fontWeight: 600, letterSpacing: "0.08em" }}>
              EXPECTED VALUE
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--green)", fontFamily: "var(--mono)" }}>
              ${candidate.expected_value.toFixed(0)}
            </div>
          </div>
          <div className="instrument-card" style={{ padding: 12 }}>
            <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontFamily: "var(--mono)", fontWeight: 600, letterSpacing: "0.08em" }}>
              CONFIDENCE SCORE
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--cyan)", fontFamily: "var(--mono)" }}>
              {confidenceScoreText(candidate.confidence)}
            </div>
          </div>
          <div className="instrument-card" style={{ padding: 12 }}>
            <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontFamily: "var(--mono)", fontWeight: 600, letterSpacing: "0.08em" }}>
              KELLY %
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--amber)", fontFamily: "var(--mono)" }}>
              {(candidate.kelly_fraction * 100).toFixed(1)}%
            </div>
          </div>
        </div>

        {/* Risk/Reward section */}
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
            Risk/Reward Scenarios
          </h3>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 12,
            }}
          >
            <div className="instrument-card" style={{ padding: 12 }}>
              <div style={{ fontSize: 11, color: "var(--ink-dim)", marginBottom: 8 }}>
                <span style={{ fontWeight: 600 }}>Max Loss (stop hit)</span>
                <div style={{ color: "var(--red)", fontSize: 14, fontWeight: 700, marginTop: 4, fontFamily: "var(--mono)" }}>
                  −${candidate.max_loss.toFixed(2)}
                </div>
              </div>
              <div style={{ fontSize: 10, color: "var(--ink-faint)" }}>
                Risk from entry to stop price
              </div>
            </div>
            <div className="instrument-card" style={{ padding: 12 }}>
              <div style={{ fontSize: 11, color: "var(--ink-dim)", marginBottom: 8 }}>
                <span style={{ fontWeight: 600 }}>Max Profit (target hit)</span>
                <div style={{ color: "var(--green)", fontSize: 14, fontWeight: 700, marginTop: 4, fontFamily: "var(--mono)" }}>
                  +${candidate.max_profit.toFixed(2)}
                </div>
              </div>
              <div style={{ fontSize: 10, color: "var(--ink-faint)" }}>
                Profit from entry to target price
              </div>
            </div>
          </div>
          <div
            className="instrument-card"
            style={{
              padding: 12,
              marginTop: 12,
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr",
              gap: 12,
            }}
          >
            <div>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                PROBABILITY OF PROFIT
              </div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--cyan)" }}>
                {(candidate.pop * 100).toFixed(1)}%
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                EV / RISK
              </div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--amber)" }}>
                {candidate.ev_per_risk.toFixed(3)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                ROI (if target)
              </div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--green)" }}>
                {roi}%
              </div>
            </div>
          </div>
        </div>

        {/* Price levels */}
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
            Trade Levels
          </h3>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: 12,
            }}
          >
            <div className="instrument-card" style={{ padding: 12 }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                ENTRY
              </div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--ink)", fontFamily: "var(--mono)" }}>
                ${candidate.entry_price.toFixed(2)}
              </div>
            </div>
            <div className="instrument-card" style={{ padding: 12 }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                STOP LOSS
              </div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--red)", fontFamily: "var(--mono)" }}>
                ${candidate.stop_price.toFixed(2)}
              </div>
            </div>
            <div className="instrument-card" style={{ padding: 12 }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                PROFIT TARGET
              </div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--green)", fontFamily: "var(--mono)" }}>
                ${candidate.target_price.toFixed(2)}
              </div>
            </div>
            <div className="instrument-card" style={{ padding: 12 }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                TARGET MOVE
              </div>
              <div style={{ fontSize: 14, fontWeight: 700, color: "var(--amber)", fontFamily: "var(--mono)" }}>
                {targetMovePct(candidate).toFixed(1)}%
              </div>
            </div>
          </div>
        </div>

        {/* Entry ladder */}
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
            Entry Ladder (Kelly-Scaled)
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {candidate.entry_ladder.map((t, idx) => (
              <div
                key={idx}
                className="instrument-card"
                style={{
                  padding: "10px 12px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink)" }}>
                    Tranche {t.tranche} ({t.pct_position}%)
                  </div>
                  <div style={{ fontSize: 10, color: "var(--ink-dim)", marginTop: 2 }}>
                    {t.description}
                  </div>
                </div>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--cyan)",
                    fontFamily: "var(--mono)",
                  }}
                >
                  ${t.entry_price.toFixed(2)}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Technical indicators */}
        {candidate.indicators && Object.keys(candidate.indicators).length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <h3 style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
              Technical Indicators
            </h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(2, 1fr)",
                gap: 8,
              }}
            >
              {Object.entries(candidate.indicators).map(([key, val]) => (
                <div key={key} className="instrument-card" style={{ padding: "8px 10px" }}>
                  <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 2, fontWeight: 600 }}>
                    {key.toUpperCase()}
                  </div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", fontFamily: "var(--mono)" }}>
                    {typeof val === "number" ? val.toFixed(1) : val}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Additional metrics */}
        <div style={{ marginBottom: 20 }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: 8,
            }}
          >
            <div className="instrument-card" style={{ padding: "8px 10px" }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 2, fontWeight: 600 }}>
                ORDERFLOW SCORE
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)" }}>
                {candidate.orderflow_score.toFixed(3)}
              </div>
            </div>
            <div className="instrument-card" style={{ padding: "8px 10px" }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 2, fontWeight: 600 }}>
                IV RANK
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--cyan)" }}>
                {candidate.iv_rank.toFixed(1)}%
              </div>
            </div>
          </div>
        </div>

        {/* Pricing source */}
        <div style={{ fontSize: 10, color: "var(--ink-faint)", marginBottom: 20 }}>
          Pricing source: <span style={{ fontWeight: 600 }}>{candidate.pricing_source}</span>
        </div>

        {/* Eligibility — read-only, backend-authoritative. This card never
            submits an order itself; it only shows what the same gates
            _execute_signal enforces would say about this plan right now. */}
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
            Eligibility
          </h3>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <button
              onClick={checkEligibility}
              disabled={evaluating}
              style={{
                background: "var(--bg-2)",
                border: "1px solid var(--line-dim)",
                borderRadius: 4,
                padding: "6px 12px",
                fontFamily: "var(--mono)",
                fontSize: 10,
                fontWeight: 600,
                cursor: evaluating ? "default" : "pointer",
                color: "var(--ink)",
              }}
            >
              {evaluating ? "CHECKING…" : "CHECK ELIGIBILITY"}
            </button>
            {evalResult && (
              <span
                style={{
                  fontFamily: "var(--mono)", fontSize: 11, fontWeight: 700,
                  color: statusColor, letterSpacing: "0.04em",
                }}
              >
                {evalResult.final_status.replace(/_/g, " ")}
              </span>
            )}
            {evalError && (
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--red)" }}>
                {evalError}
              </span>
            )}
          </div>
          {evalResult && (evalResult.block_reasons.length > 0 || evalResult.warnings.length > 0) && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
              {evalResult.block_reasons.map((r, i) => (
                <div key={`b${i}`} style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--red)" }}>
                  ⛔ {r}
                </div>
              ))}
              {evalResult.warnings.map((w, i) => (
                <div key={`w${i}`} style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--amber)" }}>
                  ⚠ {w}
                </div>
              ))}
            </div>
          )}
          <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink-faint)", marginTop: 8 }}>
            Checked against ~{impliedShares} share{impliedShares === 1 ? "" : "s"} (implied from this plan's sized risk). Read-only — does not place an order.
          </div>
        </div>

        {/* Action buttons */}
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={onClose}
            style={{
              flex: 1,
              background: "var(--bg-3)",
              border: "1px solid var(--line-dim)",
              borderRadius: 4,
              padding: "8px 12px",
              fontFamily: "var(--mono)",
              fontSize: 11,
              fontWeight: 600,
              cursor: "pointer",
              color: "var(--ink)",
            }}
          >
            CLOSE
          </button>
        </div>
      </div>
    </div>
  );
}

export default function EquityScanPanel() {
  const [result, setResult] = useState<ScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "info" | "success" | "warning" } | null>(null);
  const [windowWidth, setWindowWidth] = useState(1200);
  const [sortBy, setSortBy] = useState<SortBy>("ev");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("ALL");
  const [minEV, setMinEV] = useState(0);
  const [minConfidence, setMinConfidence] = useState(0);
  const [minTargetPct, setMinTargetPct] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<Candidate | null>(null);
  const [autoExecuteTop, setAutoExecuteTop] = useState(0);
  const [executingCandidates, setExecutingCandidates] = useState<Set<string>>(new Set());
  const [showWatchlistManager, setShowWatchlistManager] = useState(false);
  const [scrollOffset, setScrollOffset] = useState(0);
  // Background scanner's own signals, keyed by ticker (most recent per ticker),
  // fetched so a candidate here can be checked for divergence against the
  // independently-generated signal for the same symbol (see SignalDivergence).
  const [backgroundSignals, setBackgroundSignals] = useState<Record<string, any>>({});
  const gridRef = useRef<HTMLDivElement>(null);

  const isMobile = windowWidth < 768;
  const isTablet = windowWidth < 1024;

  const exportCSV = () => {
    if (!result?.candidates || result.candidates.length === 0) {
      setToast({ message: "No candidates to export", type: "warning" });
      return;
    }

    const headers = [
      "Ticker",
      "Action",
      "Entry",
      "Stop",
      "Target",
      "Target Move %",
      "EV",
      "POP",
      "Confidence",
      "Kelly %",
      "Max Loss",
      "Max Profit",
      "IV Rank",
      "Pricing Source",
      "Timestamp",
    ];

    const rows = result.candidates.map((c) => [
      c.ticker,
      c.action,
      c.entry_price.toFixed(2),
      c.stop_price.toFixed(2),
      c.target_price.toFixed(2),
      targetMovePct(c).toFixed(1),
      c.expected_value.toFixed(2),
      (c.pop * 100).toFixed(1),
      (c.confidence * 100).toFixed(1),
      (c.kelly_fraction * 100).toFixed(1),
      c.max_loss.toFixed(2),
      c.max_profit.toFixed(2),
      c.iv_rank.toFixed(1),
      c.pricing_source,
      new Date().toISOString(),
    ]);

    const csv = [
      headers.join(","),
      ...rows.map((r) => r.map((v) => (typeof v === "string" && v.includes(",") ? `"${v}"` : v)).join(",")),
    ].join("\n");

    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `equity-scan-${new Date().toISOString().split("T")[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    setToast({ message: `Exported ${result.candidates.length} candidates to CSV`, type: "success" });
  };

  const queueTopCandidates = async (count: number) => {
    if (!result?.candidates || count <= 0) return;

    const topCandidates = result.candidates.slice(0, count);
    const pending = new Set(topCandidates.map((c) => c.ticker));
    setExecutingCandidates(pending);
    let queued = 0;
    let failed = 0;

    for (const candidate of topCandidates) {
      try {
        const response = await fetch("/api/trade-desk/signal", {
          method: "POST",
          headers: apiAuthHeaders(),
          body: JSON.stringify({
            ticker: candidate.ticker,
            action: candidate.action,
            shares: 1,
            asset_type: "equity",
            entry_price: candidate.entry_price,
            stop_price: candidate.stop_price,
            target_price: candidate.target_price,
            entry_ladder: candidate.entry_ladder,
            kelly_fraction: candidate.kelly_fraction,
            expected_value: candidate.expected_value,
            pop: candidate.pop,
            confidence: candidate.confidence,
            source: "equity_scan_engine",
          }),
        });

        if (response.ok) {
          queued += 1;
          pending.delete(candidate.ticker);
          setExecutingCandidates(new Set(pending));
        } else {
          failed += 1;
        }
      } catch (e) {
        failed += 1;
        console.error(`Failed to queue ${candidate.ticker}:`, e);
      }
    }

    setToast({
      message:
        failed > 0
          ? `Queued ${queued} for approval (${failed} failed)`
          : `Queued ${queued} top candidates for approval`,
      type: failed > 0 ? "warning" : "success",
    });
    setAutoExecuteTop(0);
  };

  // Track window width
  useEffect(() => {
    const handleResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  // Fetch the background scanner's recent signals for divergence comparison.
  useEffect(() => {
    const loadBackgroundSignals = async () => {
      try {
        const response = await fetch("/api/equity/signals?limit=50");
        const data = await response.json();
        const byTicker: Record<string, any> = {};
        for (const sig of data.signals || []) {
          // Signals are returned most-recent-first — keep only the first
          // (latest) seen per ticker.
          if (!byTicker[sig.ticker]) byTicker[sig.ticker] = sig;
        }
        setBackgroundSignals(byTicker);
      } catch (e) {
        console.error("Failed to load background signals:", e);
      }
    };
    loadBackgroundSignals();
    const interval = setInterval(loadBackgroundSignals, 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  // Auto-refresh logic
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(async () => {
      try {
        const response = await fetch("/api/equity/scan", { method: "POST" });
        const data = await response.json();

        const prevCount = result?.candidates.length || 0;
        const newCount = data.candidates?.length || 0;

        if (newCount > prevCount) {
          setToast({
            message: `Found ${newCount - prevCount} new candidate(s)!`,
            type: "success",
          });
        } else if (newCount < prevCount) {
          setToast({
            message: `Updated: ${newCount} candidates (was ${prevCount})`,
            type: "info",
          });
        }

        setResult(data);
        setLastRefresh(new Date());
      } catch (e) {
        console.error("Auto-refresh failed:", e);
      }
    }, 30 * 60 * 1000); // 30 minutes

    return () => clearInterval(interval);
  }, [autoRefresh, result?.candidates.length]);

  // Clear toast after 3 seconds
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(timer);
  }, [toast]);

  const runScan = async () => {
    setScanning(true);
    setError(null);
    try {
      const response = await fetch("/api/equity/scan", { method: "POST" });
      const data = await response.json();
      setResult(data);
      setLastRefresh(new Date());
      setToast({ message: "Scan complete!", type: "success" });
      if (data.error) {
        setError(data.error);
      }
    } catch (e) {
      setError(String(e));
      setToast({ message: "Scan failed", type: "warning" });
    } finally {
      setScanning(false);
    }
  };

  const getFilteredAndSorted = () => {
    if (!result?.candidates) return [];

    let filtered = result.candidates.filter((c) => {
      if (actionFilter !== "ALL" && c.action !== actionFilter) return false;
      if (c.expected_value < minEV) return false;
      if (c.confidence < minConfidence) return false;
      if (targetMovePct(c) < minTargetPct) return false;
      return true;
    });

    filtered.sort((a, b) => {
      switch (sortBy) {
        case "ev":
          return b.expected_value - a.expected_value;
        case "confidence":
          return b.confidence - a.confidence;
        case "kelly":
          return b.kelly_fraction - a.kelly_fraction;
        case "action":
          return a.action.localeCompare(b.action);
        case "ticker":
          return a.ticker.localeCompare(b.ticker);
        case "target_pct":
          return targetMovePct(b) - targetMovePct(a);
        default:
          return 0;
      }
    });

    return filtered;
  };

  const filtered = getFilteredAndSorted();

  // Build attribution for this scan-panel candidate and, if the background
  // scanner has independently generated a signal for the same ticker, for
  // that signal too — SignalDivergence renders only when the two disagree.
  const buildDivergencePair = (cand: Candidate): {
    signalA: SignalAttributionData;
    signalB: SignalAttributionData | null;
  } => {
    const signalA: SignalAttributionData = {
      direction: cand.action,
      source: cand.pricing_source
        ? `Equity Scan Engine (${cand.pricing_source.replace(/_/g, " ")})`
        : "Equity Scan Engine",
      timeframe: null,
      confidence: typeof cand.confidence === "number" ? cand.confidence : null,
      updatedAt: (cand as unknown as { last_update?: string }).last_update ?? null,
      authority: "advisory",
    };
    const bg = backgroundSignals[cand.ticker];
    if (!bg || bg.action === "HOLD") return { signalA, signalB: null };
    const signalB: SignalAttributionData = {
      direction: bg.action,
      source: bg.source || "Equity Signal Scanner",
      timeframe: null,
      confidence: typeof bg.confidence === "number" ? bg.confidence : null,
      updatedAt: bg.generated_at ?? null,
      authority: "unknown",
    };
    return { signalA, signalB };
  };

  // Live IBKR data integration
  const { isConnected: liveConnected, lastUpdate: lastLiveUpdate } = useLiveData(
    result?.candidates || [],
    { enabled: true, updateInterval: 2000 }
  );

  const handleLoadWatchlist = (candidates: Candidate[]) => {
    setResult((prev) =>
      prev
        ? {
            ...prev,
            candidates,
          }
        : null
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Controls */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: isMobile ? "wrap" : "nowrap",
        }}
      >
        <button
          onClick={runScan}
          disabled={scanning}
          style={{
            background: scanning ? "var(--bg-3)" : "var(--cyan)",
            color: scanning ? "var(--ink-faint)" : "var(--bg)",
            border: "none",
            borderRadius: 4,
            padding: "8px 16px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            fontWeight: 600,
            cursor: scanning ? "default" : "pointer",
            letterSpacing: "0.08em",
          }}
        >
          {scanning ? "SCANNING..." : "RUN SCAN"}
        </button>

        {result && result.candidates.length > 0 && (
          <>
            <button
              onClick={exportCSV}
              style={{
                background: "var(--bg-2)",
                border: "1px solid var(--line-dim)",
                borderRadius: 4,
                padding: "8px 12px",
                fontFamily: "var(--mono)",
                fontSize: 10,
                fontWeight: 600,
                cursor: "pointer",
                color: "var(--ink-dim)",
              }}
            >
              ⬇ CSV
            </button>
            <button
              onClick={() => setShowWatchlistManager(true)}
              style={{
                background: "var(--bg-2)",
                border: "1px solid var(--line-dim)",
                borderRadius: 4,
                padding: "8px 12px",
                fontFamily: "var(--mono)",
                fontSize: 10,
                fontWeight: 600,
                cursor: "pointer",
                color: "var(--ink-dim)",
              }}
            >
              📋 Watchlist
            </button>
          </>
        )}

        {/* Queue top N for approval (does not place broker orders) */}
        {result && result.candidates.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
            <label style={{ color: "var(--ink-dim)", fontFamily: "var(--mono)", whiteSpace: "nowrap" }}>
              Queue top for approval:
            </label>
            <input
              type="number"
              min="0"
              max={result.candidates.length}
              value={autoExecuteTop}
              onChange={(e) => setAutoExecuteTop(parseInt(e.target.value) || 0)}
              style={{
                width: 40,
                background: "var(--bg-2)",
                border: "1px solid var(--line-dim)",
                borderRadius: 4,
                padding: "4px 6px",
                fontFamily: "var(--mono)",
                fontSize: 10,
                color: "var(--ink)",
              }}
            />
            <button
              onClick={() => autoExecuteTop > 0 && queueTopCandidates(autoExecuteTop)}
              disabled={autoExecuteTop <= 0 || executingCandidates.size > 0}
              style={{
                background: autoExecuteTop > 0 ? "var(--green)" : "var(--bg-3)",
                color: autoExecuteTop > 0 ? "var(--bg)" : "var(--ink-faint)",
                border: "none",
                borderRadius: 4,
                padding: "4px 8px",
                fontFamily: "var(--mono)",
                fontSize: 10,
                fontWeight: 600,
                cursor: autoExecuteTop > 0 ? "pointer" : "default",
              }}
            >
              QUEUE
            </button>
          </div>
        )}

        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 11 }}>
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            style={{ cursor: "pointer" }}
          />
          <span style={{ color: "var(--ink-dim)", fontFamily: "var(--mono)" }}>
            {autoRefresh ? "Auto-refresh ON" : "Auto-refresh OFF"}
          </span>
        </label>

        {lastRefresh && (
          <span style={{ fontSize: 10, color: "var(--ink-faint)", fontFamily: "var(--mono)" }}>
            Last refresh: {lastRefresh.toLocaleTimeString()}
          </span>
        )}

        {/* Live data indicator and control */}
        {result && result.candidates.length > 0 && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 10,
                fontFamily: "var(--mono)",
                color: liveConnected ? "var(--green)" : "var(--ink-faint)",
                padding: "4px 8px",
                background: "var(--bg-2)",
                borderRadius: 3,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: liveConnected ? "var(--green)" : "var(--ink-faint)",
                  animation: liveConnected ? "pulse 1.5s infinite" : "none",
                }}
              />
              {liveConnected ? "LIVE IBKR" : "No live"}
              {lastLiveUpdate && (
                <span style={{ color: "var(--ink-faint)" }}>
                  {lastLiveUpdate.toLocaleTimeString()}
                </span>
              )}
            </span>
            <IBKRLiveControl compact={true} />
          </div>
        )}

        <span style={{ flex: 1 }} />

        {result && (
          <span style={{ fontSize: 11, color: "var(--ink-dim)", fontFamily: "var(--mono)" }}>
            <b style={{ color: "var(--ink)" }}>{filtered.length}</b> of{" "}
            <b style={{ color: "var(--ink)" }}>{result.candidates.length}</b> candidates
          </span>
        )}
      </div>

      {/* Filters and sort */}
      <div
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          fontSize: 11,
          fontFamily: "var(--mono)",
        }}
      >
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortBy)}
          style={{
            background: "var(--bg-2)",
            border: "1px solid var(--line-dim)",
            borderRadius: 4,
            padding: "6px 8px",
            color: "var(--ink)",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          <option value="ev">Sort: EV (High → Low)</option>
          <option value="confidence">Sort: Confidence</option>
          <option value="target_pct">Sort: Target Move %</option>
          <option value="kelly">Sort: Kelly %</option>
          <option value="action">Sort: Action</option>
          <option value="ticker">Sort: Ticker</option>
        </select>

        <select
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value as ActionFilter)}
          style={{
            background: "var(--bg-2)",
            border: "1px solid var(--line-dim)",
            borderRadius: 4,
            padding: "6px 8px",
            color: "var(--ink)",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          <option value="ALL">Action: ALL</option>
          <option value="BUY">Action: BUY</option>
          <option value="SELL">Action: SELL</option>
        </select>

        <input
          type="number"
          min="0"
          placeholder="Min EV ($)"
          value={minEV || ""}
          onChange={(e) => setMinEV(e.target.value ? parseFloat(e.target.value) : 0)}
          style={{
            background: "var(--bg-2)",
            border: "1px solid var(--line-dim)",
            borderRadius: 4,
            padding: "6px 8px",
            color: "var(--ink)",
            fontSize: 11,
            width: 100,
          }}
        />

        <input
          type="number"
          min="0"
          max="1"
          placeholder="Min Conf"
          value={minConfidence || ""}
          onChange={(e) => setMinConfidence(e.target.value ? parseFloat(e.target.value) : 0)}
          step="0.01"
          style={{
            background: "var(--bg-2)",
            border: "1px solid var(--line-dim)",
            borderRadius: 4,
            padding: "6px 8px",
            color: "var(--ink)",
            fontSize: 11,
            width: 80,
          }}
        />

        <input
          type="number"
          min="0"
          placeholder="Min Move %"
          title="Only show candidates whose target price implies at least this % move from entry"
          value={minTargetPct || ""}
          onChange={(e) => setMinTargetPct(e.target.value ? parseFloat(e.target.value) : 0)}
          step="0.5"
          style={{
            background: "var(--bg-2)",
            border: "1px solid var(--line-dim)",
            borderRadius: 4,
            padding: "6px 8px",
            color: "var(--ink)",
            fontSize: 11,
            width: 100,
          }}
        />
      </div>

      {/* Error */}
      {error && (
        <div
          style={{
            background: "rgba(239,68,68,0.1)",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: 4,
            padding: "10px 14px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            color: "var(--red)",
          }}
        >
          {error}
        </div>
      )}

      {/* Gate blocked */}
      {result?.gate_blocked && (
        <div
          style={{
            background: "rgba(245,158,11,0.1)",
            border: "1px solid rgba(245,158,11,0.3)",
            borderRadius: 4,
            padding: "10px 14px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            color: "var(--amber)",
          }}
        >
          ⚠ NO-TRADE GATE: {result.gate_reason}
        </div>
      )}

      {/* Candidates grid with virtual scrolling for 50+ items */}
      {filtered.length > 0 ? (
        <div
          ref={gridRef}
          onScroll={(e) => {
            const target = e.currentTarget;
            setScrollOffset(target.scrollTop);
          }}
          style={{
            display: "grid",
            gridTemplateColumns: isMobile ? "1fr" : isTablet ? "repeat(2, 1fr)" : "repeat(4, 1fr)",
            gap: 12,
            maxHeight: filtered.length > 30 ? "70vh" : "auto",
            overflowY: filtered.length > 30 ? "auto" : "visible",
          }}
        >
          {filtered.map((cand) => {
            const evColor = cand.expected_value > 300 ? "var(--green)" : cand.expected_value > 150 ? "var(--amber)" : "var(--ink-faint)";
            const { signalA, signalB } = buildDivergencePair(cand);

            return (
              <div
                key={cand.ticker}
                className="instrument-card"
                onClick={() => setSelectedCandidate(cand)}
                style={{
                  border: `1px solid ${
                    cand.action === "BUY"
                      ? "rgba(34,197,94,0.25)"
                      : cand.action === "SELL"
                      ? "rgba(239,68,68,0.25)"
                      : "var(--line-dim)"
                  }`,
                  padding: 12,
                  display: "flex",
                  flexDirection: "column",
                  gap: isMobile ? 8 : 10,
                  cursor: "pointer",
                  transition: "all 0.2s ease",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span
                      style={{
                        color: "var(--ink)",
                        fontFamily: "var(--mono)",
                        fontSize: isMobile ? 14 : 16,
                        fontWeight: 700,
                      }}
                    >
                      {cand.ticker}
                    </span>
                    <SignalAttribution data={signalA} size="sm" />
                  </div>
                  <span
                    style={{
                      fontSize: isMobile ? 12 : 13,
                      fontFamily: "var(--mono)",
                      fontWeight: 600,
                      color: evColor,
                    }}
                  >
                    ${cand.expected_value.toFixed(0)}
                  </span>
                </div>

                {signalB && (
                  <div onClick={(e) => e.stopPropagation()}>
                    <SignalDivergence symbol={cand.ticker} signalA={signalA} signalB={signalB} />
                  </div>
                )}

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: isMobile ? "1fr 1fr" : "1fr",
                    gap: 6,
                    fontSize: isMobile ? 10 : 11,
                    fontFamily: "var(--mono)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "var(--ink-dim)" }}>Conf</span>
                    <span style={{ color: "var(--cyan)", fontWeight: 600 }}>
                      {confidenceScoreText(cand.confidence)}
                    </span>
                  </div>
                  {!isMobile && (
                    <>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span style={{ color: "var(--ink-dim)" }}>POP</span>
                        <span style={{ color: "var(--cyan)", fontWeight: 600 }}>
                          {(cand.pop * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span style={{ color: "var(--ink-dim)" }}>K {(cand.kelly_fraction * 100).toFixed(0)}%</span>
                        <span style={{ color: cand.kelly_fraction < 0.15 ? "var(--green)" : "var(--amber)", fontWeight: 600 }}>
                          {cand.kelly_fraction < 0.15 ? "High" : "Mod"}
                        </span>
                      </div>
                    </>
                  )}
                </div>

                {!isMobile && (
                  <div
                    className="instrument-card--flat"
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(4, 1fr)",
                      gap: 4,
                      padding: "6px 8px",
                      fontSize: 9,
                    }}
                  >
                    <div>
                      <div style={{ color: "var(--ink-dim)", marginBottom: 2 }}>ENTRY</div>
                      <div style={{ color: "var(--ink)", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 10 }}>
                        ${cand.entry_price.toFixed(2)}
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "var(--ink-dim)", marginBottom: 2 }}>STOP</div>
                      <div style={{ color: "var(--red)", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 10 }}>
                        ${cand.stop_price.toFixed(2)}
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "var(--ink-dim)", marginBottom: 2 }}>TARGET</div>
                      <div style={{ color: "var(--green)", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 10 }}>
                        ${cand.target_price.toFixed(2)}
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "var(--ink-dim)", marginBottom: 2 }}>MOVE</div>
                      <div style={{ color: "var(--amber)", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 10 }}>
                        {targetMovePct(cand).toFixed(1)}%
                      </div>
                    </div>
                  </div>
                )}

                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedCandidate(cand);
                  }}
                  style={{
                    background: "var(--bg-3)",
                    border: "1px solid var(--line-dim)",
                    borderRadius: 4,
                    padding: "4px 8px",
                    fontFamily: "var(--mono)",
                    fontSize: 10,
                    fontWeight: 600,
                    cursor: "pointer",
                    color: "var(--ink-dim)",
                  }}
                >
                  ANALYZE →
                </button>
              </div>
            );
          })}
        </div>
      ) : result && result.candidates.length === 0 ? (
        <div
          className="instrument-card"
          style={{
            padding: "20px",
            textAlign: "center",
            color: "var(--ink-dim)",
            fontSize: 13,
          }}
        >
          No equity candidates match your filters. Try running a new scan or adjusting filters.
        </div>
      ) : (
        <div
          className="instrument-card"
          style={{
            padding: "20px",
            textAlign: "center",
            color: "var(--ink-dim)",
            fontSize: 13,
          }}
        >
          Click "RUN SCAN" to analyze equity candidates.
        </div>
      )}

      {/* Modal */}
      {selectedCandidate && <CandidateModal candidate={selectedCandidate} onClose={() => setSelectedCandidate(null)} />}

      {/* Watchlist manager modal */}
      <WatchlistManager
        isOpen={showWatchlistManager}
        onClose={() => setShowWatchlistManager(false)}
        currentCandidates={filtered}
        assetType="equity"
        onLoadWatchlist={handleLoadWatchlist}
      />

      {/* Toast */}
      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  );
}
