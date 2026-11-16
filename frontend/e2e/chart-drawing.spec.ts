/**
 * Drawing tools on the Chart page (Markets > Chart), which only the Pro Workspace had.
 * Desktop gets the rail beside the chart; a phone gets a "Draw" switch, because there one
 * finger must still pan the chart by default. The touch tests send real touch events through
 * the browser's protocol: jsdom cannot show whether the chart panned or a line was drawn.
 * Drawings are notes anchored to time and price and saved in this browser; they place no orders.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const KEY = "olbos.workspace.drawings.v1";
const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const START = Date.parse("2026-08-14T13:30:00Z");
const bars = Array.from({ length: 80 }, (_, i) => ({
  timestamp: new Date(START + i * 15 * 60_000).toISOString(),
  open: 340 + Math.sin(i / 5) * 3, high: 343 + Math.sin(i / 5) * 3, low: 338 + Math.sin(i / 5) * 3,
  close: 341 + Math.sin(i / 4) * 3, volume: 1_000_000 + i * 10_000,
}));

const canvas = (page: Page) => page.locator(".dock-chart .candlestick-chart__canvas");
const tool = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const rail = (page: Page) => page.getByRole("toolbar", { name: "Drawing tools" });
const drawSwitch = (page: Page) => page.getByRole("button", { name: "Draw", exact: true });

async function open(page: Page, width: number, height: number) {
  await stubBackend(page);
  await page.route("**/api/**", (r) => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/chart/**", (r) => r.fulfill(reply({ bars, range: "5d", range_clamped: false, period: "5d" })));
  await page.route("**/api/market/snapshot/**", (r) => r.fulfill(reply({ last_close: 342.36, prev_close: 343, change_pct: -0.52 })));
  await page.route("**/api/paper-trade/positions", (r) => r.fulfill(reply({ positions: [] })));
  await page.addInitScript(() => { try { if (!sessionStorage.getItem("cleared")) { localStorage.clear(); sessionStorage.setItem("cleared", "1"); } } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.locator(".dock-chart .candlestick-chart")).toBeVisible();
}

async function stored(page: Page): Promise<{ kind: string; points: { time: number; price: number }[] }[]> {
  return page.evaluate((k) => (JSON.parse(localStorage.getItem(k) || "{}")["AAPL"] ?? []), KEY);
}

async function box(page: Page) {
  const b = (await canvas(page).boundingBox())!;
  return { X: (f: number) => b.x + b.width * f, Y: (f: number) => b.y + b.height * f, b };
}

/** One finger down, along a path, up. Real touch input, so the chart's own touch handling is in play. */
async function swipe(page: Page, from: [number, number], to: [number, number], steps = 8) {
  const cdp = await page.context().newCDPSession(page);
  const pt = (x: number, y: number) => [{ x, y, id: 1 }];
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: pt(...from) });
  for (let i = 1; i <= steps; i++) {
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove", touchPoints: pt(from[0] + ((to[0] - from[0]) * i) / steps, from[1] + ((to[1] - from[1]) * i) / steps),
    });
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await cdp.detach();
}

test.describe("desktop", () => {
  test("the rail sits beside the chart, and a mouse drag draws a trend line that is saved", async ({ page }) => {
    await open(page, 1440, 900);
    await expect(rail(page)).toBeVisible();
    await expect(drawSwitch(page)).toHaveCount(0); // the Draw switch is a phone control
    const r = (await rail(page).boundingBox())!, c = (await canvas(page).boundingBox())!;
    expect(r.x + r.width).toBeLessThanOrEqual(c.x + 1); // beside the chart, not over it
    await tool(page, "Trend line").click();
    const { X, Y } = await box(page);
    await page.mouse.move(X(0.2), Y(0.3));
    await page.mouse.down();
    await page.mouse.move(X(0.5), Y(0.15), { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    expect((await stored(page))[0].kind).toBe("trend");
    await expectNoHorizontalOverflow(page, 1440);
  });

  test("the volume profile and indicator controls are still in the toolbar", async ({ page }) => {
    await open(page, 1440, 900);
    await expect(page.getByRole("button", { name: "Volume profile", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /Indicators/ })).toBeVisible();
  });
});

test.describe("phone", () => {
  test.use({ hasTouch: true });

  test("the default phone chart is unchanged: no toolbar, a Draw switch that is off", async ({ page }) => {
    await open(page, 390, 844);
    await expect(drawSwitch(page)).toBeVisible();
    await expect(drawSwitch(page)).toHaveAttribute("aria-pressed", "false");
    await expect(rail(page)).toHaveCount(0);
    // The chart still fills the phone's width exactly: wrapping it for the rail must not widen or narrow it.
    const c = (await canvas(page).boundingBox())!;
    expect(c.width).toBeGreaterThan(330);
    expect(c.x + c.width).toBeLessThanOrEqual(390);
    await expectNoHorizontalOverflow(page, 390);
  });

  test("with Draw off, one finger on the chart draws nothing", async ({ page }) => {
    await open(page, 390, 844);
    const { X, Y } = await box(page);
    await swipe(page, [X(0.2), Y(0.3)], [X(0.6), Y(0.2)]);
    expect(await stored(page)).toEqual([]);
  });

  test("Draw opens a finger-sized bar; a finger drag draws a line; switching Draw off hides the bar", async ({ page }) => {
    await open(page, 390, 844);
    await drawSwitch(page).click();
    await expect(drawSwitch(page)).toHaveAttribute("aria-pressed", "true");
    await expect(rail(page)).toBeVisible();
    for (const name of ["Trend line", "Undo"]) {
      const b = (await tool(page, name).boundingBox())!;
      expect(b.width, `${name} width`).toBeGreaterThanOrEqual(44);
      expect(b.height, `${name} height`).toBeGreaterThanOrEqual(44);
    }
    await expectNoHorizontalOverflow(page, 390);
    await tool(page, "Trend line").click();
    const { X, Y } = await box(page);
    await swipe(page, [X(0.2), Y(0.3)], [X(0.6), Y(0.2)]);
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    expect((await stored(page))[0].kind).toBe("trend");
    await drawSwitch(page).click();
    await expect(rail(page)).toHaveCount(0);
  });

  test("a drawn line can be dragged by a finger that lands a little off it", async ({ page }) => {
    await open(page, 390, 844);
    await drawSwitch(page).click();
    // The chart must fit the phone's width (a tap beyond it would be off-screen).
    expect((await canvas(page).boundingBox())!.width).toBeLessThanOrEqual(390);
    await tool(page, "Horizontal line").click();
    const { X, Y } = await box(page);
    const y0 = Y(0.3);
    await swipe(page, [X(0.5), y0], [X(0.5), y0], 1); // a tap places it
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    const before = (await stored(page))[0].points[0].price;
    await swipe(page, [X(0.4), y0 + 10], [X(0.4), y0 - 40]); // 10px off the line: a mouse would miss
    await expect.poll(async () => (await stored(page))[0].points[0].price).not.toBe(before);
  });

  test("at 320px wide the Draw bar causes no sideways scroll", async ({ page }) => {
    await open(page, 320, 640);
    await drawSwitch(page).click();
    await expect(rail(page)).toBeVisible();
    await expectNoHorizontalOverflow(page, 320);
    const r = (await rail(page).boundingBox())!;
    expect(r.x + r.width).toBeLessThanOrEqual(320 + 1); // scrolls inside itself
  });
});
