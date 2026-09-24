import React from "react";
import { useLocation, useNavigate } from "react-router-dom";
import "./index.css";
import { canonicalPageKey, pageKeyToUrl, pathToPageKey } from "./terminalRoutes";
import TerminalLayout  from "./components/TerminalLayout";
import Dashboard       from "./pages/Dashboard";
import TradeDesk       from "./pages/TradeDesk";
import TradeDeskV2     from "./pages/TradeDeskV2";
import Journal         from "./pages/Journal";
import ModeAnalytics   from "./pages/ModeAnalytics";
// Consolidated hubs (each folds two former pages behind tabs)
import RiskCenter      from "./pages/RiskCenter";          // Risk Monitor + Guardrails
import SignalsCenter   from "./pages/SignalsCenter";       // Equity Signals + Strategy
import OptionsSignals  from "./pages/OptionsSignals";       // Live options spread signal feed
import SignalResearch  from "./pages/SignalResearch";       // Forward-return study over tracked signals
import ResearchCenter  from "./pages/ResearchCenter";      // Research Lab: Strategy Lab + Market/Regime + Chart + Intel
import BacktestCenter  from "./pages/BacktestCenter";      // Backtest + Symphony
import ScanCenter      from "./pages/ScanCenter";           // Options + Equity EV scan engines
import OptionsFlow     from "./pages/OptionsFlow";           // Options flow (grouped-nav sub-item)
import OptionsChain    from "./pages/options/OptionsChain";  // Live calls/puts for a symbol
import IncomeStrategiesCenter from "./pages/options/IncomeStrategiesCenter";
import SystemCenter    from "./pages/SystemCenter";
import AccountCenter   from "./pages/AccountCenter";
import StrategyBuilder from "./pages/strategies/StrategyBuilder"; // Configure + register a strategy experiment
import Alerts          from "./pages/strategies/Alerts";     // Smart Alert rules + notifications
// Markets module
import Heatmap         from "./pages/markets/Heatmap";
import Watchlists      from "./pages/markets/Watchlists";
import SectorRotation  from "./pages/markets/SectorRotation";
import ChartWorkstation from "./pages/ChartWorkstation";     // Price-action / market-structure chart
import NewsEventsCenter from "./pages/markets/NewsEventsCenter";
import { isTradeDeskV2Enabled } from "./trade-desk/featureFlags";
import SignalCalendar from "./components/SignalCalendar";

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

export function tradeDeskPages(v2: boolean): Record<string, React.ComponentType> {
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

export const BASE_PAGES: Record<string, React.ComponentType> = {
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
  "account:profile":  () => <AccountCenter initialTab="profile" />,
  "account:password": () => <AccountCenter initialTab="password" />,
  "account:sessions": () => <AccountCenter initialTab="sessions" />,
  "account:brokers":  () => <AccountCenter initialTab="brokers" />,
  "system:market":  () => <SystemCenter initialTab="market" />,
  "system:quality": () => <SystemCenter initialTab="quality" />,
};

export default function App() {
  // The URL is the single source of truth for which page is open.
  //
  // This was `useState("dashboard")`, which never read the pathname — so every
  // /terminal/* URL rendered the Dashboard while the address bar claimed
  // otherwise. Reload, bookmark, share and the browser Back button were all
  // silently broken. Deriving the page from the location fixes all four at
  // once, and history navigation comes free because the browser already tracks
  // it; there is no second copy of this state to drift.
  const location = useLocation();
  const navigate = useNavigate();

  const page = pathToPageKey(location.pathname);
  const v2 = isTradeDeskV2Enabled();
  const TRADE_PAGES = tradeDeskPages(v2);
  const PAGES = { ...BASE_PAGES, ...TRADE_PAGES };
  const isDeskV2Shell = v2 && Object.prototype.hasOwnProperty.call(TRADE_PAGES, page);

  // hasOwnProperty, not a bare PAGES[page]. The key comes straight from the
  // URL, so a plain lookup also finds everything on Object.prototype:
  // /terminal/constructor resolved to Object.prototype.constructor and threw
  // React error #31, and /terminal/__proto__ resolved to Object.prototype
  // itself and threw #130 — which took down the WHOLE SHELL, not just the
  // page, because it is not inside the page ErrorBoundary. Any visitor could
  // white-screen the terminal by typing a URL.
  const Page = Object.prototype.hasOwnProperty.call(PAGES, page)
    ? PAGES[page]
    : UnknownPage;

  // Nav pushes a history entry, so Back returns to the previous page rather
  // than leaving the terminal entirely — but only for a real change.
  //
  // Navigating unconditionally pushed an entry even when the target was the
  // page already open, so clicking the active nav item three times added three
  // identical entries and the next Back went nowhere. Back looked broken; it
  // was being asked to return to where it already was.
  const handleNav = React.useCallback(
    (key: string) => {
      const target = pageKeyToUrl(key);
      if (target === location.pathname) return;

      // Same page reached by a different spelling — /terminal and
      // /terminal/dashboard are both the Dashboard. Canonicalise the URL, but
      // replace rather than push: a Back that lands on a different URL showing
      // the identical page is the same confusion in a subtler form.
      // Compare CANONICAL keys. Some pages answer to two spellings —
      // /terminal/risk and /terminal/risk/heat are both RiskCenter's monitor
      // tab — and comparing the raw keys treated those as a real change, so
      // clicking the active nav item from the bare URL still pushed an entry
      // and Back still landed on an identical view.
      const samePage =
        canonicalPageKey(pathToPageKey(target)) ===
        canonicalPageKey(pathToPageKey(location.pathname));
      navigate(target, { replace: samePage });
    },
    [navigate, location.pathname],
  );

  return (
    <TerminalLayout activePage={page} onNav={handleNav} isDeskV2Shell={isDeskV2Shell}>
      <Page />
    </TerminalLayout>
  );
}
