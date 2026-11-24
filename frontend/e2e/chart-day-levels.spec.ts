/**
 * Day levels on the Chart page: prior day high/low/close, the opening range and session VWAP, drawn
 * as lines with a one-line readout. Off by default. In a real browser because the readout's layout on
 * a phone and the saved choice are what jsdom cannot show. It describes the day; it is not a signal.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

/** 15-minute regular-hours bars for two New York sessions (November, so UTC-5). */
function session(date: string, base: number, until = 15 * 60 + 45) {
  const out = [];
  for (let m = 9 * 60 + 30; m <= until; m += 15) {
    const t = Date.parse(`${date}T00:00:00Z`) + (Math.floor(m / 60) + 5) * 3600e3 + (m % 60) * 60e3;
    const wob = Math.sin(m / 40);
    out.push({ timestamp: new Date(t).toISOString(), open: base + wob, high: base + wob + 0.6, low: base + wob - 0.6, close: base + wob + 0.2, volume: 100000 + m });
  }
  return out;
}
const bars = [...session("2026-11-16", 340), ...session("2026-11-17", 342, 11 * 60)];

const toggle = (page: Page) => page.getByRole("button", { name: "Day levels", exact: true });
const readout = (page: Page) => page.getByLabel("Day levels", { exact: true });

async function open(page: Page, width = 1440, height = 900, keepStorage = false) {
  await stubBackend(page);
  await page.route("**/api/**", (r) => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/chart/**", (r) => r.fulfill(reply({ bars, range: "5d", range_clamped: false, period: "5d" })));
  await page.route("**/api/market/snapshot/**", (r) => r.fulfill(reply({ last_close: 342.36, prev_close: 343, change_pct: -0.52 })));
  if (!keepStorage) await page.addInitScript(() => { try { if (!sessionStorage.getItem("c")) { localStorage.clear(); sessionStorage.setItem("c", "1"); } } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
}

test("desktop: off by default; turning it on shows the prior day, opening range and VWAP in words", async ({ page }) => {
  await open(page);
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "false");
  await expect(readout(page)).toHaveCount(0);
  await toggle(page).click();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "true");
  await expect(readout(page)).toContainText("prior day H");
  await expect(readout(page)).toContainText("opening range");
  await expect(readout(page)).toContainText("VWAP");
  await expect(readout(page)).not.toContainText(/\b(buy|sell|signal)\b/i);
  await expectNoHorizontalOverflow(page, 1440);
});

test("the choice is remembered after a reload", async ({ page }) => {
  await open(page);
  await toggle(page).click();
  await expect(readout(page)).toBeVisible();
  await page.reload();
  await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
  await expect(toggle(page)).toHaveAttribute("aria-pressed", "true");
  await expect(readout(page)).toBeVisible();
});

test("turning it on does not resize the chart", async ({ page }) => {
  await open(page);
  const before = (await page.locator(".ws-chart__canvas").boundingBox())!;
  await toggle(page).click();
  await expect(readout(page)).toBeVisible();
  const after = (await page.locator(".ws-chart__canvas").boundingBox())!;
  expect(Math.abs(after.height - before.height)).toBeLessThanOrEqual(1);
});

test("on daily bars it says why nothing is drawn, instead of drawing anything", async ({ page }) => {
  await open(page);
  await toggle(page).click();
  await page.getByRole("group", { name: "Chart timeframe" }).getByRole("button", { name: "1 day", exact: true }).click();
  await expect(readout(page)).toContainText("1, 5, 15 or 30 minute bars");
});

for (const w of [320, 390]) {
  test(`phone ${w}px: a real touch target, one truncated line, nothing overflows, the chart keeps most of its height`, async ({ page }) => {
    await open(page, w, 844);
    const btn = (await toggle(page).boundingBox())!;
    expect(btn.height).toBeGreaterThanOrEqual(44);
    const before = (await page.locator(".ws-chart__canvas").boundingBox())!;
    await toggle(page).click();
    await expect(readout(page)).toBeVisible();
    const line = (await readout(page).boundingBox())!;
    expect(line.height, "one line, never several").toBeLessThanOrEqual(20);
    const after = (await page.locator(".ws-chart__canvas").boundingBox())!;
    expect(before.height - after.height, "on a phone the readout costs the chart one line, no more").toBeLessThanOrEqual(30);
    expect(after.height).toBeGreaterThanOrEqual(300);
    await expectNoHorizontalOverflow(page, w);
  });
}
