/**
 * A terminal URL must open the page it names.
 *
 * Until `terminalRoutes.ts` existed, App held its page in useState("dashboard")
 * and never read the pathname, so every /terminal/* URL rendered the Dashboard
 * while the address bar claimed otherwise. Reload, bookmark, share and Back
 * were all silently wrong — and "silently" is the point: nothing failed, you
 * just got a different page than the one you asked for.
 *
 * These are browser tests rather than unit tests because the thing under test
 * IS the browser's own machinery: a real address bar, a real reload, and a real
 * session history. jsdom has none of those.
 */

import { test, expect, type Page } from "@playwright/test";
import { stubBackend } from "./helpers";

/**
 * Each URL and the status-bar label the shell should show for it.
 *
 * The label is the assertion rather than page content, and deliberately: the
 * backend is stubbed, so some pages render their ErrorBoundary instead of real
 * content (ChartWorkstation does). Asserting on content would then fail for a
 * reason that has nothing to do with routing. The status bar is derived from
 * the app's own `activePage`, which is now derived from the URL — so if
 * routing regressed to "everything is the dashboard", every row below reads
 * COMMAND CENTER and every row fails. That is exactly the bug being guarded.
 */
const PAGES = [
  { url: "/terminal/journal",        label: "JOURNAL" },
  { url: "/terminal/risk",           label: "PORTFOLIO & RISK" },
  { url: "/terminal/markets/chart",  label: "MARKETS · CHART" },
  { url: "/terminal/trade/overview", label: "TRADE DESK · COMMAND OVERVIEW" },
];

async function open(page: Page, url: string, width = 1280) {
  await stubBackend(page);
  await page.setViewportSize({ width, height: 900 });
  await page.goto(url);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(800);
}

/**
 * What the shell says is open.
 *
 * NOT document.body: the first few hundred characters of every terminal page
 * are the same ticker strip, so a marker check against the body passes or
 * fails on chrome that is identical across all pages — it looks like a page
 * assertion and is not one.
 */
async function statusLabel(page: Page): Promise<string> {
  return page.locator(".instrument-status span").first().innerText();
}

/** The text of the page body, for the cases where the page itself renders. */
async function pageText(page: Page): Promise<string> {
  return page.evaluate(() => {
    const main = document.querySelector("main");
    return (main instanceof HTMLElement ? main.innerText : "") || "";
  });
}

test.describe("a terminal URL opens the page it names", () => {
  for (const { url, label } of PAGES) {
    test(`${url} opens its own page, not the dashboard`, async ({ page }) => {
      await open(page, url);
      expect(
        await statusLabel(page),
        `${url} did not open its own page — this is the deep-linking bug, ` +
        `where every /terminal/* URL rendered the Dashboard`
      ).toBe(label);
      expect(page.url()).toContain(url);
    });
  }

  test("a bare /terminal opens the dashboard", async ({ page }) => {
    // Every CTA on the marketing page links here.
    await open(page, "/terminal");
    expect(await statusLabel(page)).toBe("COMMAND CENTER");
  });

  test("an unknown page says so instead of quietly showing another one", async ({ page }) => {
    await open(page, "/terminal/not/a/real/page");
    expect(await pageText(page)).toMatch(/page unavailable/i);
    // The URL must stay put: a silent rewrite to the dashboard is the exact
    // behaviour this change removes.
    expect(page.url()).toContain("/terminal/not/a/real/page");
  });
});

test.describe("the URL tracks in-app navigation", () => {
  test("clicking a nav item changes the address bar, and reload stays put", async ({ page }) => {
    // Phone width: the drawer toggle only carries an accessible name in the
    // mobile header.
    await open(page, "/terminal/dashboard", 390);

    await page.getByRole("button", { name: /toggle navigation/i }).click();
    await page.getByText(/^Journal$/i).first().click();
    await page.waitForTimeout(500);

    expect(page.url(), "navigating did not update the URL").toContain("/terminal/journal");

    // The reload is the assertion that matters: before the fix this landed on
    // the Dashboard no matter what the address bar said.
    await page.reload();
    await page.waitForTimeout(800);
    expect(page.url()).toContain("/terminal/journal");
    expect(await statusLabel(page)).toBe("JOURNAL");
  });

  test("the browser Back button returns to the previous page", async ({ page }) => {
    // Phone width: the drawer toggle only carries an accessible name in the
    // mobile header.
    await open(page, "/terminal/dashboard", 390);

    await page.getByRole("button", { name: /toggle navigation/i }).click();
    await page.getByText(/^Journal$/i).first().click();
    await page.waitForTimeout(500);
    expect(page.url()).toContain("/terminal/journal");

    await page.goBack();
    await page.waitForTimeout(600);
    expect(page.url(), "Back did not return to the previous terminal page").toContain("/terminal/dashboard");

    await page.goForward();
    await page.waitForTimeout(600);
    expect(page.url()).toContain("/terminal/journal");
  });
});

/**
 * URLs that must not be able to break the terminal.
 *
 * Found in review. `PAGES[page]` with a key straight from the URL also finds
 * everything on Object.prototype, so these were not merely wrong pages:
 *
 *   /terminal/constructor  ->  React error #31, page ErrorBoundary catches it
 *   /terminal/__proto__    ->  React error #130, and the WHOLE SHELL dies —
 *                              no status bar, no nav, nothing to click
 *
 * The second is the serious one: it is outside the page ErrorBoundary, so any
 * visitor could white-screen the terminal by typing a URL. Browser tests
 * because a crash that escapes an ErrorBoundary is only observable by actually
 * rendering the app.
 */
test.describe("hostile URLs cannot break the terminal", () => {
  for (const key of ["constructor", "__proto__", "toString", "valueOf", "hasOwnProperty"]) {
    test(`/terminal/${key} renders Page unavailable, not a crash`, async ({ page }) => {
      const crashes: string[] = [];
      page.on("pageerror", (e) => crashes.push(String(e)));

      await open(page, `/terminal/${key}`);

      // The shell must still be there. When __proto__ took out the render,
      // this locator found nothing at all.
      await expect(
        page.locator(".instrument-status"),
        `the shell did not render for /terminal/${key} — the whole terminal is down`
      ).toBeVisible();

      expect(await pageText(page)).toMatch(/page unavailable/i);
      expect(crashes, `uncaught error on /terminal/${key}: ${crashes[0]}`).toEqual([]);
    });
  }
});

test.describe("URL spellings that should still resolve", () => {
  test("a percent-encoded separator opens the same page", async ({ page }) => {
    // Some clients encode "/" when a URL is pasted or logged.
    await open(page, "/terminal/markets%2Fchart");
    expect(await statusLabel(page)).toBe("MARKETS · CHART");
  });

  test("an upper-case base path opens the page", async ({ page }) => {
    // React Router matches /terminal/* case-insensitively, so this mounts the
    // terminal; the key parsing has to agree with the router that accepted it.
    await open(page, "/TERMINAL/risk");
    expect(await statusLabel(page)).toBe("PORTFOLIO & RISK");
  });
});

test.describe("history has one entry per real navigation", () => {
  test("clicking the page you are already on does not stack history", async ({ page }) => {
    await open(page, "/terminal/dashboard", 390);
    await page.waitForSelector(".mobile-bottom-nav");

    const historyLength = () => page.evaluate(() => history.length);
    const before = await historyLength();

    const home = page.locator(".mobile-bottom-nav").getByRole("button", { name: "Home", exact: true });
    for (let i = 0; i < 3; i++) {
      await home.click();
      await page.waitForTimeout(250);
    }

    expect(
      await historyLength(),
      "clicking the active nav item pushed history entries — Back then appears " +
      "broken because it returns to the URL it is already on"
    ).toBe(before);
  });

  async function selectRisk(page: Page) {
    await page.locator(".mobile-bottom-nav").getByRole("button", {name:"More", exact:true}).click();
    const target=page.getByRole("button", {name:"Risk Overview", exact:true});
    if(await target.count()===0) await page.getByRole("button", {name:"Risk", exact:true}).click();
    await target.click();
  }

  test("Back still works after clicking around", async ({ page }) => {
    await open(page, "/terminal/dashboard", 390);
    await page.waitForSelector(".mobile-bottom-nav");
    await selectRisk(page);
    await page.waitForTimeout(400);
    await selectRisk(page);  // no-op
    await page.waitForTimeout(400);

    await page.goBack();
    await page.waitForTimeout(600);
    expect(page.url()).toContain("/terminal/dashboard");
  });

  test("an alias of the page you are on does not stack history either", async ({ page }) => {
    // /terminal/risk and /terminal/risk/heat are both RiskCenter's monitor
    // tab, and Risk Overview in More navigates to risk:heat. Arriving on the
    // bare URL and clicking it therefore looked like a real navigation to a
    // key-only comparison: it pushed an entry, and Back returned to a view
    // identical to the one you were already looking at.
    await open(page, "/terminal/risk", 390);
    await page.waitForSelector(".mobile-bottom-nav");

    const historyLength = () => page.evaluate(() => history.length);
    const before = await historyLength();

    await selectRisk(page);
    await page.waitForTimeout(400);

    expect(
      await historyLength(),
      "navigating to an alias of the current page pushed a history entry — " +
      "Back now returns to an identical view"
    ).toBe(before);

    // The URL is still allowed to canonicalise (risk -> risk:heat), which adds
    // the leaf name to the status label — so assert the page, not the exact
    // string. What must not change is the history depth, above.
    expect(await statusLabel(page)).toMatch(/^PORTFOLIO & RISK/);
  });
});
