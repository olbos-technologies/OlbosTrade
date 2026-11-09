/**
 * Signals ledger, in a real browser: one ranked list of candidates, blocked ideas
 * folded away (and opened for you when nothing else is left), a status word on
 * every row, and "not checked" - never "clear" - when the desk state is unreadable.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const plan = { entry_price: 100, stop_price: 95, target_price: 110, target_move_pct: 10, shares: 10, risk_reward: 2, risk_dollars: 50 };
const sig = (ticker: string, action: string, confidence: number, over: Record<string, unknown> = {}) => ({
  id: `s-${ticker}`, ticker, action, confidence, orderflow_score: 0.1, iv_overlay_boost: 0, earnings_gated: false,
  generated_at: "2026-11-09T14:00:00Z", reasons: ["Momentum aligned"], trade_plan: plan,
  opportunity_score: { score: Math.round(confidence * 100), components: {} }, ...over,
});
const SIGNALS = [
  sig("AAA", "BUY", 0.9), sig("BBB", "SELL", 0.85), sig("CCC", "BUY", 0.5), // CCC is under the 0.70 floor
  sig("HHH", "HOLD", 0.8, { trade_plan: undefined }), sig("EEE", "BUY", 0.95, { earnings_gated: true }),
];

/** A folded group, found by its heading rather than by text anywhere inside it. */
const group = (page: Page, heading: RegExp) =>
  page.locator("details.signal-group").filter({ has: page.locator("summary.signal-group-head", { hasText: heading }) });

async function open(page: Page, { desk, width = 1280, openPositions = 0 }: { desk: boolean; width?: number; openPositions?: number }) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/equity/signals?**", r => r.fulfill(reply({ signals: SIGNALS })));
  if (desk) {
    await page.route("**/api/mode/current", r => r.fulfill(reply({ mode: "aggressive", max_concurrent: 3 })));
    await page.route("**/api/mode/all", r => r.fulfill(reply({ aggressive: { min_confidence: 0.7, is_active: true } })));
    await page.route("**/api/guardrails/status", r => r.fulfill(reply({ trading_allowed: true })));
    await page.route("**/api/risk/kill-switch/status", r => r.fulfill(reply({ engaged: false })));
    await page.route("**/api/trade-desk/kill-switch", r => r.fulfill(reply({ engaged: false })));
    await page.route("**/api/paper-trade/positions", r => r.fulfill(reply({
      positions: Array.from({ length: openPositions }, (_, i) => ({ symbol: `P${i}`, status: "open" })),
    })));
  }
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/terminal/equity");
}

test("candidates are ranked by score and a low-confidence idea is folded into Blocked", async ({ page }) => {
  await open(page, { desk: true });
  const cands = page.getByRole("region", { name: "Candidates" });
  await expect(cands.locator(".signal-ledger-row")).toHaveCount(2);
  await expect(cands.locator(".signal-ledger-row").first()).toContainText("AAA");
  await expect(cands.locator(".signal-ledger-row").nth(1)).toContainText("BBB");
  await expect(cands.locator(".signal-status--clear").first()).toContainText("Clear");
  const blocked = group(page, /^Blocked/);
  await expect(blocked).not.toHaveAttribute("open", "");
  await blocked.locator("summary.signal-group-head").click();
  await expect(blocked.locator(".signal-ledger-row")).toHaveCount(1);
  await expect(blocked.locator(".signal-status--blocked")).toContainText("Blocked · below floor");
  await expect(page.locator(".signals-summary")).toContainText("3 ideas");
});

test("HOLD and earnings-gated ideas are not mixed into the candidates", async ({ page }) => {
  await open(page, { desk: true });
  const parked = group(page, /^Not actionable/);
  await parked.locator("summary.signal-group-head").click();
  await expect(parked.locator(".signal-ledger-row")).toHaveCount(2);
  await expect(parked).toContainText("Earnings gate");
  await expect(parked).toContainText("Hold");
  await expect(page.getByRole("region", { name: "Candidates" })).not.toContainText("HHH");
  await expect(page.getByRole("region", { name: "Candidates" })).not.toContainText("EEE");
});

test("a full book moves every idea to Blocked, which opens itself and says so", async ({ page }) => {
  await open(page, { desk: true, openPositions: 3 });
  await expect(page.getByRole("region", { name: "Candidates" })).toHaveCount(0);
  const blocked = group(page, /^Blocked/);
  await expect(blocked).toHaveAttribute("open", "");
  await expect(blocked.locator(".signal-status--blocked").first()).toContainText("max positions 3/3");
  await expect(page.getByText(/None of the 3 ideas can be taken right now/)).toBeVisible();
});

test("direction filter narrows the candidates", async ({ page }) => {
  await open(page, { desk: true });
  await page.getByRole("button", { name: /^Sell/ }).click();
  const cands = page.getByRole("region", { name: "Candidates" });
  await expect(cands.locator(".signal-ledger-row")).toHaveCount(1);
  await expect(cands).toContainText("BBB");
  await expect(page.getByRole("button", { name: /^Sell/ })).toHaveAttribute("aria-pressed", "true");
});

test("when the desk state cannot be read, rows say 'Not checked', never 'Clear'", async ({ page }) => {
  await open(page, { desk: false });
  await expect(page.getByText(/Desk state could not be read/)).toBeVisible();
  await expect(page.locator(".signal-status--clear")).toHaveCount(0);
  await expect(page.locator(".signal-status--unknown").first()).toContainText("Not checked");
});

test("the table shows move and stop distance on desktop and fits a phone", async ({ page }) => {
  await open(page, { desk: true });
  const first = page.getByRole("region", { name: "Candidates" }).locator(".signal-ledger-row").first();
  await expect(first.locator(".signal-ledger-move")).toContainText("+10.0%");
  await expect(first.locator(".signal-ledger-stop")).toContainText("−5.0%");
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await expectNoHorizontalOverflow(page, width);
    await expect(first.locator(".signal-ledger-move")).toBeHidden();
    await expect(first.locator(".signal-status")).toBeVisible();
  }
});

for (const width of [1280, 390]) {
  test(`Options / Equities sit at the left, lined up with the search box at ${width}px`, async ({ page }) => {
    await open(page, { desk: true, width });
    const options = await page.getByRole("button", { name: "OPTIONS", exact: true }).boundingBox();
    const search = await page.getByRole("textbox", { name: "Search signals" }).boundingBox();
    expect(options).not.toBeNull();
    expect(search).not.toBeNull();
    // The first button starts at the content's left edge (within the button's own padding).
    expect(Math.abs(options!.x - search!.x)).toBeLessThanOrEqual(2);
    // ...and it is not centered: it sits well left of the middle of the page.
    expect(options!.x + options!.width).toBeLessThan(width / 2);
  });
}
