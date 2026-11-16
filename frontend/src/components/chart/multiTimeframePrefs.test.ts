import { describe, expect, it } from "vitest";
import { INTERVAL_OPTIONS } from "./timeframes";
import {
  DEFAULT_INTERVALS, MAX_TILES, MIN_TILES, MTF_LAYOUT_KEY, RANGE_FOR_INTERVAL, defaultLayout, normaliseLayout, readLayout, resizeLayout, writeLayout,
} from "./multiTimeframePrefs";

const store = (initial?: string) => {
  const m = new Map<string, string>(initial === undefined ? [] : [[MTF_LAYOUT_KEY, initial]]);
  return { m, getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } };
};

describe("defaults", () => {
  it("every default layout is a real one, higher timeframes first", () => {
    for (const n of [2, 3, 4]) {
      expect(DEFAULT_INTERVALS[n]).toHaveLength(n);
      expect(defaultLayout(n).intervals).toEqual(DEFAULT_INTERVALS[n]);
    }
    expect(defaultLayout().intervals).toEqual(["1d", "1h", "15m"]);
  });
  it("every interval has a range, and each is one the chart page offers", () => {
    for (const o of INTERVAL_OPTIONS) expect(RANGE_FOR_INTERVAL[o.key]).toBeTruthy();
  });
  it("a count outside the allowed range is clamped, and junk becomes the default", () => {
    expect(defaultLayout(1).intervals).toHaveLength(MIN_TILES);
    expect(defaultLayout(99).intervals).toHaveLength(MAX_TILES);
    expect(defaultLayout(NaN).intervals).toHaveLength(3);
  });
});

describe("normaliseLayout", () => {
  it("keeps known intervals in the order given", () => {
    expect(normaliseLayout({ intervals: ["1w", "1h"] }).intervals).toEqual(["1w", "1h"]);
  });
  it("drops unknown ones, and falls back to the default if fewer than two are left", () => {
    expect(normaliseLayout({ intervals: ["1d", "nope", "15m"] }).intervals).toEqual(["1d", "15m"]);
    expect(normaliseLayout({ intervals: ["1d", "nope"] })).toEqual(defaultLayout());
  });
  it("never has more than the maximum", () => {
    expect(normaliseLayout({ intervals: ["1m", "5m", "15m", "30m", "1h", "1d"] }).intervals).toHaveLength(MAX_TILES);
  });
  it.each([null, undefined, 5, "x", [], {}, { intervals: "1d" }, { intervals: null }, { intervals: { 0: "1d" } }])("falls back to the default for %j", (v) => {
    expect(normaliseLayout(v)).toEqual(defaultLayout());
  });
  it("rejects prototype-pollution style values", () => {
    expect(normaliseLayout({ intervals: ["__proto__", "constructor", "1d", "1h"] }).intervals).toEqual(["1d", "1h"]);
  });
});

describe("resizeLayout", () => {
  it("keeps the charts already chosen and fills new ones from the defaults for that size", () => {
    expect(resizeLayout({ intervals: ["1w", "4h"] }, 4).intervals).toEqual(["1w", "4h", "1d", "1h"]);
  });
  it("never adds a copy of a chart that is already there", () => {
    expect(resizeLayout({ intervals: ["1d", "1h", "15m"] }, 4).intervals).toEqual(["1d", "1h", "15m", "1w"]);
    expect(resizeLayout({ intervals: ["1w", "1d"] }, 3).intervals).toEqual(["1w", "1d", "1h"]);
    for (const start of [["1d", "15m"], ["1h", "5m"], ["1w", "1M"]] as const) {
      const out = resizeLayout({ intervals: [...start] }, 4).intervals;
      expect(new Set(out).size).toBe(out.length);
    }
  });
  it("drops from the end when there are fewer", () => {
    expect(resizeLayout({ intervals: ["1w", "1d", "1h", "15m"] }, 2).intervals).toEqual(["1w", "1d"]);
  });
  it("stays within the allowed counts", () => {
    expect(resizeLayout(defaultLayout(3), 1).intervals).toHaveLength(MIN_TILES);
    expect(resizeLayout(defaultLayout(3), 9).intervals).toHaveLength(MAX_TILES);
  });
});

describe("the saved layout", () => {
  it("is the default with no storage or nothing saved", () => {
    expect(readLayout(store())).toEqual(defaultLayout());
    expect(readLayout(null)).toEqual(defaultLayout());
  });
  it("round-trips", () => {
    const s = store();
    writeLayout(s, { intervals: ["1w", "1d", "30m"] });
    expect(readLayout(s).intervals).toEqual(["1w", "1d", "30m"]);
  });
  it.each(["not json", "{", "null", "[]", '{"intervals":["x"]}', ""])("falls back for %j", (raw) => {
    expect(readLayout(store(raw))).toEqual(defaultLayout());
  });
  it("survives storage that throws, and writes only a valid layout", () => {
    const boom = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(readLayout(boom)).toEqual(defaultLayout());
    expect(() => writeLayout(boom, defaultLayout())).not.toThrow();
    const s = store();
    writeLayout(s, { intervals: ["1d", "bogus", "1h"] } as never);
    expect(JSON.parse(s.m.get(MTF_LAYOUT_KEY)!).intervals).toEqual(["1d", "1h"]);
  });
});
