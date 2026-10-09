/**
 * The landing nav row must not overflow a narrow phone viewport.
 *
 * This guards a bug that shipped twice. The row was a fixed 373.9px wide at
 * EVERY viewport, because .landing-nav-actions is flex-shrink: 0 and the CTA
 * label is unbreakable text — so it cleared a 375px screen by 1.1px, overhung
 * 360px by 14px and 320px by 54px. The first fix tuned it for the one screen
 * it was measured on (390px) and left no headroom for anything narrower.
 *
 * ── Why this test looks like this ────────────────────────────────────────
 *
 * The honest test is a browser at four widths asserting document.scrollWidth.
 * We cannot write that here: vitest runs under jsdom, which does NO layout.
 * Every element measures 0x0, no stylesheet is applied, and scrollWidth is
 * always 0 — so a layout assertion in this suite would pass against the bug
 * and against the fix identically. That is worse than no test.
 *
 * So this asserts the two STRUCTURAL invariants the fix rests on, the same way
 * noAlphaConcat.test.ts guards a CSS bug the runtime cannot see:
 *
 *   1. the phone breakpoint lets the row shrink, and
 *   2. both CTA labels exist for the CSS to swap between.
 *
 * It catches the regression that actually threatens this fix — someone
 * deleting the shrink guard or a label while the suite stays green — and it
 * does not pretend to verify pixel geometry. Real layout coverage needs
 * Playwright in CI; measured by hand in Chromium at 390/375/360/320 for now.
 */

import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import Landing from "../Landing";

/**
 * The stylesheet is read from disk rather than imported.
 *
 * Vite's ?raw is what noAlphaConcat.test.ts uses, but it yields an empty
 * string for CSS here: vitest stubs stylesheet modules (test.css defaults to
 * false) and the stub wins over the query. Setting test.css = true does make
 * ?raw work, but it also applies real stylesheets inside jsdom, which hides
 * `.landing-nav-toggle` (display: none until the phone breakpoint) from the
 * accessibility tree and breaks the existing Landing nav-toggle test. Not
 * worth reshaping how every suite loads styles for one assertion.
 *
 * @ts-expect-error keeps this free of @types/node, which the project does not
 * depend on — and should not, since it would swap the DOM's `number` timer
 * handles for NodeJS.Timeout across the whole frontend.
 */
// @ts-expect-error - node:fs is untyped here; vitest runs in Node, so it resolves at runtime.
import { readFileSync } from "node:fs";

// NOTE: assembled by string, NOT `new URL(..., import.meta.url)`. Vite
// rewrites that exact pattern into an asset URL, so the literal form resolves
// to a bundler path and readFileSync fails. This stays correct whether vitest
// is run from frontend/ or from the repo root.
const TEST_DIR = import.meta.url.replace(/^file:\/\//, "").replace(/\/[^/]*$/, "");
const LANDING_CSS: string = readFileSync(`${TEST_DIR}/../../landing.css`, "utf8");

/** The phone breakpoint the nav collapse lives in. */
const PHONE_QUERY = "@media (max-width: 600px)";

/**
 * Pull one @media block out by counting braces. A regex cannot do this: the
 * block contains nested rules, so `[^}]*` stops at the first inner `}`.
 */
function mediaBlock(css: string, query: string): string {
  const start = css.indexOf(query);
  if (start === -1) throw new Error(`no ${query} block in landing.css`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  throw new Error(`unterminated ${query} block`);
}

/**
 * Split CSS into [selector, declarations] pairs.
 *
 * Comments are stripped FIRST. A commented-out rule still contains braces, so
 * parsing before stripping would read it as live CSS — which is exactly the
 * false pass the decoy test below guards against.
 *
 * `[^{}]+` cannot span a nested `{`, so an @media wrapper never matches as a
 * selector; the rules inside it are found individually instead.
 */
function rules(css: string): Array<[string, string]> {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Array<[string, string]> = [];
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = rule.exec(source)) !== null) {
    out.push([m[1].trim().replace(/\s+/g, " "), m[2]]);
  }
  return out;
}

/**
 * Every declaration written for exactly this selector, concatenated.
 * Matched whole, so `.landing-cta-compact` never reads a rule that is really
 * `.landing-nav-cta .landing-cta-compact`.
 */
function declarationsFor(css: string, selector: string): string {
  return rules(css)
    .filter(([sel]) => sel === selector)
    .map(([, decls]) => decls)
    .join(";") + ";";
}

/** Does this selector end up with the given property value here? */
function has(css: string, selector: string, property: string, value: string): boolean {
  return new RegExp(`${property}\\s*:\\s*${value}\\s*[;}]`)
    .test(declarationsFor(css, selector));
}

describe("landing nav cannot overflow a phone viewport", () => {
  const phone = mediaBlock(LANDING_CSS, PHONE_QUERY);

  it("lets the actions row shrink instead of pinning it to its content width", () => {
    // The base rule is flex-shrink: 0. Without an override here the row keeps
    // its full content width no matter how narrow the screen is, which is the
    // entire bug.
    expect(
      has(phone, ".landing-nav-actions", "flex-shrink", "1"),
      "the base .landing-nav-actions rule is flex-shrink: 0 — without an " +
      "override at the phone breakpoint the nav row cannot give width back " +
      "and pushes the document wider than the viewport"
    ).toBe(true);
  });

  it("lets the CTA itself shrink below its label width", () => {
    // A flex item's automatic minimum size is its min-content width, so a
    // shrinkable parent alone is not enough — the CTA has to be allowed to go
    // narrower than its own text too.
    expect(
      has(phone, ".landing-nav-cta", "min-width", "0"),
      "without min-width: 0 the CTA floors at its label width and the row " +
      "still cannot shrink past it"
    ).toBe(true);
  });

  it("carries a single short label, so nothing needs swapping", () => {
    // This file used to check a two-label swap: "Start Paper Trading" shown
    // on desktop and a compact "Start Free" on phones, because the full label
    // measures 173px — most of the phone nav row's budget.
    //
    // The CTA is now "Sign Up", roughly a third of that width, so the swap
    // has nothing to do and the spans, their CSS and three tests went with
    // it. What still matters is that the label stays SHORT: the overflow
    // tests above pass because the row can shrink, not because any label is
    // small, and a future edit back to a long label would re-create the
    // original bug with no swap left to rescue it.
    render(React.createElement(MemoryRouter, null, React.createElement(Landing)));

    const nav = document.querySelector(".landing-nav-cta");
    expect(nav, ".landing-nav-cta is gone — the phone CSS has no target").not.toBeNull();

    const label = (nav!.textContent || "").trim();
    expect(label).toBe("Sign Up");
    expect(label.length).toBeLessThanOrEqual(12);

    // The swap machinery is gone from the markup, not merely unused.
    expect(nav!.querySelector(".landing-cta-full")).toBeNull();
    expect(nav!.querySelector(".landing-cta-compact")).toBeNull();
  });

  it("would actually catch a regression", () => {
    // A guard test that cannot fail is decoration. Each mutation below is a
    // real way this fix has been or could be undone.
    const dropShrink = phone.replace(/\.landing-nav-actions\s*\{\s*flex-shrink:\s*1;\s*\}/, "");
    expect(dropShrink).not.toBe(phone);
    expect(has(dropShrink, ".landing-nav-actions", "flex-shrink", "1")).toBe(false);

    // Targeted at the CTA's own rule: .landing-nav-actions also carries a
    // min-width: 0, so a blanket replace would mutate the wrong rule and this
    // assertion would pass while testing nothing.
    const dropMinWidth = phone.replace(
      /(\.landing-nav-cta\s*\{[^}]*?)min-width:\s*0;/,
      "$1"
    );
    expect(dropMinWidth).not.toBe(phone);
    expect(has(dropMinWidth, ".landing-nav-cta", "min-width", "0")).toBe(false);
    // ...and the rule it should NOT have touched is still intact.
    expect(has(dropMinWidth, ".landing-nav-actions", "flex-shrink", "1")).toBe(true);

    // Was a mutation of the label-swap rule, which no longer exists — the CTA
    // carries one short label now. Re-aimed at the nav toggle, which is the
    // other rule this media query must carry: without it the hamburger never
    // appears and the links row cannot collapse at all.
    const dropToggle = phone.replace(/\.landing-nav-toggle\s*\{[^}]*\}/, "");
    expect(dropToggle).not.toBe(phone);
    expect(has(dropToggle, ".landing-nav-toggle", "display", "inline-flex")).toBe(false);
  });

  it("does not pass on a stylesheet that merely mentions the properties", () => {
    // The opposite failure: a matcher loose enough to be satisfied by a
    // comment or an unrelated selector, which would make all of the above
    // decoration.
    const decoy = `
      /* .landing-nav-actions { flex-shrink: 1; } */
      .something-else { flex-shrink: 1; min-width: 0; }
    `;
    expect(has(decoy, ".landing-nav-actions", "flex-shrink", "1")).toBe(false);
    expect(has(decoy, ".landing-nav-cta", "min-width", "0")).toBe(false);
  });
});
