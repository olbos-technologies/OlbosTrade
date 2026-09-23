/**
 * The execution mode switcher must be fully readable on a phone.
 *
 * Label + three buttons + a status pill in a no-wrap row measures 446px. It
 * survived 390px by 13px and clipped at 360px — one of the most common
 * Android viewports — cutting AUTOPILOT mid-word. That is the control
 * deciding whether the system trades unattended, so a button you cannot read
 * is the wrong one to leave clipped.
 *
 * This is a browser test because it is a layout fact: jsdom does no layout,
 * so the clipping is invisible to vitest by construction.
 */

import { test, expect } from "@playwright/test";
import { stubBackend, SIGNED_IN } from "./helpers";

for (const width of [390, 360, 320]) {
  test(`execution switcher is not clipped at ${width}px`, async ({ page }) => {
    await stubBackend(page, SIGNED_IN);
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/terminal/dashboard");
    await page.waitForSelector(".mobile-bottom-nav");
    await page.locator(".mobile-bottom-nav")
      .getByRole("button", { name: "Positions", exact: true }).click();
    await page.waitForSelector(".exec-mode-row");

    const row = page.locator(".exec-mode-row");
    const { scrollW, clientW } = await row.evaluate((el) => ({
      scrollW: el.scrollWidth, clientW: el.clientWidth,
    }));
    expect(
      scrollW,
      `the row needs ${scrollW}px in ${clientW}px, so a mode button is cut off`
    ).toBeLessThanOrEqual(clientW);

    // And every mode button is inside the viewport, not merely scrollable to.
    const rights = await page.evaluate(() =>
      [...document.querySelectorAll(".exec-mode-row button")]
        .map(b => Math.round(b.getBoundingClientRect().right)));
    expect(rights.length).toBe(3);
    for (const r of rights) expect(r).toBeLessThanOrEqual(width);
  });
}

test("each mode button is a real touch target", async ({ page }) => {
  await stubBackend(page, SIGNED_IN);
  await page.setViewportSize({ width: 360, height: 844 });
  await page.goto("/terminal/dashboard");
  await page.waitForSelector(".mobile-bottom-nav");
  await page.locator(".mobile-bottom-nav")
    .getByRole("button", { name: "Positions", exact: true }).click();
  await page.waitForSelector(".exec-mode-row");

  const heights = await page.evaluate(() =>
    [...document.querySelectorAll(".exec-mode-row button")]
      .map(b => Math.round(b.getBoundingClientRect().height)));
  for (const h of heights) expect(h, "below the 44px tappable minimum").toBeGreaterThanOrEqual(44);
});
