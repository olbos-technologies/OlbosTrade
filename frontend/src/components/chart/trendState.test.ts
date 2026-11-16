import { describe, expect, it } from "vitest";
import type { ChartBar } from "../CandlestickChart";
import { alignment, sideOf, trendState, type TrendState } from "./trendState";

const bars = (closes: number[]): ChartBar[] =>
  closes.map((c, i) => ({ timestamp: new Date(Date.UTC(2026, 0, 1) + i * 86400e3).toISOString(), open: c, high: c + 1, low: c - 1, close: c, volume: 1000 }));
const rising = (n: number) => bars(Array.from({ length: n }, (_, i) => 100 + i));
const falling = (n: number) => bars(Array.from({ length: n }, (_, i) => 200 - i));
const state = (vsMa50: TrendState["vsMa50"]): TrendState => ({ close: 1, ma20: 1, ma50: 1, vsMa20: vsMa50, vsMa50 });

describe("sideOf", () => {
  it("says above, below, or at, and at only within a hair of the average", () => {
    expect(sideOf(101, 100)).toBe("above");
    expect(sideOf(99, 100)).toBe("below");
    expect(sideOf(100, 100)).toBe("at");
    expect(sideOf(100.005, 100)).toBe("at");          // 0.005%: a wobble, not a flip
    expect(sideOf(100.02, 100)).toBe("above");
  });
  it("says nothing when it cannot: no average, a non-number, or an average of zero", () => {
    expect(sideOf(100, null)).toBeNull();
    expect(sideOf(null, 100)).toBeNull();
    expect(sideOf(NaN, 100)).toBeNull();
    expect(sideOf(100, 0)).toBeNull();
    expect(sideOf(100, Infinity)).toBeNull();
  });
});

describe("trendState", () => {
  it("a steadily rising series is above both averages, a falling one below both", () => {
    expect(trendState(rising(60))).toMatchObject({ vsMa20: "above", vsMa50: "above" });
    expect(trendState(falling(60))).toMatchObject({ vsMa20: "below", vsMa50: "below" });
  });
  it("a flat series is at its averages", () => {
    expect(trendState(bars(Array(60).fill(100)))).toMatchObject({ vsMa20: "at", vsMa50: "at", ma20: 100, ma50: 100 });
  });
  it("needs 20 bars for the 20-bar average and 50 for the 50-bar one, and never invents either", () => {
    expect(trendState(rising(19))).toMatchObject({ ma20: null, vsMa20: null, ma50: null, vsMa50: null });
    expect(trendState(rising(30))).toMatchObject({ vsMa20: "above", ma50: null, vsMa50: null });
    expect(trendState(rising(50)).vsMa50).toBe("above");
  });
  it("is empty for no bars", () => {
    expect(trendState([])).toEqual({ close: null, ma20: null, ma50: null, vsMa20: null, vsMa50: null });
  });
  it("uses the last close against the averages of the closes up to it", () => {
    const s = trendState(bars([...Array(49).fill(100), 120]));
    expect(s.close).toBe(120);
    expect(s.ma50).toBeCloseTo((49 * 100 + 120) / 50, 10);
    expect(s.vsMa50).toBe("above");
  });
});

describe("alignment", () => {
  it("all above, all below, and all at say so", () => {
    expect(alignment([state("above"), state("above"), state("above")]).summary).toBe("Price is above its 50-bar average on all 3 timeframes.");
    expect(alignment([state("below"), state("below")]).summary).toBe("Price is below its 50-bar average on all 2 timeframes.");
    expect(alignment([state("at"), state("at")]).summary).toBe("Price is at its 50-bar average on all 2 timeframes.");
  });
  it("a disagreement is called mixed, with the counts", () => {
    const a = alignment([state("above"), state("below"), state("above"), state("at")]);
    expect(a).toMatchObject({ known: 4, above: 2, below: 1, at: 1 });
    expect(a.summary).toBe("Mixed: above the 50-bar average on 2 of 4, below on 1, at it on 1.");
  });
  it("says so when some timeframes cannot be compared, and does not count them", () => {
    const a = alignment([state("above"), null, state("above")], 3);
    expect(a.known).toBe(2);
    expect(a.summary).toBe("Price is above its 50-bar average on the 2 of 3 timeframes with enough bars.");
    expect(alignment([state("above"), state("below"), null], 3).summary).toBe("Mixed: above the 50-bar average on 1 of 2, below on 1 (1 without enough bars).");
  });
  it("says it cannot compare when none can", () => {
    expect(alignment([null, null]).summary).toBe("Not enough bars loaded to compare the timeframes.");
    expect(alignment([]).summary).toBe("Not enough bars loaded to compare the timeframes.");
    expect(alignment([{ close: 1, ma20: null, ma50: null, vsMa20: null, vsMa50: null }]).known).toBe(0);
  });
  it("is worded as description, never as advice", () => {
    const text = [
      alignment([state("above"), state("above")]).summary, alignment([state("below"), state("below")]).summary,
      alignment([state("above"), state("below")]).summary,
    ].join(" ").toLowerCase();
    expect(text).not.toMatch(/\b(buy|sell|bullish|bearish|long|short|signal|entry)\b/);
  });
});
