/**
 * Markets > Chart on a phone, with a real-looking last bar: the price is not
 * covered by the symbol box, the last bar reads on a few lines (not one token per
 * line), and the legend button never sits on the indicator readout.
 * Regression: the wider monospace face squeezed the quote column to ~60px.
 */
import { test, expect } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const json = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

for (const width of [390, 320]) {
  test(`chart header is readable at ${width}px`, async ({ page }) => {
    await stubBackend(page);
    await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
      ? json({ auth_enabled: false, authenticated: false, user: null })
      : { status: 503, contentType: "application/json", body: "{}" }));
    const bars = Array.from({ length: 60 }, (_, i) => ({
      time: 1760000000 + i * 900, open: 350.75, high: 351.89, low: 350.41, close: 350.49, volume: 4100000 }));
    await page.route("**/api/equity/chart**", r => r.fulfill(json({ bars, symbol: "AAPL" })));
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/terminal/markets/chart");
    const price = page.locator(".instrument-bar__price strong");
    await expect(price).toBeVisible();
    await expect(page.locator(".instrument-bar__ohlc")).toContainText("Vol");
    await page.waitForTimeout(400);

    const box = async (sel: string) => (await page.locator(sel).first().boundingBox())!;
    const p = await box(".instrument-bar__price strong");
    const goto = await box(".instrument-bar__goto");
    // The price and the symbol box do not overlap on either axis.
    const overlap = p.x < goto.x + goto.width && goto.x < p.x + p.width && p.y < goto.y + goto.height && goto.y < p.y + p.height;
    expect(overlap, `price ${JSON.stringify(p)} vs symbol box ${JSON.stringify(goto)}`).toBe(false);

    // The last bar is a full-width row of at most two lines (it was one token per line).
    const ohlc = await box(".instrument-bar__ohlc");
    expect(ohlc.width).toBeGreaterThan(width * 0.8);
    expect(ohlc.height).toBeLessThanOrEqual(40);

    // The legend button sits under the readout, not on it.
    const readout = await box(".candlestick-chart__readout");
    const legend = await box(".candlestick-chart__legend-toggle");
    expect(legend.y).toBeGreaterThanOrEqual(readout.y + readout.height - 1);
    await expectNoHorizontalOverflow(page, width);
  });
}
