import { describe, expect, it } from "vitest";
import { computeIntradayLevels, describeLevels, levelLines, type BarLike } from "./intradayLevels";

/** UTC offset of New York on the given date string: winter (EST) is UTC-5, summer (EDT) UTC-4. */
const offset = (date: string) => (Number(date.slice(5, 7)) >= 4 && Number(date.slice(5, 7)) <= 10 ? 4 : 5);

/** One 15-minute bar starting at hh:mm New York time on `date`. */
function bar(date: string, hh: number, mm: number, o: number, h: number, l: number, c: number, v = 1000): BarLike {
  const utc = Date.UTC(+date.slice(0, 4), +date.slice(5, 7) - 1, +date.slice(8, 10), hh + offset(date), mm);
  return { timestamp: new Date(utc).toISOString(), open: o, high: h, low: l, close: c, volume: v };
}

/** A whole prior session, 09:30 to 15:45, with a known high, low and close. */
function priorSession(date: string, until = 15 * 60 + 45): BarLike[] {
  const out: BarLike[] = [];
  for (let m = 9 * 60 + 30; m <= until; m += 15) {
    out.push(bar(date, Math.floor(m / 60), m % 60, 100, 100.5, 99.5, 100));
  }
  out[3] = { ...out[3], high: 102 };      // prior high
  out[5] = { ...out[5], low: 98 };        // prior low
  out[out.length - 1] = { ...out[out.length - 1], close: 100.4 };   // prior close
  return out;
}

const PRIOR = "2026-11-16", TODAY = "2026-11-17";

function today(upToMinute = 10 * 60 + 30): BarLike[] {
  const rows: BarLike[] = [];
  const seq = [
    [9, 30, 101, 101.6, 100.8, 101.4],
    [9, 45, 101.4, 101.9, 101.2, 101.8],
    [10, 0, 101.8, 102.4, 101.7, 102.2],
    [10, 15, 102.2, 102.6, 102.0, 102.5],
  ] as const;
  for (const [h, m, o, hi, lo, c] of seq) if (h * 60 + m <= upToMinute) rows.push(bar(TODAY, h, m, o, hi, lo, c));
  return rows;
}

describe("computeIntradayLevels", () => {
  it("reads the prior session, the gap, the opening range, VWAP and where price sits", () => {
    const l = computeIntradayLevels([...priorSession(PRIOR), ...today()], "15m");
    expect(l.ready).toBe(true);
    expect(l.prior).toEqual({ date: PRIOR, high: 102, low: 98, close: 100.4 });
    expect(l.open).toBe(101);
    expect(l.gapPct).toBeCloseTo(((101 - 100.4) / 100.4) * 100, 6);
    expect(l.openingRange).toEqual({ high: 101.9, low: 100.8, complete: true });   // 09:30 and 09:45 bars only
    expect(l.last).toBe(102.5);
    expect(l.vsOpeningRange).toBe("above");
    expect(l.vsPriorRange).toBe("above");     // 102.5 is over the prior high of 102
    // equal volumes, so VWAP is the mean of the four typical prices
    const typ = [(101.6 + 100.8 + 101.4) / 3, (101.9 + 101.2 + 101.8) / 3, (102.4 + 101.7 + 102.2) / 3, (102.6 + 102.0 + 102.5) / 3];
    expect(l.vwap).toBeCloseTo(typ.reduce((a, b) => a + b) / 4, 6);
    expect(l.vsVwap).toBe("above");
  });

  it("calls the opening range still forming until a bar at or after 10:00 exists", () => {
    const l = computeIntradayLevels([...priorSession(PRIOR), ...today(9 * 60 + 45)], "15m");
    expect(l.openingRange).toEqual({ high: 101.9, low: 100.8, complete: false });
    expect(describeLevels(l)).toContain("still forming");
    expect(levelLines(l).map((x) => x.label)).toEqual(expect.arrayContaining(["~ORH", "~ORL"]));
  });

  it("works the same in summer time (UTC-4) as in winter (UTC-5)", () => {
    const P = "2026-08-12", T = "2026-08-13";
    const t = [bar(T, 9, 30, 50, 51, 49.5, 50.5), bar(T, 9, 45, 50.5, 51.2, 50, 51), bar(T, 10, 0, 51, 51.5, 50.8, 51.2)];
    const l = computeIntradayLevels([...priorSession(P), ...t], "15m");
    expect(l.ready).toBe(true);
    expect(l.openingRange).toEqual({ high: 51.2, low: 49.5, complete: true });
    expect(l.open).toBe(50);
  });

  it("says price is inside the opening range and below VWAP when it is", () => {
    const rows = [bar(TODAY, 9, 30, 101, 102, 100, 101.5), bar(TODAY, 9, 45, 101.5, 102, 100.5, 100.6)];
    const l = computeIntradayLevels([...priorSession(PRIOR), ...rows], "15m");
    expect(l.vsOpeningRange).toBe("inside");
    expect(l.vsVwap).toBe("below");
  });

  it("writes a negative gap with a minus sign and never as zero", () => {
    const rows = [bar(TODAY, 9, 30, 99, 99.5, 98.5, 99.2)];
    const l = computeIntradayLevels([...priorSession(PRIOR), ...rows], "15m");
    expect(l.gapPct!).toBeLessThan(-1);
    expect(describeLevels(l)).toMatch(/opened −1\.\d\d% vs prior close/);
  });

  it("ignores pre-market and after-hours bars", () => {
    const extra = [bar(TODAY, 8, 0, 120, 130, 90, 125), bar(TODAY, 16, 30, 50, 60, 40, 55)];
    const l = computeIntradayLevels([...priorSession(PRIOR), ...extra, ...today()], "15m");
    expect(l.openingRange).toEqual({ high: 101.9, low: 100.8, complete: true });
    expect(l.last).toBe(102.5);
    const withPriorExtra = computeIntradayLevels([...priorSession(PRIOR), bar(PRIOR, 17, 0, 1, 999, 0.5, 1), ...today()], "15m");
    expect(withPriorExtra.prior!.high).toBe(102);          // 999 after the close did not move the prior high
  });
});

describe("computeIntradayLevels: refuses rather than guesses", () => {
  it.each(["1h", "4h", "1d", "1w", "1M"] as const)("%s bars are not enough for day levels", (iv) => {
    const l = computeIntradayLevels([...priorSession(PRIOR), ...today()], iv);
    expect(l.ready).toBe(false);
    expect(l.reason).toMatch(/1, 5, 15 or 30 minute/);
  });

  it("one session only: asks for more history", () => {
    const l = computeIntradayLevels(today(), "15m");
    expect(l.ready).toBe(false);
    expect(l.reason).toMatch(/prior session/);
  });

  it("no bars at all", () => {
    expect(computeIntradayLevels([], "15m").ready).toBe(false);
  });

  it("a prior session that stops at 11:00 gives no prior levels and no gap", () => {
    const l = computeIntradayLevels([...priorSession(PRIOR, 11 * 60), ...today()], "15m");
    expect(l.prior).toBeUndefined();
    expect(l.gapPct).toBeUndefined();
    expect(l.openingRange).toBeDefined();          // today's own levels still stand
  });

  it("a half-day prior session (last bar 12:45) still counts", () => {
    const l = computeIntradayLevels([...priorSession(PRIOR, 12 * 60 + 45), ...today()], "15m");
    expect(l.prior).toBeDefined();
  });

  it("today's missing opening bar gives no open, no gap, no opening range and no VWAP", () => {
    const rows = [bar(TODAY, 9, 45, 101, 101.5, 100.5, 101.2), bar(TODAY, 10, 0, 101.2, 101.8, 101, 101.6)];
    const l = computeIntradayLevels([...priorSession(PRIOR), ...rows], "15m");
    expect(l.open).toBeUndefined();
    expect(l.gapPct).toBeUndefined();
    expect(l.openingRange).toBeUndefined();
    expect(l.vwap).toBeUndefined();
    expect(l.prior).toBeDefined();
  });

  it("skips a bar with a NaN price instead of using it", () => {
    const bad = bar(TODAY, 9, 30, NaN, 105, 90, 100);
    const l = computeIntradayLevels([...priorSession(PRIOR), bad, ...today().slice(1)], "15m");
    expect(l.openingRange).toBeUndefined();                          // the only 09:30 bar was broken, so no opening range
  });

  it("skips a bar whose high is below its low", () => {
    const crossed = bar(TODAY, 9, 30, 101, 99, 105, 100);           // high < low
    const l = computeIntradayLevels([...priorSession(PRIOR), crossed, ...today().slice(1)], "15m");
    expect(l.openingRange).toBeUndefined();
  });

  it("no volume: no VWAP, and the rest still works", () => {
    const rows = today().map((b) => ({ ...b, volume: 0 }));
    const l = computeIntradayLevels([...priorSession(PRIOR), ...rows], "15m");
    expect(l.vwap).toBeUndefined();
    expect(l.vsVwap).toBeUndefined();
    expect(l.openingRange).toBeDefined();
  });

  it("an unparseable timestamp is skipped", () => {
    const rows = [{ ...bar(TODAY, 9, 30, 1, 2, 0.5, 1), timestamp: "garbage" }, ...today()];
    expect(computeIntradayLevels([...priorSession(PRIOR), ...rows], "15m").ready).toBe(true);
  });
});

describe("levelLines and describeLevels", () => {
  const l = computeIntradayLevels([...priorSession(PRIOR), ...today()], "15m");

  it("draws the prior day, the opening range and VWAP, labelled", () => {
    const lines = levelLines(l);
    expect(lines.map((x) => x.label)).toEqual(["PDH", "PDL", "PDC", "ORH", "ORL", "VWAP"]);
    expect(lines.find((x) => x.label === "PDH")!.value).toBe(102);
  });

  it("never uses the colours that mean profit, loss or caution", () => {
    for (const line of levelLines(l)) {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(line.color.slice(i, i + 2), 16));
      const greenish = g > r + 40 && g > b + 40, reddish = r > g + 60 && r > b + 60, amberish = r > 180 && g > 120 && b < 100;
      expect(greenish || reddish || amberish, `${line.label} ${line.color}`).toBe(false);
    }
  });

  it("describes the day in words and never says to buy or sell", () => {
    const text = describeLevels(l);
    expect(text).toContain("prior day H 102.00 L 98.00 C 100.40");
    expect(text).toContain("opening range 100.80–101.90");
    expect(text).toContain("above the opening range and above VWAP");
    expect(text).not.toMatch(/\b(buy|sell|long|short|enter|exit|signal)\b/i);
  });

  it("says why when nothing is available, and draws nothing", () => {
    const none = computeIntradayLevels([], "15m");
    expect(describeLevels(none)).toMatch(/No bars/);
    expect(levelLines(none)).toEqual([]);
  });
});
