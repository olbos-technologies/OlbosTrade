import { test, expect, type Page } from "@playwright/test";
import { stubBackend, SIGNED_IN } from "./helpers";

const positions = ["ADI", "GOOGL", "ADBE", "NVDA", "MSFT"].map((symbol, index) => ({
  id: `trade-${symbol}`,
  symbol,
  asset_type: "equity",
  spread_type: "equity_long",
  strategy: "adopted_untracked",
  tracked: true,
  quantity: index + 1,
  avg_cost: 200 + index,
  unrealized_pnl: -(index + 1) * 89,
  mfe_pnl: 12,
  mae_pnl: -110,
  hold_days: 6,
  trading_mode: "balanced",
}));

async function openPositions(page: Page, width: number) {
  await stubBackend(page, SIGNED_IN);
  await page.route("**/api/paper-trade/positions", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ positions }),
  }));
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/terminal/trade/positions");
  await expect(page.locator(".t-table--cards tbody tr")).toHaveCount(5);
}

for (const width of [320, 390]) {
  test(`compact position rows remain scannable at ${width}px`, async ({ page }) => {
    await openPositions(page, width);
    const cards = page.locator(".t-table--cards tbody tr");
    const heights = await cards.evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height));
    expect(Math.max(...heights)).toBeLessThan(100);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);

    const first = cards.first();
    await expect(first.locator('[data-col="action"]')).toBeHidden();
    const toggle = first.getByRole("button", { name: "Show details for ADI position 1" });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(first.getByRole("button", { name: "Hide details for ADI position 1" })).toHaveAttribute("aria-expanded", "true");
    await expect(first.locator('[data-col="action"]')).toBeVisible();
    await expect(first.getByRole("button", { name: "Review close ADI" })).toBeVisible();
    await expect(cards.nth(1).locator('[data-col="action"]')).toBeHidden();
    // Never submit a close order in a layout test.
  });
}

test("desktop keeps the full position table", async ({ page }) => {
  await openPositions(page, 1280);
  const first = page.locator(".t-table--cards tbody tr").first();
  await expect(first.locator(".position-mobile-expand")).toBeHidden();
  await expect(first.locator('[data-col="action"]')).toBeVisible();
  await expect(first.getByRole("button", { name: "Review close ADI" })).toBeVisible();
});
