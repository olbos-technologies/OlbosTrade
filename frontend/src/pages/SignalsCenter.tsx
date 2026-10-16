/**
 * SignalsCenter — single "Signals" nav entry combining the live Equity Signals
 * feed with the Strategy reference page (its "current signals" overlapped Equity
 * Signals), a History tab surfacing each asset class's persisted signal log,
 * and a Health tab surfacing the full per-strategy diagnosis.
 */
import React, { useState } from "react";
import TabBar from "../components/TabBar";
import ErrorBoundary from "../components/ErrorBoundary";
import EquitySignals, { AssetToggle, type AssetTab } from "./EquitySignals";
import Strategy from "./Strategy";
import SignalResearch from "./SignalResearch";
import OptionsSignalHistory from "./OptionsSignalHistory";
import StrategyHealthPanel from "./StrategyHealthPanel";
import AlphaEdgePanel from "./AlphaEdgePanel";
import { useTabRoute } from "../hooks/useTabRoute";

export const TABS = [
  { key: "alpha-edge", label: "Alpha Edge" },
  { key: "signals", label: "Live Signals" },
  { key: "history", label: "History" },
  { key: "strategies", label: "Strategy Library" },
  { key: "health", label: "Health" },
];

function SignalsHistory() {
  const [asset, setAsset] = useState<AssetTab>("equities");
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "12px 16px 0" }}>
        <AssetToggle tab={asset} onChange={setAsset} />
      </div>
      <div style={{ flex: 1, overflow: asset === "equities" ? "hidden" : "auto", padding: asset === "options" ? 16 : 0 }}>
        {asset === "equities" ? <SignalResearch /> : <OptionsSignalHistory />}
      </div>
    </div>
  );
}

/** Tab -> the page key that renders it, so a tab change shows up in the URL. */
/** Exported so the route table can be checked against the page registry — see src/hooks/__tests__/tabRouteTable.test.tsx. */
/** The tab the page opens on when the URL names it without one. */
export const DEFAULT_TAB = "signals";

export const TAB_PAGE_KEYS = {
  signals: "equity",
  strategies: "strat:cards",
  health: "strat:health",
  "alpha-edge": "strat:alpha-edge",
  history: "strat:signal-history",
} as const;

export default function SignalsCenter({ initialTab = DEFAULT_TAB }: { initialTab?: string }) {
  const [tab, setTab] = useTabRoute(initialTab, TAB_PAGE_KEYS);
  return (
    <div className={`signals-center${tab === "signals" ? " signals-center--live" : ""}`}>
      <TabBar tabs={TABS} active={tab} onChange={setTab} label="Signal views" />
      <ErrorBoundary label="Signals">
        {tab === "alpha-edge" ? <AlphaEdgePanel />
          : tab === "signals" ? <EquitySignals />
          : tab === "history" ? <SignalsHistory />
          : tab === "health" ? <StrategyHealthPanel />
          : <Strategy />}
      </ErrorBoundary>
    </div>
  );
}
