/**
 * Setup Readiness lives on the Signals page. It used to sit on the chart page,
 * which is now just the chart; this checks it is reachable, deep-linkable and still
 * explains why a setup is blocked, in a real browser.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const row = (symbol: string, status: string, reasons: string[]) => ({
  symbol, bias: "bearish", confirmation_score: 57, structure_state: "range_bound",
  timeframe_alignment: "mixed", macro_risk: "elevated", status, blocked_reasons: reasons,
  unblock_condition: reasons.length ? "Daily closes back above the 20-day average" : null,
  modes_allowed: [], strategy_fit: [],
});

async function open(page: Page, url: string, width = 1280, scanner: "ok" | "fail" = "ok") {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/chart/scanner**", r => scanner === "fail"
    ? r.fulfill({ status: 503, contentType: "application/json", body: "{}" })
    : r.fulfill(reply({ results: [
        row("QQQ", "eligible", []),
        row("NVDA", "blocked", ["Daily timeframe disagrees with the 15m setup", "Macro risk elevated"]),
      ] })));
  await page.setViewportSize({ width, height: 900 });
  await page.goto(url);
}

test("the Signals page has a Setup Readiness tab that opens it and puts it in the URL", async ({ page }) => {
  await open(page, "/terminal/equity");
  const tab = page.getByRole("tab", { name: "Setup Readiness" });
  await expect(tab).toBeVisible();
  await tab.click();
  await expect(page).toHaveURL(/\/terminal\/strat\/readiness$/);
  await expect(page.getByRole("tab", { name: "Setup Readiness" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('button[aria-controls="setup-detail-QQQ"]')).toBeVisible();
});

test("the URL opens the readiness tab directly, and Live Signals is still the default", async ({ page }) => {
  await open(page, "/terminal/strat/readiness");
  await expect(page.getByRole("tab", { name: "Setup Readiness" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(/Blocked: Daily timeframe disagrees/)).toBeVisible();

  await page.goto("/terminal/equity");
  await expect(page.getByRole("tab", { name: "Live Signals" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Setup Readiness" })).toHaveAttribute("aria-selected", "false");
});

test("a blocked setup says why on its row and expands by keyboard", async ({ page }) => {
  await open(page, "/terminal/strat/readiness");
  const nvda = page.locator('button[aria-controls="setup-detail-NVDA"]');
  await expect(nvda).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText(/Blocked:/)).toHaveCount(1);          // QQQ is eligible: no reason line
  await nvda.focus();
  await page.keyboard.press("Enter");
  await expect(nvda).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(/Macro risk elevated/)).toBeVisible();
  await expect(page.getByText(/Daily closes back above/)).toBeVisible();
});

test("a failed scan says unavailable, never an empty list", async ({ page }) => {
  await open(page, "/terminal/strat/readiness", 1280, "fail");
  await expect(page.getByText("Setup readiness unavailable. Rescan to retry.")).toBeVisible();
  await expect(page.getByText("No results.")).toHaveCount(0);
});

for (const width of [390, 1280]) {
  test(`readiness fits at ${width}px`, async ({ page }) => {
    await open(page, "/terminal/strat/readiness", width);
    await expect(page.locator('button[aria-controls="setup-detail-QQQ"]')).toBeVisible();
    await expectNoHorizontalOverflow(page, width);
  });
}
