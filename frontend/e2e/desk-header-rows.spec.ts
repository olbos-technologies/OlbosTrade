/**
 * The desk header rows - the ENV chip row, the Session strip, the tab row and the
 * "Additional desk tools" row - read as one set: same background, same single
 * bottom rule, no raised box or outline on any of them, and the Session, tab and
 * tools labels start at the same left edge.
 */
import { test, expect } from "@playwright/test";
import { stubBackend } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

test("desk header rows share one background, one bottom rule and one label edge", async ({ page }) => {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/market/broker", r => r.fulfill(reply({ broker: "ibkr", paper_mode: true, status: "connected" })));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/terminal/trade/positions");
  await expect(page.locator(".instrument-rail")).toBeVisible();
  await expect(page.locator(".desk-tablist .desk-tab").first()).toBeVisible();
  await expect(page.locator(".desk-auxiliary-tools > summary")).toBeVisible();

  const m = await page.evaluate(() => {
    const textLeft = (el: Element | null) => {
      if (!el) return null;
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let n: Node | null;
      while ((n = w.nextNode())) {
        if ((n.textContent || "").trim()) { const r = document.createRange(); r.selectNodeContents(n); return Math.round(r.getBoundingClientRect().x); }
      }
      return null;
    };
    const style = (sel: string) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      return { bg: cs.backgroundColor, img: cs.backgroundImage, shadow: cs.boxShadow, bb: cs.borderBottom, bt: cs.borderTopWidth, bl: cs.borderLeftWidth, radius: cs.borderTopLeftRadius };
    };
    return {
      session: textLeft(document.querySelector(".instrument-rail .instrument-chip-label")),
      tab: textLeft(document.querySelector(".desk-tablist .desk-tab")),
      tools: textLeft(document.querySelector(".desk-auxiliary-tools > summary")),
      rows: [".global-risk-status", ".instrument-rail", ".desk-tablist", ".desk-auxiliary-tools"].map(style),
    };
  });

  // The labels of the three rows start at the same edge (to the pixel).
  expect(m.session).not.toBeNull();
  expect(m.tab).toBe(m.session);
  expect(m.tools).toBe(m.session);

  // One background and one bottom rule across all four rows, with nothing raised.
  const [env, ...rest] = m.rows;
  expect(env).not.toBeNull();
  for (const row of rest) {
    expect(row).not.toBeNull();
    expect(row!.bg).toBe(env!.bg);
    expect(row!.bb).toBe(env!.bb);
    expect(row!.img).toBe("none");
    expect(row!.shadow).toBe("none");
  }
  // The Session strip is no longer an outlined, rounded box.
  const rail = m.rows[1]!;
  expect([rail.bt, rail.bl, rail.radius]).toEqual(["0px", "0px", "0px"]);
});
