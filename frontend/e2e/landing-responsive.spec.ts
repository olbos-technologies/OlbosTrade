/**
 * The public landing page must fit every phone, and the nav row must stay
 * fitted no matter what the CTA says.
 *
 * This is the test that could not be written in vitest. The bug it guards
 * shipped twice: the nav row was a fixed 373.9px wide at EVERY viewport
 * because .landing-nav-actions is flex-shrink: 0 and the CTA label is
 * unbreakable text. It cleared a 375px screen by 1.1px, overhung 360px by 14px
 * and 320px by 54px — and the second-widest phone viewport in the world
 * scrolled sideways on the marketing page.
 *
 * The first fix was measured on one screen (390px) and passed. That is the
 * failure mode the width table below exists to prevent: a fix verified at the
 * width the author happened to open.
 */

import { test, expect } from "@playwright/test";
import { expectNoHorizontalOverflow } from "./helpers";

/**
 * Real device widths, not round numbers. 360 is the most common Android
 * viewport worldwide; 375 covers the iPhone SE/mini line; 320 is the narrow
 * floor (iPhone 5/SE 1st gen, and any browser at minimum zoom-out).
 */
const PHONE_WIDTHS = [430, 414, 393, 390, 375, 360, 344, 320];

test.describe("landing page fits every phone", () => {
  for (const width of PHONE_WIDTHS) {
    test(`no horizontal overflow at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/");
      await page.waitForSelector(".landing-nav-cta");
      await expectNoHorizontalOverflow(page, width);
    });
  }
});

test.describe("nav CTA label", () => {
  /**
   * There used to be TWO labels here — a full "Start Paper Trading" and a
   * compact "Start Free" that CSS swapped at the phone breakpoint — and these
   * tests checked that the right one showed at each width.
   *
   * The CTA is now a single "Sign Up", about a third of the width, so the swap
   * has nothing left to do and the spans are gone from the markup. What still
   * matters is that ONE label renders at every width: the failure the swap
   * created was both labels being in the DOM at once, which made textContent
   * read "Start Paper TradingStart Free" and announced that to a screen
   * reader. A future edit that reintroduces a second span would bring that
   * back, so this keeps checking for it rather than being deleted.
   */
  for (const width of [360, 1280]) {
    test(`renders exactly one label at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/");
      const cta = page.locator(".landing-nav-cta");

      // useInnerText, because textContent would concatenate a hidden second
      // label instead of reporting what is on screen — the exact hole the
      // two-label version of this test existed to close.
      await expect(cta).toHaveText("Sign Up", { useInnerText: true });
      await expect(cta).toHaveAccessibleName("Sign Up");
    });
  }

  test("the label-swap markup is gone, not merely unused", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 844 });
    await page.goto("/");
    await page.waitForSelector(".landing-nav-cta");
    await expect(page.locator(".landing-nav-cta .landing-cta-full")).toHaveCount(0);
    await expect(page.locator(".landing-nav-cta .landing-cta-compact")).toHaveCount(0);
  });
});

test.describe("the shrink guard, not just the shorter label", () => {
  /**
   * A short label alone would be a fix that works until someone lengthens the
   * copy. The load-bearing part is that the row can actually SHRINK.
   *
   * The old version of this forced the long label back on with a CSS override
   * that flipped the two swap spans. Those spans no longer exist, so the long
   * label is injected directly into the CTA instead — same purpose, and it
   * now tests the real element rather than a span that was only ever there
   * for the swap.
   */
  const LONG_LABEL = "Start Paper Trading Right Now";

  async function forceLongLabel(page: import("@playwright/test").Page) {
    await page.waitForSelector(".landing-nav-cta");
    await page.locator(".landing-nav-cta").evaluate((el, text) => {
      el.textContent = text;
    }, LONG_LABEL);
  }

  test("a long CTA label cannot push the document wider than the viewport", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await page.goto("/");
    await forceLongLabel(page);
    await expectNoHorizontalOverflow(page, 320);
  });

  test("a long CTA label ellipsizes rather than being hard-clipped", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await page.goto("/");
    await forceLongLabel(page);

    const label = page.locator(".landing-nav-cta");
    const { rendered, natural } = await label.evaluate((el) => ({
      rendered: Math.ceil(el.getBoundingClientRect().width),
      natural: el.scrollWidth,
    }));

    // The CTA must shrink below its own text width. It can, because
    // min-width: 0 makes the flex automatic minimum size resolve to 0
    // (css-flexbox-1 §4.5) — which is what lets text-overflow produce an
    // ellipsis instead of the text simply being cut off by the parent.
    expect(
      rendered,
      `the label did not shrink (rendered ${rendered}px, text ${natural}px), ` +
      `so text-overflow cannot ellipsize it`
    ).toBeLessThan(natural);
    await expect(label).toHaveCSS("text-overflow", "ellipsis");
  });
});
