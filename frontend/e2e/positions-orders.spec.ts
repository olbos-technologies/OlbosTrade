/**
 * The Positions tab's broker-orders panel and per-row stop marker, measured in a
 * browser. jsdom does no layout, so a unit test cannot tell whether the panel fits
 * a phone or the marker survives the card reflow.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, SIGNED_IN, expectNoHorizontalOverflow } from "./helpers";

const POSITIONS = [
  { id: "t1", symbol: "AMZN", underlying: "AMZN", asset_type: "equity", strategy: "equity", quantity: 12, unrealized_pnl: 10, tracked: true },
  { id: "t2", symbol: "FER", underlying: "FER", asset_type: "equity", strategy: "adopted_untracked", quantity: 337, unrealized_pnl: -4, tracked: true },
];
const BOOK = {
  available: true, source: "refreshed", unprotected_is_reliable: true,
  positions_without_stop: [{ ticker: "FER" }], app_client_id: 2,
  orders: [
    { order_id: 1, symbol: "AMZN", action: "SELL", order_type: "STP", quantity: 12, stop_price: 243.82, status: "PreSubmitted", is_protective: true, placed_by: "this_app", client_id: 2 },
    { order_id: 2, symbol: "XYZ", action: "BUY", order_type: "LMT", quantity: 5, limit_price: 10, status: "Submitted", placed_by: "other_api_client", client_id: 7 },
  ],
};

async function open(page: Page, width: number, book: unknown) {
  await stubBackend(page, SIGNED_IN);
  await page.route("**/api/paper-trade/positions", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ positions: POSITIONS }) }));
  await page.route("**/api/portfolio/open-orders", (r) =>
    book === "fail" ? r.fulfill({ status: 500, body: "{}" }) : r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(book) }));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/terminal/trade/positions");
  await page.waitForSelector(".order-coverage");
}

for (const width of [390, 1280]) {
  test(`orders panel and stop markers fit at ${width}px`, async ({ page }) => {
    await open(page, width, BOOK);
    const panel = page.locator(".order-coverage");
    await expect(panel).toContainText("1 position with no stop at broker: FER");
    await expect(panel).toContainText("This app");
    await expect(panel).toContainText("Other API client (id 7)");
    await expect(page.locator(".position-stop--unprotected")).toContainText("No stop at broker");
    await expect(page.locator(".position-stop--protected")).toContainText("Stop in place");
    await expectNoHorizontalOverflow(page, width);
    // The sender must be on screen without scrolling the table sideways.
    const who = page.locator('.order-coverage td[data-label="Placed by"]').last();
    const box = await who.boundingBox();
    expect(box && box.x >= 0 && box.x + box.width <= width, "Placed by is visible without scrolling").toBe(true);
  });
}

test("a failed read shows unverified, never a protected stop", async ({ page }) => {
  await open(page, 1280, "fail");
  await expect(page.locator(".order-coverage")).toContainText("unverified");
  await expect(page.locator(".position-stop--protected")).toHaveCount(0);
  await expect(page.locator(".position-stop--unprotected")).toHaveCount(0);
  await expect(page.locator(".position-stop").first()).toContainText("Stop unverified");
});
