import { describe, expect, it } from "vitest";
import { alignActiveTab } from "../tabScroll";

/** A strip and a tab with chosen geometry (jsdom does no layout). */
function strip(opts: { scrollWidth: number; clientWidth: number; listLeft: number; tabLeft: number; pad?: number; scrollLeft?: number }) {
  const list = document.createElement("div");
  const tab = document.createElement("button");
  list.appendChild(tab);
  document.body.appendChild(list);
  list.style.paddingLeft = `${opts.pad ?? 0}px`;
  Object.defineProperty(list, "scrollWidth", { value: opts.scrollWidth, configurable: true });
  Object.defineProperty(list, "clientWidth", { value: opts.clientWidth, configurable: true });
  list.getBoundingClientRect = () => ({ left: opts.listLeft, right: opts.listLeft + opts.clientWidth, top: 0, bottom: 0, width: opts.clientWidth, height: 0, x: opts.listLeft, y: 0, toJSON() {} }) as DOMRect;
  tab.getBoundingClientRect = () => ({ left: opts.tabLeft, right: opts.tabLeft + 90, top: 0, bottom: 0, width: 90, height: 0, x: opts.tabLeft, y: 0, toJSON() {} }) as DOMRect;
  list.scrollLeft = opts.scrollLeft ?? 0;
  return { list, tab };
}

describe("alignActiveTab", () => {
  it("scrolls the strip so the selected tab sits at the strip's start", () => {
    const { list, tab } = strip({ scrollWidth: 900, clientWidth: 358, listLeft: 16, tabLeft: 316 });
    alignActiveTab(list, tab);
    expect(list.scrollLeft).toBe(300); // 316 - 16
  });

  it("keeps the strip's own left padding as the margin", () => {
    const { list, tab } = strip({ scrollWidth: 900, clientWidth: 390, listLeft: 0, tabLeft: 216, pad: 16 });
    alignActiveTab(list, tab);
    expect(list.scrollLeft).toBe(200); // 216 - 0 - 16
  });

  it("scrolls back when the selected tab is left of the start", () => {
    const { list, tab } = strip({ scrollWidth: 900, clientWidth: 358, listLeft: 16, tabLeft: -184, scrollLeft: 400 });
    alignActiveTab(list, tab);
    expect(list.scrollLeft).toBe(200); // 400 + (-184 - 16)
  });

  it("floors a fractional position so it never overshoots the margin", () => {
    const { list, tab } = strip({ scrollWidth: 900, clientWidth: 358, listLeft: 16, tabLeft: 375.97 });
    alignActiveTab(list, tab);
    expect(list.scrollLeft).toBe(359); // 359.97 floored, not 360
  });

  it("does nothing when the strip fits, or when it is already aligned", () => {
    const fits = strip({ scrollWidth: 358, clientWidth: 358, listLeft: 16, tabLeft: 116 });
    alignActiveTab(fits.list, fits.tab);
    expect(fits.list.scrollLeft).toBe(0);
    const aligned = strip({ scrollWidth: 900, clientWidth: 358, listLeft: 16, tabLeft: 16, scrollLeft: 120 });
    alignActiveTab(aligned.list, aligned.tab);
    expect(aligned.list.scrollLeft).toBe(120);
  });

  it("is safe with nothing to align", () => {
    expect(() => alignActiveTab(null, null)).not.toThrow();
    const { list } = strip({ scrollWidth: 900, clientWidth: 358, listLeft: 0, tabLeft: 0 });
    expect(() => alignActiveTab(list, null)).not.toThrow();
  });
});
