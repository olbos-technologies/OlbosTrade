/**
 * A link that sets its own colour must outrank the `a { color: inherit }`
 * reset in the same stylesheet.
 *
 * This is here because the obvious thing is wrong and looks right. Both
 * auth.css and landing.css open with a reset of the form
 *
 *     .auth-page a { color: inherit; text-decoration: none; }
 *
 * which is specificity (0,1,1) — a class AND a type selector. A later rule
 * written the natural way, `.auth-link { color: var(--accent) }`, is (0,1,0)
 * and LOSES to it, however far down the file it sits. The link then silently
 * inherits its parent's colour.
 *
 * Both files had it. Verified in Chromium before the fix:
 *   .auth-note .auth-link   computed rgb(107,122,144)  (--ink-faint)
 *   .landing-signin         computed rgb(232,237,244)  (--ink)
 * where --accent and --ink-dim were intended. On the landing page that made
 * "Sign In" brighter than the nav links beside it and its :hover a no-op,
 * which is visible in a screenshot once you know to look.
 *
 * jsdom does not compute the cascade, so no render test in this suite could
 * have caught it, and neither colour is obviously wrong to the eye at 11px on
 * a dark background. So the check is static: parse the stylesheet, find the
 * reset, and assert every rule that sets a colour on an anchor class can
 * actually beat it.
 */

import { describe, it, expect } from "vitest";
// @ts-expect-error - node:fs is untyped here; vitest runs in Node, so it resolves at runtime.
// Same idiom and same reason as landingNavOverflow.test.ts: this project has
// no @types/node on purpose, since adding it would swap the DOM's `number`
// timer handles for NodeJS.Timeout across the whole frontend.
import { readFileSync } from "node:fs";

// Assembled by string, NOT `new URL(..., import.meta.url)` — Vite rewrites
// that exact pattern into an asset URL, so the literal form resolves to a
// bundler path and readFileSync fails.
const TEST_DIR = import.meta.url.replace(/^file:\/\//, "").replace(/\/[^/]*$/, "");

/** Specificity as (ids, classes, types). Enough for the selectors in play —
 *  these stylesheets use no ids, no attribute selectors and no :is()/:where(). */
function specificity(selector: string): [number, number, number] {
  const base = selector.replace(/::?[a-z-]+(\([^)]*\))?/g, "");  // drop pseudos
  const ids = (base.match(/#[\w-]+/g) || []).length;
  const classes = (base.match(/\.[\w-]+/g) || []).length;
  const types = (base.match(/(^|[\s>+~])[a-z][\w-]*/g) || []).length;
  return [ids, classes, types];
}

function beats(a: [number, number, number], b: [number, number, number]): boolean {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return true;                       // equal specificity — later rule wins
}

type Rule = { selector: string; body: string };

function rules(css: string): Rule[] {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Rule[] = [];
  for (const m of stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const sel of m[1].split(",")) {
      const s = sel.trim();
      if (s && !s.startsWith("@")) out.push({ selector: s, body: m[2] });
    }
  }
  return out;
}

const SHEETS: Array<[string, string]> = [
  ["auth.css", readFileSync(`${TEST_DIR}/../../auth.css`, "utf8")],
  ["landing.css", readFileSync(`${TEST_DIR}/../../landing.css`, "utf8")],
];

describe.each(SHEETS)("%s", (name, css) => {
  const parsed = rules(css);

  // The reset: a rule ending in a bare `a` that sets colour.
  const resets = parsed.filter(
    (r) => /(^|\s)a$/.test(r.selector) && /(^|[;{\s])color\s*:/.test(r.body)
  );

  it("has exactly one anchor colour reset, so there is one thing to outrank", () => {
    expect(resets.length).toBe(1);
  });

  it("every anchor class that sets a colour outranks that reset", () => {
    const reset = resets[0];
    const resetSpec = specificity(reset.selector);

    const losers = parsed
      .filter((r) => r !== reset)
      .filter((r) => /(^|[;{\s])color\s*:/.test(r.body))
      // Rules aimed at links: the selector names a class whose own name marks
      // it as a link, or names the `a` element explicitly.
      .filter((r) => /a\.[\w-]|[-.](link|signin|nav-link)\b/.test(r.selector))
      .filter((r) => !beats(specificity(r.selector), resetSpec))
      .map((r) => r.selector);

    expect(losers).toEqual([]);
  });

  it("the reset is not doing more than it needs to", () => {
    // If the reset ever stops setting colour, the rules above no longer need
    // their element qualifier and this whole class of bug goes away — at
    // which point this file should be deleted rather than left passing
    // vacuously.
    expect(resets[0].body).toMatch(/color\s*:\s*inherit/);
  });
});
