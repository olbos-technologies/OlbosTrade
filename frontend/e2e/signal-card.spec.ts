/**
 * The signal card: the big symbol, one headline number, and a small labelled table of the numbers.
 * Checked in a real browser because the two-column layout on a desktop, the stacked layout on a phone
 * and "nothing overflows at 320px" are layout, which jsdom does not do. The card is research only:
 * the headline never reads as a probability and a missing number is a dash.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function open(page: Page, width: number, opts: { options?: boolean } = {}) {
  await stubBackend(page);
  await page.route("**/api/**", (r) => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/signals?**", (r) => r.fulfill(reply({ signals: [
    { id: "s1", ticker: "PAYX", action: "BUY", confidence: 0.78, orderflow_score: 0.2, iv_overlay_boost: 0, earnings_gated: false,
      generated_at: "2026-11-02T21:09:02Z", reasons: ["MACD above signal"], opportunity_score: { score: 78, components: {} },
      trade_plan: { entry_price: 115.49, stop_price: 109.15, target_price: 128.16, target_move_pct: 11, shares: 170, risk_reward: 2, risk_dollars: 1077 } },
    { id: "s2", ticker: "NOPLAN", action: "BUY", confidence: 0.7, orderflow_score: 0, iv_overlay_boost: 0, earnings_gated: false,
      generated_at: "2026-11-02T21:09:02Z", reasons: ["x"], opportunity_score: { score: 60, components: {} }, trade_plan: {} },
  ] })));
  await page.route("**/api/options/signals**", (r) => r.fulfill(reply({ signals: [
    { id: "o1", ticker: "SPY", action: "SELL_SPREAD", confidence: 0.7, pop: 0.66, generated_at: "2026-11-02T21:09:02Z",
      strategy: "bull_put_spread", quantity: 2, regime: "high_vol_trending",
      spread: { short_strike: 415, long_strike: 410, option_type: "put", expiration: "2026-12-18", dte: 24, net_credit: 1.35, max_loss: 365, breakeven: 413.65 },
      intelligence: { reward_risk: 0.37 } },
  ] })));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/terminal/equity");
  if (opts.options) {
    await page.getByRole("button", { name: "OPTIONS", exact: true }).click();
  } else {
    const row = page.locator(".design-signal-card").first();
    await expect(row).toContainText("PAYX");
    await row.locator(":scope > summary").click();
  }
}

for (const width of [1440, 390, 320]) {
  test(`equity card at ${width}px: big symbol, one headline, a labelled table, nothing overflows`, async ({ page }) => {
    await open(page, width);
    const card = page.locator(".signal-hero").first();
    await expect(card).toBeVisible();
    await expect(card.getByRole("heading", { name: "PAYX" })).toBeVisible();
    await expect(card.locator(".signal-hero__headline")).toContainText("78/100");
    await expect(card.locator(".signal-hero__headline")).toContainText("Not a probability of profit");
    const table = card.locator(".signal-hero__stats");
    for (const [label, value] of [["ENTRY", "$115.49"], ["STOP", "$109.15"], ["TARGET", "$128.16"], ["MOVE", "11.0%"], ["SHARES", "170"], ["R:R", "2.00x"], ["RISK", "$1077"]]) {
      await expect(table.locator(".signal-hero__row", { has: page.getByText(label, { exact: true }) })).toContainText(value);
    }
    expect(await table.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "table fits its box").toBe(true);
    const main = (await card.locator(".signal-hero__main").boundingBox())!;
    const stats = (await table.boundingBox())!;
    if (width >= 700) expect(stats.x, "desktop: the table sits beside the headline").toBeGreaterThan(main.x + main.width - 2);
    else expect(stats.y, "phone: the table sits under the headline").toBeGreaterThanOrEqual(main.y + main.height - 2);
    await expect(card.getByRole("button", { name: /Review idea/ })).toBeVisible();
    await expectNoHorizontalOverflow(page, width);
  });
}

test("an idea with no trade plan says so, in words, instead of showing empty or zero numbers", async ({ page }) => {
  await open(page, 390);
  await page.locator(".design-signal-card", { hasText: "NOPLAN" }).locator(":scope > summary").click();
  const card = page.locator(".signal-hero", { hasText: "NOPLAN" });
  await expect(card.getByRole("status")).toContainText("No trade plan for this idea.");
  await expect(card.locator(".signal-hero__stats")).toHaveCount(0);
});

for (const width of [1440, 390, 320]) {
  test(`options card at ${width}px: strikes in the name, net credit headline, max loss and breakeven labelled`, async ({ page }) => {
    await open(page, width, { options: true });
    const card = page.locator(".signal-hero").first();
    await expect(card).toBeVisible();
    await expect(card.getByRole("heading")).toContainText("SPY");
    await expect(card.getByRole("heading")).toContainText("415/410P");
    await expect(card.locator(".signal-hero__headline")).toContainText("Net credit");
    await expect(card.locator(".signal-hero__headline")).toContainText("$1.35");
    await expect(card.locator(".signal-hero__headline")).toContainText("Not a profit estimate");
    const table = card.locator(".signal-hero__stats");
    for (const [label, value] of [["CREDIT", "$1.35"], ["MAX LOSS", "$365.00"], ["BREAKEVEN", "$413.65"], ["CONTRACTS", "2"], ["DTE", "24"], ["R:R", "0.37x"], ["STRIKES", "415/410"]]) {
      await expect(table.locator(".signal-hero__row", { has: page.getByText(label, { exact: true }) })).toContainText(value);
    }
    await expectNoHorizontalOverflow(page, width);
  });
}
