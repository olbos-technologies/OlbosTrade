/**
 * The default sidebar stays small, and stays aligned to the product's goals.
 *
 * The goals, stated so this test can be argued with rather than guessed at:
 *   1. A dense, Bloomberg-style terminal — few destinations, not a site map.
 *   2. High-quality equity AND options signals with an explicit trade plan.
 *   3. Automated execution, with its risk controls always one click away.
 *
 * Before this guard the default rail carried 19 destinations across 10 groups:
 * Crypto Signals, Signal Calendar, Signal Research, Alpha Edge, Watchlists,
 * Journal, Performance and four Account pages all competed for attention with
 * the three things the product is for. Nothing was wrong with any of them
 * individually, which is exactly how a rail gets to 19 — each addition is
 * locally reasonable and nobody owns the total.
 *
 * So the total is owned here. Everything demoted is still REACHABLE: it sits
 * behind the Advanced toggle, keeps its URL, and keeps its page. Nothing was
 * deleted. Flipping one `advanced` flag brings any of it back, deliberately
 * and visibly, instead of by accretion.
 *
 * Both models are checked. navModels.ts exports NAV_MODEL_LEGACY and
 * NAV_MODEL_V2 and the trade_desk_v2 flag decides which renders, so a
 * destination added to one alone is the same regression with a longer fuse —
 * the bug its own comments record from #78.
 */
import { describe, it, expect } from "vitest";

import { NAV_MODEL_LEGACY, NAV_MODEL_V2 } from "../navModels";
import { filterNavForDisplay } from "../navLabels";

const MODELS: [string, typeof NAV_MODEL_LEGACY][] = [
  ["legacy", NAV_MODEL_LEGACY],
  ["v2", NAV_MODEL_V2],
];

/** What a signed-in operator sees with Advanced closed — the daily rail. */
function defaultRail(model: typeof NAV_MODEL_LEGACY) {
  // Positional, and deliberately NOT cast: an object literal here silently
  // becomes `showAdvanced = truthy`, every advanced item renders, and the
  // whole file passes while measuring nothing.
  return filterNavForDisplay(model, /* showAdvanced */ false, "dashboard", /* signedIn */ true);
}

function leafKeys(model: typeof NAV_MODEL_LEGACY): string[] {
  return defaultRail(model).flatMap((g) =>
    g.children?.length ? g.children.map((c) => c.key) : g.key ? [g.key] : [],
  );
}

/** Destinations that must survive any future tidy-up, with the goal each serves. */
const LOAD_BEARING: Record<string, string> = {
  dashboard: "goal 1 — the terminal screen itself",
  "trade:positions": "goal 3 — the open book an automated desk is accountable for",
  "risk:heat": "goal 3 — risk controls must never be behind a toggle",
  "system:broker": "goal 3 — execution is dead without a broker connection",
  "markets:chart": "goal 2 — a signal is unreadable without price context",
};

describe("nav stays aligned to the product goals", () => {
  it.each(MODELS)("%s rail is small enough to be a terminal", (_name, model) => {
    const keys = leafKeys(model);
    expect(keys.length).toBeLessThanOrEqual(10);
    expect(defaultRail(model).length).toBeLessThanOrEqual(6);
  });

  it.each(MODELS)("%s keeps every load-bearing destination visible", (_name, model) => {
    const keys = new Set(leafKeys(model));
    for (const [key, why] of Object.entries(LOAD_BEARING)) {
      expect(keys.has(key), `${key} must stay on the default rail — ${why}`).toBe(true);
    }
  });

  it.each(MODELS)("%s reaches signals for BOTH asset classes by default", (_name, model) => {
    const rail = defaultRail(model);
    const labels = rail.flatMap((g) => (g.children ?? []).map((c) => c.label)).join(" | ");
    const keys = new Set(leafKeys(model));
    // Either two destinations (legacy) or one that carries both (v2's Signal
    // Center hosts the EQUITIES|OPTIONS toggle). Options must not be the one
    // that gets demoted: goal 2 names both asset classes explicitly.
    const bothSplit = keys.has("equity") && keys.has("options:signals");
    const bothMerged = keys.has("equity") && /equity\s*&\s*options/i.test(labels);
    expect(bothSplit || bothMerged, `neither split nor merged signals found in: ${labels}`)
      .toBe(true);
  });

  it.each(MODELS)("%s keeps research and admin off the daily rail", (_name, model) => {
    const keys = new Set(leafKeys(model));
    for (const key of [
      "crypto:signals",     // phase-1 read-only, and neither equity nor options
      "strat:research",
      "strat:calendar",
      "strat:alpha-edge",   // scored on demand, never persisted — not in the decision path
      "strat:signal-history",
      "strat:cards",
      "strat:health",
      "markets:watchlists", // configuration, not a decision surface
      "journal",
      "analytics",
      "account:profile",
      "account:password",
      "account:sessions",
    ]) {
      expect(keys.has(key), `${key} is back on the default rail`).toBe(false);
    }
  });

  it.each(MODELS)("%s demotes rather than deletes — everything is still reachable", (_name, model) => {
    const all = model.flatMap((g) => (g.children ?? []).map((c) => c.key).concat(g.key ?? []));
    for (const key of ["crypto:signals", "journal", "analytics", "account:profile",
                       "strat:research", "markets:watchlists", "strat:alpha-edge",
                       "strat:cards", "strat:health", "strat:signal-history"]) {
      expect(all, `${key} was DELETED from the model, not demoted`).toContain(key);
    }
  });
});
