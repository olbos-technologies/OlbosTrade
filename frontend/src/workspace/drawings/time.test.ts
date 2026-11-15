import { describe, expect, it } from "vitest";
import { logicalToTime, timeToLogical } from "./time";

const hourly = Array.from({ length: 10 }, (_, i) => 1_000_000 + i * 3600);
// a weekend gap in the middle: 1h bars, then a 60h jump
const withGap = [0, 3600, 7200, 10800, 10800 + 216000, 10800 + 216000 + 3600, 10800 + 216000 + 7200];

describe("timeToLogical", () => {
  it("is the bar index at a bar's own time", () => {
    hourly.forEach((t, i) => expect(timeToLogical(hourly, t)).toBe(i));
  });
  it("is fractional between bars", () => {
    expect(timeToLogical(hourly, hourly[3] + 1800)).toBeCloseTo(3.5);
    expect(timeToLogical(hourly, hourly[3] + 900)).toBeCloseTo(3.25);
  });
  it("extends past the last bar using the typical gap", () => {
    expect(timeToLogical(hourly, hourly[9] + 3600 * 5)).toBeCloseTo(14);
  });
  it("extends before the first bar", () => {
    expect(timeToLogical(hourly, hourly[0] - 3600 * 2)).toBeCloseTo(-2);
  });
  it("a weekend gap does not distort the extension (median gap, not the biggest)", () => {
    const last = withGap[withGap.length - 1];
    expect(timeToLogical(withGap, last + 3600 * 3)).toBeCloseTo(withGap.length - 1 + 3);
    expect(timeToLogical(withGap, 10800 + 108000)).toBeCloseTo(3.5); // halfway across the gap
  });
  it("handles degenerate input without throwing", () => {
    expect(timeToLogical([], 5)).toBeNull();
    expect(timeToLogical(hourly, NaN)).toBeNull();
    expect(timeToLogical([100], 100)).toBe(0);
    expect(timeToLogical([100], 160)).toBeCloseTo(1);
    expect(timeToLogical([5, 5, 5], 5)).not.toBeNull(); // duplicate times: no divide by zero
    expect(Number.isFinite(timeToLogical([5, 5, 5], 99)!)).toBe(true);
  });
});

describe("logicalToTime is the inverse", () => {
  it("round-trips anywhere: inside, between, before and after the data", () => {
    for (const t of [hourly[0], hourly[4], hourly[4] + 1234, hourly[9], hourly[9] + 99_999, hourly[0] - 40_000]) {
      expect(logicalToTime(hourly, timeToLogical(hourly, t)!)).toBeCloseTo(t, 4);
    }
    for (const t of [0, 5000, 10800, 10800 + 100_000, withGap[6] + 12345]) {
      expect(logicalToTime(withGap, timeToLogical(withGap, t)!)).toBeCloseTo(t, 4);
    }
  });
  it("is monotonic", () => {
    let prev = -Infinity;
    for (let l = -3; l <= 13; l += 0.25) { const t = logicalToTime(hourly, l)!; expect(t).toBeGreaterThan(prev); prev = t; }
  });
  it("handles degenerate input", () => {
    expect(logicalToTime([], 1)).toBeNull();
    expect(logicalToTime(hourly, NaN)).toBeNull();
    expect(logicalToTime([100], 2)).toBe(220);
  });
});
