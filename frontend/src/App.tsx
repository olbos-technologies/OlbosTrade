import React, { lazy, Suspense, useState } from "react";
import "./index.css";
import TerminalLayout  from "./components/TerminalLayout";
const Dashboard = lazy(() => import("./pages/Dashboard"));
const TradeDesk = lazy(() => import("./pages/TradeDesk"));
const TradeDeskV2 = lazy(() => import("./pages/TradeDeskV2"));
const Journal = lazy(() => import("./pages/Journal"));
const ModeAnalytics = lazy(() => import("./pages/ModeAnalytics"));
// Consolidated hubs (each folds two former pages behind tabs)
const RiskCenter = lazy(() => import("./pages/RiskCenter"));
const SignalsCenter = lazy(() => import("./pages/SignalsCenter"));
const OptionsSignals = lazy(() => import("./pages/OptionsSignals"));
const SignalResearch = lazy(() => import("./pages/SignalResearch"));
const ResearchCenter = lazy(() => import("./pages/ResearchCenter"));
const BacktestCenter = lazy(() => import("./pages/BacktestCenter"));
const ScanCenter = lazy(() => import("./pages/ScanCenter"));
const OptionsFlow = lazy(() => import("./pages/OptionsFlow"));
const OptionsChain = lazy(() => import("./pages/options/OptionsChain"));
const IncomeStrategiesCenter = lazy(() => import("./pages/options/IncomeStrategiesCenter"));
const SystemCenter = lazy(() => import("./pages/SystemCenter"));
const StrategyBuilder = lazy(() => import("./pages/strategies/StrategyBuilder"));
const Alerts = lazy(() => import("./pages/strategies/Alerts"));
// Markets module
const Heatmap = lazy(() => import("./pages/markets/Heatmap"));
const Watchlists = lazy(() => import("./pages/markets/Watchlists"));
const SectorRotation = lazy(() => import("./pages/markets/SectorRotation"));
const ChartWorkstation = lazy(() => import("./pages/ChartWorkstation"));
const NewsEventsCenter = lazy(() => import("./pages/markets/NewsEventsCenter"));
import { isTradeDeskV2Enabled } from "./trade-desk/featureFlags";
const SignalCalendar = lazy(() => import("./components/SignalCalendar"));

function UnknownPage() {
  return (
    <div style={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 8,
      color: "var(--ink-faint)", fontFamily: "var(--mono)",
    }}>
      <div style={{ fontSize: 13, letterSpacing: "0.08em", textTransform: "uppercase" }}>Page unavailable</div>
      <div style={{ fontSize: 11, color: "var(--ink-dim)" }}>Return to Command Center or select another workspace.</div>
    </div>
  );
}

function tradeDeskPages(v2: boolean): Record<string, React.ComponentType> {
  if (!v2) {
    return {
      paper: TradeDesk,
      "trade:overview":  () => <TradeDesk initialTab="overview" />,
      "trade:copilot":   () => <TradeDesk initialTab="approvals" />,
      "trade:orders":    () => <TradeDesk initialTab="signals" />,
      "trade:positions": () => <TradeDesk initialTab="positions" />,
      "trade:logs":      () => <TradeDesk initialTab="pnl" />,
      "trade:execlog":   () => <TradeDesk initialTab="execution" />,
    };
  }
  return {
    paper: () => <TradeDeskV2 initialTab="trade:overview" />,
    "trade:overview":  () => <TradeDeskV2 initialTab="trade:overview" />,
    "trade:equity":    () => <TradeDeskV2 initialTab="trade:equity" />,
    "trade:options":   () => <TradeDeskV2 initialTab="trade:options" />,
    "trade:copilot":   () => <TradeDeskV2 initialTab="trade:copilot" />,
    "trade:positions": () => <TradeDeskV2 initialTab="trade:positions" />,
    "trade:orders":    () => <TradeDeskV2 initialTab="trade:orders" />,
    "trade:execlog":   () => <TradeDeskV2 initialTab="trade:execlog" />,
    "trade:replay":    () => <TradeDeskV2 initialTab="trade:replay" />,
    "trade:settings":  () => <TradeDeskV2 initialTab="trade:settings" />,
    // Legacy deep-links → overview or closest v2 tab
    "trade:logs":      () => <TradeDeskV2 initialTab="trade:overview" />,
  };
}

const BASE_PAGES: Record<string, React.ComponentType> = {
  dashboard: Dashboard,
  equity:    SignalsCenter,
  backtest:  BacktestCenter,
  lab:       ResearchCenter,
  risk:      RiskCenter,
  journal:   Journal,
  analytics: ModeAnalytics,
  scan:      ScanCenter,
  settings:  SystemCenter,

  "strat:cards":     () => <SignalsCenter initialTab="strategies" />,
  "strat:health":    () => <SignalsCenter initialTab="health" />,
  "strat:calendar":  SignalCalendar,
  "strat:alpha-edge": () => <SignalsCenter initialTab="alpha-edge" />,
  "strat:signal-history": () => <SignalsCenter initialTab="history" />,
  "strat:builder":   StrategyBuilder,
  "strat:alerts":    Alerts,
  "options:signals": OptionsSignals,
  "strat:research":  SignalResearch,
  "options:chain":   OptionsChain,
  "options:income":  IncomeStrategiesCenter,
  "options:flow":    OptionsFlow,
  "risk:heat":       () => <RiskCenter initialTab="monitor" />,
  "risk:rules":      () => <RiskCenter initialTab="guardrails" />,
  "lab:scenario":    () => <ResearchCenter initialTab="scenario" />,
  "lab:strategy":    () => <ResearchCenter initialTab="lab" />,
  "lab:market":      () => <ResearchCenter initialTab="market" />,
  "lab:intel":       () => <ResearchCenter initialTab="intel" />,
  "lab:models":      () => <ResearchCenter initialTab="models" />,

  "markets:heatmaps":   Heatmap,
  "markets:watchlists": Watchlists,
  "markets:chart":      ChartWorkstation,
  "markets:news":       NewsEventsCenter,
  "markets:sector-rotation": SectorRotation,

  "system:broker":  () => <SystemCenter initialTab="broker" />,
  "system:market":  () => <SystemCenter initialTab="market" />,
  "system:quality": () => <SystemCenter initialTab="quality" />,
};

export default function App() {
  const [page, setPage] = useState("dashboard");
  const v2 = isTradeDeskV2Enabled();
  const PAGES = { ...BASE_PAGES, ...tradeDeskPages(v2) };
  const Page = PAGES[page] || UnknownPage;

  return (
    <TerminalLayout activePage={page} onNav={setPage}>
      <Suspense fallback={<div className="workspace-state"><strong>Loading workspace</strong><span>Preparing the requested terminal view…</span></div>}>
        <Page />
      </Suspense>
    </TerminalLayout>
  );
}
