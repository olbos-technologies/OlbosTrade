import React from "react";

/**
 * Minimal terminal-style tab bar. Used to fold related pages into one nav entry
 * (e.g. Risk = Monitor + Guardrails) without rewriting their internals.
 */
export interface Tab { key: string; label: string; }

export default function TabBar({ tabs, active, onChange, label }: {
  tabs: Tab[];
  active: string;
  onChange: (key: string) => void;
  label?: string;
}) {
  const moveFocus = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const buttons = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') || []);
    if (!buttons.length) return;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus();
    buttons[next].click();
  };
  return (
    <div className="terminal-tabbar" role="tablist" aria-label={label || "Workspace views"}>
      {tabs.map((t, index) => {
        const on = t.key === active;
        return (
          <button
            type="button"
            role="tab"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onKeyDown={event => moveFocus(event, index)}
            key={t.key}
            onClick={() => onChange(t.key)}
            className={`mono terminal-tabbar__tab${on ? " is-active" : ""}`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
