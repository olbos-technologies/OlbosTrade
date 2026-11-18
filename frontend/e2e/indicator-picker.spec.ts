/**
 * The indicator picker on the Chart page (the former Pro Workspace). Measured in a real browser: where the
 * panel sits, whether the toolbar clips it, the phone sheet, the saved choice across pages, and that
 * the chart really draws a new panel (it is a canvas, so those are compared as pixels).
 */
import { expect, test, type Page } from "@playwright/test";
import { expectNoHorizontalOverflow } from "./helpers";
import { diffShots, type Shot } from "./imageDiff";
import { mockData } from "./workspaceMocks";

const picker = (page: Page) => page.getByRole("button", { name: "Indicators", exact: true });
const dialog = (page: Page) => page.getByRole("dialog", { name: "Chart indicators" });
const box = (page: Page, name: string) => dialog(page).getByRole("checkbox", { name, exact: true });
const readout = (page: Page) => page.getByLabel("Indicator readout");
const canvasShot = async (page: Page): Promise<Shot> => {
  await page.mouse.move(0, 0);
  return page.locator(".candlestick-chart__canvas").first().screenshot({ animations: "disabled" });
};

async function openChartPage(page: Page, width = 1440, height = 900) {
  await mockData(page);
  await page.addInitScript(() => { try { if (!sessionStorage.getItem("c")) { localStorage.clear(); sessionStorage.setItem("c", "1"); } } catch { /* ignore */ } });
  await page.setViewportSize({ width, height });
  await page.goto("/terminal/markets/chart?symbol=AAPL");
  await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
}

test("desktop: the button sits in the chart toolbar and opens a panel that is fully on screen", async ({ page }) => {
  await openChartPage(page);
  const bar = (await page.locator(".ws-chart__bar").boundingBox())!;
  const btn = (await picker(page).boundingBox())!;
  expect(btn.y).toBeGreaterThanOrEqual(bar.y - 1);
  expect(btn.y + btn.height).toBeLessThanOrEqual(bar.y + bar.height + 1);
  await picker(page).click();
  await expect(dialog(page)).toBeVisible();
  const d = (await dialog(page).boundingBox())!;
  expect(d.x, "not cut off on the left").toBeGreaterThanOrEqual(0);
  expect(d.x + d.width, "not cut off on the right").toBeLessThanOrEqual(1440);
  expect(d.y, "opens below the button").toBeGreaterThanOrEqual(btn.y + btn.height);
  expect(d.y + d.height, "not cut off at the bottom").toBeLessThanOrEqual(900);
  await expect(page.locator(".ws-chart__bar").locator('[role="dialog"]')).toHaveCount(0);   // not inside the toolbar
  await expectNoHorizontalOverflow(page, 1440);
});

test("desktop: the default selection is ticked, and the readout is what it always was", async ({ page }) => {
  await openChartPage(page);
  await expect(readout(page)).toContainText("MA5");
  await expect(readout(page)).toContainText("MACD");
  await expect(readout(page)).not.toContainText("RSI");
  await picker(page).click();
  for (const on of ["MA5", "MA10", "MA20", "VWAP", "Volume", "MACD"]) await expect(box(page, on)).toBeChecked();
  for (const off of ["MA50", "MA200", "EMA9", "EMA21", "EMA50", "Bollinger", "RSI", "ATR"]) await expect(box(page, off)).not.toBeChecked();
});

test("desktop: adding RSI draws a new panel on the chart and adds it to the readout; removing it undoes that", async ({ page }) => {
  await openChartPage(page);
  const before = await canvasShot(page);
  await picker(page).click();
  await box(page, "RSI").check();
  await page.keyboard.press("Escape");
  await expect(dialog(page)).toHaveCount(0);
  await expect(readout(page)).toContainText("RSI");
  await expect(page.locator(".ws-chart .candlestick-chart canvas").first()).toBeVisible();
  const withRsi = await canvasShot(page);
  const diff = await diffShots(page, before, withRsi);
  expect(diff.total, "the chart drew something new (a new panel changes many pixels)").toBeGreaterThan(500);
  await picker(page).click();
  await box(page, "RSI").uncheck();
  await page.keyboard.press("Escape");
  await expect(readout(page)).not.toContainText("RSI");
});

test("desktop: overlays that need more bars than are loaded read as a dash, never a made-up number", async ({ page }) => {
  await openChartPage(page);
  await picker(page).click();
  await box(page, "MA200").check();
  await page.keyboard.press("Escape");
  await expect(readout(page)).toContainText("MA200 —");
});

test("desktop: the choice is remembered across a reload, and Reset restores the default", async ({ page }) => {
  await openChartPage(page);
  await picker(page).click();
  await box(page, "EMA21").check();
  await box(page, "Volume").uncheck();
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
  await expect(readout(page)).toContainText("EMA21");
  await picker(page).click();
  await expect(box(page, "EMA21")).toBeChecked();
  await expect(box(page, "Volume")).not.toBeChecked();
  await dialog(page).getByRole("button", { name: "Reset to default" }).click();
  await expect(box(page, "EMA21")).not.toBeChecked();
  await expect(box(page, "Volume")).toBeChecked();
  await dialog(page).getByRole("button", { name: "Done" }).click();
  await expect(readout(page)).not.toContainText("EMA21");
});

test("keyboard: the button opens it, Tab stays inside, Escape closes and returns to the button", async ({ page }) => {
  await openChartPage(page);
  await picker(page).focus();
  await page.keyboard.press("Enter");
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page)).toBeFocused();
  for (let i = 0; i < 40; i++) await page.keyboard.press("Tab");           // far more than there are controls
  const inside = await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'));
  expect(inside, "focus never left the panel").toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog(page)).toHaveCount(0);
  await expect(picker(page)).toBeFocused();
});

test("phone: the picker is a sheet above the bottom nav, every row is a real touch target, and nothing overflows", async ({ page }) => {
  await openChartPage(page, 390, 844);
  await picker(page).scrollIntoViewIfNeeded();
  await picker(page).click();
  await expect(dialog(page)).toBeVisible();
  const d = (await dialog(page).boundingBox())!;
  expect(d.x).toBeGreaterThanOrEqual(0);
  expect(d.x + d.width).toBeLessThanOrEqual(390);
  expect(d.y, "starts on screen").toBeGreaterThanOrEqual(0);
  expect(d.y + d.height, "stays above the 56px bottom nav").toBeLessThanOrEqual(844 - 56);
  for (const name of ["MA5", "RSI", "Bollinger"]) {
    const row = (await box(page, name).locator("xpath=ancestor::label").boundingBox())!;
    expect(row.height, `${name} row is a touch target`).toBeGreaterThanOrEqual(43);
  }
  await dialog(page).getByRole("button", { name: "Done" }).scrollIntoViewIfNeeded();
  await expect(dialog(page).getByRole("button", { name: "Done" })).toBeVisible();
  await expectNoHorizontalOverflow(page, 390);
  await box(page, "RSI").check();
  await dialog(page).getByRole("button", { name: "Done" }).click();
  await expect(readout(page)).toContainText("RSI");
});

test("phone 320px: the default chart still starts early enough, because the default is unchanged", async ({ page }) => {
  await openChartPage(page, 320, 844);
  const plot = (await page.getByLabel("Price chart", { exact: true }).boundingBox())!;
  expect(plot.y + 300, "at least 300px of the chart above the bottom nav").toBeLessThanOrEqual(844 - 56);
});
