/**
 * The desk header rows - the ENV chip row, the Session strip, the tab row and the
 * "Additional desk tools" row - read as one set: every item is a bordered box like
 * the ENV and MODE chips (1px border, 3px radius, the same fill), the boxes start at
 * the same left edge, and the rows share one background and one bottom rule with
 * nothing raised.
 */
import { test, expect } from "@playwright/test";
import { stubBackend } from "./helpers";

const reply = (b: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(b) });

test("desk header items are bordered boxes like the ENV chips, on one left edge", async ({ page }) => {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/market/broker", r => r.fulfill(reply({ broker: "ibkr", paper_mode: true, status: "connected" })));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/terminal/trade/positions");
  await expect(page.locator(".instrument-rail .instrument-chip").first()).toBeVisible();
  await expect(page.locator(".desk-tablist .desk-tab").first()).toBeVisible();
  await expect(page.locator(".desk-auxiliary-tools > summary")).toBeVisible();

  const m = await page.evaluate(() => {
    const box = (sel: string) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
      return { x: Math.round(r.x), h: Math.round(r.height), bg: cs.backgroundColor, radius: cs.borderTopLeftRadius, bw: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth].join(" "), bs: cs.borderTopStyle };
    };
    const row = (sel: string) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const cs = getComputedStyle(el);
      return { bg: cs.backgroundColor, img: cs.backgroundImage, shadow: cs.boxShadow, bb: cs.borderBottom, bt: cs.borderTopWidth, bl: cs.borderLeftWidth, radius: cs.borderTopLeftRadius };
    };
    return {
      env: box(".global-risk-status > *"),
      session: box(".instrument-rail .instrument-chip"),
      tab: box(".desk-tablist .desk-tab"),
      tools: box(".desk-auxiliary-tools > summary"),
      rows: [".global-risk-status", ".instrument-rail", ".desk-tablist", ".desk-auxiliary-tools"].map(row),
    };
  });

  // Every item is a box like the ENV chip: 1px border all round, 3px radius, same fill.
  const env = m.env!;
  expect(env).not.toBeNull();
  for (const [name, b] of Object.entries({ session: m.session, tab: m.tab, tools: m.tools })) {
    expect(b, name).not.toBeNull();
    expect(b!.bw, `${name} border`).toBe("1px 1px 1px 1px");
    expect(b!.bs, `${name} style`).toBe("solid");
    expect(b!.radius, `${name} radius`).toBe(env.radius);
    expect(b!.bg, `${name} fill`).toBe(env.bg);
    // ...and they start at the same left edge as the ENV chip.
    expect(b!.x, `${name} left edge`).toBe(env.x);
  }

  // One background and one bottom rule across all four rows, with nothing raised.
  const [r0, ...rest] = m.rows;
  for (const r of rest) {
    expect(r!.bg).toBe(r0!.bg);
    expect(r!.bb).toBe(r0!.bb);
    expect(r!.img).toBe("none");
    expect(r!.shadow).toBe("none");
  }
  // The Session strip is not itself an outlined, rounded box any more.
  expect([m.rows[1]!.bt, m.rows[1]!.bl, m.rows[1]!.radius]).toEqual(["0px", "0px", "0px"]);
});

async function sessionStrip(page: import("@playwright/test").Page, width: number) {
  await stubBackend(page);
  await page.route("**/api/**", r => r.fulfill(r.request().url().includes("/api/auth/status")
    ? reply({ auth_enabled: false, authenticated: false, user: null })
    : { status: 503, contentType: "application/json", body: "{}" }));
  await page.route("**/api/market/broker", r => r.fulfill(reply({ broker: "ibkr", paper_mode: true, status: "connected" })));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/terminal/trade/positions");
  await expect(page.locator(".instrument-rail .instrument-chip:visible").first()).toBeVisible();
}

// Wide screens (over 100px to spare even with wider fonts): one line, kill-switch control on the same row.
for (const width of [1920, 2200]) {
  test(`the Session strip is one line with the kill-switch control beside it at ${width}px`, async ({ page }) => {
    await sessionStrip(page, width);
    const m = await page.evaluate(() => {
      const chips = Array.from(document.querySelectorAll(".instrument-rail .instrument-chip")).filter(e => (e as HTMLElement).offsetParent) as HTMLElement[];
      const kill = document.querySelector(".instrument-rail-kill") as HTMLElement;
      const rects = chips.map(c => c.getBoundingClientRect());
      const k = kill.getBoundingClientRect();
      return { count: chips.length, ys: Array.from(new Set(rects.map(r => Math.round(r.y)))), top: rects[0].top, bottom: rects[0].bottom, kTop: k.top, kBottom: k.bottom };
    });
    expect(m.count).toBe(7); // all seven boxes, Kill switch included, at this width
    expect(m.ys).toHaveLength(1);
    expect(m.kTop <= m.bottom && m.kBottom >= m.top, "the kill-switch control overlaps the boxes' row").toBe(true);
  });
}

// Laptop widths: wrapping is allowed (it is how a row that does not fit behaves), but no value is
// ever cut off, nothing runs past the strip, and the duplicate Kill switch box is hidden.
for (const width of [1280, 1440, 1599, 1600]) {
  test(`the Session strip never truncates or overflows at ${width}px`, async ({ page }) => {
    await sessionStrip(page, width);
    const m = await page.evaluate(() => {
      const rail = document.querySelector(".instrument-rail") as HTMLElement;
      const vals = Array.from(rail.querySelectorAll(".instrument-chip-value")).filter(e => (e as HTMLElement).offsetParent) as HTMLElement[];
      const killChip = rail.querySelector(".instrument-chip--kill") as HTMLElement;
      return {
        clipped: vals.filter(e => e.scrollWidth > e.clientWidth + 1).length,
        overflowX: rail.scrollWidth - rail.clientWidth,
        killChipShown: !!killChip.offsetParent,
      };
    });
    expect(m.clipped).toBe(0);
    expect(m.overflowX).toBeLessThanOrEqual(1);
    // The duplicate Kill switch box is hidden under 1600px and shown from 1600px up.
    expect(m.killChipShown).toBe(width >= 1600);
  });
}
