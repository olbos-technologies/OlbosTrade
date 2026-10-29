import type { ReactNode } from "react";
import "./WorkspaceFrame.css";

type Workspace = { title: string; eyebrow: string; description: string; kind: string };
const WORKSPACES: Record<string, Workspace> = {
  risk: { title: "Risk & protection", eyebrow: "PORTFOLIO OVERSIGHT", description: "Review exposure, loss limits and protective controls.", kind: "risk" },
  journal: { title: "Trading journal", eyebrow: "REVIEW YOUR DECISIONS", description: "Review recorded trades, outcomes and lessons.", kind: "journal" },
  analytics: { title: "Performance analytics", eyebrow: "UNDERSTAND YOUR RESULTS", description: "Compare recorded performance across trading styles.", kind: "analytics" },
  backtest: { title: "Backtesting", eyebrow: "TEST BEFORE TRADING", description: "Explore historical simulations. Results do not predict future returns.", kind: "backtest" },
  lab: { title: "Research workspace", eyebrow: "EXPLORE & EVALUATE", description: "Inspect scenarios, research evidence and model health.", kind: "research" },
  settings: { title: "System settings", eyebrow: "PLATFORM CONNECTIONS", description: "Review the platform broker, market feeds and data quality.", kind: "system" },
  account: { title: "Your account", eyebrow: "ACCOUNT & SECURITY", description: "Manage your profile, password and signed-in sessions.", kind: "account" },
  strat: { title: "Strategy workspace", eyebrow: "RESEARCH & PLANNING", description: "Inspect strategies, evidence, calendars and alerts.", kind: "strategy" },
  options: { title: "Options workspace", eyebrow: "CONTRACTS & RESEARCH", description: "Review options data, strategies and research context.", kind: "options" },
  crypto: { title: "Crypto research", eyebrow: "MARKET RESEARCH", description: "Inspect crypto data and research signals.", kind: "crypto" },
  scan: { title: "Market scanners", eyebrow: "DISCOVER & REVIEW", description: "Review scan configuration and results before taking action.", kind: "scanner" },
};

export function workspaceForPage(page: string): Workspace | undefined {
  if (page === "account:brokers") return undefined; // Already has its own designed heading.
  const family = page.split(":")[0];
  const key = family === "system" ? "settings" : family;
  return Object.prototype.hasOwnProperty.call(WORKSPACES, key) ? WORKSPACES[key] : undefined;
}

export default function WorkspaceFrame({ page, children }: { page: string; children: ReactNode }) {
  const workspace = workspaceForPage(page);
  if (!workspace) return <>{children}</>;
  return <div className={`workspace-frame workspace-frame--${workspace.kind}`}>
    <header className="workspace-frame__heading">
      <span className="design-eyebrow">{workspace.eyebrow}</span>
      <h1>{workspace.title}</h1>
      <p>{workspace.description}</p>
    </header>
    <div className="workspace-frame__body">{children}</div>
  </div>;
}
