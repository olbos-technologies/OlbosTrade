/**
 * The positions list reflows into cards on a phone.
 *
 * It was an eleven-column table — Symbol, Type, Strategy, Entry Credit,
 * Unreal P&L, MFE, MAE, Hold Days, Status, Mode, Action — rendered at 390px.
 * About 35px a column, and an existing rule (`.t-table { min-width: 680px }`)
 * made it scroll sideways instead, so the list genuinely could not be read on
 * a phone. That was the original complaint.
 *
 * ── Why CSS text and not a render ─────────────────────────────────────────
 *
 * jsdom does no layout and applies no stylesheet, so a rendered `display`
 * reads the same before and after. Geometry belongs to Playwright; what is
 * worth pinning here is that the rules exist, stay inside the phone
 * breakpoint, and keep the two overrides that were each load-bearing.
 *
 * ── Why the DOM was not rewritten ─────────────────────────────────────────
 *
 * The reflow is pure CSS over the same table markup. Every branch of the
 * close-position logic — canClose, canCloseUntracked, the hold-to-confirm
 * buttons, the "not at broker" and "no trade id" fallbacks — is untouched, so
 * a presentation change cannot alter which positions can be closed.
 */

import { describe, expect, it } from "vitest";
// @ts-expect-error - node:fs is untyped here; vitest runs in Node.
import { readFileSync } from "node:fs";

const DIR = import.meta.url.replace(/^file:\/\//, "").replace(/\/[^/]*$/, "");
const CSS: string = readFileSync(`${DIR}/../../index.css`, "utf8");
const TSX: string = readFileSync(`${DIR}/../TradeDesk.tsx`, "utf8");

/** Every `@media (max-width: 760px)` block, concatenated — index.css has more
 *  than one, and reading only the first finds the wrong rules. */
function phoneBlocks(): string {
  const marker = "@media (max-width: 760px)";
  const out: string[] = [];
  let from = 0;
  for (;;) {
    const start = CSS.indexOf(marker, from);
    if (start === -1) break;
    const open = CSS.indexOf("{", start);
    let depth = 0, closed = -1;
    for (let i = open; i < CSS.length; i++) {
      if (CSS[i] === "{") depth++;
      else if (CSS[i] === "}" && --depth === 0) { closed = i; break; }
    }
    if (closed === -1) throw new Error("unterminated 760px block");
    out.push(CSS.slice(open + 1, closed));
    from = closed;
  }
  if (!out.length) throw new Error("no 760px block in index.css");
  return out.join("\n");
}

describe("positions render as cards on a phone", () => {
  const phone = phoneBlocks();

  it("marks the table so the reflow has something to target", () => {
    expect(TSX).toContain('className="t-table t-table--cards"');
  });

  it("tags every cell the card grid places", () => {
    // Placement is by data-col, not nth-child: an nth-child map silently
    // relabels every cell the moment a column is inserted.
    for (const col of ["symbol", "type", "strategy", "credit", "pnl",
                       "mfe", "mae", "hold", "status", "mode", "action"]) {
      expect(TSX, `no cell tagged data-col="${col}"`).toContain(`data-col="${col}"`);
      expect(phone, `the grid never places ${col}`).toContain(`grid-area: ${col}`);
    }
  });

  it("overrides the 680px table min-width", () => {
    // `.t-table { min-width: 680px }` elsewhere in this breakpoint makes
    // ordinary tables scroll sideways — right for a table, wrong for a card
    // list. Without this override every card was 680px wide inside a 390px
    // column and half of each one sat off-screen, present but invisible.
    expect(CSS).toMatch(/\.t-table\s*\{\s*min-width:\s*680px/);
    const cardsRule = phone.match(/\.t-table--cards,\s*\.t-table--cards tbody\s*\{[^}]*\}/)?.[0] || "";
    expect(cardsRule, "the card table still inherits min-width: 680px")
      .toMatch(/min-width:\s*0/);
  });

  it("keeps minmax(0, 1fr) so long text cannot widen the card", () => {
    // A grid track's automatic minimum is its min-content width, so a plain
    // 1fr let "MOMENTUM BREAKOUT" push the row past the viewport. Same defect
    // the landing nav fixed with min-width: 0.
    const rowRule = phone.match(/\.t-table--cards tbody tr\s*\{[^}]*\}/)?.[0] || "";
    expect(rowRule).toMatch(/grid-template-columns:\s*minmax\(\s*0\s*,\s*1fr\s*\)/);
  });

  it("labels the bare numbers, since the header row is hidden", () => {
    // "$4.20  +$300  -$120" with no header is three unexplained figures.
    for (const [col, label] of [["credit", "Entry"], ["mfe", "MFE"],
                                ["mae", "MAE"], ["hold", "Held"]] as const) {
      expect(TSX, `${col} has no data-label`).toContain(`data-label="${label}"`);
    }
    expect(phone).toMatch(/content:\s*attr\(data-label\)/);
  });

  it("gives the close control a real touch target", () => {
    const rule = phone.match(/\.t-table--cards td\[data-col="action"\] button\s*\{[^}]*\}/)?.[0] || "";
    expect(rule).toMatch(/min-height:\s*44px/);
  });

  it("stays inside the phone breakpoint", () => {
    // Outside it, the desktop positions table would become cards too.
    const idx = CSS.indexOf(".t-table--cards thead");
    expect(idx).toBeGreaterThan(-1);
    let depth = 0, enclosing = "";
    for (let j = idx; j >= 0; j--) {
      if (CSS[j] === "}") depth++;
      else if (CSS[j] === "{") {
        if (depth === 0) { enclosing = CSS.slice(CSS.lastIndexOf("\n", j) + 1, j).trim(); break; }
        depth--;
      }
    }
    expect(enclosing).toBe("@media (max-width: 760px)");
  });
});
