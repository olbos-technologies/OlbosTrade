/**
 * Sidebar nav models — legacy vs Trade Desk 2.0.
 *
 * Product loop (Bloomberg-style desk): Command Center → Markets → Signals →
 * Trade Desk → Risk → Performance / Journal → Brokers. Lab/research and
 * secondary scanners stay in the model for URL / deep-link ownership but are
 * omitted from the sidebar (`sidebar: false`).
 */

import type { NavGroup } from "./navLabels";

/** Pre–Trade Desk 2.0 sidebar (rollback when trade_desk_v2 is off). */
export const NAV_MODEL_LEGACY: NavGroup[] = [
  { id: "dashboard", label: "Command Center", icon: "dashboard", key: "dashboard" },
  { id: "markets",   label: "Markets",        icon: "markets", children: [
    { key: "markets:chart",      label: "Chart" },
    { key: "markets:watchlists", label: "Watchlists" },
    { key: "markets:heatmaps",   label: "Heatmaps", sidebar: false },
    { key: "markets:news",       label: "News & Events", sidebar: false },
    { key: "markets:sector-rotation", label: "Sector Rotation", sidebar: false },
  ]},
  { id: "trade", label: "Trade Desk", icon: "paper", children: [
    { key: "trade:overview",  label: "Overview" },
    { key: "trade:positions", label: "Positions" },
    { key: "trade:copilot",   label: "Copilot Review", sidebar: false },
    { key: "trade:orders",    label: "Desk signals", sidebar: false },
    { key: "trade:execlog",   label: "Execution Monitor", sidebar: false },
    { key: "trade:logs",      label: "P&L Breakdown", sidebar: false },
    { key: "options:chain",   label: "Options Chain", sidebar: false },
    { key: "scan",            label: "Spread Scanner", sidebar: false },
    { key: "options:income",  label: "Income Strategies", sidebar: false },
    { key: "options:flow",    label: "Options Flow", sidebar: false },
  ]},
  { id: "strat", label: "Signals", icon: "strategy", children: [
    { key: "strat:alpha-edge", label: "Alpha Edge" },
    { key: "equity", label: "Live Signals" },
    { key: "strat:signal-history", label: "History" },
    { key: "strat:cards", label: "Strategy Library", sidebar: false },
    { key: "strat:health", label: "Health", sidebar: false },
  ]},
  { id: "risk", label: "Risk", icon: "risk", key: "risk", children: [
    { key: "risk:heat",     label: "Risk Overview" },
    { key: "risk:rules",    label: "Risk Rules", sidebar: false },
  ]},
  // Lab stays URL-reachable; not part of the daily desk sidebar.
  { id: "lab", label: "Research", icon: "lab", sidebar: false, children: [
    { key: "strat:research", label: "Signal Research" },
    { key: "strat:calendar", label: "Signal Calendar" },
    { key: "strat:builder", label: "Strategy Builder" },
    { key: "strat:alerts", label: "Alerts" },
    { key: "crypto:signals", label: "Crypto Research" },
    { key: "options:signals", label: "Options Signals" },
    { key: "lab:scenario",   label: "Scenario Lab" },
    { key: "lab:strategy",   label: "Strategy Research" },
    { key: "lab:market",     label: "Market & Regime" },
    { key: "lab:models",     label: "Model Health" },
    { key: "lab:intel",      label: "Intelligence" },
    { key: "backtest",       label: "Backtests" },
  ]},
  { id: "journal",   label: "Journal",     icon: "journal",   key: "journal" },
  { id: "analytics", label: "Performance", icon: "analytics", key: "analytics" },
  { id: "system", label: "Brokers", icon: "connections", children: [
    { key: "system:broker",  label: "Broker Connections" },
    { key: "system:market",  label: "Market Data", sidebar: false },
    { key: "system:quality", label: "Data Quality", sidebar: false },
  ]},
  { id: "account", label: "Account", icon: "account", requiresAuth: true, children: [
    { key: "account:profile",  label: "Profile" },
    { key: "account:password", label: "Password" },
    { key: "account:sessions", label: "Sessions" },
    { key: "account:brokers",  label: "My Brokers" },
  ]},
];

/** Trade Desk 2.0 — same product loop; Equity/Options desks under Trade Desk. */
export const NAV_MODEL_V2: NavGroup[] = [
  { id: "dashboard", label: "Command Center", icon: "dashboard", key: "dashboard" },
  { id: "markets",   label: "Markets",        icon: "markets", children: [
    { key: "markets:chart",      label: "Chart" },
    { key: "markets:watchlists", label: "Watchlists" },
    { key: "markets:heatmaps",   label: "Heatmaps", sidebar: false },
    { key: "markets:news",       label: "News & Events", sidebar: false },
    { key: "markets:sector-rotation", label: "Sector Rotation", sidebar: false },
  ]},
  { id: "trade", label: "Trade Desk", icon: "paper", children: [
    { key: "trade:overview",  label: "Command Overview" },
    { key: "trade:equity",    label: "Equity Desk" },
    { key: "trade:options",   label: "Options Desk" },
    { key: "trade:positions", label: "Positions" },
    { key: "trade:copilot",   label: "Copilot Queue", sidebar: false },
    { key: "trade:orders",    label: "Orders", sidebar: false },
    { key: "trade:execlog",   label: "Execution Monitor", sidebar: false },
    { key: "trade:replay",    label: "Trade Replay", sidebar: false },
    { key: "trade:settings",  label: "Desk Settings", sidebar: false },
  ]},
  { id: "strat", label: "Signals", icon: "strategy", children: [
    { key: "strat:alpha-edge", label: "Alpha Edge" },
    { key: "equity", label: "Live Signals" },
    { key: "strat:signal-history", label: "History" },
    { key: "strat:cards", label: "Strategy Library", sidebar: false },
    { key: "strat:health", label: "Health", sidebar: false },
  ]},
  { id: "risk", label: "Risk", icon: "risk", key: "risk", children: [
    { key: "risk:heat",     label: "Risk Overview" },
    { key: "risk:rules",    label: "Risk Rules", sidebar: false },
  ]},
  { id: "lab", label: "Research", icon: "lab", sidebar: false, children: [
    { key: "strat:research", label: "Signal Research" },
    { key: "strat:calendar", label: "Signal Calendar" },
    { key: "strat:builder", label: "Strategy Builder" },
    { key: "strat:alerts", label: "Alerts" },
    { key: "crypto:signals", label: "Crypto Research" },
    { key: "options:chain", label: "Options Chain" },
    { key: "scan", label: "Spread Scanner" },
    { key: "options:income", label: "Income Strategies" },
    { key: "options:flow", label: "Options Flow" },
    { key: "lab:scenario",   label: "Scenario Lab" },
    { key: "lab:strategy",   label: "Strategy Research" },
    { key: "lab:market",     label: "Market & Regime" },
    { key: "lab:models",     label: "Model Health" },
    { key: "lab:intel",      label: "Intelligence" },
    { key: "backtest",       label: "Backtests" },
  ]},
  { id: "journal",   label: "Journal",     icon: "journal",   key: "journal" },
  { id: "analytics", label: "Performance", icon: "analytics", key: "analytics" },
  { id: "system", label: "Brokers", icon: "connections", children: [
    { key: "system:broker",  label: "Broker Connections" },
    { key: "system:market",  label: "Market Data", sidebar: false },
    { key: "system:quality", label: "Data Quality", sidebar: false },
  ]},
  { id: "account", label: "Account", icon: "account", requiresAuth: true, children: [
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
