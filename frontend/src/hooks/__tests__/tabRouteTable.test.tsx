/**
 * Every hub's tab table must agree with the page registry, in BOTH directions.
 *
 * A hub page keeps its active tab in the URL by calling onNav with the page key
 * for that tab (TAB_PAGE_KEYS). App.tsx maps that key back to a component with
 * an initialTab prop. Those are two independently written tables pointing at
 * each other, and nothing makes them agree — a typo in either one produces a
 * URL that opens a DIFFERENT tab from the one clicked, which looks like the bug
 * we just fixed rather than like a mistake.
 *
 * This checks the round trip without rendering anything:
 *
 *   tab --TAB_PAGE_KEYS--> key --pageKeyToUrl--> url
 *       --pathToPageKey--> key --BASE_PAGES--> <Hub initialTab={tab}/>
 *
 * The last hop calls the registry's factory and reads the element's props. It
 * is a pure function returning a React element — no DOM, no data, no fetch —
 * which is what lets this cover ResearchCenter, whose panels throw under the
 * e2e suite's empty-payload stub and so cannot be reached in the browser.
 */

import { describe, it, expect } from "vitest";
import React from "react";
import { BASE_PAGES, tradeDeskPages } from "../../App";
import { pageKeyToUrl, pathToPageKey } from "../../terminalRoutes";

import RiskCenter,     { TABS as RISK_TABS,     TAB_PAGE_KEYS as RISK_KEYS,     DEFAULT_TAB as RISK_DEFAULT }     from "../../pages/RiskCenter";
import SystemCenter,   { TABS as SYSTEM_TABS,   TAB_PAGE_KEYS as SYSTEM_KEYS,   DEFAULT_TAB as SYSTEM_DEFAULT }   from "../../pages/SystemCenter";
import ResearchCenter, { TABS as RESEARCH_TABS, TAB_PAGE_KEYS as RESEARCH_KEYS, DEFAULT_TAB as RESEARCH_DEFAULT } from "../../pages/ResearchCenter";
import SignalsCenter,  { TABS as SIGNALS_TABS,  TAB_PAGE_KEYS as SIGNALS_KEYS,  DEFAULT_TAB as SIGNALS_DEFAULT }  from "../../pages/SignalsCenter";
import AccountCenter,  { TABS as ACCOUNT_TABS,  TAB_PAGE_KEYS as ACCOUNT_KEYS,  DEFAULT_TAB as ACCOUNT_DEFAULT }  from "../../pages/AccountCenter";
import TradeDesk,      { TAB_PAGE_KEYS as DESK_KEYS, DEFAULT_TAB as DESK_DEFAULT } from "../../pages/TradeDesk";

type TabDef = { key: string; label: string };

const HUBS: Array<{
  name: string;
  component: React.ComponentType<{ initialTab?: never }>;
  /** null for the legacy desk, which has no TABS array — its tabs are a union type. */
  tabs: readonly TabDef[] | null;
  keys: Readonly<Record<string, string>>;
  defaultTab: string;
}> = [
  { name: "RiskCenter",     component: RiskCenter     as never, tabs: RISK_TABS,     keys: RISK_KEYS, defaultTab: RISK_DEFAULT },
  { name: "SystemCenter",   component: SystemCenter   as never, tabs: SYSTEM_TABS,   keys: SYSTEM_KEYS, defaultTab: SYSTEM_DEFAULT },
  { name: "ResearchCenter", component: ResearchCenter as never, tabs: RESEARCH_TABS, keys: RESEARCH_KEYS, defaultTab: RESEARCH_DEFAULT },
  { name: "SignalsCenter",  component: SignalsCenter  as never, tabs: SIGNALS_TABS,  keys: SIGNALS_KEYS, defaultTab: SIGNALS_DEFAULT },
  { name: "AccountCenter",  component: AccountCenter  as never, tabs: ACCOUNT_TABS,  keys: ACCOUNT_KEYS, defaultTab: ACCOUNT_DEFAULT },
  // The legacy desk is registered only when trade_desk_v2 is off; with the flag
  // on, the same keys render TradeDeskV2 instead. Check it against the registry
  // it actually appears in.
  { name: "TradeDesk (legacy)", component: TradeDesk as never, tabs: null, keys: DESK_KEYS, defaultTab: DESK_DEFAULT },
];

/** BASE_PAGES plus the legacy desk's entries — the registry App renders with v2 off. */
const REGISTRY: Record<string, React.ComponentType> = {
  ...BASE_PAGES,
  ...tradeDeskPages(false),
};

/** What the registry entry for `key` mounts, and with which initialTab. */
function resolve(key: string): { type: unknown; initialTab: unknown } {
  const entry = REGISTRY[key];
  // Every entry that carries an initialTab is a `() => <Hub initialTab=... />`
  // wrapper: calling it only builds an element, so it is safe outside React.
  const el = (entry as () => React.ReactElement)();
  return { type: el.type, initialTab: (el.props as { initialTab?: unknown }).initialTab };
}

/**
 * resolve(), but null for entries that cannot be called outside a render.
 *
 * Most registry entries are the page component itself (`dashboard: Dashboard`),
 * and a real component calling useState with no React dispatcher installed
 * throws. Those are exactly the entries with no initialTab to check, so
 * skipping them loses nothing — and it cannot hide a regression, because the
 * forward test above calls the throwing resolve() on every key a hub's table
 * names. If a table target ever became uncallable, that test fails loudly
 * rather than being quietly skipped here.
 */
function tryResolve(key: string): { type: unknown; initialTab: unknown } | null {
  try {
    const r = resolve(key);
    return React.isValidElement({ type: r.type, props: {}, key: null } as never) || r.type
      ? r
      : null;
  } catch {
    return null;
  }
}

describe.each(HUBS)("$name tab table", ({ name, component, tabs, keys, defaultTab }) => {
  const entries = Object.entries(keys);

  it("is not empty", () => {
    // Guards the whole suite: an emptied table would make every it.each below
    // vacuous and the file would still pass.
    expect(entries.length).toBeGreaterThan(0);
  });

  if (tabs) {
    it("opens on a tab it actually has", () => {
      expect(tabs.map((t) => t.key)).toContain(defaultTab);
    });

    it("names only tabs the page actually has", () => {
      const known = new Set(tabs.map((t) => t.key));
      for (const [tab] of entries) {
        expect(known, `${name} maps a tab "${tab}" that is not in its TABS`).toContain(tab);
      }
    });
  }

  it.each(entries)("tab %s -> page key %s round-trips to the same tab", (tab, key) => {
    expect(
      Object.prototype.hasOwnProperty.call(REGISTRY, key),
      `${name} sends tab "${tab}" to page key "${key}", which is not in the page registry — ` +
      `that URL renders the "page unavailable" placeholder`
    ).toBe(true);

    // The key has to survive the URL, or the reload lands somewhere else.
    const url = pageKeyToUrl(key);
    expect(pathToPageKey(url), `${key} does not survive a round trip through ${url}`).toBe(key);

    // Two shapes of registry entry. Most are `() => <Hub initialTab="x" />`
    // wrappers, but a few are the hub component itself — `equity: SignalsCenter`
    // — and those open on the hub's own default tab. Both are legitimate; a
    // bare entry is only correct for the tab that IS the default.
    const entry = REGISTRY[key];
    if (entry === component) {
      expect(
        tab,
        `${name} sends tab "${tab}" to ${url}, whose registry entry is the bare ` +
        `component — that opens the page on its default tab "${defaultTab}"`
      ).toBe(defaultTab);
      return;
    }

    const { type, initialTab } = resolve(key);
    expect(
      type,
      `${name} tab "${tab}" -> ${url}, but that URL renders a different page`
    ).toBe(component);
    expect(
      initialTab,
      `${name} tab "${tab}" -> ${url}, which opens the page on tab "${String(initialTab)}" instead`
    ).toBe(tab);
  });
});

describe("the registry's own initialTab props are real tabs", () => {
  /**
   * The reverse direction, which the per-hub round trip cannot see: a registry
   * entry can name a tab that no longer exists, and nothing points back at it
   * from the hub. That entry renders the page on its DEFAULT tab, silently.
   */
  const TAB_SETS: Array<[React.ComponentType, readonly TabDef[]]> = [
    [RiskCenter     as React.ComponentType, RISK_TABS],
    [SystemCenter   as React.ComponentType, SYSTEM_TABS],
    [ResearchCenter as React.ComponentType, RESEARCH_TABS],
    [SignalsCenter  as React.ComponentType, SIGNALS_TABS],
  ];

  it.each(TAB_SETS.map(([c, t]) => [c.name || "hub", c, t] as const))(
    "%s",
    (_name, component, tabDefs) => {
      const known = new Set(tabDefs.map((t) => t.key));
      const offenders: string[] = [];

      for (const key of Object.keys(REGISTRY)) {
        const resolved = tryResolve(key);
        if (!resolved) continue;
        const { type, initialTab } = resolved;
        if (type !== component) continue;
        if (typeof initialTab === "string" && !known.has(initialTab)) {
          offenders.push(`${key} -> initialTab="${initialTab}"`);
        }
      }

      expect(
        offenders,
        `these registry entries open a tab the page no longer has, so they ` +
        `silently fall back to its default tab: ${offenders.join(", ")}`
      ).toEqual([]);
    },
  );
});
