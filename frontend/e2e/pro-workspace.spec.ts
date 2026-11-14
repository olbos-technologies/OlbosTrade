import { expect, test, type Page } from "@playwright/test";
import { expectNoHorizontalOverflow, stubBackend } from "./helpers";

/**
 * Pro Workspace: the dockable multi-panel view. These run in a real browser
 * because dragging, live resizing and persistence are exactly what jsdom cannot
 * show. The page is presentation only: it places no orders and changes no
 * execution or risk setting.
 */

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

function bars(symbol: string, n = 160) {
  let seed = [...symbol].reduce((a, c) => a + c.charCodeAt(0), 0), px = 100 + (seed % 90), t = Date.UTC(2026, 8, 1, 14, 30);
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  return Array.from({ length: n }, () => {
    const o = px, c = o * (1 + (rnd() - 0.48) * 0.01);
    px = c; t += 3600e3;
    return { timestamp: new Date(t).toISOString(), open: +o.toFixed(2), high: +(Math.max(o, c) * 1.002).toFixed(2), low: +(Math.min(o, c) * 0.998).toFixed(2), close: +c.toFixed(2), volume: 1_000_000 + Math.round(rnd() * 500_000) };
  });
}

async function mockData(page: Page, opts: { chartFails?: boolean } = {}) {
  await stubBackend(page);
  await page.route("**/api/equity/chart/**", (r) => {
    if (opts.chartFails) return r.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "feed down" }) });
    const sym = new URL(r.request().url()).pathname.split("/").pop() ?? "X";
    return r.fulfill(json({ bars: bars(sym) }));
  });
  await page.route("**/api/market/snapshot/**", (r) => r.fulfill(json({ last_close: 187.25, change_pct: 0.84 })));
  await page.route("**/api/options-flow**", (r) => r.fulfill(json({ results: [] })));
}

const box = async (page: Page, sel: string) => (await page.locator(sel).first().boundingBox())!;
const go = (page: Page) =>
  page.getByRole("toolbar", { name: "Workspace controls" }).getByRole("button", { name: "Go", exact: true }).click();
const sep = (page: Page, name: RegExp) => page.getByRole("separator", { name });

async function drag(page: Page, name: RegExp, dx: number, dy: number) {
  const b = (await sep(page, name).boundingBox())!;
  const x = b.x + b.width / 2, y = b.y + b.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 10 });
  await page.mouse.up();
}

async function open(page: Page, width = 1440, height = 900, query = "") {
  await page.setViewportSize({ width, height });
  await page.goto(`/terminal/markets/workspace${query}`);
  await expect(page.getByRole("region", { name: "Chart", exact: true })).toBeVisible();
}

test.describe("Pro Workspace layout", () => {
  test("shows the chart, a watchlist, unusual options and the scanner dock, and fits the screen", async ({ page }) => {
    await mockData(page);
    await open(page);
    await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
    await expect(page.getByRole("region", { name: "Watchlist" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Unusual options" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Scanner" })).toBeVisible();
    await expectNoHorizontalOverflow(page, 1440);
    const c = await box(page, ".ws-slot--chart");
    expect(c.width).toBeGreaterThan(600);
    expect(c.height).toBeGreaterThan(250);
  });

  test("is reachable from the sidebar under Markets", async ({ page }) => {
    await mockData(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/terminal/markets/chart");
    await page.getByRole("button", { name: "Pro Workspace" }).click();
    await expect(page).toHaveURL(/\/terminal\/markets\/workspace$/);
    await expect(page.getByRole("region", { name: "Chart", exact: true })).toBeVisible();
  });

  test("dragging the vertical splitter resizes the chart, the chart redraws to fit, and it stops at its limit", async ({ page }) => {
    await mockData(page);
    await open(page);
    const before = await box(page, ".ws-slot--chart");
    await drag(page, /Resize chart and side panels/, -150, 0);
    const narrower = await box(page, ".ws-slot--chart");
    expect(narrower.width).toBeLessThan(before.width - 100);
    // the canvas follows its panel instead of overflowing it
    const canvas = await box(page, ".ws-chart .candlestick-chart");
    expect(canvas.width).toBeLessThanOrEqual(narrower.width + 1);
    await drag(page, /Resize chart and side panels/, 4000, 0);
    const wide = await box(page, ".ws-slot--chart");
    const top = await box(page, ".ws-top");
    expect(wide.width / top.width).toBeLessThanOrEqual(0.86); // the 85% limit (less the splitter track)
    expect((await box(page, ".ws-side")).width).toBeGreaterThan(100); // the side column is never lost
  });

  test("dragging the horizontal splitter resizes the dock and the chart height follows", async ({ page }) => {
    await mockData(page);
    await open(page);
    const before = await box(page, ".ws-chart .candlestick-chart");
    await drag(page, /Resize chart area and bottom dock/, 0, -120);
    const after = await box(page, ".ws-chart .candlestick-chart");
    expect(after.height).toBeLessThan(before.height - 60);
    await drag(page, /Resize chart area and bottom dock/, 0, 4000);
    const dock = await box(page, '[aria-label="Scanner"]');
    expect(dock.height).toBeGreaterThan(80); // the dock is never dragged to nothing
  });

  test("a splitter works from the keyboard and Enter resets it", async ({ page }) => {
    await mockData(page);
    await open(page);
    const s = sep(page, /Resize chart and side panels/);
    const start = Number(await s.getAttribute("aria-valuenow"));
    await s.focus();
    for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowLeft");
    expect(Number(await s.getAttribute("aria-valuenow"))).toBe(start - 8);
    await page.keyboard.press("Shift+ArrowRight");
    expect(Number(await s.getAttribute("aria-valuenow"))).toBe(start + 2);
    await page.keyboard.press("Enter");
    expect(Number(await s.getAttribute("aria-valuenow"))).toBe(start);
  });

  test("the layout is remembered across a reload", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drag(page, /Resize chart and side panels/, -140, 0);
    const resized = Number(await sep(page, /Resize chart and side panels/).getAttribute("aria-valuenow"));
    await page.getByLabel("Panel shown in the top side slot").selectOption("news");
    await page.reload();
    await expect(page.getByRole("region", { name: "Chart", exact: true })).toBeVisible();
    expect(Number(await sep(page, /Resize chart and side panels/).getAttribute("aria-valuenow"))).toBe(resized);
    await expect(page.getByLabel("Panel shown in the top side slot")).toHaveValue("news");
  });

  test("presets and region toggles change what is shown", async ({ page }) => {
    await mockData(page);
    await open(page);
    await page.getByRole("button", { name: "Chart only" }).click();
    await expect(page.getByRole("region", { name: "Watchlist" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Scanner" })).toHaveCount(0);
    const full = await box(page, ".ws-slot--chart");
    expect(full.width).toBeGreaterThan(1000);
    await page.getByRole("button", { name: "Side panels" }).click();
    await expect(page.getByRole("region", { name: "Watchlist" })).toBeVisible();
    await page.getByRole("button", { name: "Trader" }).click();
    await expect(page.getByRole("region", { name: "Scanner" })).toBeVisible();
    await page.getByRole("button", { name: "Reset layout" }).click();
    await expect(page.getByLabel("Panel shown in the top side slot")).toHaveValue("watchlist");
  });

  test("any panel can go in any slot", async ({ page }) => {
    await mockData(page);
    await open(page);
    await page.getByLabel("Panel shown in the bottom dock").selectOption("backtest");
    await expect(page.getByRole("region", { name: "Backtest" })).toBeVisible();
    await page.getByLabel("Panel shown in the bottom side slot").selectOption("positions");
    await expect(page.getByRole("region", { name: "Positions" })).toBeVisible();
  });
});

test.describe("Pro Workspace data and safety", () => {
  test("changing the symbol reloads the chart, and an invalid symbol is refused", async ({ page }) => {
    await mockData(page);
    await open(page);
    await expect(page.locator(".ws-chart__symbol")).toHaveText("AAPL");
    await page.getByLabel("Symbol", { exact: true }).fill("msft");
    await go(page);
    await expect(page.locator(".ws-chart__symbol")).toHaveText("MSFT");
    await page.getByLabel("Symbol", { exact: true }).fill("bad sym;");
    await go(page);
    await expect(page.getByRole("alert")).toContainText("Not a valid symbol");
    await expect(page.locator(".ws-chart__symbol")).toHaveText("MSFT"); // unchanged
  });

  test("clicking a watchlist symbol shows it on the chart", async ({ page }) => {
    await mockData(page);
    await open(page);
    await page.getByRole("button", { name: "NVDA", exact: true }).click();
    await expect(page.locator(".ws-chart__symbol")).toHaveText("NVDA");
  });

  test("a symbol from the URL is used, and a hostile one is ignored", async ({ page }) => {
    await mockData(page);
    await open(page, 1440, 900, "?symbol=tsla");
    await expect(page.locator(".ws-chart__symbol")).toHaveText("TSLA");
    await open(page, 1440, 900, "?symbol=%3Cscript%3E");
    await expect(page.locator(".ws-chart__symbol")).toHaveText("AAPL");
  });

  test("a failed chart request says so and never draws made-up bars", async ({ page }) => {
    await mockData(page, { chartFails: true });
    await open(page);
    await expect(page.getByRole("status").filter({ hasText: "Chart data unavailable for AAPL" })).toBeVisible();
    await expect(page.locator(".ws-chart .candlestick-chart")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Watchlist" })).toBeVisible(); // the rest still works
  });

  test("a corrupted saved layout does not break the page", async ({ page }) => {
    await mockData(page);
    await page.addInitScript(() => localStorage.setItem("olbos.workspace.layout.v1", '{"v":1,"chartFrac":99,"sideTop":"evil","bottom":"__proto__"}'));
    await open(page);
    await expect(page.getByRole("region", { name: "Watchlist" })).toBeVisible();
    await expect(page.getByLabel("Panel shown in the bottom dock")).toHaveValue("scanner");
    const t = await box(page, ".ws-top");
    expect((await box(page, ".ws-slot--chart")).width / t.width).toBeLessThanOrEqual(0.86);
  });

  test("adds and removes a watchlist symbol, sharing the Chart page's list", async ({ page }) => {
    await mockData(page);
    await open(page);
    await page.getByLabel("Add a symbol to the watchlist").fill("amd");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByRole("button", { name: "AMD", exact: true })).toBeVisible();
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem("olbos.chart.watchlist") || "[]"))).toContain("AMD");
    await page.getByRole("button", { name: "Remove AMD from Market Watch" }).click();
    await expect(page.getByRole("button", { name: "AMD", exact: true })).toHaveCount(0);
  });

  test("the workspace never offers an order or changes the execution mode", async ({ page }) => {
    const writes: string[] = [];
    await mockData(page);
    page.on("request", (r) => { if (r.url().includes("/api/") && r.method() !== "GET") writes.push(`${r.method()} ${new URL(r.url()).pathname}`); });
    await open(page);
    await page.getByRole("button", { name: "Chart only" }).click();
    await page.getByRole("button", { name: "Trader" }).click();
    await page.getByLabel("Symbol", { exact: true }).fill("spy");
    await go(page);
    await expect(page.locator(".ws-chart__symbol")).toHaveText("SPY");
    expect(writes).toEqual([]); // browsing the workspace writes nothing to the server
  });
});

test.describe("Pro Workspace on every screen and brand", () => {
  for (const width of [1920, 1440, 1280, 1024, 901]) {
    test(`fits at ${width}px`, async ({ page }) => {
      await mockData(page);
      await open(page, width, 800);
      await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
      await expectNoHorizontalOverflow(page, width);
    });
  }

  for (const width of [900, 768, 390, 320]) {
    test(`stacks into one panel at a time at ${width}px and fits`, async ({ page }) => {
      await mockData(page);
      await open(page, width, 800);
      await expect(page.locator(".ws-root--stacked")).toBeVisible();
      await expect(page.getByRole("separator")).toHaveCount(0); // nothing to drag on a phone
      await expectNoHorizontalOverflow(page, width);
      await page.getByRole("group", { name: "Workspace panels" }).getByRole("button", { name: "Watchlist" }).click();
      await expect(page.getByRole("region", { name: "Watchlist" })).toBeVisible();
      await expectNoHorizontalOverflow(page, width);
      await page.getByRole("group", { name: "Workspace panels" }).getByRole("button", { name: "Chart" }).click();
      await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
    });
  }

  for (const brand of ["olbos", "cimphawng"]) {
    test(`works under the ${brand} brand with its own colours`, async ({ page }) => {
      await mockData(page);
      await open(page, 1440, 900, `?brand=${brand}`);
      await expect(page.locator("html")).toHaveAttribute("data-brand", brand);
      await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
      await expectNoHorizontalOverflow(page, 1440);
      const accent = await page.evaluate(() => {
        const pressed = document.querySelector('.ws-toolbar button[aria-pressed="true"]')!;
        return getComputedStyle(pressed).borderTopColor;
      });
      expect(accent).not.toBe("");
      if (brand === "cimphawng") {
        const [r, , b] = (accent.match(/\d+/g) ?? []).map(Number);
        expect(b).toBeGreaterThan(r); // Cimphawng's accent is blue, not Olbos gold
      }
    });
  }
});
