/**
 * A hub page's TAB belongs in the URL too.
 *
 * #58 put the page in the URL. One level down, the hub pages kept their active
 * tab in local state and told nobody: clicking a tab called setTab and stopped
 * there, so the address bar still read /terminal/risk/heat while you were
 * looking at Guardrails. Reload, bookmark, share and Back then restored the
 * page but not the tab — the same class of silent wrongness, one level in.
 *
 * Most of these tabs already had their own page key in App.tsx (risk:heat and
 * risk:rules are both RiskCenter), so the URL could always name them. Nothing
 * was routing the change through it.
 *
 * Browser tests because the claim is about the address bar, a real reload and
 * real session history. jsdom has none of those.
 *
 * NOTE: the tab control is `role="tab"`, not a button. An explicit role
 * overrides the implicit one, so getByRole("button") matches nothing here —
 * the same trap as the account menu's `role="menuitem"`.
 */

import { test, expect, type Page } from "@playwright/test";
import { stubBackend } from "./helpers";

/** Hub pages, the tab to click, and the URL each should produce. */
const HUBS = [
  {
    name: "Risk",
    from: "/terminal/risk/heat",
    tab: /guardrails/i,
    to: "/terminal/risk/rules",
    label: "PORTFOLIO & RISK \u00b7 RISK RULES",
  },
  {
    name: "System",
    from: "/terminal/system/broker",
    tab: /data quality/i,
    to: "/terminal/system/quality",
    label: "DATA & INTEGRATIONS \u00b7 DATA QUALITY",
  },
  {
    name: "Signals",
    from: "/terminal/equity",
    tab: /strategy library/i,
    to: "/terminal/strat/cards",
    label: "STRATEGIES \u00b7 STRATEGY CARDS",
  },
];

/**
 * ResearchCenter is covered in vitest, not here, and that is a limitation of
 * the STUB rather than a gap in the fix.
 *
 * stubBackend answers every /api/** with `{}`, and ScenarioLab reads a nested
 * field off that empty object and throws ("Cannot read properties of undefined
 * (reading 'toLowerCase')"). Its ErrorBoundary catches it and renders "Page
 * content failed to render" — so the tab bar never mounts and there is nothing
 * to click. Risk, System and Signals tolerate the empty payload; Research does
 * not.
 *
 * Giving Research a hand-shaped fixture would make this test depend on one
 * page's response schema, which is exactly the coupling the flat `{}` stub
 * avoids. The tab wiring it would prove is instead asserted in
 * src/hooks/__tests__/useTabRoute.test.tsx, which checks every hub's tab table
 * against the real page registry — Research included.
 */

async function open(page: Page, url: string) {
  await stubBackend(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(url);
  await page.waitForSelector(".instrument-status");
  await page.waitForTimeout(700);
}

const statusLabel = (page: Page) =>
  page.locator(".instrument-status span").first().innerText();

const selectedTab = (page: Page) =>
  page.getByRole("tab", { selected: true }).first().innerText();

test.describe("a tab change reaches the URL", () => {
  for (const hub of HUBS) {
    test(`${hub.name}: clicking a tab updates the address bar`, async ({ page }) => {
      await open(page, hub.from);
      await page.getByRole("tab", { name: hub.tab }).first().click();
      await page.waitForTimeout(600);

      expect(
        page.url(),
        `clicking the ${hub.name} tab left the URL at ${hub.from} — the tab is ` +
        `still only in component state, so reload and Back cannot restore it`
      ).toContain(hub.to);
      expect(await statusLabel(page)).toBe(hub.label);
    });

    test(`${hub.name}: the tab survives a reload`, async ({ page }) => {
      await open(page, hub.from);
      await page.getByRole("tab", { name: hub.tab }).first().click();
      await page.waitForTimeout(600);

      const chosen = await selectedTab(page);
      await page.reload();
      await page.waitForTimeout(900);

      expect(page.url()).toContain(hub.to);
      expect(
        await selectedTab(page),
        "reloading dropped back to the page's default tab"
      ).toBe(chosen);
    });
  }
});

test.describe("tab history", () => {
  test("Back returns to the previous tab, not the previous page", async ({ page }) => {
    await open(page, "/terminal/risk/heat");
    const first = await selectedTab(page);

    await page.getByRole("tab", { name: /guardrails/i }).first().click();
    await page.waitForTimeout(600);
    expect(page.url()).toContain("/terminal/risk/rules");

    await page.goBack();
    await page.waitForTimeout(800);

    expect(page.url()).toContain("/terminal/risk/heat");
    expect(
      await selectedTab(page),
      "Back changed the URL but left the old tab selected — the two are out of sync"
    ).toBe(first);
  });

  test("clicking the tab you are already on does not stack history", async ({ page }) => {
    // Same rule as the nav items: a no-op must not cost a Back press.
    await open(page, "/terminal/risk/heat");
    const before = await page.evaluate(() => history.length);

    for (let i = 0; i < 3; i++) {
      await page.getByRole("tab", { name: /risk monitor/i }).first().click();
      await page.waitForTimeout(250);
    }

    expect(await page.evaluate(() => history.length)).toBe(before);
  });
});

test.describe("the legacy desk nested inside TradeDeskV2", () => {
  /**
   * TradeDeskV2's Positions tab renders the legacy TradeDesk as a PANEL
   * (TradeDeskV2.tsx). Its tab keys mean something else in that role: with
   * trade_desk_v2 on, `trade:orders` mounts V2's own OrdersWorkspace, not this
   * component's signals tab. So URL-routing the nested tabs navigated the user
   * out of the page they were on.
   *
   * Caught in review, and it only reproduces with the flag in its SHIPPING
   * position, which is why the nested case needs its own test rather than
   * riding on the page-level ones above.
   *
   * These are plain buttons, not role="tab" — the legacy desk predates TabBar.
   */
  test("clicking a nested tab switches the panel without navigating", async ({ page }) => {
    await open(page, "/terminal/trade/positions");
    await page.route("**/api/paper-trade/positions", route => route.fulfill({
      status:200, contentType:"application/json", body:JSON.stringify({positions:[]}),
    }));
    await page.reload();

    const urlBefore = page.url();
    await page.getByText("Additional desk tools & execution settings",{exact:true}).click();
    // "Desk signals" in the DOM; the uppercase is CSS, and the accessible name
    // comes from the text content, not the painted glyphs.
    const deskSignals = page.getByRole("button", { name: /^Desk signals$/i });
    await expect(
      deskSignals,
      "the nested legacy desk did not render — this test is not exercising it"
    ).toBeVisible();
    await expect(page.getByText("No open positions")).toBeVisible();

    await deskSignals.click();
    await page.waitForTimeout(600);

    expect(
      page.url(),
      "a nested panel tab changed the URL, which navigates out of the page it is nested in"
    ).toBe(urlBefore);
    expect(
      await statusLabel(page),
      "the shell moved to a different workspace"
    ).toBe("TRADE DESK \u00b7 POSITIONS");

    // And it must still actually switch the panel — a fix that simply stopped
    // the nested tabs working would also satisfy the two assertions above.
    await expect(
      page.getByText("No open positions"),
      "the panel did not switch away from Positions"
    ).toBeHidden();
  });
});

/**
 * The legacy Trade Desk's unmapped tabs ("Trading style", "Manual Trade") are
 * covered in src/hooks/__tests__/useTabRoute.test.tsx, not here.
 *
 * They only exist when the trade_desk_v2 flag is OFF, and it ships ON, so a
 * browser test for them would skip on every CI run — which is a test that never
 * executes, not a guard. The claim is about the hook's behaviour for a tab with
 * no page key, and the hook can be called directly.
 */
