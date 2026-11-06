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
    await expect(ticker.getByTestId("exec-mode-state")).toHaveCount(0);
    for (const box of [groupBox!]) {
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

for (const width of [820, 1280, 1971]) {
  test(`the account chip sits at the top right of the header at ${width}px`, async ({ page }) => {
    await openHeader(page, width);
    const header = page.locator(".desktop-terminal-header");
    const chip = page.getByRole("button", { name: /^Account:/ });
    await expect(chip, "account control never mounted — auth stub not applied").toBeVisible();
    // Exactly one: the status bar must not carry a second copy.
    await expect(chip).toHaveCount(1);

    const h = (await header.boundingBox())!;
    const c = (await chip.boundingBox())!;
    // Inside the header's top row, at its right-hand end.
    expect(c.y).toBeGreaterThanOrEqual(h.y);
    expect(c.y + c.height).toBeLessThanOrEqual(h.y + 64);
    expect(c.x + c.width).toBeLessThanOrEqual(h.x + h.width);
    expect(c.x + c.width).toBeGreaterThan(h.x + h.width - 160);

    // It must not overlap the execution-mode control.
    const g = (await header.getByRole("group", { name: "Execution mode" }).boundingBox())!;
    expect(c.x).toBeGreaterThanOrEqual(g.x + g.width);

    // The menu opens downward from the chip, and Sign out is reachable.
    await chip.click();
    const out = page.getByRole("menuitem", { name: /sign out/i });
    const o = (await out.boundingBox())!;
    expect(o.y).toBeGreaterThan(c.y + c.height);
    expect(o.y + o.height).toBeLessThanOrEqual(844);
  });
}

test("the adopted-from-broker warning stays inside its Strategy cell on desktop", async ({ page }) => {
  await stubBackend(page, SIGNED_IN);
  await page.route("**/api/paper-trade/positions", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ positions: [{
        id: "trade-fer", symbol: "FER", asset_type: "equity", spread_type: "equity_long",
        strategy: "adopted_untracked", tracked: true, quantity: -337, avg_cost: 58.57,
        unrealized_pnl: -221, mfe_pnl: -221, mae_pnl: -221, hold_days: 0, trading_mode: "balanced",
      }] }),
    }));
  await page.setViewportSize({ width: 1971, height: 1100 });
  await page.goto("/terminal/trade/positions");

  const warning = page.getByText(/Adopted from broker/);
  await expect(warning, "the adopted row never rendered").toBeVisible();
  const cell = page.locator('td[data-col="strategy"]').first();
  const credit = page.locator('td[data-col="credit"]').first();
  const w = (await warning.boundingBox())!;
  const s = (await cell.boundingBox())!;
  const c = (await credit.boundingBox())!;
  expect(w.x + w.width, "warning spills out of its cell").toBeLessThanOrEqual(s.x + s.width + 1);
  expect(w.x + w.width, "warning runs into the Entry column").toBeLessThanOrEqual(c.x + 1);
});
