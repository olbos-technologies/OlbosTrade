/**
 * Capital-at-risk strip must share the body column with page content — not
 * span full-bleed over the desktop sidebar. Geometry is Playwright's job;
 * this guards the call-site structure so a revert cannot quietly undo it.
 */
import { describe, expect, it } from "vitest";
// @ts-expect-error - node:fs is untyped here; vitest runs in Node.
import { readFileSync } from "node:fs";

const DIR = import.meta.url.replace(/^file:\/\//, "").replace(/\/[^/]*$/, "");
const LAYOUT: string = readFileSync(`${DIR}/../TerminalLayout.tsx`, "utf8");

describe("capital-at-risk body alignment", () => {
  it("mounts GlobalRiskStatus inside workspace-body-column after the Sidebar", () => {
    const sidebar = LAYOUT.indexOf("<Sidebar");
    const column = LAYOUT.indexOf('className="workspace-body-column"');
    const risk = LAYOUT.indexOf("<GlobalRiskStatus");
    expect(sidebar).toBeGreaterThan(-1);
    expect(column).toBeGreaterThan(-1);
    expect(risk).toBeGreaterThan(-1);
    expect(column).toBeGreaterThan(sidebar);
    expect(risk).toBeGreaterThan(column);
  });
});
