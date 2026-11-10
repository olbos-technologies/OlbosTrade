/**
 * Phone gutter, every page: the page heading and the first card start on the same
 * 16px left edge as the ENV chip and the "Find workspace" box, not 20-40px in.
 * Left edges only - they do not depend on font widths.
 */
import { test, expect } from "@playwright/test";
import { stubBackend } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
const ROUTES = [
  "risk/rules", "backtest", "lab/scenario", "lab/intel", "strat/cards", "strat/health",
  "strat/alpha-edge", "strat/signal-history", "options/income", "options/flow",
  "markets/chart", "markets/watchlists", "trade/settings",
];

for (const width of [390, 320]) {
  test(`page content sits on the 16px gutter at ${width}px`, async ({ page }) => {
    test.setTimeout(180000);
    await stubBackend(page);
    await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
      ? reply({ auth_enabled: false, authenticated: false, user: null })
      : { status: 503, contentType: "application/json", body: "{}" }));
    await page.setViewportSize({ width, height: 900 });
    for (const route of ROUTES) {
      await page.goto(`/terminal/${route}`);
      await page.locator("main h1, main h2, main .instrument-card").first().waitFor({ timeout: 8000 }).catch(() => { throw new Error(`${route}: no heading rendered`); });
      await page.waitForTimeout(300);
      const xs = await page.evaluate(() => {
        const pick = (sel: string) => {
          const el = document.querySelector(sel) as HTMLElement | null;
          return el ? Math.round(el.getBoundingClientRect().x) : null;
        };
        return { heading: pick("main h1, main h2"), card: pick("main .instrument-card") };
      });
      const want = 16; // the ENV chip and the Find workspace box
      expect(xs.heading !== null || xs.card !== null, `${route}: nothing to measure`).toBe(true);
      if (xs.heading !== null) expect(Math.abs(xs.heading - want), `${route}: heading x=${xs.heading}`).toBeLessThanOrEqual(1);
      if (xs.card !== null) expect(Math.abs(xs.card - want), `${route}: card x=${xs.card}`).toBeLessThanOrEqual(1);
    }
  });
}
