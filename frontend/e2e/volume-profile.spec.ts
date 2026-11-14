import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";
import { expectNoHorizontalOverflow } from "./helpers";
import { mockData } from "./workspaceMocks";

/**
 * The volume profile on the workspace chart. It is drawn on a canvas, so besides
 * the readout text these tests compare pixels with the profile on and off.
 */

const readout = (page: Page) => page.getByLabel("Volume profile", { exact: true });
const toggle = (page: Page) => page.getByRole("button", { name: "Volume profile", exact: true });
const canvasBox = (page: Page) => page.locator(".ws-chart .candlestick-chart__canvas");

/** A screenshot, typed through Playwright so this project needs no Node typings. */
type Shot = Awaited<ReturnType<Locator["screenshot"]>>;

async function open(page: Page, width = 1440, height = 900, query = "") {
  await page.setViewportSize({ width, height });
  await page.goto(`/terminal/markets/workspace${query}`);
  await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
}

/**
 * Page errors thrown by a flow, with the profile on or off.
 *
 * The charting library throws "Object is disposed" from its own redraw when a chart
 * is removed with an update pending. That happens on the existing Chart page too
 * (verified on a clean checkout of main), so the right check is not "no errors" but
 * "the profile adds none": same flow, profile on versus off.
 */
async function errorsFrom(browser: Browser, profileOff: boolean, flow: (page: Page) => Promise<void>): Promise<string[]> {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await mockData(page);
  if (profileOff) await page.addInitScript(() => localStorage.setItem("olbos.workspace.volumeProfile", "off"));
  await flow(page);
  await context.close();
  return errors;
}

async function shot(page: Page): Promise<Shot> {
  await page.mouse.move(0, 0); // keep the crosshair off the chart
  return canvasBox(page).screenshot({ animations: "disabled" });
}

/** Where two screenshots differ, measured in the browser (no image library needed). */
async function diff(page: Page, a: Shot, b: Shot) {
  return page.evaluate(async ([x, y]) => {
    const load = (b64: string) => new Promise<ImageData>((resolve) => {
      const im = new Image();
      im.onload = () => {
        const c = document.createElement("canvas");
        c.width = im.width; c.height = im.height;
        const g = c.getContext("2d")!;
        g.drawImage(im, 0, 0);
        resolve(g.getImageData(0, 0, c.width, c.height));
      };
      im.src = "data:image/png;base64," + b64;
    });
    const A = await load(x), B = await load(y);
    const w = A.width, h = A.height;
    const leftRows = new Set<number>();
    let leftCount = 0, rightCount = 0, total = 0;
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
      const i = (py * w + px) * 4;
      const d = Math.max(Math.abs(A.data[i] - B.data[i]), Math.abs(A.data[i + 1] - B.data[i + 1]), Math.abs(A.data[i + 2] - B.data[i + 2]));
      if (d <= 8) continue;
      total++;
      if (px < w * 0.55) { leftCount++; leftRows.add(py); }
      else if (px > w * 0.62 && px < w - 70) rightCount++;
    }
    return { w, h, total, leftCount, rightCount, leftRowSpan: leftRows.size ? Math.max(...leftRows) - Math.min(...leftRows) + 1 : 0 };
  }, [a.toString("base64"), b.toString("base64")]);
}

test.describe("Volume profile on the workspace chart", () => {
  test("is on by default and the readout states what it covers", async ({ page }) => {
    await mockData(page);
    await open(page);
    await expect(toggle(page)).toHaveAttribute("aria-pressed", "true");
    await expect(readout(page)).toBeVisible();
    await expect(readout(page)).toHaveText(/^Volume profile · \d+ bars · POC \d+\.\d{2} · value area \d+\.\d{2}–\d+\.\d{2}$/);
    // It says plainly that it is approximate and is not a signal.
    await expect(readout(page)).toHaveAttribute("title", /Approximate.*not a signal/);
  });

  test("really draws bars at the right edge, and a dashed line across, only when on", async ({ page }) => {
    await mockData(page);
    await open(page);
    const on = await shot(page);
    await toggle(page).click();
    await expect(toggle(page)).toHaveAttribute("aria-pressed", "false");
    await expect(readout(page)).toHaveCount(0);
    const off = await shot(page);
    const d = await diff(page, on, off);
    expect(d.rightCount, "no profile bars found at the right of the price pane").toBeGreaterThan(300);
    // On the left half the only difference is the thin point-of-control line.
    expect(d.leftRowSpan, "something other than the POC line changed on the left").toBeLessThanOrEqual(2);
    expect(d.leftCount).toBeGreaterThan(20); // the line itself is there
    // And turning it back on restores exactly the same picture.
    await toggle(page).click();
    await expect(readout(page)).toBeVisible();
    const again = await diff(page, on, await shot(page));
    expect(again.total).toBeLessThanOrEqual(5);
  });

  test("follows zoom and scroll: the bars in view and the profile change", async ({ page }) => {
    await mockData(page);
    await open(page);
    const all = (await readout(page).innerText());
    const count = (t: string) => Number(/(\d+) bars/.exec(t)![1]);
    expect(count(all)).toBeGreaterThan(100);
    const box = (await canvasBox(page).boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.3);
    for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, -500); await page.waitForTimeout(60); } // zoom in
    await expect.poll(async () => count(await readout(page).innerText()), { timeout: 5000 }).toBeLessThan(count(all) - 30);
    const zoomed = await readout(page).innerText();
    expect(zoomed).not.toBe(all);
    // pan back through history
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.3, { steps: 12 });
    await page.mouse.up();
    await expect.poll(async () => await readout(page).innerText(), { timeout: 5000 }).not.toBe(zoomed);
  });

  test("the bars keep to the pane: nothing is drawn past the chart or over the price scale", async ({ page }) => {
    await mockData(page);
    await open(page);
    const on = await shot(page);
    await toggle(page).click();
    const off = await shot(page);
    // The price scale is the right ~70px of the canvas; the profile must not touch it.
    const probe = await page.evaluate(async ([x, y]) => {
      const load = (b64: string) => new Promise<ImageData>((resolve) => {
        const im = new Image();
        im.onload = () => { const c = document.createElement("canvas"); c.width = im.width; c.height = im.height; const g = c.getContext("2d")!; g.drawImage(im, 0, 0); resolve(g.getImageData(0, 0, c.width, c.height)); };
        im.src = "data:image/png;base64," + b64;
      });
      const A = await load(x), B = await load(y);
      let scaleDiffs = 0;
      for (let py = 0; py < A.height; py++) for (let px = A.width - 40; px < A.width; px++) {
        const i = (py * A.width + px) * 4;
        if (Math.abs(A.data[i] - B.data[i]) + Math.abs(A.data[i + 1] - B.data[i + 1]) + Math.abs(A.data[i + 2] - B.data[i + 2]) > 24) scaleDiffs++;
      }
      return scaleDiffs;
    }, [on.toString("base64"), off.toString("base64")]);
    expect(probe).toBe(0);
  });

  test("the choice is remembered across a reload", async ({ page }) => {
    await mockData(page);
    await open(page);
    await toggle(page).click();
    await page.reload();
    await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
    await expect(toggle(page)).toHaveAttribute("aria-pressed", "false");
    await expect(readout(page)).toHaveCount(0);
    await toggle(page).click();
    await page.reload();
    await expect(toggle(page)).toHaveAttribute("aria-pressed", "true");
    await expect(readout(page)).toBeVisible();
  });

  test("it recomputes for a new symbol and survives a resize and an interval change", async ({ browser }) => {
    const flow = async (page: Page, profileOn: boolean) => {
      await open(page);
      let first = "";
      if (profileOn) { await expect(readout(page)).toBeVisible(); first = await readout(page).innerText(); }
      await page.getByLabel("Symbol", { exact: true }).fill("nvda");
      await page.getByRole("toolbar", { name: "Workspace controls" }).getByRole("button", { name: "Go", exact: true }).click();
      await expect(page.locator(".ws-chart__symbol")).toHaveText("NVDA");
      if (profileOn) await expect.poll(async () => await readout(page).innerText()).not.toBe(first);
      await page.getByRole("separator", { name: /Resize chart and side panels/ }).focus();
      for (let i = 0; i < 5; i++) await page.keyboard.press("ArrowLeft");
      await page.getByRole("group", { name: "Chart timeframe" }).getByRole("button", { name: "1 hour", exact: true }).click();
      await page.waitForTimeout(300);
    };
    const withProfile = await errorsFrom(browser, false, async (page) => { await flow(page, true); await expect(readout(page)).toBeVisible(); });
    const without = await errorsFrom(browser, true, async (page) => { await flow(page, false); });
    expect(withProfile.every((m) => m === "Object is disposed"), `unexpected error: ${withProfile.join(" | ")}`).toBe(true);
    expect(withProfile.length).toBeLessThanOrEqual(without.length);
  });

  test("leaving the page tears the profile down without adding errors", async ({ browser }) => {
    const flow = async (page: Page) => {
      await open(page);
      await page.getByRole("button", { name: "Chart", exact: true }).first().click();
      await expect(page).toHaveURL(/\/terminal\/markets\/chart$/);
      await expect(readout(page)).toHaveCount(0);
      // Back through history is client-side, so the workspace mounts again from scratch.
      await page.goBack();
      await expect(page).toHaveURL(/\/terminal\/markets\/workspace$/);
      await page.waitForTimeout(300);
      await page.goForward();
      await expect(page).toHaveURL(/\/terminal\/markets\/chart$/);
      await page.waitForTimeout(300);
    };
    const withProfile = await errorsFrom(browser, false, flow);
    const without = await errorsFrom(browser, true, flow);
    expect(withProfile.every((m) => m === "Object is disposed"), `unexpected error: ${withProfile.join(" | ")}`).toBe(true);
    expect(withProfile.length).toBeLessThanOrEqual(without.length);
  });

  test("a failed or empty chart shows no profile and no error", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await mockData(page, { chartFails: true });
    await open(page, 1440, 900).catch(() => undefined);
    await expect(page.getByRole("status").filter({ hasText: "Chart data unavailable" })).toBeVisible();
    await expect(readout(page)).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test("it works on a phone, where the chart is one panel, and the readout fits", async ({ page }) => {
    await mockData(page);
    await open(page, 390, 844);
    await expect(readout(page)).toBeVisible();
    await expectNoHorizontalOverflow(page, 390);
  });

  for (const brand of ["olbos", "cimphawng"]) {
    test(`shows under the ${brand} brand`, async ({ page }) => {
      await mockData(page);
      await open(page, 1440, 900, `?brand=${brand}`);
      await expect(page.locator("html")).toHaveAttribute("data-brand", brand);
      await expect(readout(page)).toBeVisible();
    });
  }
});

test.describe("Other charts are unchanged", () => {
  test("the Chart page has no volume profile and no toggle", async ({ page }) => {
    await mockData(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/terminal/markets/chart");
    await expect(page.locator(".candlestick-chart").first()).toBeVisible();
    await expect(readout(page)).toHaveCount(0);
    await expect(toggle(page)).toHaveCount(0);
  });
});
