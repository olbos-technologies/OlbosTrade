/**
 * The desktop chart layout: one instrument bar, the chart high on the screen at
 * full width, and the research in a tabbed dock. Measured in a real browser,
 * because "the chart starts below the middle of the screen" is a layout fact
 * that jsdom cannot see.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const bars = Array.from({ length: 60 }, (_, i) => ({
  timestamp: `2026-08-14T${String(9 + Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}:00-04:00`,
  open: 340 + Math.sin(i / 5) * 3, high: 343 + Math.sin(i / 5) * 3, low: 338 + Math.sin(i / 5) * 3,
  close: 341 + Math.sin(i / 4) * 3, volume: 1_000_000 + i * 10_000,
}));
const scan = (symbol: string, status: string, reasons: string[]) => ({
  symbol, bias: "bearish", confirmation_score: 57, structure_state: "range_bound",
  timeframe_alignment: "mixed", macro_risk: "elevated", status, blocked_reasons: reasons,
  unblock_condition: reasons.length ? "Daily closes back above the 20-day average" : null,
  modes_allowed: [], strategy_fit: [],
});

async function open(page: Page, width = 1440, height = 900) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/chart/**", r => r.fulfill(reply({ bars })));
  await page.route("**/api/market/snapshot/**", r => {
    const t = r.request().url().split("/").pop()!.split("?")[0];
    return r.fulfill(reply({ last_close: t === "AAPL" ? 342.36 : 100.5, prev_close: 343, change_pct: t === "AAPL" ? -0.52 : 1.2 }));
  });
  await page.route("**/api/chart/scanner**", r => r.fulfill(reply({ results: [
    scan("QQQ", "eligible", []), scan("NVDA", "blocked", ["Daily timeframe disagrees with the 15m setup"]),
  ] })));
  await page.route("**/api/paper-trade/positions", r => r.fulfill(reply({ positions: [
    { symbol: "AMZN", quantity: 7, avg_cost: 262.35, unrealized_pnl: -21, hold_days: 2 },
    { symbol: "FER", quantity: -337, avg_cost: 58.57, unrealized_pnl: -46, hold_days: 2 },
  ] })));
  await page.addInitScript(() => { try { localStorage.clear(); } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.getByRole("heading", { name: "Market workspace" })).toBeVisible();
}

test("the chart sits high on the screen and the dock is on the first screen", async ({ page }) => {
  await open(page);
  const plot = await page.getByLabel("Price chart", { exact: true }).boundingBox();
  const dock = await page.getByLabel("Chart research dock").boundingBox();
  expect(plot!.y, "chart starts in the top third").toBeLessThan(330);
  expect(plot!.width, "chart is not squeezed into a column").toBeGreaterThan(1000);
  expect(dock!.y, "dock header is on the first screen").toBeLessThan(900);
  await expectNoHorizontalOverflow(page, 1440);
});

test("the instrument bar carries the price, the last bar and the controls once", async ({ page }) => {
  await open(page);
  const bar = page.getByLabel("Selected instrument", { exact: true });
  await expect(bar.getByLabel("Selected instrument quote")).toContainText("342.36");
  await expect(bar.getByLabel("Selected instrument quote")).toContainText("−0.52%");
  await expect(bar.getByLabel("Last bar")).toContainText("Vol");
  await expect(bar).toContainText("Last close snapshot");
  await expect(bar.getByRole("button", { name: "Signal detail" })).toBeVisible();
  await expect(page.getByLabel("Indicator readout")).toContainText("MA5");
  await expect(page.getByLabel("Chart legend")).toHaveCount(0);
  // the old duplicate chrome is gone on desktop
  await expect(page.locator(".workstation-top-strip")).toHaveCount(0);
  await expect(page.locator(".chart-quote")).toHaveCount(0);
  expect(await bar.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
});

test("dock tabs switch, keep the research reachable and work from the keyboard", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("tab", { name: /Setup readiness/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(/Blocked: Daily timeframe disagrees/)).toBeVisible();
  await page.getByRole("tab", { name: /^Watchlist/ }).click();
  const row = page.getByRole("table", { name: "Watchlist" });
  await expect(row).toContainText("AAPL");
  await expect(row).toContainText("342.36");
  await page.getByRole("tab", { name: /^Positions/ }).click();
  await expect(page.getByRole("table", { name: "Open positions" })).toContainText("FER");
  await expect(page.getByRole("table", { name: "Open positions" })).toContainText("−$46.00");
  await page.getByRole("tab", { name: /^Safety/ }).click();
  await expect(page.getByLabel("Chart research dock")).toContainText("Reconciliation");
  await page.getByRole("tab", { name: /^Research/ }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^Safety/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Home");
  await expect(page.getByRole("tab", { name: /Setup readiness/ })).toHaveAttribute("aria-selected", "true");
  await expectNoHorizontalOverflow(page, 1440);
});

test("choosing a symbol from the watchlist tab changes the instrument", async ({ page }) => {
  await open(page);
  await page.getByRole("tab", { name: /^Watchlist/ }).click();
  await page.getByRole("table", { name: "Watchlist" }).getByRole("button", { name: "MSFT", exact: true }).click();
  await expect(page.getByLabel("Selected instrument quote")).toContainText("100.50");
  await expect(page.locator(".instrument-bar .chart-symbol-picker button[aria-pressed='true']")).toHaveText("MSFT");
});

test("focus chart still hides the research and can be undone", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Focus chart", exact: true }).click();
  await expect(page.getByLabel("Chart research dock")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Show research panels", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Show research panels", exact: true }).click();
  await expect(page.getByLabel("Chart research dock")).toBeVisible();
});

test("a laptop-height window still shows the chart and the dock tabs", async ({ page }) => {
  await open(page, 1280, 720);
  const plot = await page.getByLabel("Price chart", { exact: true }).boundingBox();
  expect(plot!.height, "price chart keeps a usable height").toBeGreaterThan(300);
  await expect(page.getByRole("tab", { name: /Setup readiness/ })).toBeVisible();
  await expectNoHorizontalOverflow(page, 1280);
});
