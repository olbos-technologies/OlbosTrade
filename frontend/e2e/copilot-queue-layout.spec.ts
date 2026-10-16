import { expect, test, type Page } from "@playwright/test";
import { stubBackend } from "./helpers";

const signal = (i: number) => ({
  id: `signal-${i}`,
  ticker: `T${String(i).padStart(3, "0")}`,
  action: "BUY",
  asset_type: "equity",
  confidence: 0.72,
  source: "Equity Signal Scanner",
  queued_at: "2026-10-16T12:00:00Z",
});

async function openQueue(page: Page, count: number, width: number) {
  await stubBackend(page);
  await page.route("**/api/trade-desk/pending", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ pending: Array.from({ length: count }, (_, i) => signal(i)), mode: "copilot" }),
    }),
  );
  await page.route("**/api/trade-desk/execution-log", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ log: [] }) }),
  );
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/terminal/trade/copilot");
  await expect(page.locator(".mission-list > .mission-card")).toHaveCount(count);
}

for (const width of [390, 1280]) {
  for (const count of [2, 174]) {
    test(`${count} Copilot cards keep content and actions visible at ${width}px`, async ({ page }) => {
      await openQueue(page, count, width);
      const queue = page.locator(".mission-list").filter({ has: page.locator(".mission-card__actions") });
      const first = queue.locator(".mission-card").first();
      const last = queue.locator(".mission-card").last();

      const layout = await first.evaluate((card) => {
        const box = card.getBoundingClientRect();
        const title = card.querySelector(".mission-card__title")!.getBoundingClientRect();
        const actions = card.querySelector(".mission-card__actions")!.getBoundingClientRect();
        const buttons = [...card.querySelectorAll(".mission-card__actions button")]
          .map((button) => button.getBoundingClientRect());
        return {
          height: box.height,
          titleInside: title.top >= box.top && title.bottom <= box.bottom,
          actionsInside: actions.top >= box.top && actions.bottom <= box.bottom,
          buttonsInside: buttons.every((button) => button.top >= box.top && button.bottom <= box.bottom),
        };
      });
      expect(layout.height).toBeGreaterThan(70);
      expect(layout.titleInside).toBe(true);
      expect(layout.actionsInside).toBe(true);
      expect(layout.buttonsInside).toBe(true);
      await expect(first.getByRole("button", { name: "APPROVE" })).toBeVisible();
      await expect(first.getByRole("button", { name: "REJECT" })).toBeVisible();

      const scroll = await queue.evaluate((el) => ({
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        overflowY: getComputedStyle(el).overflowY,
      }));
      expect(scroll.overflowY).toBe("auto");
      if (count === 174) expect(scroll.scrollHeight).toBeGreaterThan(scroll.clientHeight);

      await last.scrollIntoViewIfNeeded();
      await expect(last.getByRole("button", { name: "APPROVE" })).toBeVisible();
      await expect(last.getByRole("button", { name: "REJECT" })).toBeVisible();
      const lastAction = await last.getByRole("button", { name: "APPROVE" }).boundingBox();
      const queueBox = await queue.boundingBox();
      expect(lastAction!.y).toBeGreaterThanOrEqual(queueBox!.y);
      expect(lastAction!.y + lastAction!.height).toBeLessThanOrEqual(queueBox!.y + queueBox!.height);

      const recent = page.getByText("RECENT DECISIONS", { exact: true });
      await recent.scrollIntoViewIfNeeded();
      const recentBox = await recent.boundingBox();
      expect(recentBox!.y).toBeGreaterThanOrEqual(0);
      expect(recentBox!.y + recentBox!.height).toBeLessThanOrEqual(844);
    });
  }
}
