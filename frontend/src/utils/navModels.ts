/**
 * Sidebar nav models — legacy vs Trade Desk 2.0 (Options under Trade Desk).
 */

import type { NavGroup } from "./navLabels";

// Simple-by-default IA: the everyday loop is "check signals → check
// positions → check performance", with everything else (manual chart/options
// research tooling, strategy config, risk-rule config, ops/debug pages)
// tucked behind "Show advanced" rather than cluttering the default view.
// Journal and Performance were previously advanced-only despite being core
// to "is this working" — promoted to simple here.

/** Pre–Trade Desk 2.0 sidebar (rollback when trade_desk_v2 is off). */
export const NAV_MODEL_LEGACY: NavGroup[] = [
  { id: "dashboard", label: "Command Center", icon: "dashboard", key: "dashboard" },
  { id: "markets",   label: "Markets",        icon: "markets", children: [
    { key: "markets:chart",      label: "Chart" },
    { key: "markets:watchlists", label: "Watchlists" },
    { key: "markets:heatmaps",   label: "Heatmaps", advanced: true },
    { key: "markets:news",       label: "News & Events", advanced: true },
    { key: "markets:sector-rotation", label: "Sector Rotation", advanced: true },
  ]},
  { id: "trade", label: "Trade Desk", icon: "paper", children: [
    { key: "trade:overview",  label: "Overview" },
    { key: "trade:positions", label: "Positions" },
    { key: "trade:copilot",   label: "Copilot Review", advanced: true },
    { key: "trade:orders",    label: "Desk signals", advanced: true },
    { key: "trade:execlog",   label: "Execution Monitor", advanced: true },
    { key: "trade:logs",      label: "P&L Breakdown", advanced: true },
    { key: "options:chain",   label: "Options Chain", advanced: true },
    { key: "scan",            label: "Spread Scanner", advanced: true },
    { key: "options:income",  label: "Income Strategies", advanced: true },
    { key: "options:flow",    label: "Options Flow", advanced: true },
  ]},
  { id: "strat", label: "Strategies", icon: "strategy", children: [
    { key: "strat:alpha-edge", label: "Alpha Edge" },
    { key: "equity",           label: "Equity Signals" },
    // ADDED TO BOTH MODELS, like the Account group below — see that comment for
    // why one is never enough. Not marked advanced: phase 1 is read-only, and a
    // feature whose whole purpose is to accumulate observations nobody looks at
    // is not worth shipping behind a toggle.
    { key: "crypto:signals",   label: "Crypto Signals" },
    { key: "options:signals",  label: "Options Signals" },
    { key: "strat:research",   label: "Signal Research" },
    { key: "strat:cards",   label: "Strategy Cards", advanced: true },
    { key: "strat:health",  label: "Strategy Health", advanced: true },
    { key: "strat:calendar", label: "Signal Calendar" },
    // Was in SignalsCenter TABS and the page registry but in neither nav
    // model, so it was reachable only from the tab strip or by URL.
    { key: "strat:signal-history", label: "Signal History", advanced: true },
    { key: "strat:builder", label: "Strategy Builder", advanced: true },
    { key: "strat:alerts",  label: "Alerts", advanced: true },
  ]},
  { id: "risk", label: "Portfolio & Risk", icon: "risk", key: "risk", children: [
    { key: "risk:heat",     label: "Risk Overview" },
    { key: "risk:rules",    label: "Risk Rules", advanced: true },
  ]},
  { id: "lab", label: "Research", icon: "lab", advanced: true, children: [
    { key: "lab:scenario",   label: "Scenario Lab" },
    { key: "lab:strategy",   label: "Strategy Research" },
    { key: "lab:market",     label: "Market & Regime" },
    { key: "lab:models",     label: "Model Health" },
    { key: "lab:intel",      label: "Intelligence" },
    { key: "backtest",       label: "Backtests" },
  ]},
  { id: "journal",   label: "Journal & Replay", icon: "journal",   key: "journal" },
  { id: "analytics", label: "Performance",      icon: "analytics", key: "analytics" },
  { id: "system", label: "System", icon: "data", children: [
    { key: "system:broker",  label: "Broker" },
    { key: "system:market",  label: "Market Data", advanced: true },
    { key: "system:quality", label: "Data Quality", advanced: true },
  ]},
  // Everything belonging to the signed-in person, as opposed to the platform.
  // Last in the list because it is visited rarely, unlike the trading surfaces
  // above it. The group above is what the PLATFORM connects to; this is what
  // you own, which is why My Brokers moved out of it.
  //
  // ADDED TO BOTH MODELS deliberately. This file defines NAV_MODEL_LEGACY and
  // NAV_MODEL_V2, and which one renders depends on the trade_desk_v2 flag, so
  // a group added to one alone disappears when the flag flips. #78 missed the
  // nav model entirely and shipped My Brokers reachable only by URL; adding it
  // to one model would be the same bug with a longer fuse.
  { id: "account", label: "Account", icon: "data", requiresAuth: true, children: [
    { key: "account:profile",  label: "Profile" },
    { key: "account:password", label: "Password" },
    { key: "account:sessions", label: "Sessions" },
    { key: "account:brokers",  label: "My Brokers" },
  ]},
];

/**
 * Trade Desk 2.0 IA — Options tools remain reachable under Strategies (advanced)
 * until Phase D moves them into the Options Desk workspace. Trade Desk group
 * hosts Command Overview + desk tabs.
 */
export const NAV_MODEL_V2: NavGroup[] = [
  { id: "dashboard", label: "Command Center", icon: "dashboard", key: "dashboard" },
  { id: "markets",   label: "Markets",        icon: "markets", children: [
    { key: "markets:chart",      label: "Chart" },
    { key: "markets:watchlists", label: "Watchlists" },
    { key: "markets:heatmaps",   label: "Heatmaps", advanced: true },
    { key: "markets:news",       label: "News & Events", advanced: true },
    { key: "markets:sector-rotation", label: "Sector Rotation", advanced: true },
  ]},
  { id: "trade", label: "Trade Desk", icon: "paper", children: [
    { key: "trade:overview",  label: "Command Overview" },
    { key: "trade:equity",    label: "Equity Desk" },
    { key: "trade:options",   label: "Options Desk" },
    { key: "trade:positions", label: "Positions" },
    { key: "trade:copilot",   label: "Copilot Queue", advanced: true },
    { key: "trade:orders",    label: "Orders", advanced: true },
    { key: "trade:execlog",   label: "Execution Monitor", advanced: true },
    { key: "trade:replay",    label: "Trade Replay", advanced: true },
    { key: "trade:settings",  label: "Desk Settings", advanced: true },
  ]},
  { id: "strat", label: "Strategies", icon: "strategy", children: [
    { key: "strat:alpha-edge", label: "Alpha Edge" },
    { key: "equity",        label: "Signal Center" },
    { key: "crypto:signals", label: "Crypto Signals" },
    { key: "strat:research", label: "Signal Research" },
    { key: "strat:cards",   label: "Strategy Cards", advanced: true },
    { key: "strat:health",  label: "Strategy Health", advanced: true },
    { key: "strat:calendar", label: "Signal Calendar" },
    // Was in SignalsCenter TABS and the page registry but in neither nav
    // model, so it was reachable only from the tab strip or by URL.
    { key: "strat:signal-history", label: "Signal History", advanced: true },
    { key: "strat:builder", label: "Strategy Builder", advanced: true },
    { key: "strat:alerts",  label: "Alerts", advanced: true },
    { key: "options:chain",  label: "Options Chain", advanced: true },
    { key: "scan",           label: "Spread Scanner", advanced: true },
    { key: "options:income", label: "Income Strategies", advanced: true },
    { key: "options:flow",   label: "Options Flow", advanced: true },
  ]},
  { id: "risk", label: "Portfolio & Risk", icon: "risk", key: "risk", children: [
    { key: "risk:heat",     label: "Risk Overview" },
    { key: "risk:rules",    label: "Risk Rules", advanced: true },
  ]},
  { id: "lab", label: "Research", icon: "lab", advanced: true, children: [
    { key: "lab:scenario",   label: "Scenario Lab" },
    { key: "lab:strategy",   label: "Strategy Research" },
    { key: "lab:market",     label: "Market & Regime" },
    { key: "lab:models",     label: "Model Health" },
    { key: "lab:intel",      label: "Intelligence" },
    { key: "backtest",       label: "Backtests" },
  ]},
  { id: "journal",   label: "Journal",     icon: "journal",   key: "journal" },
  { id: "analytics", label: "Performance", icon: "analytics", key: "analytics" },
  { id: "system", label: "Data & Integrations", icon: "data", children: [
    { key: "system:broker",  label: "Broker Connections" },
    { key: "system:market",  label: "Market Data", advanced: true },
    { key: "system:quality", label: "Data Quality", advanced: true },
  ]},
  // Everything belonging to the signed-in person, as opposed to the platform.
  // Last in the list because it is visited rarely, unlike the trading surfaces
  // above it. The group above is what the PLATFORM connects to; this is what
  // you own, which is why My Brokers moved out of it.
  //
  // ADDED TO BOTH MODELS deliberately. This file defines NAV_MODEL_LEGACY and
  // NAV_MODEL_V2, and which one renders depends on the trade_desk_v2 flag, so
  // a group added to one alone disappears when the flag flips. #78 missed the
  // nav model entirely and shipped My Brokers reachable only by URL; adding it
  // to one model would be the same bug with a longer fuse.
  { id: "account", label: "Account", icon: "data", requiresAuth: true, children: [
    { key: "account:profile",  label: "Profile" },
    { key: "account:password", label: "Password" },
    { key: "account:sessions", label: "Sessions" },
    { key: "account:brokers",  label: "My Brokers" },
  ]},
];

export function groupIdForKey(key: string, model: NavGroup[]): string | null {
  for (const g of model) {
    if (g.key === key) return g.id;
    if (g.children?.some((c) => c.key === key)) return g.id;
  }
  return null;
}
