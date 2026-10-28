import { test, expect } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

async function home(page: import("@playwright/test").Page, width: number) {
  await stubBackend(page);
  const response = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/dashboard/summary", r => r.fulfill(response({ total_equity: 261074.34, total_pnl: 100, day_pnl: -25 })));
  await page.route("**/api/market/broker", r => r.fulfill(response({ broker: "ibkr", paper_mode: true, status: "connected" })));
  await page.route("**/api/trade-desk/execution-mode", r => r.fulfill(response({ mode: "manual", persistence: "confirmed" })));
  await page.route("**/api/trade-desk/kill-switch", r => r.fulfill(response({ engaged: false })));
  await page.route("**/api/trade-desk/pending", r => r.fulfill(response({ pending: [] })));
  await page.route("**/api/paper-trade/positions", r => r.fulfill(response({ positions: [] })));
  await page.route("**/api/portfolio/open-orders", r => r.fulfill(response({ available: true, source: "cache", unprotected_is_reliable: false, positions_without_stop: [] })));
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/terminal/dashboard");
  await expect(page.getByTestId("mobile-home")).toBeVisible();
  await expect(page.getByText("$261,074.34", { exact: true })).toBeVisible();
}

for (const width of [320, 390, 768]) {
  test(`mobile home preserves content and touch targets at ${width}px`, async ({ page }) => {
    await home(page, width);
    await expectNoHorizontalOverflow(page, width);
    const root = page.getByTestId("mobile-home");
    await root.getByText("System details", { exact: true }).click();
    await expect(root.getByRole("button", { name: /View connection details/ })).toBeVisible();
    const sizes = await root.getByRole("button").evaluateAll(buttons =>
      buttons.map(el => ({ text: el.textContent, height: el.getBoundingClientRect().height })));
    expect(sizes.length).toBeGreaterThan(4);
    for (const b of sizes) expect(b.height, b.text || "button").toBeGreaterThanOrEqual(44);
    await expect(root.getByText("Stop coverage unverified", { exact: true })).toBeVisible();
    const emergency = root.getByText("Emergency controls", { exact: true });
    await emergency.click();
    await expect(root.getByRole("button", { name: "Engage kill switch", exact: true })).toBeVisible();
    // Opening the existing dialog is safe: never click its submission button.
    await root.getByRole("button", { name: "Engage kill switch", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("flatten positions");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await root.getByRole("button", { name: /Review signal queue/ }).click();
    await expect(page).toHaveURL(/trade\/copilot/);
  });
}

test("mobile failed reads stay unavailable instead of showing zeroes", async ({ page }) => {
  await home(page, 390);
  await page.route("**/api/dashboard/summary", r => r.fulfill({ status: 503, body: "{}" }));
  await page.route("**/api/trade-desk/pending", r => r.fulfill({ status: 503, body: "{}" }));
  await page.getByRole("button", { name: "Refresh home" }).click();
  await expect(page.getByText("Account data unavailable. Refresh to retry.")).toBeVisible();
  await expect(page.getByText("Signal queue unavailable.", { exact: true })).toBeVisible();
  await expect(page.getByTestId("mobile-home").getByText("$261,074.34", { exact: true })).toHaveCount(0);
});

test("desktop dashboard keeps the existing executive summary", async ({ page }) => {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? {status:200,contentType:"application/json",body:JSON.stringify({auth_enabled:false,authenticated:false,user:null})}
    : {status:503,contentType:"application/json",body:"{}"}));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/terminal/dashboard");
  await expect(page.getByTestId("mobile-home")).toBeVisible();
  await page.getByText("Advanced dashboard and analytics", { exact: true }).click();
  await expect(page.getByText("EXECUTIVE SUMMARY", { exact: true })).toBeVisible();
});

