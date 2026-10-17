import { expect, test, type Page } from "@playwright/test";
import { SIGNED_IN, stubBackend } from "./helpers";

async function openHeader(page: Page, width: number, modeAvailable = true) {
  await stubBackend(page, SIGNED_IN);
  await page.route("**/api/trade-desk/execution-mode", (route) =>
    route.fulfill({
      status: modeAvailable ? 200 : 503,
      contentType: "application/json",
      body: modeAvailable ? JSON.stringify({ mode: "autopilot" }) : "{}",
    }),
  );
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/terminal/dashboard");
}

for (const width of [820, 1280, 1971]) {
  test(`desktop execution mode stays inside the header at ${width}px`, async ({ page }) => {
    await openHeader(page, width);
    const ticker = page.locator(".instrument-ticker");
    const group = ticker.getByRole("group", { name: "Execution mode" });
    await expect(group.getByRole("button", { name: "AUTOPILOT" })).toHaveAttribute("aria-pressed", "true");

    const headerBox = await ticker.boundingBox();
    const groupBox = await group.boundingBox();
    const stateBox = await ticker.getByTestId("exec-mode-state").boundingBox();
    for (const box of [groupBox!, stateBox!]) {
      expect(box.y).toBeGreaterThanOrEqual(headerBox!.y);
      expect(box.y + box.height).toBeLessThanOrEqual(headerBox!.y + headerBox!.height);
      expect(box.x).toBeGreaterThanOrEqual(headerBox!.x);
      expect(box.x + box.width).toBeLessThanOrEqual(headerBox!.x + headerBox!.width);
    }
    for (const name of ["MANUAL", "COPILOT", "AUTOPILOT"]) {
      await expect(group.getByRole("button", { name })).toBeVisible();
    }
  });
}

test("an unconfirmed mode warning is not clipped out of the desktop header", async ({ page }) => {
  await openHeader(page, 1280, false);
  const ticker = page.locator(".instrument-ticker");
  const warning = ticker.getByTestId("exec-mode-stale");
  await expect(warning).toBeVisible();
  const headerBox = await ticker.boundingBox();
  const warningBox = await warning.boundingBox();
  expect(warningBox!.y).toBeGreaterThanOrEqual(headerBox!.y);
  expect(warningBox!.y + warningBox!.height).toBeLessThanOrEqual(headerBox!.y + headerBox!.height);
});
