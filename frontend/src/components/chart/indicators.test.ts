import { describe, expect, it } from "vitest";
import {
  atrValues, bollingerValues, COLOR, DEFAULT_INDICATORS, emaValues, INDICATORS, INDICATOR_IDS, isIndicatorId,
  legendFor, MAX_INDICATORS, normaliseSelection, readoutFor, rsiValues, selectionKey, smaValues, vwapValues,
  type IndicatorId,
} from "./indicators";
import type { ChartBar } from "../CandlestickChart";

const round2 = (xs: (number | null)[]) => xs.filter((v) => v != null).map((v) => Number((v as number).toFixed(2)));

describe("RSI (Wilder), against the published worked example", () => {
  // The classic 14-period example: closes, and the RSI values it publishes from the 15th close on.
  const closes = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931, 46.0328,
    45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7138, 46.4515, 45.7835,
    45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314];
  const published = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87,
    45.46, 37.30, 33.08, 37.77];

  it("reproduces every published value", () => {
    expect(round2(rsiValues(closes, 14))).toEqual(published);
  });
  it("is empty until there are period + 1 closes, and starts at index `period`", () => {
    const r = rsiValues(closes, 14);
    expect(r.slice(0, 14).every((v) => v === null)).toBe(true);
    expect(r[14]).not.toBeNull();
    expect(rsiValues(closes.slice(0, 14), 14).every((v) => v === null)).toBe(true);
  });
  it("reads 100 with no losses and 50 with no movement", () => {
    expect(rsiValues([1, 2, 3, 4, 5, 6], 3)[3]).toBe(100);
    expect(rsiValues([5, 5, 5, 5, 5, 5], 3)[3]).toBe(50);
  });
  it("stays within 0 to 100", () => {
    for (const v of rsiValues(closes.map((c, i) => c * (i % 2 ? 1.07 : 0.93)), 5)) if (v != null) { expect(v).toBeGreaterThanOrEqual(0); expect(v).toBeLessThanOrEqual(100); }
  });
});

describe("ATR (Wilder), checked by hand", () => {
  // TR: 2, 2, 3 -> first ATR(3) = 7/3; next bar TR 2 -> (7/3*2 + 2)/3
  const highs = [10, 11, 12, 13], lows = [8, 9, 9, 11], closes = [9, 10, 11, 12];
  it("uses high-low for the first bar, the largest of three ranges after, and Wilder smoothing", () => {
    const a = atrValues(highs, lows, closes, 3);
    expect(a[0]).toBeNull(); expect(a[1]).toBeNull();
    expect(a[2]).toBeCloseTo(7 / 3, 10);
    expect(a[3]).toBeCloseTo((7 / 3 * 2 + 2) / 3, 10);
  });
  it("counts a gap: yesterday's close outside today's range widens the true range", () => {
    // bar 1 gaps up: high 20, low 19, previous close 10 -> TR = 10, not 1
    const a = atrValues([11, 20], [9, 19], [10, 19.5], 2);
    expect(a[1]).toBeCloseTo((2 + 10) / 2, 10);
  });
  it("is empty with too few bars or mismatched inputs", () => {
    expect(atrValues([1, 2], [0, 1], [1, 2], 3).every((v) => v === null)).toBe(true);
    expect(atrValues([1, 2, 3], [0, 1], [1, 2, 3], 2).every((v) => v === null)).toBe(true);
  });
});

describe("Bollinger bands, checked by hand", () => {
  it("is the SMA with population-standard-deviation bands", () => {
    const b = bollingerValues([1, 2, 3, 4, 5], 3, 2);
    const sd = Math.sqrt(2 / 3);
    expect(b.mid[2]).toBeCloseTo(2, 10);
    expect(b.upper[2]).toBeCloseTo(2 + 2 * sd, 10);
    expect(b.lower[2]).toBeCloseTo(2 - 2 * sd, 10);
    expect(b.mid[4]).toBeCloseTo(4, 10);
    expect(b.upper[0]).toBeNull();
  });
  it("collapses to the average when price does not move", () => {
    const b = bollingerValues([7, 7, 7, 7], 3, 2);
    expect(b.upper[3]).toBe(7); expect(b.lower[3]).toBe(7);
  });
});

describe("moving averages", () => {
  it("SMA is the mean of its own window and null before it fills", () => {
    expect(smaValues([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
    expect(smaValues([1, 2], 3)).toEqual([null, null]);
  });
  it("EMA is seeded with the SMA, then recurses with 2/(n+1)", () => {
    expect(emaValues([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);   // k = 0.5: 4*.5+2*.5=3, 5*.5+3*.5=4
    expect(emaValues([1, 2], 3)).toEqual([null, null]);
  });
});

describe("VWAP", () => {
  it("is cumulative over the loaded bars and never divides by zero volume", () => {
    const bars = [
      { timestamp: "2026-08-14T09:30:00Z", open: 1, high: 3, low: 1, close: 2, volume: 100 },
      { timestamp: "2026-08-14T09:45:00Z", open: 1, high: 6, low: 4, close: 5, volume: 0 },
    ] as ChartBar[];
    const v = vwapValues(bars);
    expect(v[0]).toBeCloseTo(2, 10);                                   // typical (3+1+2)/3 = 2
    expect(Number.isFinite(v[1])).toBe(true);                          // zero volume is floored at 1
  });
});

describe("the registry", () => {
  it("has unique ids and every default is a real indicator", () => {
    expect(new Set(INDICATOR_IDS).size).toBe(INDICATORS.length);
    expect(DEFAULT_INDICATORS.every(isIndicatorId)).toBe(true);
    expect(DEFAULT_INDICATORS.length).toBeLessThanOrEqual(MAX_INDICATORS);
  });
  it("the default is exactly what the chart drew before the picker existed", () => {
    expect([...DEFAULT_INDICATORS]).toEqual(["ma5", "ma10", "ma20", "vwap", "volume", "macd"]);
  });
  it("no indicator is drawn in the green or red that mean profit and loss", () => {
    for (const d of INDICATORS) {
      expect(d.color).not.toBe(COLOR.upCandle);
      expect(d.color).not.toBe(COLOR.downCandle);
    }
  });
  it("every indicator has a short phrase that fits a compact row", () => {
    for (const d of INDICATORS) {
      expect(d.short.length).toBeGreaterThan(10);
      expect(d.short.length).toBeLessThanOrEqual(36);
      expect(d.short.toLowerCase()).not.toMatch(/\b(buy|sell|signal to|will rise|will fall|guarantee)\b/);
    }
  });
  it("every indicator explains itself in a sentence and none claims to predict", () => {
    for (const d of INDICATORS) {
      expect(d.description.length).toBeGreaterThan(15);
      expect(d.description.toLowerCase()).not.toMatch(/\b(buy|sell|signal to|will rise|will fall|guarantee)\b/);
    }
  });
});

describe("normaliseSelection", () => {
  it("keeps known ids, drops unknown and duplicate ones, and orders them as the registry does", () => {
    expect(normaliseSelection(["macd", "nonsense", "ma5", "ma5", 7, null, "rsi"])).toEqual(["ma5", "macd", "rsi"]);
  });
  it("is empty for anything that is not an array", () => {
    for (const v of [undefined, null, "ma5", 5, {}, { 0: "ma5" }]) expect(normaliseSelection(v)).toEqual([]);
  });
  it("cannot exceed the cap, and cuts from the end of the registry order", () => {
    const all = normaliseSelection(INDICATOR_IDS);
    expect(all).toHaveLength(MAX_INDICATORS);
    expect(all).toEqual(INDICATOR_IDS.slice(0, MAX_INDICATORS));
  });
  it("rejects prototype-pollution style keys", () => {
    expect(normaliseSelection(["__proto__", "constructor", "toString", "ma5"])).toEqual(["ma5"]);
  });
  it("selectionKey is stable for the same set in any click order", () => {
    expect(selectionKey(normaliseSelection(["rsi", "ma5"]))).toBe(selectionKey(normaliseSelection(["ma5", "rsi"])));
  });
});

describe("readout and legend", () => {
  const bars: ChartBar[] = Array.from({ length: 60 }, (_, i) => ({
    timestamp: new Date(Date.UTC(2026, 7, 14, 13, 30) + i * 900e3).toISOString(),
    open: 100 + i * 0.2 + Math.sin(i), high: 101.5 + i * 0.2 + Math.sin(i), low: 99 + i * 0.2 + Math.sin(i),
    close: 100.5 + i * 0.2 + Math.sin(i / 2), volume: 1000 + i * 10,
  }));
  it("writes one item per indicator, in the order selected, with the label first", () => {
    const sel: IndicatorId[] = ["ma5", "ma50", "ema9", "bb", "rsi", "atr"];
    const texts = readoutFor(bars, sel).map((r) => r.text);
    expect(texts.map((t) => t.split(" ")[0])).toEqual(["MA5", "MA50", "EMA9", "BB", "RSI", "ATR"]);
    expect(texts.every((t) => !t.includes("NaN") && !t.includes("undefined"))).toBe(true);
  });
  it("MACD contributes three items, as before", () => {
    expect(readoutFor(bars, ["macd"]).map((r) => r.text.split(" ")[0])).toEqual(["MACD", "Sig", "Hist"]);
  });
  it("shows a dash, not a number, for an indicator without enough bars", () => {
    expect(readoutFor(bars.slice(0, 10), ["ma200"])[0].text).toBe("MA200 —");
    expect(readoutFor(bars.slice(0, 10), ["rsi"])[0].text).toBe("RSI —");
  });
  it("volume has no readout item, and an empty chart has none at all", () => {
    expect(readoutFor(bars, ["volume"])).toEqual([]);
    expect(readoutFor([], ["ma5", "rsi"])).toEqual([]);
  });
  it("the legend lists the selection and nothing else", () => {
    expect(legendFor(["rsi", "volume"]).map((l) => l.label)).toEqual(["RSI", "Volume"]);
    expect(legendFor([])).toEqual([]);
  });
  it("moving-average readouts match the series the chart draws", () => {
    const closes = bars.map((b) => b.close);
    const want = smaValues(closes, 20)[closes.length - 1] as number;
    expect(readoutFor(bars, ["ma20"])[0].text).toBe(`MA20 ${want.toFixed(2)}`);
  });
});
