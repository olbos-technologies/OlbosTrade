/**
 * The desktop chart page: one instrument bar, the chart in the top two thirds and
 * a Positions / Watchlist panel in the bottom third. Measured in a real browser:
 * the split and "the chart starts high" are layout facts jsdom cannot see.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const bars = Array.from({ length: 60 }, (_, i) => ({
  timestamp: `2026-08-14T${String(9 + Math.floor(i / 4)).padStart(2, "0")}:${String((i % 4) * 15).padStart(2, "0")}:00-04:00`,
  open: 340 + Math.sin(i / 5) * 3, high: 343 + Math.sin(i / 5) * 3, low: 338 + Math.sin(i / 5) * 3,
  close: 341 + Math.sin(i / 4) * 3, volume: 1_000_000 + i * 10_000,
}));

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
  await page.route("**/api/paper-trade/positions", r => r.fulfill(reply({ positions: [
    { symbol: "AMZN", quantity: 7, avg_cost: 262.35, unrealized_pnl: -21, hold_days: 2 },
    { symbol: "FER", quantity: -337, avg_cost: 58.57, unrealized_pnl: -46, hold_days: 2 },
  ] })));
  await page.addInitScript(() => { try { localStorage.clear(); } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.getByRole("heading", { name: "Market workspace" })).toBeVisible();
}

test("the chart takes the top two thirds and the panel the bottom third", async ({ page }) => {
  await open(page);
  const chart = await page.locator(".dock-chart").boundingBox();
  const panel = await page.getByLabel("Chart research dock").boundingBox();
  expect(chart!.y, "chart starts in the top third").toBeLessThan(330);
  expect(chart!.width, "full width").toBeGreaterThan(1000);
  const ratio = chart!.height / panel!.height;
  expect(ratio, `chart ${chart!.height} : panel ${panel!.height} should be about 2 : 1`).toBeGreaterThan(1.6);
  expect(ratio).toBeLessThan(2.5);
  expect(panel!.height, "panel is tall enough to read").toBeGreaterThan(140);
  await expect(page.getByRole("tab")).toHaveCount(2);
  await expect(page.locator(".workstation-right, .workstation-watchlist, .workstation-top-strip, .chart-quote")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Focus chart|Show research/ })).toHaveCount(0);
  await expectNoHorizontalOverflow(page, 1440);
  await page.screenshot({ path: "/tmp/claude-0/-home-user/04e87dd5-b271-5930-8e11-808c5124d165/scratchpad/two3-1440.png" });
});

test("the bottom panel shows positions first and the watchlist on the second tab", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("tab", { name: /Positions/ })).toHaveAttribute("aria-selected", "true");
  const positions = page.getByRole("table", { name: "Open positions" });
  await expect(positions).toContainText("FER");
  await expect(positions).toContainText("−$46.00");
  await page.getByRole("tab", { name: /Watchlist/ }).click();
  const list = page.getByRole("table", { name: "Watchlist" });
  await expect(list).toContainText("AAPL");
  await expect(list).toContainText("342.36");
  await list.getByRole("button", { name: "MSFT", exact: true }).click();
  await expect(page.getByLabel("Selected instrument quote")).toContainText("100.50");
  await expect(page.getByRole("button", { name: "Remove MSFT from Market Watch" }).first()).toBeVisible();
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
  await page.getByRole("button", { name: "Remove AMD from Market Watch" }).click();
  // removing the symbol you are looking at moves you to another one
  await expect(page.locator(".instrument-bar .chart-symbol-picker button", { hasText: "AMD" })).toHaveCount(0);
  await expect(page.locator(".instrument-bar .chart-symbol-picker button[aria-pressed='true']")).not.toHaveText("AMD");
  await input.fill("not a symbol!");
  await input.press("Enter");
  await expect(input).toHaveAttribute("aria-invalid", "true");
});

test("choosing a picker symbol changes the instrument", async ({ page }) => {
  await open(page);
  await page.locator(".instrument-bar .chart-symbol-picker button", { hasText: "MSFT" }).click();
  await expect(page.getByLabel("Selected instrument quote")).toContainText("100.50");
});

test("a laptop-height window keeps both the chart and the panel usable", async ({ page }) => {
  await open(page, 1280, 720);
  const plot = await page.getByLabel("Price chart", { exact: true }).boundingBox();
  expect(plot!.height, "price chart keeps a usable height").toBeGreaterThan(250);
  expect((await page.getByLabel("Chart research dock").boundingBox())!.height).toBeGreaterThan(140);
  await expectNoHorizontalOverflow(page, 1280);
  await page.screenshot({ path: "/tmp/claude-0/-home-user/04e87dd5-b271-5930-8e11-808c5124d165/scratchpad/two3-1280.png" });
});
