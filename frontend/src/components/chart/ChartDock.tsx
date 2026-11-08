import React from "react";

export type DockTab = { key: string; label: string; count?: number | string };

/**
 * The tabbed dock under the chart. Flat tabs with a gold underline on the
 * selected one, like the rest of the terminal's navigation. Left / Right / Home /
 * End move between tabs; only the selected tab is in the tab order.
 */
export default function ChartDock({
  tabs, active, onChange, children,
}: {
  tabs: DockTab[];
  active: string;
  onChange: (key: string) => void;
  children: React.ReactNode;
}) {
  const move = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const last = tabs.length - 1;
    const next = event.key === "Home" ? 0 : event.key === "End" ? last
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    onChange(tabs[next].key);
    const buttons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    buttons?.[next]?.focus();
  };
  return (
    <section className="chart-dock" aria-label="Chart research dock">
      <div className="chart-dock__tabs" role="tablist" aria-label="Chart research">
        {tabs.map((tab, index) => {
          const on = tab.key === active;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              id={`dock-tab-${tab.key}`}
              aria-selected={on}
              aria-controls="dock-panel"
              tabIndex={on ? 0 : -1}
              className={`chart-dock__tab${on ? " chart-dock__tab--on" : ""}`}
              onClick={() => onChange(tab.key)}
              onKeyDown={(event) => move(event, index)}
            >
              {tab.label}
              {tab.count !== undefined && <span className="chart-dock__count">{tab.count}</span>}
            </button>
          );
        })}
      </div>
      <div id="dock-panel" role="tabpanel" aria-labelledby={`dock-tab-${active}`} className="chart-dock__body">
        {children}
      </div>
    </section>
  );
}
