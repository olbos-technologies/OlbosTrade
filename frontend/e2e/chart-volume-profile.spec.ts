/**
 * The volume profile on the Chart page (Markets > Chart). It was only on the Pro Workspace.
 * Measured in a real browser: the toggle's size, the saved choice and the readout are layout and
 * storage, which jsdom does not do.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
// 15-minute bars from a real start time, so every timestamp is a valid date.
const START = Date.parse("2026-08-14T13:30:00Z");
const bars = Array.from({ length: 80 }, (_, i) => ({
  timestamp: new Date(START + i * 15 * 60_000).toISOString(),
  open: 340 + Math.sin(i / 5) * 3, high: 343 + Math.sin(i / 5) * 3, low: 338 + Math.sin(i / 5) * 3,
  close: 341 + Math.sin(i / 4) * 3, volume: 1_000_000 + i * 10_000,
}));

const toggle = (page: Page) => page.getByRole("button", { name: "Volume profile", exact: true });
const readout = (page: Page) => page.getByLabel("Volume profile", { exact: true });

async function open(page: Page, width: number, height: number, keepStorage = false) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/chart/**", r => r.fulfill(reply({ bars, range: "5d", range_clamped: false, period: "5d" })));
  await page.route("**/api/market/snapshot/**", r => r.fulfill(reply({ last_close: 342.36, prev_close: 343, change_pct: -0.52 })));
  await page.route("**/api/paper-trade/positions", r => r.fulfill(reply({ positions: [] })));
  if (!keepStorage) await page.addInitScript(() => { try { if (!sessionStorage.getItem("cleared")) { localStorage.clear(); sessionStorage.setItem("cleared", "1"); } } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.getByRole("heading", { name: "Market workspace" })).toBeVisible();
  await expect(page.locator(".dock-chart .candlestick-chart")).toBeVisible();
}

test("desktop: the profile is on by default, with its readout, and the button turns it off and on", async ({ page }) => {
  await open(page, 1440, 900);
  await expect(toggle(page)).toBeVisible();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "true");
  await expect(readout(page)).toContainText("POC");
  await toggle(page).click();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "false");
  await expect(readout(page)).toHaveCount(0);
  await toggle(page).click();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "true");
  await expect(readout(page)).toContainText("POC");
});

test("desktop: the choice to turn it off is remembered after a reload", async ({ page }) => {
  await open(page, 1440, 900);
  await toggle(page).click();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "false");
  await page.reload();
  await expect(page.locator(".dock-chart .candlestick-chart")).toBeVisible();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "false");
  await expect(readout(page)).toHaveCount(0);
});

test("desktop: the toggle sits in the chart toolbar, right of the interval strip, and the layout is unchanged", async ({ page }) => {
  await open(page, 1440, 900);
  const bar = (await page.locator(".chart-toolbar").boundingBox())!;
  const btn = (await toggle(page).boundingBox())!;
  const strip = (await page.locator(".chart-toolbar .interval-strip").boundingBox())!;
  expect(btn.x, "right of the intervals").toBeGreaterThan(strip.x + strip.width - 1);
  expect(btn.x + btn.width, "inside the toolbar").toBeLessThanOrEqual(bar.x + bar.width + 1);
  expect(Math.abs(bar.height - 33), "the toolbar did not grow").toBeLessThan(8);
  const left = (await page.getByLabel("Open positions panel").boundingBox())!;
  const chart = (await page.locator(".dock-chart").boundingBox())!;
  const right = (await page.getByLabel("Watchlist panel").boundingBox())!;
  expect(left.x + left.width).toBeLessThanOrEqual(chart.x + 1);
  expect(right.x).toBeGreaterThanOrEqual(chart.x + chart.width - 1);
  await expectNoHorizontalOverflow(page, 1440);
});

test("phone: the toggle is a real touch target, nothing overflows, and the chart still has its height", async ({ page }) => {
  await open(page, 390, 844);
  const btn = (await toggle(page).boundingBox())!;
  expect(btn.height, "touch target").toBeGreaterThanOrEqual(43);
  expect(btn.width).toBeGreaterThanOrEqual(43);
  const vw = 390;
  // the toolbar scrolls sideways by design on a phone; the toggle must at least start on screen
  expect(btn.x).toBeGreaterThanOrEqual(0);
  await expectNoHorizontalOverflow(page, 390);
  const chart = (await page.locator(".dock-chart").boundingBox())!;
  expect(chart.height, "chart is still tall").toBeGreaterThan(300);
  expect(chart.width).toBeLessThanOrEqual(vw);
  await toggle(page).scrollIntoViewIfNeeded();
  await toggle(page).click();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "false");
});
