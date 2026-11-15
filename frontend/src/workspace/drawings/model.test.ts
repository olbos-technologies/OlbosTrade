import { describe, expect, it } from "vitest";
import {
  DEFAULT_COLOR, DEFAULT_WIDTH, DRAWINGS_STORAGE_KEY, KIND_POINTS, MAX_PER_SYMBOL, MAX_SYMBOLS, PALETTE,
  isKind, loadDrawings, makeDrawing, newId, parseDrawing, parseDrawings, parseStore, saveDrawings, type Drawing,
} from "./model";

const store = (initial: Record<string, string> = {}) => {
  const m = new Map(Object.entries(initial));
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
};
const trend = (id = "a1"): Drawing => ({ id, kind: "trend", points: [{ time: 1000, price: 10 }, { time: 2000, price: 12 }], color: "#7daaff", width: 2 });

describe("kinds and ids", () => {
  it("knows its kinds, and rejects anything else including prototype keys", () => {
    for (const k of ["trend", "hline", "vline", "rect", "fib"]) expect(isKind(k)).toBe(true);
    for (const k of ["", "arrow", "constructor", "__proto__", 5, null]) expect(isKind(k), String(k)).toBe(false);
  });
  it("every kind has a point count of 1 or 2", () => {
    for (const n of Object.values(KIND_POINTS)) expect([1, 2]).toContain(n);
  });
  it("makes unique, storable ids", () => {
    const ids = new Set(Array.from({ length: 500 }, newId));
    expect(ids.size).toBe(500);
    for (const id of ids) expect(parseDrawing({ ...trend(), id })).not.toBeNull();
  });
  it("the palette avoids pure green and red, which mean profit and loss", () => {
    for (const c of PALETTE) {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.value.slice(i, i + 2), 16));
      const pureGreen = g > 150 && r < 90 && b < 140, pureRed = r > 200 && g < 110 && b < 110;
      expect(pureGreen || pureRed, c.name).toBe(false);
    }
  });
});

describe("parseDrawing rejects anything malformed", () => {
  it("accepts a good one and keeps its fields", () => {
    expect(parseDrawing(trend())).toEqual(trend());
  });
  it("rejects wrong shapes", () => {
    for (const bad of [null, undefined, 5, "x", [], {}, { ...trend(), id: "" }, { ...trend(), id: "has space" }, { ...trend(), id: "x".repeat(65) }, { ...trend(), kind: "arrow" }]) {
      expect(parseDrawing(bad), JSON.stringify(bad)).toBeNull();
    }
  });
  it("rejects the wrong number of points for the kind", () => {
    expect(parseDrawing({ ...trend(), points: [{ time: 1, price: 1 }] })).toBeNull();
    expect(parseDrawing({ ...trend(), kind: "hline" })).toBeNull(); // hline needs exactly one
    expect(parseDrawing({ ...trend(), kind: "hline", points: [{ time: 1, price: 1 }] })).not.toBeNull();
    expect(parseDrawing({ ...trend(), points: [] })).toBeNull();
  });
  it("rejects non-finite, out-of-range and wrongly typed anchors", () => {
    for (const p of [{ time: NaN, price: 1 }, { time: 1, price: Infinity }, { time: "1", price: 1 }, { time: -5, price: 1 }, { time: 5e9, price: 1 }, { time: 1, price: 1e12 }, null, { time: 1 }]) {
      expect(parseDrawing({ ...trend(), points: [p, { time: 2, price: 2 }] }), JSON.stringify(p)).toBeNull();
    }
  });
  it("repairs a bad colour or width instead of rejecting the drawing", () => {
    const d = parseDrawing({ ...trend(), color: "url(javascript:alert(1))", width: 9 })!;
    expect(d.color).toBe(DEFAULT_COLOR);
    expect(d.width).toBe(DEFAULT_WIDTH);
    expect(parseDrawing({ ...trend(), color: "#ABCDEF" })!.color).toBe("#abcdef");
  });
  it("drops unknown extra fields", () => {
    const d = parseDrawing({ ...trend(), evil: "<script>", __proto__: { x: 1 } })!;
    expect("evil" in d).toBe(false);
  });
});

describe("parseDrawings and parseStore", () => {
  it("keeps the good, drops the bad and duplicate ids, never throws", () => {
    const list = parseDrawings([trend("a"), null, 5, trend("a"), trend("b"), { id: "c" }]);
    expect(list.map((d) => d.id)).toEqual(["a", "b"]);
    for (const bad of [null, undefined, 5, "x", {}]) expect(parseDrawings(bad)).toEqual([]);
  });
  it("caps one symbol's drawings", () => {
    const many = Array.from({ length: MAX_PER_SYMBOL + 40 }, (_, i) => trend(`d${i}`));
    expect(parseDrawings(many)).toHaveLength(MAX_PER_SYMBOL);
  });
  it("keeps only well-formed symbol keys, so a hostile key cannot get in", () => {
    const raw = JSON.parse('{"AAPL":[{"id":"a","kind":"hline","points":[{"time":1,"price":2}]}],"__proto__":[{"id":"b","kind":"hline","points":[{"time":1,"price":2}]}],"bad key":[],"aapl":[]}');
    const s = parseStore(raw);
    expect(Object.keys(s)).toEqual(["AAPL"]);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
  it("tolerates junk at the top level", () => {
    for (const bad of [null, 5, "x", [], undefined]) expect(Object.keys(parseStore(bad))).toEqual([]);
  });
});

describe("storage", () => {
  it("round-trips per symbol and keeps the others", () => {
    const s = store();
    saveDrawings(s, "AAPL", [trend("a")]);
    saveDrawings(s, "MSFT", [trend("b")]);
    expect(loadDrawings(s, "AAPL").map((d) => d.id)).toEqual(["a"]);
    expect(loadDrawings(s, "MSFT").map((d) => d.id)).toEqual(["b"]);
    expect(loadDrawings(s, "TSLA")).toEqual([]);
    saveDrawings(s, "AAPL", [trend("a"), trend("c")]);
    expect(loadDrawings(s, "AAPL")).toHaveLength(2);
    expect(loadDrawings(s, "MSFT")).toHaveLength(1);
  });
  it("saving an empty list removes the symbol", () => {
    const s = store();
    saveDrawings(s, "AAPL", [trend("a")]);
    saveDrawings(s, "AAPL", []);
    expect(JSON.parse(s.m.get(DRAWINGS_STORAGE_KEY)!)).toEqual({});
  });
  it("drops the oldest symbols past the limit", () => {
    const s = store();
    for (let i = 0; i < MAX_SYMBOLS + 5; i++) saveDrawings(s, `S${i}`, [trend(`d${i}`)]);
    const keys = Object.keys(JSON.parse(s.m.get(DRAWINGS_STORAGE_KEY)!));
    expect(keys).toHaveLength(MAX_SYMBOLS);
    expect(keys).not.toContain("S0");
    expect(keys).toContain(`S${MAX_SYMBOLS + 4}`);
  });
  it("refuses a bad symbol and survives corrupt, missing or throwing storage", () => {
    const s = store({ [DRAWINGS_STORAGE_KEY]: "{not json" });
    expect(loadDrawings(s, "AAPL")).toEqual([]);
    expect(loadDrawings(null, "AAPL")).toEqual([]);
    expect(loadDrawings({ getItem: () => { throw new Error("denied"); } }, "AAPL")).toEqual([]);
    expect(loadDrawings(s, "bad symbol")).toEqual([]);
    saveDrawings(s, "bad symbol", [trend()]);
    expect(() => saveDrawings({ getItem: () => null, setItem: () => { throw new Error("quota"); } }, "AAPL", [trend()])).not.toThrow();
    expect(() => saveDrawings(null, "AAPL", [trend()])).not.toThrow();
  });
  it("repairs what it reads: a tampered store cannot inject a bad drawing", () => {
    const s = store({ [DRAWINGS_STORAGE_KEY]: JSON.stringify({ AAPL: [{ id: "a", kind: "trend", points: [{ time: 1, price: 1 }, { time: 2, price: 2 }], color: "red;x", width: 7 }, { id: "b", kind: "nope" }] }) });
    const list = loadDrawings(s, "AAPL");
    expect(list).toHaveLength(1);
    expect(list[0].color).toBe(DEFAULT_COLOR);
  });
  it("makeDrawing copies its anchors so later edits cannot change the original", () => {
    const pts = [{ time: 1, price: 1 }, { time: 2, price: 2 }];
    const d = makeDrawing("trend", pts);
    pts[0].price = 999;
    expect(d.points[0].price).toBe(1);
  });
});
