/**
 * The Orders and Execution header rows use the same flat row and boxed controls as the
 * other desk rows, and their filters start at the left edge (they used to be centered,
 * so a long filter row could push out past the left edge).
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
const LOG = ["AVGO", "KHC", "SPCX"].map((t, i) => ({ signal_id: `s${i}`, ticker: t, asset_type: "equity", action: "BUY", status: "blocked", executed_at: "2026-11-09T17:30:00Z", reason: "portfolio_gate: Max concurrent positions reached: 3/3" }));

async function open(page: Page, url: string, width: number) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/trade-desk/execution-log**", r => r.fulfill(reply({ log: LOG })));
  await page.route("**/api/trade-desk/pending**", r => r.fulfill(reply({ pending: [] })));
  await page.setViewportSize({ width, height: 900 });
  await page.goto(url);
}

for (const [name, url] of [["Orders", "/terminal/trade/orders"], ["Execution", "/terminal/trade/execlog"]] as const) {
  test(`${name}: flat row, boxed left-aligned filters, nothing past the left edge at 1280px`, async ({ page }) => {
    await open(page, url, 1280);
    const rail = page.locator(".desk-tool-rail").first();
    await expect(rail).toBeVisible();
    await expect(rail.locator(".asset-toggle__btn").first()).toBeVisible();
    const m = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.style.background = "var(--bg-3)";
      (document.querySelector(".app-shell") ?? document.body).appendChild(probe);
      const chipFill = getComputedStyle(probe).backgroundColor;
      probe.remove();
      const railEl = document.querySelector(".desk-tool-rail") as HTMLElement;
      const rcs = getComputedStyle(railEl);
      const rr = railEl.getBoundingClientRect();
      const filters = Array.from(railEl.querySelectorAll(".asset-toggle__btn")) as HTMLElement[];
      const tab = document.querySelector(".desk-tablist .desk-tab") as HTMLElement;
      const tabBar = document.querySelector(".desk-tablist") as HTMLElement;
      return {
        chipFill,
        railImg: rcs.backgroundImage, railShadow: rcs.boxShadow, railBg: rcs.backgroundColor, tabBarBg: getComputedStyle(tabBar).backgroundColor,
        railLeft: Math.round(rr.left), tabX: Math.round(tab.getBoundingClientRect().x),
        firstFilterX: Math.round(filters[0].getBoundingClientRect().x),
        minFilterLeft: Math.min(...filters.map(f => Math.round(f.getBoundingClientRect().left))),
        filters: filters.map(f => { const cs = getComputedStyle(f); return { bw: [cs.borderTopWidth, cs.borderLeftWidth].join(" "), radius: cs.borderTopLeftRadius, bg: cs.backgroundColor }; }),
      };
    });
    expect(m.railImg).toBe("none");
    expect(m.railShadow).toBe("none");
    expect(m.railBg).toBe(m.tabBarBg);
    // Filters start at the same left edge as the tab boxes above (not centered), and never left of the rail.
    expect(m.firstFilterX).toBe(m.tabX);
    expect(m.minFilterLeft).toBeGreaterThanOrEqual(m.railLeft);
    for (const f of m.filters) { expect(f.bw).toBe("1px 1px"); expect(f.radius).toBe("3px"); expect(f.bg).toBe(m.chipFill); }
  });
}

test("Orders on a phone: the rail's controls stay inside the screen and are at least 40px tall", async ({ page }) => {
  await open(page, "/terminal/trade/orders", 390);
  const rail = page.locator(".desk-tool-rail").first();
  await expect(rail.locator(".asset-toggle__btn").first()).toBeVisible();
  const boxes = await rail.locator("button").evaluateAll(els => els.map(e => { const r = e.getBoundingClientRect(); return { left: Math.round(r.left), right: Math.round(r.right), h: Math.round(r.height) }; }));
  for (const b of boxes) {
    expect(b.left).toBeGreaterThanOrEqual(0);
    expect(b.right).toBeLessThanOrEqual(390);
    expect(b.h).toBeGreaterThanOrEqual(40);
  }
});
