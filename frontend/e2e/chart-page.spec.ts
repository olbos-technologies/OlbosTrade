/**
 * The chart page: one instrument bar; Positions on the left, the chart in the
 * middle (interval strip above it, range bar below, right-aligned) and the
 * Watchlist on the right. Measured in a real browser: layout is not visible to jsdom.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const bars = Array.from({ length: 60 }, (_, i) => ({
  timestamp: `2026-08-14T${String(9 + Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}:00-04:00`,
  open: 340 + Math.sin(i / 5) * 3, high: 343 + Math.sin(i / 5) * 3, low: 338 + Math.sin(i / 5) * 3,
  close: 341 + Math.sin(i / 4) * 3, volume: 1_000_000 + i * 10_000,
}));
const requests: string[] = [];
const lastRequest = () => requests[requests.length - 1];

async function open(page: Page, width = 1440, height = 900, signalResponse?: unknown) {
  requests.length = 0;
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/chart/**", r => {
    requests.push(r.request().url());
    const u = new URL(r.request().url());
    // the real endpoint clamps a wide range at 1-minute bars to the last 7 days
    const clamped = u.searchParams.get("timeframe") === "1m" && u.searchParams.get("range") !== "1d" && u.searchParams.get("range") !== "5d";
    return r.fulfill(reply({ bars, range: u.searchParams.get("range"), range_clamped: clamped, period: clamped ? "7d" : "5d" }));
  });
  await page.route("**/api/market/snapshot/**", r => {
    const t = r.request().url().split("/").pop()!.split("?")[0];
    return r.fulfill(reply({ last_close: t === "AAPL" ? 342.36 : 100.5, prev_close: 343, change_pct: t === "AAPL" ? -0.52 : 1.2 }));
  });
  await page.route("**/api/paper-trade/positions", r => r.fulfill(reply({ positions: [
    { symbol: "AMZN", quantity: 7, avg_cost: 262.35, unrealized_pnl: -21, hold_days: 2 },
    { symbol: "FER", quantity: -337, avg_cost: 58.57, unrealized_pnl: -46, hold_days: 2 },
  ] })));
  if (signalResponse !== undefined) {
    await page.route("**/api/equity/signals?*", r => r.fulfill(reply(signalResponse)));
  }
  await page.addInitScript(() => { try { localStorage.clear(); } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.getByRole("heading", { name: "Market workspace" })).toBeVisible();
}

for (const width of [1440, 390]) {
  test(`a failed signal read is not presented as no signal at ${width}px`, async ({ page }) => {
    await open(page, width, 900);
    await expect(page.getByLabel("Selected instrument", { exact: true })).toContainText("Signals unavailable");
    await expect(page.getByLabel("Selected instrument", { exact: true })).not.toContainText("No signal");
    await page.getByRole("button", { name: "Signal detail" }).click();
    await expect(page.getByText("Signals are unavailable. The signal feed could not be verified.")).toBeVisible();
    await expectNoHorizontalOverflow(page, width);
  });
}

test("a successful empty signal read is an empty state", async ({ page }) => {
  await open(page, 1440, 900, { signals: [] });
  await expect(page.getByLabel("Selected instrument", { exact: true })).toContainText("No signal");
  await expect(page.getByLabel("Selected instrument", { exact: true })).not.toContainText("Signals unavailable");
});

test("positions on the left, the chart in the middle, the watchlist on the right", async ({ page }) => {
  await open(page);
  const left = (await page.getByLabel("Open positions panel").boundingBox())!;
  const chart = (await page.locator(".dock-chart").boundingBox())!;
  const right = (await page.getByLabel("Watchlist panel").boundingBox())!;
  expect(left.x + left.width, "positions end where the chart starts").toBeLessThanOrEqual(chart.x + 1);
  expect(right.x, "watchlist starts where the chart ends").toBeGreaterThanOrEqual(chart.x + chart.width - 1);
  expect(chart.width, "the chart is the widest column").toBeGreaterThan(left.width * 2);
  expect(chart.width).toBeGreaterThan(right.width * 2);
  expect(left.width, "panel wide enough to read").toBeGreaterThan(200);
  expect(chart.height, "chart is tall").toBeGreaterThan(380);
  expect(chart.y, "chart starts in the top half").toBeLessThan(450);
  await expect(page.getByLabel("Open positions panel")).toContainText("FER");
  await expect(page.getByLabel("Open positions panel")).toContainText("−$46.00");
  await expect(page.getByLabel("Watchlist panel")).toContainText("342.36");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.locator(".workstation-right, .workstation-watchlist, .workstation-top-strip, .chart-quote")).toHaveCount(0);
  await expectNoHorizontalOverflow(page, 1440);
});

test("the interval strip is at the top-left of the chart and the range bar at the bottom-right", async ({ page }) => {
  await open(page);
  const chart = (await page.locator(".dock-chart").boundingBox())!;
  const strip = (await page.getByRole("group", { name: "Chart timeframe" }).boundingBox())!;
  const range = (await page.getByRole("group", { name: "Chart range" }).boundingBox())!;
  expect(strip.y + strip.height, "interval strip is above the chart").toBeLessThanOrEqual(chart.y + 1);
  expect(strip.x, "interval strip hugs the left edge").toBeLessThan(chart.x + 60);
  expect(range.y, "range bar is below the chart").toBeGreaterThanOrEqual(chart.y + chart.height - 1);
  expect(range.x + range.width, "range bar hugs the right edge").toBeGreaterThan(chart.x + chart.width - 60);
  await expect(page.getByRole("group", { name: "Chart timeframe" }).getByRole("button")).toHaveCount(9);
  await expect(page.getByRole("group", { name: "Chart range" }).getByRole("button")).toHaveCount(9);
  await expect(page.getByLabel("Selected instrument").getByRole("group", { name: "Chart timeframe" })).toHaveCount(0);
});

test("picking an interval keeps the range; picking a range picks a fitting interval", async ({ page }) => {
  await open(page);
  const strip = page.getByRole("group", { name: "Chart timeframe" });
  const ranges = page.getByRole("group", { name: "Chart range" });
  await expect(strip.getByRole("button", { name: "15 minutes" })).toHaveAttribute("aria-pressed", "true");
  await expect(ranges.getByRole("button", { name: "5 days" })).toHaveAttribute("aria-pressed", "true");
  await strip.getByRole("button", { name: "4 hours" }).click();
  await expect(strip.getByRole("button", { name: "4 hours" })).toHaveAttribute("aria-pressed", "true");
  await expect(ranges.getByRole("button", { name: "5 days" })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => lastRequest()).toContain("timeframe=4h");
  expect(lastRequest()).toContain("range=5d");
  await ranges.getByRole("button", { name: "1 year" }).click();
  await expect(strip.getByRole("button", { name: "1 day" })).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => lastRequest()).toContain("range=1y");
  expect(lastRequest()).toContain("timeframe=1d");
});

test("a range the data cannot cover at that interval says so, and the note clears", async ({ page }) => {
  await open(page);
  const strip = page.getByRole("group", { name: "Chart timeframe" });
  await page.getByRole("group", { name: "Chart range" }).getByRole("button", { name: "1 year" }).click();
  await strip.getByRole("button", { name: "1 minute" }).click();
  await expect(page.locator(".range-bar__note")).toContainText("Showing the last 7 days: 1 minute bars don't go back further.");
  await strip.getByRole("button", { name: "1 day" }).click();
  await expect(page.locator(".range-bar__note")).toHaveCount(0);
});

test("the instrument bar carries the price, the last bar and the controls once", async ({ page }) => {
  await open(page);
  const bar = page.getByLabel("Selected instrument", { exact: true });
  await expect(bar.getByLabel("Selected instrument quote")).toContainText("342.36");
  await expect(bar.getByLabel("Selected instrument quote")).toContainText("−0.52%");
  await expect(bar.getByLabel("Last bar")).toContainText("Vol");
  await expect(bar).toContainText("Last close snapshot");
  await expect(page.getByLabel("Indicator readout")).toContainText("MA5");
  await expect(page.getByLabel("Chart legend")).toHaveCount(0);
  expect(await bar.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
});

test("Signal detail is a flat outline, not a raised key", async ({ page }) => {
  await open(page);
  const button = page.getByRole("button", { name: "Signal detail" });
  await expect(button).toHaveClass(/btn-t--flat/);
  const style = await button.evaluate(el => { const c = getComputedStyle(el); return { shadow: c.boxShadow, image: c.backgroundImage }; });
  expect(style.shadow).toBe("none");
  expect(style.image).toBe("none");
});

test("any symbol can be typed, pinned to the picker, and removed again", async ({ page }) => {
  await open(page);
  const input = page.getByLabel("Go to symbol");
  await input.fill("amd");
  await input.press("Enter");
  await expect(page.locator(".instrument-bar .chart-symbol-picker button[aria-pressed='true']")).toHaveText("AMD");
  await page.locator(".instrument-bar").getByRole("button", { name: "Remove AMD from Market Watch" }).click();
  await expect(page.locator(".instrument-bar .chart-symbol-picker button", { hasText: "AMD" })).toHaveCount(0);
  await expect(page.locator(".instrument-bar .chart-symbol-picker button[aria-pressed='true']")).not.toHaveText("AMD");
  await input.fill("not a symbol!");
  await input.press("Enter");
  await expect(input).toHaveAttribute("aria-invalid", "true");
});

test("choosing from the watchlist panel changes the instrument", async ({ page }) => {
  await open(page);
  await page.getByLabel("Watchlist panel").getByRole("button", { name: "MSFT", exact: true }).click();
  await expect(page.getByLabel("Selected instrument quote")).toContainText("100.50");
});

test("choosing a picker symbol changes the instrument", async ({ page }) => {
  await open(page);
  await page.locator(".instrument-bar .chart-symbol-picker button", { hasText: "MSFT" }).click();
  await expect(page.getByLabel("Selected instrument quote")).toContainText("100.50");
});

test("a laptop-size window keeps the chart and both panels usable", async ({ page }) => {
  await open(page, 1280, 720);
  const plot = await page.getByLabel("Price chart", { exact: true }).boundingBox();
  expect(plot!.height, "price chart keeps a usable height").toBeGreaterThan(250);
  expect((await page.getByLabel("Watchlist panel").boundingBox())!.width).toBeGreaterThan(180);
  expect((await page.getByLabel("Open positions panel").boundingBox())!.width).toBeGreaterThan(180);
  await expectNoHorizontalOverflow(page, 1280);
});

test("on a phone the chart comes first with the controls above and below it, then the panels", async ({ page }) => {
  await open(page, 390, 844);
  const chart = (await page.locator(".dock-chart").boundingBox())!;
  const strip = (await page.getByRole("group", { name: "Chart timeframe" }).boundingBox())!;
  const range = (await page.getByRole("group", { name: "Chart range" }).boundingBox())!;
  const positions = (await page.getByLabel("Open positions panel").boundingBox())!;
  expect(strip.y + strip.height).toBeLessThanOrEqual(chart.y + 1);
  expect(range.y).toBeGreaterThanOrEqual(chart.y + chart.height - 1);
  expect(positions.y, "panels are below the chart").toBeGreaterThan(range.y);
  expect(chart.y + 300, "at least 300px of the chart is visible above the bottom nav").toBeLessThanOrEqual(844 - 56);
  for (const b of await page.getByRole("group", { name: "Chart timeframe" }).getByRole("button").all()) {
    expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await expectNoHorizontalOverflow(page, 390);
});
