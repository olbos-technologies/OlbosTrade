/**
 * The PLAN's vocabulary rule, enforced.
 *
 * docs/ui-ux-phase1-4/PLAN.md states it plainly: "Never call trading style
 * 'Risk profile' or execution mode 'AI mode' in user-facing copy." The two are
 * genuinely different controls — trading style sets sizing and strategy
 * selection, execution mode decides whether a trade needs your approval — and
 * calling one by the other's name is how a user ends up believing they changed
 * something they did not.
 *
 * A source scan rather than a component test, because this is a rule about every
 * string in the app, not the behaviour of one component. Five user-facing
 * occurrences plus four stale comments had drifted in across Dashboard,
 * TradeDesk, Guardrails, CspScreener, ModeAnalytics and TradeDeskHeader; without
 * a check that reads the whole tree, a tenth is only a matter of time.
 *
 * Comments are held to the rule too. They are not user-facing, but stale naming
 * in a comment is precisely how the wrong word gets reintroduced by the next
 * person to touch the file — and exempting them would mean distinguishing a
 * comment from a string literal by regex, which is a worse trade than just
 * keeping the vocabulary consistent everywhere.
 *
 * ONE DELIBERATE EXCEPTION, and it is not a loophole: CspScreener has its own
 * candidate filter that shares the conservative/balanced/aggressive value names.
 * It is not the account's trading style, so it is named "Screener risk filter" —
 * the word "profile" is gone from it entirely, which is why it needs no
 * exemption here.
 *
 * Sources read through Vite's own glob rather than node:fs, matching
 * noAlphaConcat.test.ts: no @types/node needed, and the bundler's own resolver
 * decides what counts as source.
 */

import { describe, expect, it } from "vitest";

const FILES = import.meta.glob("/src/**/*.{ts,tsx}", {
  query: "?raw", eager: true, import: "default",
}) as Record<string, string>;

/** Files allowed to contain the banned terms, each for a stated reason. */
const EXEMPT = [
  "/src/utils/__tests__/vocabulary.test.ts",   // this file quotes them
];

const BANNED: Array<[RegExp, string]> = [
  [/risk\s+profile/i, 'trading style called "Risk profile"'],
  [/\bAI\s+mode\b/i, 'execution mode called "AI mode"'],
];

describe("PLAN vocabulary rules", () => {
  it("scans a meaningful number of files", () => {
    // Guards the guard: a broken glob would make the assertions below vacuous.
    expect(Object.keys(FILES).length).toBeGreaterThan(50);
  });

  for (const [pattern, label] of BANNED) {
    it(`never has ${label}`, () => {
      const offenders: string[] = [];
      for (const [path, source] of Object.entries(FILES)) {
        if (EXEMPT.includes(path)) continue;
        source.split("\n").forEach((line, i) => {
          if (pattern.test(line)) offenders.push(`${path}:${i + 1}: ${line.trim()}`);
        });
      }
      expect(offenders).toEqual([]);
    });
  }
});
