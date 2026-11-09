/**
 * The Session strip and the tab rows line up with the "Find workspace" box above them, on a phone
 * and on desktop: the same left edge, the same bordered boxes, and (on a phone) the kill-switch
 * control ending where the Find box ends. The strips are scrollable, so each is measured from
 * its scroll start.
 */
import { test, expect, type Page } from "@playwright/test";
import { stubBackend } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

async function open(page: Page, url: string, width: number) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/market/broker", r => r.fulfill(reply({ broker: "ibkr", paper_mode: true, status: "connected" })));
  await page.setViewportSize({ width, height: 900 });
  await page.goto(url);
  await expect(page.locator(".workspace-command-search input")).toBeVisible();
}

const boxInfo = (page: Page, sel: string) => page.evaluate((s) => {
  const el = document.querySelector(s) as HTMLElement;
  const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
  return { x: Math.round(r.x), right: Math.round(r.right), bw: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth].join(" "), st: cs.borderTopStyle, radius: cs.borderTopLeftRadius };
}, sel);

for (const width of [390, 1280]) {
  test(`Session and tab rows share the Find workspace box's left edge at ${width}px`, async ({ page }) => {
    await open(page, "/terminal/trade/positions", width);
    await page.evaluate(() => document.querySelectorAll(".desk-tablist").forEach(el => { (el as HTMLElement).scrollLeft = 0; }));
    const find = await boxInfo(page, ".workspace-command-search input");
    const session = await boxInfo(page, ".instrument-rail .instrument-chip");
    const tab = await boxInfo(page, ".desk-tablist .desk-tab");
    expect(session.x, "Session box left edge").toBe(find.x);
    expect(tab.x, "first tab left edge").toBe(find.x);
    for (const [name, b] of Object.entries({ session, tab })) {
      expect(b.bw, `${name} border`).toBe("1px 1px 1px 1px");
      expect(b.st, `${name} border style`).toBe("solid");
      expect(b.radius, `${name} radius`).toBe("3px");
    }
  });
}

test("on a phone the kill-switch control ends where the Find workspace box ends", async ({ page }) => {
  await open(page, "/terminal/trade/positions", 390);
  const find = await boxInfo(page, ".workspace-command-search input");
  const kill = await boxInfo(page, ".instrument-rail-kill");
  expect(kill.right).toBe(find.right);
});

test("tab rows on other pages also start at the Find workspace box's left edge on a phone", async ({ page }) => {
  await open(page, "/terminal/system/broker", 390);
  await page.evaluate(() => document.querySelectorAll('[role="tablist"]').forEach(el => { (el as HTMLElement).scrollLeft = 0; }));
  const find = await boxInfo(page, ".workspace-command-search input");
  const tab = await boxInfo(page, '[role="tablist"].tab-bar [role="tab"]');
  expect(tab.x).toBe(find.x);
});
