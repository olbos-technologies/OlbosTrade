import { expect, test } from "@playwright/test";
import { SIGNED_IN, stubBackend } from "./helpers";

const claim = (symbol: string, token: string) => ({
  claim_token: token, scope: "s", underlying: symbol, asset_class: "equity", state: "submitted",
  idempotency_key: `oc-${token}`, dispatch_id: null, unresolved_reason: null,
  claimed_at: "2026-11-06T15:00:00Z", submitted_at: "2026-11-06T15:00:05Z",
  releasing_allows_a_new_entry: true,
});

async function openPage(page: import("@playwright/test").Page, width: number) {
  await stubBackend(page, SIGNED_IN);
  await page.route("**/api/paper-trade/positions", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ positions: [] }) }));
  await page.route("**/api/admin/position-claims", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ count: 2, claims: [claim("ADP", "11111111-1111-4111-8111-aaaaaaaaaaaa"), claim("TTWO", "22222222-2222-4222-8222-bbbbbbbbbbbb")] }) }));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/terminal/system/claims");
}

for (const width of [820, 1280, 1971]) {
  test(`blocked entries: list, gated release of one claim, no overflow at ${width}px`, async ({ page }) => {
    let posted: { url: string; body: string } | null = null;
    await openPage(page, width);
    await page.route("**/api/admin/position-claims/*/release", async (route) => {
      posted = { url: route.request().url(), body: route.request().postData() || "" };
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ released: true, claim_token: "x", operator: "operator@example.test" }) });
    });

    await expect(page.getByText("2 blocked")).toBeVisible();
    const noOverflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    expect(noOverflow, "the page must not scroll sideways").toBe(true);

    await page.getByRole("button", { name: "Release claim for ADP" }).click();
    const dlg = page.getByRole("group", { name: "Release the claim on ADP" });
    const go = dlg.getByRole("button", { name: "Release claim" });
    await expect(go).toBeDisabled();
    await dlg.getByRole("textbox").first().fill("no order or position in IBKR");
    await dlg.getByRole("checkbox").check();
    await expect(go).toBeEnabled();

    // The panel is on screen and its button is genuinely clickable (hit-tested).
    await go.click();
    await expect.poll(() => posted?.url ?? "").toContain("11111111-1111-4111-8111-aaaaaaaaaaaa/release");
    expect(JSON.parse(posted!.body)).toEqual({ reason: "no order or position in IBKR" });
    await expect(page.getByText(/Released the claim on ADP/)).toBeVisible();
  });
}

test("blocked entries is reachable from the sidebar", async ({ page }) => {
  await openPage(page, 1280);
  await expect(page.getByRole("button", { name: /Blocked Entries/i }).first()).toBeVisible();
});
