/**
 * The chart legend starts collapsed and opens on demand, in a real browser:
 * jsdom does no layout, so it cannot tell whether the key covers the price line.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const bars = Array.from({ length: 40 }, (_, i) => ({
  timestamp: `2026-08-14T${String(9 + Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}:00-04:00`,
  open: 100 + i * 0.1, high: 102 + i * 0.1, low: 99 + i * 0.1, close: 101 + i * 0.1, volume: 1000 + i,
}));
async function open(page: Page, width: number) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/chart/**", r => r.fulfill(reply({ bars })));
  await page.addInitScript(() => { try { localStorage.clear(); } catch { /* ignore */ } });
  await page.setViewportSize({ width, height: 1000 });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.getByRole("heading", { name: "Market workspace" })).toBeVisible();
}

test("desktop chart starts with the legend collapsed and opens it on demand", async ({ page }) => {
  await open(page, 1440);
  await expect(page.getByLabel("Indicator readout")).toContainText("MA5");
  await expect(page.getByLabel("Chart legend")).toHaveCount(0);
  await page.getByRole("button", { name: "Legend", exact: true }).click();
  await expect(page.getByLabel("Chart legend").getByText("MA20", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Hide legend" }).click();
  await expect(page.getByLabel("Chart legend")).toHaveCount(0);
  await expectNoHorizontalOverflow(page, 1440);
});
