/**
 * Trade Desk primary tabs — workspace switch without full-page remount of shell chrome.
 */

import React, { useEffect, useRef } from "react";

export type TradeDeskTab =
  | "overview"
  | "equities"
  | "options"
  | "copilot"
  | "positions"
  | "orders"
  | "execution"
  | "replay"
  | "settings";

export const TRADE_DESK_TABS: { key: TradeDeskTab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "equities", label: "Equities" },
  { key: "options", label: "Options" },
  { key: "copilot", label: "Copilot" },
  { key: "positions", label: "Positions" },
  { key: "orders", label: "Orders" },
  { key: "execution", label: "Execution" },
  { key: "replay", label: "Replay" },
  { key: "settings", label: "Settings" },
];

export function tabFromNavKey(pageKey: string): TradeDeskTab {
  const map: Record<string, TradeDeskTab> = {
    "trade:overview": "overview",
    "trade:equity": "equities",
    "trade:options": "options",
    "trade:copilot": "copilot",
    "trade:positions": "positions",
    "trade:orders": "orders",
    "trade:execlog": "execution",
    "trade:replay": "replay",
    "trade:settings": "settings",
    "trade:logs": "overview",
    paper: "overview",
  };
  return map[pageKey] ?? "overview";
}

export function navKeyFromTab(tab: TradeDeskTab): string {
  const map: Record<TradeDeskTab, string> = {
    overview: "trade:overview",
    equities: "trade:equity",
    options: "trade:options",
    copilot: "trade:copilot",
    positions: "trade:positions",
    orders: "trade:orders",
    execution: "trade:execlog",
    replay: "trade:replay",
    settings: "trade:settings",
  };
  return map[tab];
}

export default function TradeDeskTabs({
  active,
  onChange,
  badges,
  ensureActiveVisible = false,
}: {
  active: TradeDeskTab;
  onChange: (tab: TradeDeskTab) => void;
  badges?: Partial<Record<TradeDeskTab, number | string>>;
  /** Keep the selected tab in view when this tablist scrolls horizontally. */
  ensureActiveVisible?: boolean;
}) {
  const activeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!ensureActiveVisible) return;
    const selected = activeRef.current;
    if (selected && typeof selected.scrollIntoView === "function") {
      selected.scrollIntoView({ block: "nearest", inline: "center" });
    }
  }, [active, ensureActiveVisible]);

  const moveFocus = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const buttons = Array.from(
      event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') || [],
    );
    if (!buttons.length) return;
    const next =
      event.key === "Home" ? 0 :
      event.key === "End" ? buttons.length - 1 :
      (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus();
    buttons[next].click();
  };

  return (
    <div role="tablist" aria-label="Trade Desk workspaces" className="desk-tablist">
      {TRADE_DESK_TABS.map((t, index) => {
        const on = t.key === active;
        const badge = badges?.[t.key];
        return (
          <button
            ref={on ? activeRef : undefined}
            type="button"
            role="tab"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            key={t.key}
            onClick={() => onChange(t.key)}
            onKeyDown={(e) => moveFocus(e, index)}
            className={`desk-tab${on ? " desk-tab--active" : ""}`}
          >
            {t.label}
            {badge != null && badge !== 0 && badge !== "" && (
              <span className="desk-tab__badge">{badge}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
