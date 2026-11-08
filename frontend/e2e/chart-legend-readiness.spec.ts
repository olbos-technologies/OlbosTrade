/**
 * Chart legend collapsed by default, and Setup Readiness showing why a setup is
 * blocked on the row itself - in a real browser, since jsdom does no layout and
 * cannot tell whether the key still covers the price line.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const bars = Array.from({ length: 40 }, (_, i) => ({
  timestamp: `2026-08-14T${String(9 + Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}:00-04:00`,
  open: 100 + i * 0.1, high: 102 + i * 0.1, low: 99 + i * 0.1, close: 101 + i * 0.1, volume: 1000 + i,
}));
const row = (symbol: string, status: string, reasons: string[]) => ({
  symbol, bias: "bearish", confirmation_score: 57, structure_state: "range_bound",
  timeframe_alignment: "mixed", macro_risk: "elevated", status, blocked_reasons: reasons,
  unblock_condition: reasons.length ? "Daily closes back above the 20-day average" : null,
  modes_allowed: [], strategy_fit: [],
});

async function open(page: Page, width: number) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/chart/**", r => r.fulfill(reply({ bars })));
  await page.route("**/api/chart/scanner**", r => r.fulfill(reply({ results: [
    row("QQQ", "eligible", []),
    row("NVDA", "blocked", ["Daily timeframe disagrees with the 15m setup", "Macro risk elevated"]),
  ] })));
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

test("a blocked setup says why on its row, and the row expands by keyboard", async ({ page }) => {
  await open(page, 1440);
  const nvda = page.locator('button[aria-controls="setup-detail-NVDA"]');
  await expect(nvda).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText(/Blocked: Daily timeframe disagrees/)).toBeVisible();
  await expect(page.getByText(/Blocked:/)).toHaveCount(1);          // QQQ is eligible: no line
  await nvda.focus();
  await page.keyboard.press("Enter");
  await expect(nvda).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(/Daily closes back above/)).toBeVisible();
  await expectNoHorizontalOverflow(page, 1440);
});
