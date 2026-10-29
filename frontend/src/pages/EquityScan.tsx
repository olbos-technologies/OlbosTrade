/**
 * Equity Scan Page — A-grade institutional equity candidate selection.
 *
 * Integrates:
 * - Multi-asset equity scanning (not limited to watchlist)
 * - EV ranking (Probability of Profit × max profit − loss × max loss)
 * - Entry ladder visualization + kelly scaling
 * - IV rank + implied move awareness (from options market)
 * - Earnings gate + NO-TRADE gate status
 *
 * Grade: A (Institutional UX for retail autopilot)
 */

import React from "react";
import EquityScanPanel from "../components/EquityScanPanel";

export default function EquityScan() {
  return (
    <div style={{ padding: 16, height: "100%", overflowY: "auto" }}>
      {/* Page Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ margin: 0, marginBottom: 8, fontSize: 24, fontWeight: 700 }}>Equity Scan</h1>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-dim)", lineHeight: 1.5 }}>
          A-grade institutional equity candidate engine. Ranks stocks by{" "}
          <span style={{ fontFamily: "var(--mono)", fontWeight: 600, color: "var(--green)" }}>Expected Value</span>
          {" "}
          with technical indicators, entry ladder logic, and kelly-scaled position sizing.
        </p>
      </div>

      {/* Main Scan Panel */}
      <div style={{ marginBottom: 24 }}>
        <EquityScanPanel />
      </div>

      {/* Information Cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%,300px), 1fr))",
          gap: 16,
        }}
      >
        {/* What is EV? */}
        <div className="instrument-card" style={{ padding: 12 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 600 }}>Expected Value (EV)</h3>
          <p style={{ margin: 0, fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.5 }}>
            <span style={{ fontFamily: "var(--mono)" }}>EV = POP × Max Profit − (1−POP) × Max Loss</span>
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--ink-faint)" }}>
            Real dollar edge per share. Higher EV = better risk-adjusted entry setup.
          </p>
        </div>

        {/* POP Estimation */}
        <div className="instrument-card" style={{ padding: 12 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 600 }}>Probability of Profit (POP)</h3>
          <p style={{ margin: 0, fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.5 }}>
            Estimated from technical distance: <span style={{ fontFamily: "var(--mono)" }}>POP = Stop Distance / (Target Distance + Stop Distance)</span>
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--ink-faint)" }}>
            Reflects probability of reaching profit target before hitting stop loss.
          </p>
        </div>

        {/* Entry Ladder */}
        <div className="instrument-card" style={{ padding: 12 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 600 }}>Entry Ladder</h3>
          <p style={{ margin: 0, fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.5 }}>
            Kelly &lt; 15% → 1 tranche (full entry)
            <br />
            Kelly 15–30% → 2 tranches (scale-in)
            <br />
            Kelly 30–50% → 3 tranches (incremental)
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--ink-faint)" }}>
            Staged execution reduces entry risk, compounds on confirmed setups.
          </p>
        </div>

        {/* Technical Indicators */}
        <div className="instrument-card" style={{ padding: 12 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 600 }}>Technical Scoring</h3>
          <div style={{ fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.8 }}>
            <div>
              <span style={{ fontWeight: 600 }}>RSI</span> — Overbought/oversold momentum
            </div>
            <div>
              <span style={{ fontWeight: 600 }}>MACD</span> — Trend confirmation & crosses
            </div>
            <div>
              <span style={{ fontWeight: 600 }}>Bollinger Bands</span> — Mean reversion & extremes
            </div>
          </div>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--ink-faint)" }}>
            Engine synthesizes multiple indicators for robust signal generation.
          </p>
        </div>

        {/* Kelly Fraction */}
        <div className="instrument-card" style={{ padding: 12 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 600 }}>Kelly Fraction</h3>
          <p style={{ margin: 0, fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.5 }}>
            <span style={{ fontFamily: "var(--mono)" }}>f* = (b·p − q) / b</span>
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--ink-faint)" }}>
            Optimal position size. &lt;15% = high confidence, &gt;50% = thin edge (size down).
          </p>
        </div>

        {/* Earnings + IV Awareness */}
        <div className="instrument-card" style={{ padding: 12 }}>
          <h3 style={{ margin: "0 0 8px", fontSize: 13, fontWeight: 600 }}>Risk Gates</h3>
          <p style={{ margin: 0, fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.5 }}>
            • Earnings gate: Blocks signals within 3 days of earnings
            <br />
            • IV rank awareness: Options implied move informs profit targets
          </p>
          <p style={{ margin: "6px 0 0", fontSize: 11, color: "var(--ink-faint)" }}>
            Multi-layer protection against binary events and vol mispricing.
          </p>
        </div>
      </div>
    </div>
  );
}
