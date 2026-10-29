/**
 * Thumb-reachable primary navigation for phones.
 *
 * The shell already had an overlay sidebar, but every navigation started with
 * a reach to a hamburger in the top-left — the furthest point from a thumb on
 * a phone held one-handed. This puts the five destinations that carry the
 * live money path within reach, and leaves the sidebar for everything else.
 *
 * Five, not more: a sixth tab drops each target below the 44px that a finger
 * actually needs. The overflow lives behind "More", which opens the existing
 * sidebar rather than inventing a second navigation surface.
 *
 * Deliberately not a router — it calls the same onNav the sidebar does, so
 * active state and page resolution stay in one place.
 */

import React from "react";

export type BottomNavItem = {
  key: string;
  label: string;
  /** Nav keys that should light this tab up, beyond `key` itself. */
  matches?: string[];
  icon: React.ReactNode;
};

// 24px line icons — stroke, not fill, to sit with the terminal's type weight.
const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const Icon = ({ children }: { children: React.ReactNode }) => (
  <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" {...stroke}>
    {children}
  </svg>
);

export const BOTTOM_NAV_ITEMS: BottomNavItem[] = [
  {
    key: "dashboard",
    label: "Home",
    icon: <Icon><path d="M3 10.5 12 4l9 6.5" /><path d="M5.5 9.5V20h13V9.5" /></Icon>,
  },
  {
    key: "markets:chart",
    label: "Markets",
    matches: ["markets", "markets:overview", "markets:watchlist"],
    icon: <Icon><path d="M4 18V9" /><path d="M9.5 18V5" /><path d="M15 18v-6" /><path d="M20.5 18V8" /></Icon>,
  },
  {
    key: "equity",
    label: "Signals",
    matches: ["options:signals", "strat:alpha-edge", "strat:research"],
    icon: <Icon><path d="M9 18h6M10 21h4M8 14a7 7 0 1 1 8 0l-1 3H9z" /></Icon>,
  },
  {
    key: "trade:positions",
    label: "Positions",
    icon: <Icon><rect x="3" y="7" width="18" height="14" rx="2" /><path d="M8 7V4h8v3" /></Icon>,
  },
];

export default function MobileBottomNav({
  active,
  onNav,
  onOpenMore,
  moreOpen = false,
}: {
  active: string;
  onNav: (key: string) => void;
  onOpenMore: () => void;
  moreOpen?: boolean;
}) {
  const isActive = (item: BottomNavItem) =>
    active === item.key || (item.matches?.includes(active) ?? false);

  return (
    <nav className="mobile-bottom-nav" aria-label="Primary">
      {BOTTOM_NAV_ITEMS.map((item) => {
        const on = isActive(item);
        return (
          <button
            key={item.key}
            type="button"
            className={`mobile-bottom-nav__tab${on ? " is-active" : ""}`}
            aria-current={on ? "page" : undefined}
            aria-label={item.label}
            onClick={() => onNav(item.key)}
          >
            {item.icon}
            <span className="mobile-bottom-nav__label">{item.label}</span>
          </button>
        );
      })}
      <button
        type="button"
        className={`mobile-bottom-nav__tab${moreOpen ? " is-active" : ""}`}
        aria-label="More"
        aria-expanded={moreOpen}
        onClick={onOpenMore}
      >
        <Icon><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></Icon>
        <span className="mobile-bottom-nav__label">More</span>
      </button>
    </nav>
  );
}
