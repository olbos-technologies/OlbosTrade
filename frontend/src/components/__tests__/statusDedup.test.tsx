/**
 * The Trade Desk says each thing once on a phone.
 *
 * Before this, one desk screen showed paper/live three times (the risk-chip
 * strip, the desk rail, the status bar) and execution mode four. Roughly 70px
 * of an 844px screen spent repeating what was directly above it, on the page
 * whose content had 133px.
 *
 * ── Why this asserts CSS text rather than rendering ───────────────────────
 *
 * vitest runs under jsdom, which does no layout and applies no stylesheet, so
 * `display` computes the same before and after the fix. A render-based test
 * here would pass against both. Same constraint as landingNavOverflow.test.ts,
 * and the same answer: assert the rules, and leave the geometry to Playwright.
 *
 * The scoping is what matters. These bands are hidden ONLY on the desk and
 * ONLY on phones, because everywhere else they are the single place that state
 * appears — and the kill lamp is a safety display, dropped here solely because
 * the HALT button sits a few pixels above it.
 */

import { describe, expect, it } from "vitest";
// @ts-expect-error - node:fs is untyped here; vitest runs in Node.
import { readFileSync } from "node:fs";

const DIR = import.meta.url.replace(/^file:\/\//, "").replace(/\/[^/]*$/, "");
const CSS: string = readFileSync(`${DIR}/../../index.css`, "utf8");

/**
 * EVERY phone block, concatenated.
 *
 * index.css has more than one `@media (max-width: 760px)`, and the first one
 * is not the one carrying these rules — reading only the first made all four
 * assertions fail against a stylesheet that was in fact correct. Anything
 * that looks for a rule by scanning "the" media block has the same bug
 * waiting for it.
 */
function phoneBlock(): string {
  const marker = "@media (max-width: 760px)";
  const blocks: string[] = [];
  let from = 0;
  for (;;) {
    const start = CSS.indexOf(marker, from);
    if (start === -1) break;
    const open = CSS.indexOf("{", start);
    let depth = 0;
    for (let i = open; i < CSS.length; i++) {
      if (CSS[i] === "{") depth++;
      else if (CSS[i] === "}" && --depth === 0) {
        blocks.push(CSS.slice(open + 1, i));
        from = i;
        break;
      }
    }
    if (from <= start) throw new Error("unterminated 760px block");
  }
  if (!blocks.length) throw new Error("no 760px block in index.css");
  return blocks.join("\n");
}

describe("duplicated status bands are hidden on the desk, on phones only", () => {
  const phone = phoneBlock();

  it("hides the risk-chip strip and the duplicated lamps", () => {
    expect(phone).toMatch(/\.app-shell--desk\s+\.global-risk-status\s*\{[^}]*display:\s*none/);
    expect(phone).toMatch(/\.app-shell--desk\s+\.status-dup\s*\{[^}]*display:\s*none/);
  });

  it("keeps !important on the lamp rule", () => {
    // NOT a style preference. StatusLamp sets display: inline-flex as an
    // INLINE style, which beats any selector — without !important the rule
    // hid the ENV span and left Kill, Exec and Style on screen. Dropping it
    // silently restores exactly that half-applied state.
    const rule = phone.match(/\.app-shell--desk\s+\.status-dup\s*\{[^}]*\}/)?.[0] || "";
    expect(rule, "the lamps set display inline; without !important they stay visible")
      .toMatch(/display:\s*none\s*!important/);
  });

  it("keeps !important on the risk-strip rule", () => {
    const rule = phone.match(/\.app-shell--desk\s+\.global-risk-status\s*\{[^}]*\}/)?.[0] || "";
    expect(rule, "global risk strip sets display inline; without !important it stays visible")
      .toMatch(/display:\s*none\s*!important/);
  });

  it("scopes both rules to the desk, never globally", () => {
    // A rule without .app-shell--desk would blank these bands on every page,
    // where they are the only place this state is shown.
    for (const sel of [".global-risk-status", ".status-dup"]) {
      const idx = phone.indexOf(sel);
      expect(idx).toBeGreaterThan(-1);
      const lineStart = phone.lastIndexOf("\n", idx);
      const selector = phone.slice(lineStart + 1, idx + sel.length);
      expect(selector, `${sel} is hidden without scoping it to the desk`)
        .toContain(".app-shell--desk");
    }
  });

  it("would catch the rules being dropped", () => {
    const stripped = phone
      .replace(/\.app-shell--desk\s+\.status-dup\s*\{[^}]*\}/, "")
      .replace(/\.app-shell--desk\s+\.global-risk-status\s*\{[^}]*\}/, "");
    expect(stripped).not.toBe(phone);
    expect(stripped).not.toMatch(/\.app-shell--desk\s+\.status-dup/);
    expect(stripped).not.toMatch(/\.app-shell--desk\s+\.global-risk-status/);
  });

  it("keeps the mobile kill-switch button overrides that must beat inline styles", () => {
    const rule = phone.match(/\.instrument-rail-kill\s+button\s*\{[^}]*\}/)?.[0] || "";
    expect(rule).toMatch(/min-height:\s*44px\s*!important/);
    expect(rule).toMatch(/width:\s*auto\s*!important/);
  });
});
