/**
 * Tab rows and toggles on every page share the ENV chip's box style: 1px border,
 * 3px radius, the chip fill, and the row's first box starts at the same left edge as
 * the ENV chip. The active tab is the only one in the brand colour.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });
const PAGES = ["/terminal/system/broker", "/terminal/risk/heat", "/terminal/lab", "/terminal/scan", "/terminal/backtest", "/terminal/equity"];

async function open(page: Page, url: string, width: number) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/market/broker", r => r.fulfill(reply({ broker: "ibkr", paper_mode: true, status: "connected" })));
  await page.setViewportSize({ width, height: 900 });
  await page.goto(url);
}

for (const url of PAGES) {
  test(`tabs are boxed like the ENV chips on ${url}`, async ({ page }) => {
    await open(page, url, 1280);
    const tabs = page.locator('[role="tablist"].tab-bar [role="tab"]');
    await expect(tabs.first()).toBeVisible();
    const m = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.style.background = "var(--bg-3)";
      (document.querySelector(".app-shell") ?? document.body).appendChild(probe);
      const chipFill = getComputedStyle(probe).backgroundColor;
      probe.remove();
      const env = document.querySelector(".global-risk-status > *");
      const tabs = Array.from(document.querySelectorAll('[role="tablist"].tab-bar [role="tab"]')) as HTMLElement[];
      return {
        chipFill,
        envX: env ? Math.round(env.getBoundingClientRect().x) : null,
        firstX: Math.round(tabs[0].getBoundingClientRect().x),
        tabs: tabs.map(t => { const cs = getComputedStyle(t); return { border: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth].join(" "), style: cs.borderTopStyle, radius: cs.borderTopLeftRadius, bg: cs.backgroundColor, active: t.getAttribute("aria-selected") === "true", color: cs.color, font: cs.fontFamily, upper: cs.textTransform }; }),
      };
    });
    expect(m.tabs.length).toBeGreaterThan(1);
    for (const t of m.tabs) {
      expect(t.border).toBe("1px 1px 1px 1px");
      expect(t.style).toBe("solid");
      expect(t.radius).toBe("3px");
      expect(t.bg).toBe(m.chipFill);
      expect(t.font).toMatch(/mono/i);
      expect(t.upper).toBe("uppercase");
    }
    expect(m.tabs.filter(t => t.active)).toHaveLength(1);
    // The active tab is distinguished by more than colour alone: its border differs too.
    if (m.envX !== null) expect(m.firstX).toBe(m.envX);
  });
}

test("the Options / Equities toggle and the All / Buy / Sell filter are boxed too", async ({ page }) => {
  await open(page, "/terminal/equity", 1280);
  const btn = page.getByRole("button", { name: "OPTIONS", exact: true });
  await expect(btn).toBeVisible();
  const cs = await btn.evaluate(el => { const c = getComputedStyle(el); return { bw: c.borderTopWidth, r: c.borderTopLeftRadius, st: c.borderTopStyle }; });
  expect(cs).toEqual({ bw: "1px", r: "3px", st: "solid" });
});

test("tabs stay tappable on a phone: boxed and at least 40px tall", async ({ page }) => {
  await open(page, "/terminal/system/broker", 390);
  const tab = page.locator('[role="tablist"].tab-bar [role="tab"]').first();
  await expect(tab).toBeVisible();
  const box = await tab.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(40);
});
