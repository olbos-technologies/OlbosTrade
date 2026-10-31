/**
 * Equity Desk — discovery | chart | intelligence + order composer.
 * Phase C: display + evaluate + queue submit. No _execute_signal changes.
 */

import React, { useState } from "react";
import ChartWorkstation from "../../pages/ChartWorkstation";
import { PanelChrome, usePanelLayout } from "../PanelLayoutManager";
import EquityDiscoveryRail from "./EquityDiscoveryRail";
import EquityIntelligenceRail, { type EligibilitySnapshot } from "./EquityIntelligenceRail";
import EquityOrderComposer from "./EquityOrderComposer";
import "./EquityDesk.css";

export default function EquityDesk() {
  const [symbol, setSymbol] = useState("AAPL");
  const [eligibility, setEligibility] = useState<EligibilitySnapshot | null>(null);
  const { layout, toggle, reset } = usePanelLayout();

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        background: "var(--bg)",
      }}
    >
      <div className="desk-tool-rail" style={{ justifyContent: "flex-start", alignItems: "center" }}>
        <span className="kicker" style={{ letterSpacing: "0.1em", textTransform: "uppercase", marginRight: 4 }}>
          Equity desk
        </span>
        <span className="mono" style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>
          {symbol}
        </span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn-ghost" style={{ padding: "4px 10px", fontSize: 10 }} onClick={reset}>
          Reset panels
        </button>
      </div>

      <div
        className="equity-desk-grid"
        style={{
          "--discovery-width": `${layout.discoveryCollapsed ? 36 : layout.discoveryWidth}px`,
          "--intelligence-width": `${layout.intelligenceCollapsed ? 36 : layout.intelligenceWidth}px`,
        } as React.CSSProperties}
      >
        <div className="equity-desk-discovery">
        <PanelChrome
          title="Discovery"
          collapsed={layout.discoveryCollapsed}
          onToggle={() => toggle("discovery")}
          style={{ minHeight: 0 }}
        >
          <EquityDiscoveryRail symbol={symbol} onSelect={setSymbol} />
        </PanelChrome>
        </div>

        <div className="equity-desk-center">
          <div className="equity-desk-chart">
            <ChartWorkstation symbol={symbol} onSymbolChange={setSymbol} compact />
          </div>
          {!layout.activityCollapsed && (
            <EquityOrderComposer symbol={symbol} onEvaluated={setEligibility} />
          )}
          <button
            type="button"
            onClick={() => toggle("activity")}
            aria-expanded={!layout.activityCollapsed}
            className="desk-tool-rail__btn"
            style={{
              width: "100%",
              borderRadius: 0,
              borderTop: "1px solid var(--line-dim)",
              padding: 6,
            }}
          >
            {layout.activityCollapsed ? "SHOW COMPOSER" : "HIDE COMPOSER"}
          </button>
        </div>

        <div className="equity-desk-intelligence">
        <PanelChrome
          title="Intelligence"
          collapsed={layout.intelligenceCollapsed}
          onToggle={() => toggle("intelligence")}
          style={{ minHeight: 0 }}
        >
          <EquityIntelligenceRail symbol={symbol} eligibility={eligibility} />
        </PanelChrome>
        </div>
      </div>
    </div>
  );
}
