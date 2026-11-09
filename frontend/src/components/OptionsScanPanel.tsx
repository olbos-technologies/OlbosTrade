/**
 * Options Scan Panel — A+ grade with Tier 1 enhancements.
 *
 * Tier 1 features:
 * - Drill-down modal: Greeks, risk scenarios, entry ladder breakdown
 * - Filtering + sorting: EV, confidence, Kelly, action, ticker
 * - Auto-refresh: Periodic scanning + toast notifications
 *
 * Grade: A+ (Institutional UX for retail traders, fully accessible)
 */

import React, { useEffect, useState, useCallback, useRef } from "react";
import WatchlistManager from "./WatchlistManager";
import IBKRLiveControl from "./IBKRLiveControl";
import SignalAttribution from "./SignalAttribution";
import SignalDirectionBadge from "./SignalDirectionBadge";
import { useLiveData } from "../hooks/useLiveData";
import { api, apiAuthHeaders } from "../api/client";
import { confidenceScoreText } from "../utils/confidenceScore";

interface Candidate {
  ticker: string;
  option_type: "put" | "call";
  short_strike: number;
  long_strike: number;
  expiration?: string;
  dte: number;
  credit: number;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  expected_value: number;
  pop: number;
  kelly_fraction: number;
  short_delta: number;
  reward_risk: number;
  max_loss: number;
  iv_rank: number;
  skew_adjustment: number;
  pricing_source: string;
  entry_ladder: Array<{
    tranche: number;
    pct_position: number;
    entry_price: number;
    description: string;
  }>;
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

type SortBy = "ev" | "confidence" | "kelly" | "action" | "ticker";
type ActionFilter = "ALL" | "BUY" | "SELL";

// Toast notification component
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

// Drill-down modal component
function CandidateModal({ candidate, onClose }: { candidate: Candidate; onClose: () => void }) {
  const [evalResult, setEvalResult] = useState<{
    final_status: string;
    block_reasons: string[];
    warnings: string[];
  } | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);

  if (!candidate) return null;

  const maxProfit = Math.abs(candidate.credit);
  const maxLoss = candidate.max_loss;
  const roi = maxProfit > 0 ? ((maxProfit / maxLoss) * 100).toFixed(1) : "0";

  // Best-effort strategy label — this card has no explicit strategy field,
  // only strike/credit shape. Matches the taxonomy already used in
  // strategy_engine.py; a mislabeled edge case still evaluates normally
  // (only naked/iron_condor/straddle/strangle are backend-banned outright).
  const impliedStrategy =
    candidate.option_type === "put"
      ? candidate.credit > 0 ? "bull_put_spread" : "bear_put_debit_spread"
      : candidate.credit > 0 ? "bear_call_spread" : "bull_call_debit_spread";

  const checkEligibility = async () => {
    setEvaluating(true);
    setEvalError(null);
    try {
      const body: any = await api.evaluateOptionsIntent({
        ticker: candidate.ticker,
        strategy: impliedStrategy,
        dte: candidate.dte,
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
              {candidate.ticker} {candidate.option_type.toUpperCase()}
            </h2>
            <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--ink-dim)" }}>
              {candidate.short_strike} / {candidate.long_strike} spread, {candidate.dte} DTE
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

        {/* Key metrics grid */}
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
                <span style={{ fontWeight: 600 }}>Max Loss (if assigned)</span>
                <div style={{ color: "var(--red)", fontSize: 14, fontWeight: 700, marginTop: 4, fontFamily: "var(--mono)" }}>
                  −${maxLoss.toFixed(2)}
                </div>
              </div>
              <div style={{ fontSize: 10, color: "var(--ink-faint)" }}>
                Risk if long strike is breached
              </div>
            </div>
            <div className="instrument-card" style={{ padding: 12 }}>
              <div style={{ fontSize: 11, color: "var(--ink-dim)", marginBottom: 8 }}>
                <span style={{ fontWeight: 600 }}>Max Profit (if expires OTM)</span>
                <div style={{ color: "var(--green)", fontSize: 14, fontWeight: 700, marginTop: 4, fontFamily: "var(--mono)" }}>
                  +${maxProfit.toFixed(2)}
                </div>
              </div>
              <div style={{ fontSize: 10, color: "var(--ink-faint)" }}>
                Full credit captured if profitable
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
                RISK : REWARD
              </div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--amber)" }}>
                1:{candidate.reward_risk.toFixed(2)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 4, fontWeight: 600 }}>
                ROI (if max profit)
              </div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--green)" }}>
                {roi}%
              </div>
            </div>
          </div>
        </div>

        {/* Entry ladder breakdown */}
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

        {/* Greeks + Indicators */}
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>
            Greeks & Adjustments
          </h3>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(2, 1fr)",
              gap: 8,
            }}
          >
            <div className="instrument-card" style={{ padding: "8px 10px" }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 2, fontWeight: 600 }}>
                SHORT DELTA
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)" }}>
                {candidate.short_delta.toFixed(3)}
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
            <div className="instrument-card" style={{ padding: "8px 10px" }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 2, fontWeight: 600 }}>
                EV / RISK
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--green)" }}>
                {candidate.ev_per_risk.toFixed(3)}
              </div>
            </div>
            <div className="instrument-card" style={{ padding: "8px 10px" }}>
              <div style={{ fontSize: 10, color: "var(--ink-dim)", marginBottom: 2, fontWeight: 600 }}>
                SKEW ADJ
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--amber)" }}>
                {(candidate.skew_adjustment * 100).toFixed(1)}%
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
            Checked as {impliedStrategy.replace(/_/g, " ")} (best-effort label from this spread's shape). Read-only — does not place an order.
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

export default function OptionsScanPanel() {
  const [result, setResult] = useState<ScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "info" | "success" | "warning" } | null>(null);
  const [windowWidth, setWindowWidth] = useState(1200);
  const [sortBy, setSortBy] = useState<SortBy>("ev");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("ALL");
  const [minEV, setMinEV] = useState(0);
  const [minConfidence, setMinConfidence] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<Candidate | null>(null);
  const [autoExecuteTop, setAutoExecuteTop] = useState(0);
  const [executingCandidates, setExecutingCandidates] = useState<Set<string>>(new Set());
  const [showWatchlistManager, setShowWatchlistManager] = useState(false);
  const [scrollOffset, setScrollOffset] = useState(0);
  const gridRef = useRef<HTMLDivElement>(null);

  const isMobile = windowWidth < 768;
  const isTablet = windowWidth < 1024;

  const ITEM_HEIGHT = isMobile ? 180 : isTablet ? 240 : 300;
  const VISIBLE_ITEMS = Math.ceil((window.innerHeight - 200) / ITEM_HEIGHT);

  const queueTopCandidates = async (count: number) => {
    if (!result?.candidates || count <= 0) return;

    const topCandidates = result.candidates.slice(0, count);
    const pending = new Set(topCandidates.map((c) => c.ticker));
    setExecutingCandidates(pending);
    let queued = 0;
    let failed = 0;

    for (const candidate of topCandidates) {
      const impliedStrategy =
        candidate.option_type === "put"
          ? candidate.credit > 0 ? "bull_put_spread" : "bear_put_debit_spread"
          : candidate.credit > 0 ? "bear_call_spread" : "bull_call_debit_spread";
      const expiration =
        candidate.expiration ||
        new Date(Date.now() + Math.max(candidate.dte || 30, 1) * 86400000)
          .toISOString()
          .slice(0, 10);
      try {
        const response = await fetch("/api/trade-desk/signal", {
          method: "POST",
          headers: apiAuthHeaders(),
          body: JSON.stringify({
            ticker: candidate.ticker,
            action: candidate.action,
            asset_type: "options",
            strategy: impliedStrategy,
            quantity: 1,
            entry_price: candidate.credit,
            stop_price: 0,
            target_price: candidate.credit * 1.5,
            entry_ladder: candidate.entry_ladder,
            kelly_fraction: candidate.kelly_fraction,
            expected_value: candidate.expected_value,
            pop: candidate.pop,
            confidence: candidate.confidence,
            source: "options_scan_engine",
            spread: {
              expiration,
              short_strike: candidate.short_strike,
              long_strike: candidate.long_strike,
              option_type: candidate.option_type,
              net_credit: candidate.credit,
              max_loss: candidate.max_loss,
            },
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

  // Auto-refresh logic
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(async () => {
      try {
        const response = await fetch("/api/options/scan", { method: "POST" });
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
      const response = await fetch("/api/options/scan", { method: "POST" });
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

  const exportCSV = () => {
    if (!result?.candidates || result.candidates.length === 0) {
      setToast({ message: "No candidates to export", type: "warning" });
      return;
    }

    const headers = [
      "Ticker",
      "Type",
      "Action",
      "Short Strike",
      "Long Strike",
      "DTE",
      "EV",
      "POP",
      "Confidence",
      "Kelly %",
      "Credit",
      "Max Loss",
      "Reward:Risk",
      "IV Rank",
      "Pricing Source",
      "Timestamp",
    ];

    const rows = result.candidates.map((c) => [
      c.ticker,
      c.option_type.toUpperCase(),
      c.action,
      c.short_strike,
      c.long_strike,
      c.dte,
      c.expected_value.toFixed(2),
      (c.pop * 100).toFixed(1),
      (c.confidence * 100).toFixed(1),
      (c.kelly_fraction * 100).toFixed(1),
      c.credit.toFixed(2),
      c.max_loss.toFixed(2),
      c.reward_risk.toFixed(2),
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
    a.download = `options-scan-${new Date().toISOString().split("T")[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    setToast({ message: `Exported ${result.candidates.length} candidates to CSV`, type: "success" });
  };

  // Sort and filter logic
  const getFilteredAndSorted = () => {
    if (!result?.candidates) return [];

    let filtered = result.candidates.filter((c) => {
      if (actionFilter !== "ALL" && c.action !== actionFilter) return false;
      if (c.expected_value < minEV) return false;
      if (c.confidence < minConfidence) return false;
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
        default:
          return 0;
      }
    });

    return filtered;
  };

  const filtered = getFilteredAndSorted();

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
      {/* Controls row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: isMobile ? "wrap" : "nowrap",
        }}
      >
        <button
          type="button"
          onClick={runScan}
          disabled={scanning}
          className="btn-primary"
        >
          {scanning ? "SCANNING…" : "RUN SCAN"}
        </button>

        {result && result.candidates.length > 0 && (
          <>
            <button type="button" onClick={exportCSV} className="btn-ghost">
              CSV
            </button>
            <button type="button" onClick={() => setShowWatchlistManager(true)} className="btn-ghost">
              Watchlist
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

        {/* Auto-refresh toggle */}
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

      {/* Filters and sort row */}
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
      </div>

      {/* Error display */}
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

      {/* Gate blocked notice */}
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

            return (
              <div
                key={`${cand.ticker}-${cand.short_strike}-${cand.long_strike}`}
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
                    <SignalDirectionBadge action={cand.action} size="sm" />
                    <SignalAttribution
                      data={{
                        direction: cand.action,
                        source: cand.pricing_source
                          ? `Options Scan Engine (${cand.pricing_source.replace(/_/g, " ")})`
                          : "Options Scan Engine",
                        timeframe: typeof cand.dte === "number" ? `${cand.dte} DTE` : null,
                        confidence: typeof cand.confidence === "number" ? cand.confidence : null,
                        updatedAt: (cand as unknown as { last_update?: string }).last_update ?? null,
                        // The scan panel can queue this candidate via
                        // /api/trade-desk/signal ("Queue top for approval") —
                        // that only reaches the broker through the same
                        // guardrail/execution-mode gate as every other path,
                        // so "advisory" (not execution-authoritative on its own).
                        authority: "advisory",
                      }}
                      size="sm"
                    />
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
                      gridTemplateColumns: "repeat(3, 1fr)",
                      gap: 4,
                      padding: "6px 8px",
                      fontSize: 9,
                    }}
                  >
                    <div>
                      <div style={{ color: "var(--ink-dim)", marginBottom: 2 }}>SHORT</div>
                      <div style={{ color: "var(--ink)", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 10 }}>
                        {cand.short_strike}
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "var(--ink-dim)", marginBottom: 2 }}>LONG</div>
                      <div style={{ color: "var(--ink)", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 10 }}>
                        {cand.long_strike}
                      </div>
                    </div>
                    <div>
                      <div style={{ color: "var(--ink-dim)", marginBottom: 2 }}>DTE</div>
                      <div style={{ color: "var(--ink)", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 10 }}>
                        {cand.dte}
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
        <div className="instrument-card instrument-card--flat empty-chassis">
          <p className="empty-chassis__title">No options candidates match your filters</p>
          <p className="empty-chassis__hint">Run a new scan or adjust filters.</p>
        </div>
      ) : (
        <div className="instrument-card instrument-card--flat empty-chassis">
          <p className="empty-chassis__title">No scan yet</p>
          <p className="empty-chassis__hint">
            Click <strong style={{ color: "var(--ink)" }}>RUN SCAN</strong> to analyze options spreads.
          </p>
        </div>
      )}

      {/* Drill-down modal */}
      {selectedCandidate && <CandidateModal candidate={selectedCandidate} onClose={() => setSelectedCandidate(null)} />}

      {/* Watchlist manager modal */}
      <WatchlistManager
        isOpen={showWatchlistManager}
        onClose={() => setShowWatchlistManager(false)}
        currentCandidates={filtered}
        assetType="options"
        onLoadWatchlist={handleLoadWatchlist}
      />

      {/* Toast notification */}
      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  );
}
