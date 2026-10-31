/**
 * Phone Alpha Edge KPI strip: whole-word labels in a 2×2 grid.
 * Guard against the mid-word wrap that shipped as "Ope / n / posit / ions".
 */
import { test, expect } from "@playwright/test";
import { stubBackend, SIGNED_IN } from "./helpers";

test("Alpha Edge KPI labels stay whole words on a phone", async ({ page }) => {
  await stubBackend(page, SIGNED_IN);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/terminal/strat/alpha-edge");
  await expect(page.getByRole("heading", { name: "Signals", exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Find a workspace" })).toHaveCount(0);

  const strip = page.locator(".instrument-stat-strip").first();
  await expect(strip).toBeVisible();
  for (const label of ["Open positions", "Scan candidates", "Top opportunity", "Scores loaded"]) {
    const cell = strip.locator(".kicker", { hasText: label });
    await expect(cell).toBeVisible();
    const wrap = await cell.evaluate((el) => getComputedStyle(el).overflowWrap);
    expect(wrap, label).not.toBe("anywhere");
    const box = await cell.boundingBox();
    expect(box!.height, `${label} should be at most two lines`).toBeLessThan(36);
  }
});
