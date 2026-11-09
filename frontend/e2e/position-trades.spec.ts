/**
 * Positions vs Trades, in a real browser: the tab is reachable and deep-linkable, each
 * holding shows its reason, and the page fits a phone.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend, expectNoHorizontalOverflow } from "./helpers";

const reply = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const row = (over: Record<string, unknown> = {}) => ({
  id: "t1", status: "open", strategy: "equity", asset_class: "equity", direction: "long", quantity: 7,
  owner: "unattributed (made before ownership existed)", shown_on_positions_page: false,
  entry_date: "2026-11-05T14:30:00+00:00", exit_date: null, approved_by: "autopilot", ...over,
});
const REPORT = {
  viewer_scope: "single-operator", operator_scope: "single-operator", generated_at: new Date().toISOString(),
  broker: { available: true, error: null },
  summary: { holdings: 3, tracked: 1, attention: 2, rows_without_position: 0 },
  items: [
    { symbol: "AMZN", asset_class: "equity", broker_quantity: 7, code: "hidden_from_positions_page", severity: "attention",
      explanation: "An open trade row exists, but it is unattributed (made before ownership existed), not owned by the account you are signed in as. The Positions screen shows only your own rows, so it says UNKNOWN for this holding.",
      positions_page_shows: "UNKNOWN", trades: [row()], claims: [] },
    { symbol: "SPCX", asset_class: "equity", broker_quantity: -18, code: "no_trade_row", severity: "attention",
      explanation: "No trade row was ever recorded for this name, so the app has no record of opening it.",
      positions_page_shows: "UNKNOWN", trades: [],
      claims: [{ asset_class: "equity", state: "unknown", claimed_at: null, reason: "submit timed out" }] },
    { symbol: "QQQ", asset_class: "equity", broker_quantity: 5, code: "tracked", severity: "ok",
      explanation: "An open trade row exists and agrees with the broker.",
      positions_page_shows: "equity", trades: [row({ id: "t3", owner: "yours", shown_on_positions_page: true, quantity: 5 })], claims: [] },
  ],
};

async function open(page: Page, url: string, width = 1280, mode: "ok" | "forbidden" | "broker-down" = "ok") {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/admin/position-trades", r => {
    if (mode === "forbidden") return r.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "This view reads across owners and is for the operator only." }) });
    if (mode === "broker-down") return r.fulfill(reply({ ...REPORT, items: [], summary: null, broker: { available: false, error: "ConnectionError: gateway down" } }));
    return r.fulfill(reply(REPORT));
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(url);
}

test("the System page has a Positions vs Trades tab that opens it and puts it in the URL", async ({ page }) => {
  await open(page, "/terminal/system/broker");
  const tab = page.getByRole("tab", { name: "Positions vs Trades" });
  await expect(tab).toBeVisible();
  await tab.click();
  await expect(page).toHaveURL(/\/terminal\/system\/positions$/);
  await expect(page.getByRole("region", { name: "AMZN equity" })).toBeVisible();
});

test("the URL opens the tab directly and each holding shows its reason and what the screen shows", async ({ page }) => {
  await open(page, "/terminal/system/positions");
  await expect(page.getByRole("tab", { name: "Positions vs Trades" })).toHaveAttribute("aria-selected", "true");
  const amzn = page.getByRole("region", { name: "AMZN equity" });
  await expect(amzn).toContainText("Hidden from the Positions screen");
  await expect(amzn).toContainText("The Positions screen shows the strategy as: UNKNOWN");
  await expect(amzn).toContainText("unattributed (made before ownership existed)");
  await expect(page.getByRole("region", { name: "SPCX equity" })).toContainText("Entry claim: unknown · submit timed out");
  await expect(page.getByRole("region", { name: "QQQ equity" })).toContainText("✓ OK · Tracked");
  await expect(page.getByText(/3 holdings · 1 tracked/)).toContainText("2 to look at");
});

test("the operator-only refusal is shown as such, not as an empty list", async ({ page }) => {
  await open(page, "/terminal/system/positions", 1280, "forbidden");
  await expect(page.getByRole("alert")).toContainText("This view is for the operator account only.");
  await expect(page.getByText(/No holdings/)).toHaveCount(0);
});

test("an unreadable broker is reported, never shown as no holdings", async ({ page }) => {
  await open(page, "/terminal/system/positions", 1280, "broker-down");
  await expect(page.getByRole("alert")).toContainText("Holdings are unknown, which is not the same as none.");
  await expect(page.getByText(/No holdings at the broker/)).toHaveCount(0);
});

for (const width of [390, 1280]) {
  test(`positions vs trades fits at ${width}px`, async ({ page }) => {
    await open(page, "/terminal/system/positions", width);
    await expect(page.getByRole("region", { name: "AMZN equity" })).toBeVisible();
    await expectNoHorizontalOverflow(page, width);
  });
}
