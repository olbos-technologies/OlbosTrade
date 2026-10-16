/**
 * The terminal shell must fit a phone on every page a phone can reach.
 *
 * ── Why these navigate instead of using URLs ─────────────────────────────
 *
 * An earlier version of this file looped over /terminal/markets,
 * /terminal/risk and six more, and measured the same Dashboard eight times.
 * App.tsx held the active page in `useState("dashboard")` and never read
 * location.pathname, so every /terminal/* URL mounted the same page. Eight
 * tests, one page, and a green tick that meant nothing.
 *
 * So these drive the real navigation a phone user drives: the bottom nav, and
 * the drawer for what the bottom nav does not carry. That covers distinct
 * pages AND exercises the nav itself, which is where the shell bugs have
 * actually been.
 *
 * (The URL not restoring the page WAS a genuine app limitation when this file
 * was written — every /terminal/* URL rendered the Dashboard. That is fixed;
 * see terminalRoutes.ts and deep-linking.spec.ts. These tests still navigate
 * rather than deep-link, because what they measure is the shell's layout on
 * each page, and driving the real nav also exercises the nav itself.)
 *
 * ── Scope ────────────────────────────────────────────────────────────────
 *
 * This measures the SHELL — header, ticker strip, nav drawer, status bar,
 * bottom nav. The backend is stubbed, so panels render their ErrorBoundary
 * fallback rather than real data. That keeps runs hermetic and deterministic
 * while still exercising every part a horizontal overflow comes from.
 */

import { test, expect, type Page } from "@playwright/test";
import { expectNoHorizontalOverflow, stubBackend, SIGNED_IN } from "./helpers";

const PHONE_WIDTH = 390;
const NARROW_WIDTH = 320;
const WIDTHS = [PHONE_WIDTH, NARROW_WIDTH];

/** Pages a phone can actually reach, and how it reaches them. */
const PAGES: Array<{ label: string; via: "bottom" | "drawer" }> = [
  { label: "Home", via: "bottom" },
  { label: "Desk", via: "bottom" },
  { label: "Signals", via: "bottom" },
  { label: "Positions", via: "bottom" },
  { label: "Risk", via: "bottom" },
  { label: "Journal", via: "drawer" },
  { label: "Performance", via: "drawer" },
];

const burger = (page: Page) =>
  page.getByRole("button", { name: /toggle navigation/i });

async function openTerminal(page: Page, width: number) {
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/terminal/dashboard");
  await page.waitForSelector(".mobile-bottom-nav");
}

async function navigateTo(page: Page, label: string, via: "bottom" | "drawer") {
  if (via === "bottom") {
    await page.locator(".mobile-bottom-nav").getByRole("button", { name: label, exact: true }).click();
  } else {
    await burger(page).click();
    await page.getByText(new RegExp(`^${label}$`, "i")).first().click();
  }
  // The drawer animates out over 0.2s after a selection; measuring mid-slide
  // would read a transient geometry rather than the settled layout.
  await page.waitForTimeout(400);
}

for (const width of WIDTHS) {
  test.describe(`terminal shell at ${width}px`, () => {
    for (const { label, via } of PAGES) {
      test(`no horizontal overflow on ${label}`, async ({ page }) => {
        await stubBackend(page);
        await openTerminal(page, width);
        await navigateTo(page, label, via);
        await expectNoHorizontalOverflow(page, width);
      });
    }
  });
}

/**
 * How far the drawer has slid in, measured as the right edge of one of its
 * rows.
 *
 * Open/closed CANNOT be asserted with toBeVisible() here, and getting that
 * wrong is the easy mistake: on mobile the sidebar is always mounted and is
 * merely pushed off with `transform: translateX(-100%)`. A translated element
 * still has a non-empty box and `visibility: visible`, so Playwright considers
 * it visible whether the drawer is open or shut — an assertion on visibility
 * passes in both states and tests nothing.
 */
async function drawerRowRightEdge(page: Page): Promise<number> {
  const box = await page.getByText(/^Journal$/i).first().boundingBox();
  return box ? box.x + box.width : NaN;
}

test.describe("mobile nav drawer", () => {
  test("opens, its rows are actually clickable, and it closes on tap-away", async ({ page }) => {
    await stubBackend(page);
    await openTerminal(page, PHONE_WIDTH);

    await expect.poll(() => drawerRowRightEdge(page)).toBeLessThanOrEqual(0);

    // Addressed by its accessible name, not by DOM order. `button.first()`
    // would silently bind to whatever button happens to come first, so adding
    // any control above the header would leave this test green while
    // exercising the wrong element.
    await burger(page).click();
    await expect.poll(() => drawerRowRightEdge(page), { timeout: 5_000 }).toBeGreaterThan(0);

    // Selecting a destination closes the overlay — otherwise it covers the page
    // the user just asked for. The click itself is the hit-test.
    await page.getByText(/^Journal$/i).first().click();
    await expect.poll(() => drawerRowRightEdge(page), { timeout: 5_000 }).toBeLessThanOrEqual(0);

    // Tapping the backdrop must also close it. Without one the drawer covers
    // the page with no obvious way out.
    await burger(page).click();
    await expect.poll(() => drawerRowRightEdge(page), { timeout: 5_000 }).toBeGreaterThan(0);
    await page.mouse.click(PHONE_WIDTH - 30, 400);
    await expect.poll(() => drawerRowRightEdge(page), { timeout: 5_000 }).toBeLessThanOrEqual(0);
  });
});

/**
 * Sign-out on a phone, from the Account group where identity controls belong.
 *
 * The identity chip used to consume scarce room in the bottom status strip.
 * It now lives in the scrollable Account group, and its menu still has to
 * escape drawer clipping so sign-out remains reachable on a phone.
 *
 * Mutation testing pinned down which half of the fix actually holds it up.
 * Removing the portal alone still passes: the menu is `position: fixed`, and a
 * fixed element escapes an ancestor's overflow clipping whether or not it is
 * portalled. Reverting the positioning too — inline AND absolute, the original
 * shape — fails here with "intercepts pointer events". So this guards the
 * POSITIONING; the portal is belt-and-braces on top of it.
 *
 * It needs auth ENABLED. UserMenu renders null on a single-operator install,
 * so with the default auth-disabled stub none of this exists and a test here
 * would pass against the bug and against the fix alike. Clicking is what
 * proves it: Playwright hit-tests the click point, so a menu that is clipped
 * or painted behind the page fails rather than quietly satisfying a
 * visibility check.
 */
test.describe("sign out on a phone", () => {
  test("the account menu opens and its Sign out control is reachable", async ({ page }) => {
    await stubBackend(page, SIGNED_IN);

    let signOutCalled = false;
    await page.route("**/api/auth/logout", (route) => {
      signOutCalled = true;
      return route.fulfill({
        status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }),
      });
    });

    await openTerminal(page, PHONE_WIDTH);

    // Auth has resolved (the gated Account group exists), but the identity
    // chip must not occupy the bottom status strip anymore.
    await expect(page.getByRole("button", { name: "Account", exact: true })).toHaveCount(1);
    await expect(page.getByRole("button", { name: /^Account:/ })).toHaveCount(0);

    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("button", { name: "Account", exact: true }).click();

    const account = page.getByRole("button", { name: /^Account:/ });
    await expect(
      account,
      "the account control never mounted — auth stub is not being applied"
    ).toBeVisible();
    await account.click();

    // "menuitem", not "button": the control is a <button role="menuitem">, and
    // an explicit role overrides the implicit one, so getByRole("button") does
    // not match it at all.
    //
    // Not .toBeVisible() either: the bug this guards produced an element that
    // passed visibility checks and painted nothing. The click is the real
    // assertion, because Playwright hit-tests before dispatching.
    await page.getByRole("menuitem", { name: /sign out/i }).click();

    await expect
      .poll(() => signOutCalled, { timeout: 5_000 })
      .toBe(true);
  });
});
