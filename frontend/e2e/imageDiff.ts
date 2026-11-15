import type { Locator, Page } from "@playwright/test";

/** A screenshot, typed through Playwright so this project needs no Node typings. */
export type Shot = Awaited<ReturnType<Locator["screenshot"]>>;

export interface DiffResult {
  w: number;
  h: number;
  /** Pixels that differ by more than the threshold. */
  total: number;
  /** Bounding box of the differing pixels (all -1 when nothing differs). */
  minX: number; maxX: number; minY: number; maxY: number;
}

/** Where two screenshots differ, measured in the browser (no image library needed). */
export async function diffShots(page: Page, a: Shot, b: Shot, threshold = 8): Promise<DiffResult> {
  return page.evaluate(async ([x, y, th]) => {
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
    const A = await load(x as string), B = await load(y as string);
    const w = Math.min(A.width, B.width), h = Math.min(A.height, B.height);
    let total = 0, minX = Infinity, maxX = -1, minY = Infinity, maxY = -1;
    for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
      const i = (py * A.width + px) * 4, j = (py * B.width + px) * 4;
      const d = Math.max(Math.abs(A.data[i] - B.data[j]), Math.abs(A.data[i + 1] - B.data[j + 1]), Math.abs(A.data[i + 2] - B.data[j + 2]));
      if (d <= (th as number)) continue;
      total++;
      if (px < minX) minX = px; if (px > maxX) maxX = px; if (py < minY) minY = py; if (py > maxY) maxY = py;
    }
    return { w, h, total, minX: total ? minX : -1, maxX, minY: total ? minY : -1, maxY };
  }, [a.toString("base64"), b.toString("base64"), threshold] as const);
}
