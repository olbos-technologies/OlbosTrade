/**
 * The Command Center capacity panel, in a real browser (jsdom does no layout).
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, SIGNED_IN, expectNoHorizontalOverflow } from "./helpers";

const CAP = {
  global_cap: 5, style: { name: "aggressive", cap: 6 }, base_cap: 5, limited_by: "global",
  regime: { name: "high_vol_trending", known: true, multiplier: 0.75, tightened: true },
  effective_cap: 3, open_positions: 3, room: 0,
  steps: ["Global limit 5", "Trading style (aggressive) allows 6", "Tighter of the two: 5",
          "Market regime (high_vol_trending) scales that by 0.75: 5 → 3"],
  recent_blocks: [{ reason: "Max concurrent positions reached", count: 12 }, { reason: "duplicate_open", count: 2 }],
  blocks_available: true, blocks_window_hours: 24,
};

async function open(page: Page, width: number, body: unknown, status = 200) {
  await stubBackend(page, SIGNED_IN);
  await page.route("**/api/trade-desk/capacity", (r) =>
    r.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) }));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/terminal/dashboard");
}

for (const width of [390, 1280]) {
  test(`capacity panel explains the limit and fits at ${width}px`, async ({ page }) => {
    await open(page, width, CAP);
    const panel = page.getByLabel("Trading capacity");
    await expect(panel).toContainText("At capacity: 3 of 3 open");
    await expect(panel).toContainText("5 → 3");
    await expect(panel).toContainText("Max concurrent positions reached");
    await expect(panel).toContainText("×12");
    await expectNoHorizontalOverflow(page, width);
  });
}

test("a failed read says unavailable and shows no number", async ({ page }) => {
  await open(page, 1280, {}, 500);
  const panel = page.getByLabel("Trading capacity");
  await expect(panel).toContainText("Capacity unavailable");
  await expect(panel).not.toContainText(/\d+ of \d+ open/);
  await expect(panel).not.toContainText("Limit today");
});
