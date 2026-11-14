import { expect, test, type Page } from "@playwright/test";
import { expectNoHorizontalOverflow, SIGNED_IN, stubBackend } from "./helpers";

/**
 * The Cimphawng brand is a skin on the same platform. These checks cover what a
 * visitor sees (name, mark, language switch, colour) and that Olbos is untouched.
 * A brand is presentation only; nothing here touches execution or risk.
 */

const rgb = (s: string) => (s.match(/\d+(\.\d+)?/g) ?? []).slice(0, 3).map(Number);
function lum([r, g, b]: number[]) {
  const f = (c: number) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
const contrast = (a: number[], b: number[]) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

async function signedIn(page: Page) { await stubBackend(page, SIGNED_IN); }

test.describe("Cimphawng skin", () => {
  for (const width of [320, 390, 768, 1440]) {
    test(`dashboard shows the Cimphawng brand and fits ${width}px`, async ({ page }) => {
      await signedIn(page);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/terminal/dashboard?brand=cimphawng");
      await expect(page.locator("html")).toHaveAttribute("data-brand", "cimphawng");
      await expect(page).toHaveTitle(/Cimphawng/);
      await expectNoHorizontalOverflow(page, width);
      if (width >= 768) await expect(page.getByRole("img", { name: /Cimphawng/i }).first()).toBeVisible();
    });
  }

  test("landing and sign-in show the brand with a working language switch", async ({ page }) => {
    await stubBackend(page);
    await page.goto("/?brand=cimphawng");
    await expect(page.getByRole("img", { name: /Cimphawng/i }).first()).toBeVisible();
    const toggle = page.getByRole("group", { name: /language/i }).first();
    await expect(toggle).toBeVisible();
    await page.getByRole("button", { name: /zomi/i }).first().click();
    await expect(page.locator("html")).toHaveAttribute("lang", "ctd");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "ctd"); // remembered
    await page.getByRole("button", { name: /english/i }).first().click();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  });

  test("uses the Cimphawng typeface and an accent that is neither gold nor a gain/loss colour", async ({ page }) => {
    await signedIn(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/terminal/dashboard?brand=cimphawng");
    const probe = await page.evaluate(() => {
      const root = getComputedStyle(document.querySelector(".app-shell") ?? document.documentElement);
      return {
        font: getComputedStyle(document.body).fontFamily,
        action: root.getPropertyValue("--design-action").trim(),
        bg: getComputedStyle(document.body).backgroundColor,
      };
    });
    expect(probe.font).toMatch(/Inter/i);
    expect(probe.action).not.toBe("");
    // Accent hue must be in the blue family, clear of semantic green/red and of Olbos gold.
    const c = probe.action;
    const m = c.match(/#([0-9a-f]{6})/i);
    expect(m, `accent ${c} should be a hex colour`).not.toBeNull();
    const [r, g, b] = [0, 2, 4].map(i => parseInt(m![1].slice(i, i + 2), 16));
    expect(b).toBeGreaterThan(r);
    expect(b).toBeGreaterThan(g * 0.9);
    expect(contrast([r, g, b], rgb(probe.bg))).toBeGreaterThanOrEqual(4.5);
  });
});

test.describe("Olbos stays Olbos", () => {
  test("no brand attribute change, no language switch, original title and lockup", async ({ page }) => {
    await signedIn(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/terminal/dashboard");
    await expect(page.locator("html")).toHaveAttribute("data-brand", "olbos");
    await expect(page).toHaveTitle(/Olbos/);
    await expect(page.getByRole("group", { name: /language/i })).toHaveCount(0);
    await expect(page.locator('img[src^="/brands/cimphawng/"]')).toHaveCount(0);
  });

  test("a saved Zomi choice does not leak into Olbos", async ({ page }) => {
    await stubBackend(page);
    await page.addInitScript(() => localStorage.setItem("olbos.locale", "zo"));
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
  });

  test("the Cimphawng preview is dropped by an empty ?brand=", async ({ page }) => {
    await stubBackend(page);
    await page.goto("/?brand=cimphawng");
    await expect(page.locator("html")).toHaveAttribute("data-brand", "cimphawng");
    await page.goto("/?brand=");
    await expect(page.locator("html")).toHaveAttribute("data-brand", "olbos");
  });
});

test.describe("Cimphawng landing page", () => {
  for (const width of [320, 360, 390, 640, 768, 880, 900, 1024, 1440]) {
    test(`community section shows and nothing overflows at ${width}px`, async ({ page }) => {
      await stubBackend(page);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/?brand=cimphawng");
      await expect(page.locator("#community")).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 })).toContainText("Zomi community");
      await expect(page).toHaveTitle("Cimphawng Trading");
      await expectNoHorizontalOverflow(page, width);
      // The Sign Up button keeps its full label: it was squeezed to 26px at 320px
      // when the language switch sat beside it.
      const cta = page.locator(".landing-nav-cta");
      const box = (await cta.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(60);
      expect(await cta.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    });
  }

  test("on a phone the language switch lives in the menu and works", async ({ page }) => {
    await stubBackend(page);
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto("/?brand=cimphawng");
    await expect(page.locator(".landing-nav .landing-lang-nav")).toBeHidden();
    await page.getByRole("button", { name: "Open menu" }).click();
    const inMenu = page.locator(".landing-nav .landing-lang-menu");
    await expect(inMenu).toBeVisible();
    await inMenu.getByRole("button", { name: /zomi/i }).click();
    await expect(page.locator("html")).toHaveAttribute("lang", "ctd");
  });

  test("on a wide screen the switch is beside the sign-in actions, not in a menu", async ({ page }) => {
    await stubBackend(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/?brand=cimphawng");
    await expect(page.locator(".landing-nav .landing-lang-nav")).toBeVisible();
    await expect(page.locator(".landing-nav .landing-lang-menu")).toBeHidden();
  });

  test("the safety statements stay and the page makes no promise of returns", async ({ page }) => {
    await stubBackend(page);
    await page.goto("/?brand=cimphawng");
    const text = await page.locator("main").innerText();
    expect(text).toMatch(/paper-trading evaluation/i);
    expect(text).toMatch(/no order path/i);
    expect(text).not.toMatch(/guarantee|risk[- ]free|passive income/i);
    await expect(page.locator("#community")).toContainText("still English");
  });

  test("Olbos landing has no community section and no language switch", async ({ page }) => {
    await stubBackend(page);
    await page.goto("/");
    await expect(page.locator("#community")).toHaveCount(0);
    await expect(page.locator(".lang-toggle")).toHaveCount(0);
  });
});
