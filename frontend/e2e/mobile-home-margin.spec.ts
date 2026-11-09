/**
 * Phone Home: the Overview heading and every card below it start and end on the same margins as the
 * "Find workspace" box and the ENV row above them (they used to start 16px further in).
 */
import { test, expect } from "@playwright/test";
import { stubBackend } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

for (const width of [390, 320]) {
  test(`Home heading and cards share the Find workspace box's margins at ${width}px`, async ({ page }) => {
    await stubBackend(page);
    await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
      ? reply({ auth_enabled: false, authenticated: false, user: null })
      : { status: 503, contentType: "application/json", body: "{}" }));
    await page.route("**/api/dashboard/summary", r => r.fulfill(reply({ total_equity: 245753.5, day_pnl: 0, total_pnl: 2384.98 })));
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/terminal/dashboard");
    await expect(page.getByTestId("mobile-home")).toBeVisible();
    await expect(page.locator(".mobile-home .mh-card").first()).toBeVisible();
    const m = await page.evaluate(() => {
      const find = document.querySelector(".workspace-command-search input")!.getBoundingClientRect();
      const h1 = document.querySelector(".mobile-home .mh-heading h1")!.getBoundingClientRect();
      const cards = Array.from(document.querySelectorAll(".mobile-home > .mh-card, .mobile-home > section")) as HTMLElement[];
      return {
        findX: Math.round(find.x), findR: Math.round(find.right), h1X: Math.round(h1.x),
        cards: cards.filter(c => c.offsetParent).map(c => { const r = c.getBoundingClientRect(); return { x: Math.round(r.x), r: Math.round(r.right), cls: c.className.toString().slice(0, 30) }; }),
      };
    });
    expect(m.h1X, "Overview heading left edge").toBe(m.findX);
    expect(m.cards.length).toBeGreaterThan(2);
    for (const c of m.cards) {
      expect(c.x, `${c.cls} left edge`).toBe(m.findX);
      expect(c.r, `${c.cls} right edge`).toBe(m.findR);
    }
  });
}
