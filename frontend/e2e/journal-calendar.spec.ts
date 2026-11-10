/**
 * Journal > Monthly review: a month calendar of realized P&L by close date.
 * The sign is printed on every day, a failed read says "unavailable" rather than
 * "no trades", and the grid fits a phone.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
const month = new Date().toISOString().slice(0, 7);
const day = (n: number) => `${month}-${String(n).padStart(2, "0")}`;

const REVIEW = {
  month, total_trades: 3, win_rate: 0.33, total_pnl: -100, rules_followed_pct: 80, top_tags: [], top_mistakes: [],
  mistake_frequency: {}, confidence_vs_outcome: [], recommendation: "Keep going.",
  daily_pnl: [
    { date: day(2), pnl: -605.76, trades: 1 },
    { date: day(3), pnl: 210.22, trades: 2 },
    { date: day(5), pnl: -1520, trades: 3 },
  ],
};

async function open(page: Page, review: unknown, width = 1280) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/journal/entries**", r => r.fulfill(reply({ entries: [] })));
  await page.route("**/api/journal/analytics/rule-breach-impact", r => r.fulfill(reply({ impact: null })));
  await page.route("**/api/journal/review/monthly/**", r => review === "down"
    ? r.fulfill({ status: 500, contentType: "application/json", body: "{}" })
    : r.fulfill(reply({ month, review })));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/terminal/journal");
  await page.getByRole("button", { name: /monthly review/i }).first().click();
}

test("each day with closed trades shows a signed amount, other days only the date", async ({ page }) => {
  await open(page, REVIEW);
  const cal = page.getByRole("region", { name: new RegExp(`P&L calendar ${month}`) });
  await expect(cal.getByLabel(`${day(2)}: -605.76 from 1 closed trade`)).toContainText("-605.76");
  await expect(cal.getByLabel(`${day(3)}: +210.22 from 2 closed trades`)).toContainText("+210.22");
  await expect(cal.getByLabel(`${day(5)}: -1.52K from 3 closed trades`)).toContainText("-1.52K");
  await expect(cal.getByLabel(`${day(9)}: no closed trades`)).not.toContainText(/[+-]\d/);
  await expect(cal.locator(".journal-cal__summary strong")).toContainText("-1.92K");
});

test("the month arrows change the month and reload it", async ({ page }) => {
  await open(page, REVIEW);
  const seen: string[] = [];
  page.on("request", r => { const m = r.url().match(/review\/monthly\/(\d{4}-\d{2})/); if (m) seen.push(m[1]); });
  await page.getByRole("button", { name: "Previous month" }).click();
  await expect.poll(() => seen.length).toBeGreaterThan(0);
  expect(seen[seen.length - 1]).not.toBe(month);
});

test("a failed read says unavailable, never 'no trades'", async ({ page }) => {
  await open(page, "down");
  await expect(page.getByText("MONTHLY REVIEW UNAVAILABLE")).toBeVisible();
  await expect(page.getByText("NO TRADES THIS MONTH")).toHaveCount(0);
});

test("a month with no closed trades and no entries still says so", async ({ page }) => {
  await open(page, { ...REVIEW, total_trades: 0, daily_pnl: [] });
  await expect(page.getByText("NO TRADES THIS MONTH")).toBeVisible();
});

test("trades that closed this month show on the calendar even if none opened this month", async ({ page }) => {
  await open(page, { ...REVIEW, total_trades: 0 });
  await expect(page.getByRole("region", { name: /P&L calendar/ })).toBeVisible();
});

for (const width of [390, 320]) {
  test(`the calendar fits a ${width}px phone`, async ({ page }) => {
    await open(page, REVIEW, width);
    const cal = page.getByRole("region", { name: /P&L calendar/ });
    await expect(cal).toBeVisible();
    await expectNoHorizontalOverflow(page, width);
    const box = await cal.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
  });
}
