import { expect, test, type Page } from "@playwright/test";
import { diffShots, type Shot } from "./imageDiff";
import { expectNoHorizontalOverflow } from "./helpers";
import { mockData } from "./workspaceMocks";

/**
 * Drawing tools on the workspace chart, driven with a real mouse. Drawings are
 * notes anchored to time and price and saved per symbol in this browser; they
 * place no orders. These tests run in a real browser because the interesting
 * parts (a drawing painted where you clicked, a drag that moves it, the chart
 * still panning around it) cannot be shown in jsdom.
 */

const KEY = "olbos.workspace.drawings.v1";
type Stored = { id: string; kind: string; points: { time: number; price: number }[]; color: string; width: number };

const canvas = (page: Page) => page.locator(".ws-chart .candlestick-chart__canvas");
const tool = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const readout = (page: Page) => page.getByLabel("Volume profile", { exact: true });
const bar = (page: Page) => page.getByRole("toolbar", { name: "Workspace controls" });

async function open(page: Page, width = 1440, height = 900, query = "") {
  await page.setViewportSize({ width, height });
  await page.goto(`/terminal/markets/chart${query}`);
  await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
  // The chart is built after the bars arrive; wait until it can take a point.
  await expect(tool(page, "Trend line")).toBeVisible();
}

/** Fractions of the chart canvas -> page pixels. Stay in the top ~40%: that is the price pane. */
async function at(page: Page) {
  const b = (await canvas(page).boundingBox())!;
  return { X: (f: number) => b.x + b.width * f, Y: (f: number) => b.y + b.height * f, box: b };
}

async function stored(page: Page, symbol = "AAPL"): Promise<Stored[]> {
  return page.evaluate(([k, s]) => (JSON.parse(localStorage.getItem(k) || "{}")[s] ?? []), [KEY, symbol] as const);
}

async function shot(page: Page): Promise<Shot> {
  await page.mouse.move(2, 2); // keep the pointer (and any hover) off the chart
  return canvas(page).screenshot({ animations: "disabled" });
}

async function drag(page: Page, x1: number, y1: number, x2: number, y2: number) {
  await page.mouse.move(x1, y1);
  await page.mouse.down();
  await page.mouse.move(x2, y2, { steps: 8 });
  await page.mouse.up();
}

/** Colour and width are folded away until asked for. */
async function openStyle(page: Page) {
  const style = tool(page, "Style");
  if ((await style.getAttribute("aria-expanded")) !== "true") await style.click();
}

async function drawTrend(page: Page) {
  const { X, Y } = await at(page);
  await tool(page, "Trend line").click();
  await page.mouse.click(X(0.2), Y(0.3));
  await page.mouse.move(X(0.35), Y(0.2), { steps: 4 });
  await page.mouse.click(X(0.5), Y(0.12));
  await expect.poll(async () => (await stored(page)).length).toBe(1);
}

test.describe("the tools rail", () => {
  test("offers every tool, colour, width and edit control, named for assistive technology", async ({ page }) => {
    await mockData(page);
    await open(page);
    const rail = page.getByRole("toolbar", { name: "Drawing tools" });
    await expect(rail).toBeVisible();
    for (const name of ["Select and edit", "Trend line", "Horizontal line", "Vertical line", "Rectangle", "Fib retracement", "Magnet",
      "Undo", "Redo", "Delete selected drawing", "Clear all drawings"]) {
      await expect(rail.getByRole("button", { name, exact: true })).toBeVisible();
    }
    await expect(tool(page, "Style")).toHaveAttribute("aria-expanded", "false"); // folded away at rest
    await expect(rail.getByRole("group", { name: "Colour" })).toHaveCount(0);
    await openStyle(page);
    await expect(rail.getByRole("group", { name: "Colour" }).getByRole("button")).toHaveCount(6);
    await expect(rail.getByRole("group", { name: "Line width" }).getByRole("button")).toHaveCount(3);
    await expect(tool(page, "Select and edit")).toHaveAttribute("aria-pressed", "true");
    for (const name of ["Undo", "Redo", "Delete selected drawing", "Clear all drawings"]) await expect(tool(page, name)).toBeDisabled();
  });

  for (const [w, h] of [[1920, 1080], [1440, 900], [1366, 768], [1280, 720]] as const) {
    test(`every control fits with no scrolling at ${w}x${h}`, async ({ page }) => {
      await mockData(page);
      await open(page, w, h);
      const fits = await page.getByRole("toolbar", { name: "Drawing tools" }).evaluate((el) => el.scrollHeight <= el.clientHeight + 1);
      expect(fits).toBe(true);
    });
  }

  test("the Style button shows the current colour and unfolds the colours and widths", async ({ page }) => {
    await mockData(page);
    await open(page);
    await openStyle(page);
    await tool(page, "Amber colour").click();
    await expect(tool(page, "Amber colour")).toHaveAttribute("aria-pressed", "true");
    const dot = await tool(page, "Style").locator(".ws-drawtools__dot").evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(dot).toBe("rgb(242, 184, 75)"); // #f2b84b, shown on the closed button too
    await tool(page, "Style").click();
    await expect(page.getByRole("group", { name: "Colour" })).toHaveCount(0);
  });

  test("choosing a tool marks it pressed, and clicking it again from another tool switches", async ({ page }) => {
    await mockData(page);
    await open(page);
    await tool(page, "Trend line").click();
    await expect(tool(page, "Trend line")).toHaveAttribute("aria-pressed", "true");
    await expect(tool(page, "Select and edit")).toHaveAttribute("aria-pressed", "false");
    await tool(page, "Rectangle").click();
    await expect(tool(page, "Rectangle")).toHaveAttribute("aria-pressed", "true");
    await expect(tool(page, "Trend line")).toHaveAttribute("aria-pressed", "false");
  });
});

test.describe("creating drawings", () => {
  test("a trend line: click, move, click. It is saved, selected, and the tool returns to Select", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drawTrend(page);
    const [d] = await stored(page);
    expect(d.kind).toBe("trend");
    expect(d.points).toHaveLength(2);
    expect(d.points[1].time).toBeGreaterThan(d.points[0].time);
    expect(d.points[1].price).toBeGreaterThan(d.points[0].price); // it was drawn upward
    await expect(tool(page, "Select and edit")).toHaveAttribute("aria-pressed", "true");
    await expect(tool(page, "Delete selected drawing")).toBeEnabled(); // it is selected
  });

  test("a rectangle by press, drag and release", async ({ page }) => {
    await mockData(page);
    await open(page);
    const { X, Y } = await at(page);
    await tool(page, "Rectangle").click();
    await drag(page, X(0.25), Y(0.15), X(0.55), Y(0.32));
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    const [d] = await stored(page);
    expect(d.kind).toBe("rect");
    expect(Math.abs(d.points[1].time - d.points[0].time)).toBeGreaterThan(0);
    expect(Math.abs(d.points[1].price - d.points[0].price)).toBeGreaterThan(0);
  });

  test("a horizontal line is painted at the price you clicked, spanning the chart, and nowhere else", async ({ page }) => {
    await mockData(page);
    await open(page);
    const before = await shot(page);
    const { X, Y, box } = await at(page);
    await tool(page, "Horizontal line").click();
    const clickY = Y(0.25);
    await page.mouse.click(X(0.4), clickY);
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    await page.keyboard.press("Escape"); // deselect, so no handle is painted
    const after = await shot(page);
    const d = await diffShots(page, before, after);
    expect(d.total).toBeGreaterThan(300);
    const rowInCanvas = clickY - box.y;
    // Every changed row sits on the line (or its price label just above it).
    expect(d.minY).toBeGreaterThanOrEqual(rowInCanvas - 16);
    expect(d.maxY).toBeLessThanOrEqual(rowInCanvas + 4);
    expect(d.maxX - d.minX).toBeGreaterThan(box.width * 0.6); // it spans the plot
  });

  test("a vertical line is painted at the time you clicked", async ({ page }) => {
    await mockData(page);
    await open(page);
    const before = await shot(page);
    const { X, box } = await at(page);
    await tool(page, "Vertical line").click();
    const clickX = X(0.45);
    await page.mouse.click(clickX, (await at(page)).Y(0.2));
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    await page.keyboard.press("Escape");
    const d = await diffShots(page, before, await shot(page));
    expect(d.total).toBeGreaterThan(100);
    expect(d.minX).toBeGreaterThanOrEqual(clickX - box.x - 4);
    expect(d.maxX).toBeLessThanOrEqual(clickX - box.x + 4);
  });

  test("a Fibonacci retracement stores two points and draws its levels", async ({ page }) => {
    await mockData(page);
    await open(page);
    const { X, Y } = await at(page);
    const before = await shot(page);
    await tool(page, "Fib retracement").click();
    await page.mouse.click(X(0.2), Y(0.1));
    await page.mouse.click(X(0.5), Y(0.36));
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    await page.keyboard.press("Escape");
    expect((await stored(page))[0].kind).toBe("fib");
    const d = await diffShots(page, before, await shot(page));
    expect(d.total).toBeGreaterThan(800); // seven level lines, their labels and the connector
  });

  test("Escape cancels a half-placed drawing and nothing is saved", async ({ page }) => {
    await mockData(page);
    await open(page);
    const { X, Y } = await at(page);
    await tool(page, "Trend line").click();
    await page.mouse.click(X(0.2), Y(0.3));
    await page.mouse.move(X(0.4), Y(0.2), { steps: 4 });
    await page.keyboard.press("Escape");
    await page.mouse.click(X(0.6), Y(0.2)); // with the pending point gone this is a fresh first point, not a second one
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    expect(await stored(page)).toEqual([]);
  });

  test("clicks over the price scale or below the price pane do not make drawings", async ({ page }) => {
    await mockData(page);
    await open(page);
    const { Y, box } = await at(page);
    await tool(page, "Horizontal line").click();
    await page.mouse.click(box.x + box.width - 25, Y(0.2)); // over the price axis
    await page.mouse.click(box.x + box.width * 0.4, Y(0.88)); // down in the MACD pane
    expect(await stored(page)).toEqual([]);
    await expect(tool(page, "Horizontal line")).toHaveAttribute("aria-pressed", "true"); // still waiting for a real point
  });
});

test.describe("editing drawings", () => {
  test("dragging a drawing moves it, and one undo puts it back", async ({ page }) => {
    await mockData(page);
    await open(page);
    const { X, Y } = await at(page);
    await tool(page, "Horizontal line").click();
    await page.mouse.click(X(0.4), Y(0.15));
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    const original = (await stored(page))[0].points[0].price;
    await drag(page, X(0.7), Y(0.15), X(0.7), Y(0.3)); // pick it up anywhere along its length
    await expect.poll(async () => (await stored(page))[0].points[0].price).toBeLessThan(original - 1);
    await page.keyboard.press("Control+z");
    await expect.poll(async () => (await stored(page))[0].points[0].price).toBeCloseTo(original, 6);
  });

  test("dragging a handle moves only that end", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drawTrend(page);
    const { X, Y } = await at(page);
    const [d0] = await stored(page);
    await drag(page, X(0.5), Y(0.12), X(0.7), Y(0.3)); // the second end, which is selected after creating
    await expect.poll(async () => (await stored(page))[0].points[1].time).toBeGreaterThan(d0.points[1].time);
    const [d1] = await stored(page);
    expect(d1.points[0]).toEqual(d0.points[0]); // the first end did not move
    expect(d1.points[1].price).toBeLessThan(d0.points[1].price);
  });

  test("Delete removes the selected drawing and Ctrl+Z / Ctrl+Shift+Z bring it back and away again", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drawTrend(page);
    await page.keyboard.press("Delete");
    await expect.poll(async () => (await stored(page)).length).toBe(0);
    await page.keyboard.press("Control+z");
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    await page.keyboard.press("Control+Shift+z");
    await expect.poll(async () => (await stored(page)).length).toBe(0);
  });

  test("the buttons undo, redo, delete and clear, and know when they cannot", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drawTrend(page);
    await tool(page, "Horizontal line").click();
    const { X, Y } = await at(page);
    await page.mouse.click(X(0.7), Y(0.3));
    await expect.poll(async () => (await stored(page)).length).toBe(2);
    await tool(page, "Clear all drawings").click();
    await expect.poll(async () => (await stored(page)).length).toBe(0);
    await expect(tool(page, "Clear all drawings")).toBeDisabled();
    await tool(page, "Undo").click();
    await expect.poll(async () => (await stored(page)).length).toBe(2);
    await tool(page, "Redo").click();
    await expect.poll(async () => (await stored(page)).length).toBe(0);
    await expect(tool(page, "Redo")).toBeDisabled();
  });

  test("colour and width set a new drawing's style and restyle the selected one", async ({ page }) => {
    await mockData(page);
    await open(page);
    await openStyle(page);
    await tool(page, "Amber colour").click();
    await tool(page, "Line width 3").click();
    await drawTrend(page);
    expect(await stored(page)).toMatchObject([{ color: "#f2b84b", width: 3 }]);
    await tool(page, "Violet colour").click(); // the drawing is still selected, so it is recoloured
    await tool(page, "Line width 1").click();
    await expect.poll(async () => (await stored(page))[0]).toMatchObject({ color: "#a98bff", width: 1 });
  });

  test("clicking empty chart deselects", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drawTrend(page);
    await expect(tool(page, "Delete selected drawing")).toBeEnabled();
    const { X, Y } = await at(page);
    await page.mouse.click(X(0.85), Y(0.38)); // empty space
    await expect(tool(page, "Delete selected drawing")).toBeDisabled();
    expect((await stored(page)).length).toBe(1); // deselecting deletes nothing
  });

  test("the magnet is a toggle", async ({ page }) => {
    await mockData(page);
    await open(page);
    await expect(tool(page, "Magnet")).toHaveAttribute("aria-pressed", "false");
    await tool(page, "Magnet").click();
    await expect(tool(page, "Magnet")).toHaveAttribute("aria-pressed", "true");
  });
});

test.describe("the chart keeps working around drawings", () => {
  async function zoomIn(page: Page) {
    const { X, Y } = await at(page);
    await page.mouse.move(X(0.5), Y(0.3));
    for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, -500); await page.waitForTimeout(60); }
    await expect.poll(async () => /(\d+) bars/.exec(await readout(page).innerText())![1]).not.toBe("160");
  }

  test("panning still works on empty chart; drawing with a tool does NOT pan it", async ({ page }) => {
    await mockData(page);
    await open(page);
    await zoomIn(page);
    const { X, Y } = await at(page);
    const view = await readout(page).innerText();

    // With a tool active, a press-drag makes a drawing and the chart stays where it is.
    await tool(page, "Rectangle").click();
    await drag(page, X(0.3), Y(0.15), X(0.6), Y(0.3));
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    await page.waitForTimeout(300);
    expect(await readout(page).innerText()).toBe(view);

    // With Select and nothing under the pointer, the same drag pans the chart.
    await page.mouse.click(X(0.85), Y(0.38)); // deselect
    await drag(page, X(0.3), Y(0.36), X(0.8), Y(0.36));
    await expect.poll(async () => await readout(page).innerText()).not.toBe(view);
  });

  test("a drawing stays on its candle and price when the chart is zoomed", async ({ page }) => {
    await mockData(page);
    await open(page);
    const { X, Y } = await at(page);
    await tool(page, "Vertical line").click();
    await page.mouse.click(X(0.5), Y(0.2));
    await expect.poll(async () => (await stored(page)).length).toBe(1);
    await page.keyboard.press("Escape");
    const wide = await shot(page);
    await zoomIn(page);
    const zoomed = await shot(page);
    // The line is somewhere else on screen now (it followed its candle), not left behind at the old column.
    const d = await diffShots(page, wide, zoomed);
    expect(d.total).toBeGreaterThan(1000);
    const [saved] = await stored(page);
    expect(saved.points[0].time).toBeGreaterThan(0); // and its anchor is still a time, not a pixel
  });

  test("keys are not stolen from a text field", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drawTrend(page);
    const field = page.getByLabel("Add a symbol to the watchlist");
    await field.click();
    await field.fill("AB");
    await page.keyboard.press("Backspace");
    await expect(field).toHaveValue("A");            // the field got its backspace
    expect((await stored(page)).length).toBe(1);     // and the drawing was not deleted
    await page.keyboard.press("Delete");
    await page.keyboard.press("Control+z");          // the field's own undo, not the chart's
    expect((await stored(page)).length).toBe(1);     // the drawing is still there
  });
});

test.describe("saving", () => {
  test("drawings survive a reload and belong to their own symbol", async ({ page }) => {
    await mockData(page);
    await open(page);
    await drawTrend(page);
    await page.reload();
    await expect(tool(page, "Trend line")).toBeVisible();
    await expect(tool(page, "Clear all drawings")).toBeEnabled(); // AAPL's drawing is back

    await page.getByLabel("Symbol", { exact: true }).fill("nvda");
    await bar(page).getByRole("button", { name: "Go", exact: true }).click();
    await expect(page.locator(".ws-chart__symbol")).toHaveText("NVDA");
    await expect(tool(page, "Clear all drawings")).toBeDisabled(); // a different symbol starts clean
    const { X, Y } = await at(page);
    await tool(page, "Horizontal line").click();
    await page.mouse.click(X(0.5), Y(0.25));
    await expect.poll(async () => (await stored(page, "NVDA")).length).toBe(1);
    expect((await stored(page, "AAPL")).length).toBe(1); // AAPL's is untouched

    await bar(page).getByLabel("Symbol", { exact: true }).fill("aapl");
    await bar(page).getByRole("button", { name: "Go", exact: true }).click();
    await expect(page.locator(".ws-chart__symbol")).toHaveText("AAPL");
    await expect(tool(page, "Clear all drawings")).toBeEnabled();
  });

  test("opening a symbol writes nothing; only an edit does", async ({ page }) => {
    await mockData(page);
    await open(page);
    expect(await page.evaluate((k) => localStorage.getItem(k), KEY)).toBeNull();
    await drawTrend(page);
    expect(await page.evaluate((k) => localStorage.getItem(k), KEY)).not.toBeNull();
  });

  test("a tampered store cannot break the page or inject anything", async ({ page }) => {
    await mockData(page);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.addInitScript((k) => localStorage.setItem(k, JSON.stringify({
      AAPL: [
        { id: "ok1", kind: "hline", points: [{ time: 1788400000, price: 250 }], color: "url(javascript:alert(1))", width: 99 },
        { id: "bad kind", kind: "arrow", points: [] },
        { id: "nan", kind: "trend", points: [{ time: "x", price: 1 }, { time: 2, price: 2 }] },
        null, 5, "x",
      ],
      "__proto__": [{ id: "p", kind: "hline", points: [{ time: 1, price: 1 }] }],
      "bad key!": [],
    })), KEY);
    await open(page);
    await expect(tool(page, "Clear all drawings")).toBeEnabled(); // the one valid drawing was kept
    await tool(page, "Clear all drawings").click();
    await expect.poll(async () => await page.evaluate((k) => localStorage.getItem(k), KEY)).toBe("{}");
    expect(errors).toEqual([]);
  });

  test("corrupt JSON in storage is ignored", async ({ page }) => {
    await mockData(page);
    await page.addInitScript((k) => localStorage.setItem(k, "{not json"), KEY);
    await open(page);
    await expect(tool(page, "Clear all drawings")).toBeDisabled();
    await drawTrend(page);
    expect((await stored(page)).length).toBe(1);
  });
});

test.describe("screens and brands", () => {
  test("the rail is not offered on the stacked phone layout, saved drawings are left alone, and nothing overflows", async ({ page }) => {
    await mockData(page);
    await page.addInitScript((k) => localStorage.setItem(k, JSON.stringify({ AAPL: [{ id: "a1", kind: "hline", points: [{ time: 1788400000, price: 250 }], color: "#7daaff", width: 2 }] })), KEY);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/terminal/markets/chart");
    await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
    await expect(page.getByRole("toolbar", { name: "Drawing tools" })).toHaveCount(0);
    await expectNoHorizontalOverflow(page, 390);
    expect((await stored(page)).length).toBe(1);
    expect(errors).toEqual([]);
  });

  for (const width of [1920, 1440, 1280, 1024, 901]) {
    test(`fits at ${width}px`, async ({ page }) => {
      await mockData(page);
      await open(page, width, 800);
      await expect(page.getByRole("toolbar", { name: "Drawing tools" })).toBeVisible();
      await expectNoHorizontalOverflow(page, width);
    });
  }

  for (const brand of ["olbos", "cimphawng"]) {
    test(`works under the ${brand} brand`, async ({ page }) => {
      await mockData(page);
      await open(page, 1440, 900, `?brand=${brand}`);
      await expect(page.locator("html")).toHaveAttribute("data-brand", brand);
      await drawTrend(page);
      const pressed = await tool(page, "Select and edit").evaluate((el) => getComputedStyle(el).borderTopColor);
      if (brand === "cimphawng") {
        const [r, , b] = (pressed.match(/\d+/g) ?? []).map(Number);
        expect(b).toBeGreaterThan(r); // the brand's blue accent, not Olbos gold
      }
    });
  }
});

test.describe("teardown", () => {
  /**
   * The charting library throws "Object is disposed" from its own redraw when a chart is
   * removed with an update pending. It does so on every chart page, and whether it
   * fires in a given run is timing. A count-based version of this test failed 7 runs in 30 on
   * clean main under load, so this checks only that nothing ELSE is thrown. The order of
   * teardown is pinned deterministically in CandlestickChartTeardown.test.tsx and
   * drawings/useDrawings.test.tsx.
   */
  test("rebuilding the chart and leaving the page throw nothing of our own", async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await mockData(page);
    await open(page);
    await drawTrend(page);
    const { X, Y } = await at(page);
    await tool(page, "Fib retracement").click();
    await page.mouse.click(X(0.2), Y(0.1));
    await page.mouse.click(X(0.5), Y(0.3));
    await page.getByRole("group", { name: "Chart timeframe" }).getByRole("button", { name: "1 hour", exact: true }).click(); // rebuilds the chart
    await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
    await page.getByRole("button", { name: "Watchlists", exact: true }).first().click();
    await expect(page).toHaveURL(/\/terminal\/markets\/watchlists$/);
    await page.goBack();
    await expect(page.locator(".ws-chart .candlestick-chart")).toBeVisible();
    await page.waitForTimeout(300);
    await context.close();
    expect(errors.filter((m) => m !== "Object is disposed"), "an error that is not the library's own").toEqual([]);
  });
});
