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

// The strip scrolls sideways. A tab with enough tabs after it is brought flush to the page margin (the
// Find workspace box's left edge), with the tab before it fully out of view, not left as a cut-off sliver.
test("on a phone the selected Positions tab is brought to the left margin, with no half-cut neighbour", async ({ page }) => {
  await open(page, "/terminal/trade/positions", 390);
  await expect(page.locator('.desk-tablist [role="tab"][aria-selected="true"]')).toBeVisible();
  await page.waitForTimeout(300);
  const m = await page.evaluate(() => {
    const tabs = Array.from(document.querySelectorAll('.desk-tablist [role="tab"]')) as HTMLElement[];
    const i = tabs.findIndex(t => t.getAttribute("aria-selected") === "true");
    const find = document.querySelector(".workspace-command-search input")!.getBoundingClientRect();
    return { findX: find.x, activeX: tabs[i].getBoundingClientRect().x, prevRight: tabs[i - 1].getBoundingClientRect().right };
  });
  expect(Math.abs(m.activeX - m.findX)).toBeLessThanOrEqual(1);
  expect(m.prevRight).toBeLessThanOrEqual(m.findX + 1); // behind the margin, nothing peeking in
});

test("on a phone a tab near the end scrolls as far as the strip allows, never past it", async ({ page }) => {
  await open(page, "/terminal/trade/settings", 390);
  await expect(page.locator('.desk-tablist [role="tab"][aria-selected="true"]')).toBeVisible();
  await page.waitForTimeout(300);
  const m = await page.evaluate(() => {
    const list = document.querySelector(".desk-tablist") as HTMLElement;
    const last = list.querySelector('[role="tab"]:last-of-type')!.getBoundingClientRect();
    return { atEnd: Math.abs(list.scrollLeft - (list.scrollWidth - list.clientWidth)) <= 1, lastRight: last.right, listRight: list.getBoundingClientRect().right, selected: list.querySelector('[role="tab"][aria-selected="true"]')!.textContent };
  });
  expect(m.selected).toMatch(/settings/i);
  expect(m.atEnd).toBe(true);
  expect(m.lastRight).toBeLessThanOrEqual(m.listRight + 1); // the last tab is fully visible
});

test("on a phone the Overview tab starts at the left margin", async ({ page }) => {
  await open(page, "/terminal/trade/overview", 390);
  await expect(page.locator('.desk-tablist [role="tab"][aria-selected="true"]')).toBeVisible();
  await page.waitForTimeout(300);
  const m = await page.evaluate(() => {
    const tab = document.querySelector('.desk-tablist [role="tab"]')!.getBoundingClientRect();
    const find = document.querySelector(".workspace-command-search input")!.getBoundingClientRect();
    return { tab: Math.round(tab.x), find: Math.round(find.x), text: document.querySelector('.desk-tablist [role="tab"]')!.textContent };
  });
  expect(m.text).toMatch(/overview/i);
  expect(m.tab).toBe(m.find);
});
