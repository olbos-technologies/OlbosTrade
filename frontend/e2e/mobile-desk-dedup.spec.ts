/**
 * The desk dedupe, measured in a browser rather than read off the stylesheet.
 *
 * Three bugs in this change were invisible to every unit test and to my own
 * programmatic checks, and all three were CASCADE bugs — a rule losing to an
 * inline style. jsdom applies no stylesheet and does no layout, so a vitest
 * assertion computes the same value whether the rule wins or loses. Reading
 * the CSS text proves a rule EXISTS, never that it TAKES EFFECT.
 *
 *   1. `.status-dup { display: none }` lost to StatusLamp's inline
 *      display: inline-flex, so three lamps stayed on screen.
 *   2. `.global-risk-status { display: none }` lost to that component's
 *      inline display: flex, so the risk strip was never hidden at all. My
 *      probe missed it because I built the probe WITHOUT the inline style the
 *      real component sets — the one detail that mattered.
 *   3. `.instrument-rail-kill button { min-height: 40px }` lost to the
 *      button's inline minHeight: 36, so the kill switch stayed a 36px target
 *      under a rule whose whole point was 44px.
 *
 * So these assert computed values on real elements, with faithful probes where
 * the harness cannot render the component.
 */

import { test, expect, type Page } from "@playwright/test";
import { stubBackend, SIGNED_IN } from "./helpers";

const PHONE = { width: 390, height: 844 };

async function openDesk(page: Page, positions?: unknown[]) {
  await stubBackend(page, SIGNED_IN);
  if (positions) {
    await page.route("**/api/paper-trade/positions", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ positions }),
      }));
  }
  await page.setViewportSize(PHONE);
  await page.goto("/terminal/dashboard");
  await page.waitForSelector(".mobile-bottom-nav");
  await page.locator(".mobile-bottom-nav")
    .getByRole("button", { name: "Desk", exact: true }).click();
  await page.waitForSelector(".app-shell--desk");
}

test("the kill switch is a 44px touch target despite its inline height", async ({ page }) => {
  await openDesk(page);
  const h = await page.locator(".instrument-rail-kill button").evaluate(
    el => Math.round(el.getBoundingClientRect().height));
  expect(h, "the button sets minHeight: 36 inline; the rule must beat it").toBeGreaterThanOrEqual(44);
});

test("the risk strip is hidden even carrying its inline display", async ({ page }) => {
  await openDesk(page);
  // GlobalRiskStatus throws under the stub, so its real element never mounts.
  // The probe reproduces the ONE property that decides this — the inline
  // display the component sets. Without it the probe hides for the wrong
  // reason and reports success against a rule that does not work.
  const display = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "global-risk-status";
    probe.style.display = "flex";
    document.querySelector(".app-shell")!.prepend(probe);
    return getComputedStyle(probe).display;
  });
  expect(display, "an inline display: flex beats a plain rule").toBe("none");
});

test("every duplicated status lamp is hidden on the desk", async ({ page }) => {
  await openDesk(page);
  // Counted, not sampled. querySelector returns the FIRST match, and the first
  // match was the one cell that already worked — which is how this shipped
  // half-applied and looked verified.
  const visible = await page.evaluate(() =>
    [...document.querySelectorAll(".status-dup")]
      .filter(e => getComputedStyle(e).display !== "none").length);
  expect(visible).toBe(0);
});

test("positions hides the redundant execution selector and prioritizes holdings", async ({ page }) => {
  await openDesk(page);
  await page.locator(".mobile-bottom-nav")
    .getByRole("button", { name: "Positions", exact: true }).click();

  const selector = page.locator(".exec-mode-row--mobile-hidden");
  await expect(selector).toHaveCount(1);
  await expect(selector).toHaveCSS("display", "none");

  // The shell-level strip is the one workspace navigator on mobile. The
  // legacy panel's second Overview / Signals / Positions row is redundant.
  const legacyTabs = page.locator(".trade-desk-panel--embedded .legacy-desk-tabbar");
  await expect(legacyTabs).toHaveCount(1);
  await expect(legacyTabs).toHaveCSS("display", "none");

  // The market marquee looked like an empty band in the first viewport. It is
  // hidden only on the desk; other terminal pages keep their market context.
  await expect(page.locator(".mobile-market-ticker")).toHaveCSS("display", "none");

  const strip = page.locator(".trade-desk-mobile-tabs");
  const deskMain = page.locator(".trade-desk-shell > main");
  const order = await page.evaluate(() => {
    const nav = document.querySelector(".trade-desk-mobile-tabs");
    const main = document.querySelector(".trade-desk-shell > main");
    return !!nav && !!main && Boolean(nav.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(order, "the workspace selector should precede the content it changes").toBe(true);

  const activeTab = strip.getByRole("tab", { name: "Positions", exact: true });
  await expect(activeTab).toBeVisible();
  const bounds = await activeTab.evaluate((el) => {
    const tab = el.getBoundingClientRect();
    const scroller = el.parentElement!.getBoundingClientRect();
    return { left: tab.left, right: tab.right, scrollerLeft: scroller.left, scrollerRight: scroller.right };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(bounds.scrollerLeft - 1);
  expect(bounds.right).toBeLessThanOrEqual(bounds.scrollerRight + 1);

  const navLabel = page.locator('.mobile-bottom-nav__tab[aria-label="Positions"] .mobile-bottom-nav__label');
  await expect(navLabel).toHaveText("Positions");
  const labelFits = await navLabel.evaluate((el) => el.scrollWidth <= el.clientWidth + 1);
  expect(labelFits, "Positions must not paint as POSITIO…").toBe(true);

  // Removing the selector must not remove the operator's mode awareness: the
  // Trade Desk Session chip directly above still carries execution state.
  await expect(page.getByText("Session", { exact: true })).toBeVisible();
  await expect(page.locator(".positions-summary")).toBeVisible();
  await expect(page.locator(".t-table--cards")).toBeVisible();
  await expect(deskMain).toBeVisible();
});

test("a position close is reviewed before hold-to-submit", async ({ page }) => {
  await openDesk(page, [{
    id: "trade-adbe",
    symbol: "ADBE",
    asset_type: "equity",
    spread_type: "equity_long",
    strategy: "adopted_untracked",
    tracked: true,
    quantity: 4,
    avg_cost: 231.59,
    unrealized_pnl: -433,
    mfe_pnl: 12,
    mae_pnl: -1153,
    hold_days: 6,
    trading_mode: "balanced",
  }]);
  await page.locator(".mobile-bottom-nav")
    .getByRole("button", { name: "Positions", exact: true }).click();

  await expect(page.getByLabel("Open positions summary")).toContainText("1");
  await expect(page.getByLabel("Open positions summary")).toContainText("-$433");
  await expect(page.getByText(/Adopted from broker/)).toBeVisible();

  // Choosing Close is reversible and opens a review. Submission still needs
  // an intentional hold after the operator verifies symbol and quantity.
  await page.getByRole("button", { name: "Review close ADBE" }).click();
  const dialog = page.getByRole("dialog", { name: /Review close · ADBE/ });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Market close request");
  await expect(dialog).toContainText("Entire open position");
  await expect(dialog).toContainText("history may be incomplete");
  await expect(dialog.getByRole("button", { name: "Hold to submit market close" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeVisible();
});

test("the /terminal/paper alias dedupes too", async ({ page }) => {
  await stubBackend(page, SIGNED_IN);
  await page.setViewportSize(PHONE);
  await page.goto("/terminal/paper");
  await page.waitForSelector(".app-shell");
  // paper renders the same desk shell, so it must get the same treatment;
  // a startsWith("trade:") test missed it.
  await expect(page.locator(".app-shell")).toHaveClass(/app-shell--desk/);
});

test("a legacy rollback keeps the safety lamps", async ({ page }) => {
  // THE DANGEROUS DIRECTION. With trade_desk_v2 off, trade:* renders the
  // legacy TradeDesk, which has no TradeDeskHeader — so deduping there would
  // hide the kill, exec and paper/live indicators and put NOTHING in their
  // place. A dedupe that removes the only kill-switch display is worse than
  // the duplication it removes.
  await stubBackend(page, SIGNED_IN);
  await page.setViewportSize(PHONE);
  await page.addInitScript(() => {
    // olbos.flags. — plural. The singular spelling silently sets nothing and
    // the test then measures the v2 path while claiming to measure legacy.
    try { localStorage.setItem("olbos.flags.trade_desk_v2", "0"); } catch { /* ignore */ }
  });
  await page.goto("/terminal/trade/positions");
  await page.waitForSelector(".app-shell");

  const r = await page.evaluate(() => ({
    hasRail: !!document.querySelector(".instrument-rail"),
    shellClass: document.querySelector(".app-shell")!.className,
    visibleDups: [...document.querySelectorAll(".status-dup")]
      .filter(e => getComputedStyle(e).display !== "none").length,
  }));

  expect(r.hasRail, "legacy is expected to have no rail; if it grew one, revisit this").toBe(false);
  expect(r.shellClass).not.toContain("app-shell--desk");
  expect(r.visibleDups, "the only kill/exec/paper indicators were hidden with no rail to replace them")
    .toBeGreaterThan(0);
});
