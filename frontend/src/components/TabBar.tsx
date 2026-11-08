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
    <div role="tablist" aria-label={label || "Workspace views"} style={{
      display: "flex", gap: 0, borderBottom: "1px solid var(--line-dim)",
      background: "var(--bg-2)", flexWrap: "wrap",
    }}>
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
            className="mono"
            style={{
              padding: "10px 18px", fontSize: 11, letterSpacing: "0.1em",
              textTransform: "uppercase", cursor: "pointer", background: "transparent",
              border: "none",
              borderBottom: on ? "2px solid var(--brand)" : "2px solid transparent",
              color: on ? "var(--brand)" : "var(--ink-dim)",
            }}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
