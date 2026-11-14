import { describe, expect, it } from "vitest";
import {
  DEFAULT_LAYOUT, LAYOUT_STORAGE_KEY, LIMITS, PANEL_IDS, PANEL_TITLES, PRESET_IDS, clamp, clampFrac,
  fracAfterDrag, loadLayout, normalizeSymbol, parseLayout, presetLayout, saveLayout, isPanelId,
} from "./layout";
import { DEFAULT_WATCHLIST, WATCHLIST_STORAGE_KEY, readWatchlist } from "./useWatchlist";

const store = (initial: Record<string, string> = {}) => {
  const m = new Map(Object.entries(initial));
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
};

describe("clamp", () => {
  it("keeps values inside the range and survives NaN and Infinity", () => {
    expect(clamp(5, 0, 1)).toBe(1);
    expect(clamp(-5, 0, 1)).toBe(0);
    expect(clamp(0.5, 0, 1)).toBe(0.5);
    expect(clamp(NaN, 0.2, 0.8)).toBe(0.2);
    expect(clamp(Infinity, 0.2, 0.8)).toBe(0.2);
  });
});

describe("panels", () => {
  it("every panel has a title and is recognised", () => {
    for (const id of PANEL_IDS) { expect(PANEL_TITLES[id]).toBeTruthy(); expect(isPanelId(id)).toBe(true); }
  });
  it("rejects anything that is not a panel id, including prototype keys", () => {
    for (const bad of ["", "chart", "constructor", "__proto__", "toString", 7, null, undefined, {}]) expect(isPanelId(bad), String(bad)).toBe(false);
  });
});

describe("presets", () => {
  it("every preset is a valid layout within the limits", () => {
    for (const id of PRESET_IDS) {
      const l = presetLayout(id);
      expect(parseLayout(l)).toEqual(l);
      for (const k of ["chartFrac", "topFrac", "sideSplit"] as const) {
        expect(l[k]).toBeGreaterThanOrEqual(LIMITS[k].min);
        expect(l[k]).toBeLessThanOrEqual(LIMITS[k].max);
      }
    }
  });
  it("'Chart only' closes both regions and 'Trader' opens both", () => {
    expect(presetLayout("focus")).toMatchObject({ sideOpen: false, bottomOpen: false });
    expect(presetLayout("trader")).toMatchObject({ sideOpen: true, bottomOpen: true });
  });
  it("the default is the Trader preset, and presets are independent copies", () => {
    expect(DEFAULT_LAYOUT).toEqual(presetLayout("trader"));
    const a = presetLayout("trader"); a.chartFrac = 0.5;
    expect(presetLayout("trader").chartFrac).not.toBe(0.5);
  });
});

describe("parseLayout never produces a broken layout", () => {
  it("falls back to the default for non-objects and the wrong version", () => {
    for (const bad of [null, undefined, 5, "x", [], true, { v: 2 }, { v: "1" }, {}]) {
      expect(parseLayout(bad), JSON.stringify(bad)).toEqual(DEFAULT_LAYOUT);
    }
  });
  it("clamps out-of-range and non-finite sizes", () => {
    const l = parseLayout({ ...DEFAULT_LAYOUT, chartFrac: 5, topFrac: -3, sideSplit: 0 });
    expect(l.chartFrac).toBe(LIMITS.chartFrac.max);
    expect(l.topFrac).toBe(LIMITS.topFrac.min);
    expect(l.sideSplit).toBe(LIMITS.sideSplit.min);
    expect(parseLayout({ ...DEFAULT_LAYOUT, chartFrac: "0.7" }).chartFrac).toBe(DEFAULT_LAYOUT.chartFrac);
    expect(parseLayout({ ...DEFAULT_LAYOUT, chartFrac: NaN }).chartFrac).toBe(LIMITS.chartFrac.min);
  });
  it("replaces unknown panels with the default and keeps known ones", () => {
    const l = parseLayout({ ...DEFAULT_LAYOUT, sideTop: "evil", sideBottom: "news", bottom: "__proto__" });
    expect(l.sideTop).toBe(DEFAULT_LAYOUT.sideTop);
    expect(l.sideBottom).toBe("news");
    expect(l.bottom).toBe(DEFAULT_LAYOUT.bottom);
  });
  it("ignores extra keys and non-boolean flags", () => {
    const l = parseLayout({ ...DEFAULT_LAYOUT, sideOpen: "yes", bottomOpen: 0, polluted: true });
    expect(l.sideOpen).toBe(DEFAULT_LAYOUT.sideOpen);
    expect(l.bottomOpen).toBe(DEFAULT_LAYOUT.bottomOpen);
    expect("polluted" in l).toBe(false);
  });
  it("a JSON '__proto__' payload does not pollute anything", () => {
    const l = parseLayout(JSON.parse('{"v":1,"__proto__":{"sideOpen":false},"chartFrac":0.5}'));
    expect(l.chartFrac).toBe(0.5);
    expect(({} as Record<string, unknown>).sideOpen).toBeUndefined();
  });
});

describe("load and save", () => {
  it("round-trips", () => {
    const s = store();
    const layout = { ...presetLayout("research"), chartFrac: 0.55 };
    saveLayout(s, layout);
    expect(loadLayout(s)).toEqual(layout);
    expect(s.m.has(LAYOUT_STORAGE_KEY)).toBe(true);
  });
  it("corrupt JSON, missing storage and throwing storage all give the default", () => {
    expect(loadLayout(store({ [LAYOUT_STORAGE_KEY]: "{not json" }))).toEqual(DEFAULT_LAYOUT);
    expect(loadLayout(null)).toEqual(DEFAULT_LAYOUT);
    expect(loadLayout(undefined)).toEqual(DEFAULT_LAYOUT);
    expect(loadLayout({ getItem: () => { throw new Error("denied"); } })).toEqual(DEFAULT_LAYOUT);
  });
  it("saving into broken storage does not throw", () => {
    expect(() => saveLayout({ setItem: () => { throw new Error("quota"); } }, DEFAULT_LAYOUT)).not.toThrow();
    expect(() => saveLayout(null, DEFAULT_LAYOUT)).not.toThrow();
  });
});

describe("dragging", () => {
  it("moves by the pixel share of the region and respects the limits", () => {
    expect(fracAfterDrag(0.5, 100, 1000, "chartFrac")).toBeCloseTo(0.6);
    expect(fracAfterDrag(0.5, -100, 1000, "chartFrac")).toBeCloseTo(0.4);
    expect(fracAfterDrag(0.5, 9999, 1000, "chartFrac")).toBe(LIMITS.chartFrac.max);
    expect(fracAfterDrag(0.5, -9999, 1000, "chartFrac")).toBe(LIMITS.chartFrac.min);
  });
  it("a zero-size region does not divide by zero", () => {
    expect(fracAfterDrag(0.7, 50, 0, "chartFrac")).toBe(0.7);
    expect(fracAfterDrag(0.7, 50, NaN, "chartFrac")).toBe(0.7);
  });
  it("clampFrac uses each region's own limits", () => {
    expect(clampFrac("topFrac", 0)).toBe(LIMITS.topFrac.min);
    expect(clampFrac("sideSplit", 1)).toBe(LIMITS.sideSplit.max);
  });
});

describe("symbols", () => {
  it("normalizes and validates like the chart page", () => {
    expect(normalizeSymbol(" aapl ")).toBe("AAPL");
    expect(normalizeSymbol("BRK.B")).toBe("BRK.B");
    expect(normalizeSymbol("^GSPC")).toBe("^GSPC");
    for (const bad of ["", "   ", "A B", "AAPL;DROP", "<script>", "a".repeat(21), "AAPL/../x"]) expect(normalizeSymbol(bad), bad).toBeNull();
  });
});

describe("watchlist storage (shared with the Chart page)", () => {
  it("uses the Chart page's key", () => expect(WATCHLIST_STORAGE_KEY).toBe("olbos.chart.watchlist"));
  it("reads a saved list, normalizing and de-duplicating", () => {
    const s = store({ [WATCHLIST_STORAGE_KEY]: JSON.stringify(["aapl", "AAPL", "msft", "bad symbol!", 5]) });
    expect(readWatchlist(s)).toEqual(["AAPL", "MSFT"]);
  });
  it("falls back to the default for empty, malformed or unusable data", () => {
    for (const raw of ["[]", "{", "null", '"x"', JSON.stringify(["!!!", 7])]) {
      expect(readWatchlist(store({ [WATCHLIST_STORAGE_KEY]: raw })), raw).toEqual(DEFAULT_WATCHLIST);
    }
    expect(readWatchlist(null)).toEqual(DEFAULT_WATCHLIST);
    expect(readWatchlist({ getItem: () => { throw new Error("x"); } })).toEqual(DEFAULT_WATCHLIST);
  });
});
