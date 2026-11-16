/**
 * The multi-timeframe page (Markets > Multi-timeframe): one symbol on two to four charts at different bar
 * intervals. Measured in a real browser: the grid, the phone stack, and that each chart really draws.
 */
import { expect, test, type Page } from "@playwright/test";
import { expectNoHorizontalOverflow, stubBackend } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const STEP_S: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800, "1M": 2592000 };

/** 120 bars at the interval's own spacing. The daily chart falls, every other one rises, so the timeframes disagree. */
function series(interval: string) {
  const falling = interval === "1d";
  const start = Date.UTC(2026, 3, 1, 13, 30);
  return Array.from({ length: 120 }, (_, i) => {
    const c = falling ? 220 - i * 0.5 + Math.sin(i / 3) : 100 + i * 0.4 + Math.sin(i / 3);
    return { timestamp: new Date(start + i * (STEP_S[interval] ?? 900) * 1000).toISOString(), open: c - 0.3, high: c + 1, low: c - 1, close: c, volume: 1_000_000 + i * 1000 };
  });
}

interface Opts { fail?: string[] }
async function mock(page: Page, requests: Array<{ symbol: string; timeframe: string; range: string }>, opts: Opts = {}) {
  await stubBackend(page);
  await page.route("**/api/equity/chart/**", (r) => {
    const u = new URL(r.request().url());
    const timeframe = u.searchParams.get("timeframe") ?? "";
    requests.push({ symbol: u.pathname.split("/").pop() ?? "", timeframe, range: u.searchParams.get("range") ?? "" });
    if (opts.fail?.includes(timeframe)) return r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "feed down" }) });
    return r.fulfill(reply({ bars: series(timeframe), range: u.searchParams.get("range"), range_clamped: false, period: "1y" }));
  });
  await page.route("**/api/market/snapshot/**", (r) => r.fulfill(reply({ last_close: 187.25, change_pct: 0.84 })));
}

async function open(page: Page, width: number, height: number, opts: Opts = {}, path = "/terminal/markets/multi") {
  const requests: Array<{ symbol: string; timeframe: string; range: string }> = [];
  await mock(page, requests, opts);
  await page.addInitScript(() => { try { if (!sessionStorage.getItem("c")) { localStorage.clear(); sessionStorage.setItem("c", "1"); } } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto(path);
  await expect(page.getByLabel("Multi-timeframe view")).toBeVisible();
  return requests;
}

const tiles = (page: Page) => page.getByRole("region", { name: /^Chart \d/ });
const settled = (page: Page) => expect(page.getByText("Loading the timeframes…")).toHaveCount(0, { timeout: 15000 });
const boxes = async (page: Page) => Promise.all((await tiles(page).all()).map(async (t) => (await t.boundingBox())!));

test("desktop: three charts side by side, each a real chart, with the right requests", async ({ page }) => {
  const requests = await open(page, 1440, 900);
  await settled(page);
  await expect(tiles(page)).toHaveCount(3);
  const b = await boxes(page);
  expect(Math.abs(b[0].y - b[1].y), "same row").toBeLessThan(2);
  expect(Math.abs(b[1].y - b[2].y), "same row").toBeLessThan(2);
  expect(b[1].x).toBeGreaterThan(b[0].x + b[0].width - 2);
  expect(b[2].x).toBeGreaterThan(b[1].x + b[1].width - 2);
  for (const t of b) { expect(t.width, "wide enough to read").toBeGreaterThan(280); expect(t.height, "tall enough to read").toBeGreaterThanOrEqual(340); }
  for (const t of await tiles(page).all()) await expect(t.locator("canvas").first()).toBeVisible();
  expect(requests.map((r) => `${r.timeframe}/${r.range}`).sort()).toEqual(["15m/5d", "1d/1y", "1h/1m"]);
  expect(requests.every((r) => r.symbol === "AAPL")).toBe(true);
  await expectNoHorizontalOverflow(page, 1440);
});

test("the alignment line says in words that the timeframes disagree, and each chart shows arrows and words", async ({ page }) => {
  await open(page, 1440, 900);
  await settled(page);
  await expect(page.getByRole("status").filter({ hasText: "Mixed: above the 50-bar average on 2 of 3, below on 1" })).toBeVisible();
  await expect(tiles(page).nth(0).getByLabel("Price is below its 50-bar average")).toHaveText("MA50 ▼");
  await expect(tiles(page).nth(1).getByLabel("Price is above its 50-bar average")).toHaveText("MA50 ▲");
  await expect(page.getByText(/not a signal/)).toBeVisible();
});

test("four charts make a two-by-two grid", async ({ page }) => {
  await open(page, 1440, 900);
  await settled(page);
  await page.getByLabel("Number of charts").selectOption("4");
  await expect(tiles(page)).toHaveCount(4);
  await expect(tiles(page).nth(3).locator("canvas").first()).toBeVisible();
  const b = await boxes(page);
  expect(Math.abs(b[0].y - b[1].y)).toBeLessThan(2);
  expect(Math.abs(b[2].y - b[3].y)).toBeLessThan(2);
  expect(b[2].y, "second row below the first").toBeGreaterThan(b[0].y + b[0].height - 2);
  expect(Math.abs(b[0].x - b[2].x)).toBeLessThan(2);
  await expectNoHorizontalOverflow(page, 1440);
});

test("a laptop-size window uses two columns", async ({ page }) => {
  await open(page, 1100, 800);
  await settled(page);
  const b = await boxes(page);
  expect(Math.abs(b[0].y - b[1].y), "first two share a row").toBeLessThan(2);
  expect(b[2].y, "the third wraps to a second row").toBeGreaterThan(b[0].y + b[0].height - 2);
  await expectNoHorizontalOverflow(page, 1100);
});

test("changing one chart's interval asks only for that one, draws it, and is remembered after a reload", async ({ page }) => {
  const requests = await open(page, 1440, 900);
  await settled(page);
  requests.length = 0;
  await page.getByLabel("Bar interval for chart 2").selectOption("4h");
  await expect(tiles(page).nth(1)).toHaveAccessibleName("Chart 2: 4 hours");
  await expect(tiles(page).nth(1).locator("canvas").first()).toBeVisible();
  expect(requests.map((r) => `${r.timeframe}/${r.range}`)).toEqual(["4h/6m"]);
  await page.reload();
  await expect(page.getByLabel("Multi-timeframe view")).toBeVisible();
  await expect(tiles(page).nth(1)).toHaveAccessibleName("Chart 2: 4 hours");
});

test("a symbol in the address is used for every chart, and a new symbol reloads them all", async ({ page }) => {
  const requests = await open(page, 1440, 900, {}, "/terminal/markets/multi?symbol=MSFT");
  await settled(page);
  expect(requests.length).toBe(3);
  expect(requests.every((r) => r.symbol === "MSFT")).toBe(true);
  requests.length = 0;
  await page.getByPlaceholder("Symbol").fill("nvda");
  await page.getByLabel("Multi-timeframe view").getByRole("button", { name: "Go", exact: true }).click();
  await expect.poll(() => requests.length).toBe(3);
  expect(requests.every((r) => r.symbol === "NVDA")).toBe(true);
});

test("a chart whose data fails says so for itself and the other two still draw", async ({ page }) => {
  await open(page, 1440, 900, { fail: ["1h"] });
  await settled(page);
  await expect(tiles(page).nth(1).getByRole("status")).toContainText("Chart data unavailable for this timeframe");
  await expect(tiles(page).nth(1).locator("canvas")).toHaveCount(0);
  await expect(tiles(page).nth(0).locator("canvas").first()).toBeVisible();
  await expect(tiles(page).nth(2).locator("canvas").first()).toBeVisible();
  await expect(page.getByText(/on the 2 of 3 timeframes with enough bars|Mixed: .*\(1 without enough bars\)|of 2/)).toBeVisible();
});

test("one indicator choice applies to every chart", async ({ page }) => {
  await open(page, 1440, 900);
  await settled(page);
  await page.getByRole("button", { name: "Indicators", exact: true }).click();
  await page.getByRole("dialog").getByRole("checkbox", { name: "RSI", exact: true }).check();
  await page.keyboard.press("Escape");
  for (const t of await tiles(page).all()) await expect(t.getByLabel("Indicator readout")).toContainText("RSI");
});

test("it is in the Markets menu and a deep link opens it", async ({ page }) => {
  const requests: Array<{ symbol: string; timeframe: string; range: string }> = [];
  await mock(page, requests);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/terminal/markets/chart");
  await expect(page.getByRole("heading", { name: "Market workspace" })).toBeVisible();
  await page.getByRole("button", { name: "Multi-timeframe", exact: true }).click();
  await expect(page).toHaveURL(/\/terminal\/markets\/multi$/);
  await expect(page.getByLabel("Multi-timeframe view")).toBeVisible();
  await page.reload();                                                  // and the address on its own opens it
  await expect(page.getByLabel("Multi-timeframe view")).toBeVisible();
});

test("phone: the charts stack one per row, each tall enough to read, nothing overflows, the page scrolls", async ({ page }) => {
  await open(page, 390, 844);
  await settled(page);
  const b = await boxes(page);
  for (let i = 0; i < b.length; i++) {
    expect(b[i].width, `chart ${i + 1} uses the width`).toBeGreaterThan(330);
    expect(b[i].width).toBeLessThanOrEqual(390);
    expect(b[i].height, `chart ${i + 1} is tall enough`).toBeGreaterThanOrEqual(340);
    if (i) expect(b[i].y, "stacked below the previous one").toBeGreaterThanOrEqual(b[i - 1].y + b[i - 1].height - 2);
  }
  await expectNoHorizontalOverflow(page, 390);
  for (const sel of [page.getByLabel("Bar interval for chart 1"), page.getByLabel("Number of charts"), page.getByPlaceholder("Symbol")]) {
    expect((await sel.boundingBox())!.height, "touch target").toBeGreaterThanOrEqual(43);
  }
  await tiles(page).nth(2).scrollIntoViewIfNeeded();
  await expect(tiles(page).nth(2).locator("canvas").first()).toBeVisible();
});

test("phone: it can be reached from the menu, and opening it closes the menu", async ({ page }) => {
  const requests: Array<{ symbol: string; timeframe: string; range: string }> = [];
  await mock(page, requests);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/terminal/markets/chart");
  await expect(page.getByRole("heading", { name: "Market workspace" })).toBeVisible();
  await page.getByRole("button", { name: /toggle navigation/i }).click();
  const link = page.getByText(/^Multi-timeframe$/i).first();
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/terminal\/markets\/multi$/);
  await expect(page.getByLabel("Multi-timeframe view")).toBeVisible();
});
