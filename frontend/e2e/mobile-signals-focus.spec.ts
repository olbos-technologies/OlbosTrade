/** Mobile Signals opens as a signal feed, not a duplicate system dashboard. */

import { test, expect, type Page } from "@playwright/test";
import { stubBackend, SIGNED_IN } from "./helpers";

async function stubSignalContext(page: Page) {
  await page.route("**/api/market/broker", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      broker: "ibkr", status: "connected", paper_mode: true,
      supports_options: true, supports_equities: true,
    }),
  }));
  await page.route("**/api/market/greeks", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      net_delta: 0, net_vega: 0, net_theta: 0,
      equity_position_count: 0, options_position_count: 0,
      total_position_count: 0, is_delta_neutral: true, needs_hedge: false,
    }),
  }));
}

test("Signals prioritizes the live feed on a phone", async ({ page }) => {
  await stubBackend(page, SIGNED_IN);
  await stubSignalContext(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/terminal/dashboard");
  await page.waitForSelector(".mobile-bottom-nav");
  await page.locator(".mobile-bottom-nav")
    .getByRole("button", { name: "Signals", exact: true }).click();

  await expect(page.locator(".app-shell")).toHaveClass(/app-shell--signals/);
  // The hermetic API stub can make GlobalRiskStatus hit its ErrorBoundary on
  // mobile. A faithful probe keeps the important cascade assertion: the real
  // component sets display:flex inline, so the scoped rule must beat it.
  const riskDisplay = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "global-risk-status";
    probe.style.display = "flex";
    document.querySelector(".app-shell")!.prepend(probe);
    return getComputedStyle(probe).display;
  });
  expect(riskDisplay).toBe("none");
  await expect(page.locator('[role="tablist"][aria-label="Signal views"]')).toHaveCSS("display", "none");
  await expect(page.locator(".signal-feed-context")).toHaveCount(2);
  for (const context of await page.locator(".signal-feed-context").all()) {
    await expect(context).toHaveCSS("display", "none");
  }

  // The information that belongs to this destination remains first-class.
  await expect(page.locator(".asset-toggle")).toBeVisible();
  await expect(page.getByText("Equity Signals", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "RUN SCAN" })).toBeVisible();
});

test("desktop Signals retains its complete context", async ({ page }) => {
  await stubBackend(page, SIGNED_IN);
  await stubSignalContext(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/terminal/equity");

  await expect(page.getByRole("region", { name: "Capital-at-risk status" })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Signal views" })).toBeVisible();
  await expect(page.locator(".signal-feed-context").first()).toBeVisible();
});
