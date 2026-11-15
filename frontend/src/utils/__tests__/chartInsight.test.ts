import { describe, expect, it } from "vitest";
import {
  DASH, INSIGHT_TABS, ageLabel, distancePct, fmtPrice, fmtSignedPct, nextTab, rewardToRisk,
} from "../chartInsight";

describe("display formatting", () => {
  it("shows a dash, never zero, for a missing or non-finite value", () => {
    for (const bad of [null, undefined, NaN, Infinity]) {
      expect(fmtPrice(bad as never)).toBe(DASH);
      expect(fmtSignedPct(bad as never)).toBe(DASH);
    }
    expect(fmtPrice(0)).toBe("0.00");
  });
  it("signs percentages with a true minus", () => {
    expect(fmtSignedPct(-7.04)).toBe("−7.0%");
    expect(fmtSignedPct(14)).toBe("+14.0%");
    expect(fmtSignedPct(0)).toBe("0.0%");
  });
});

describe("distancePct", () => {
  it("is measured from entry and signed by direction", () => {
    expect(distancePct(296.71, 260.36)).toBeCloseTo(13.96, 1);
    expect(distancePct(242.18, 260.36)).toBeCloseTo(-6.98, 1);
  });
  it("is null when either side is unusable, including a non-positive entry", () => {
    expect(distancePct(null, 100)).toBeNull();
    expect(distancePct(100, undefined)).toBeNull();
    expect(distancePct(100, 0)).toBeNull();
    expect(distancePct(100, -5)).toBeNull();
  });
});

describe("rewardToRisk", () => {
  it("computes long and short plans", () => {
    expect(rewardToRisk(100, 95, 110)).toBeCloseTo(2);
    expect(rewardToRisk(100, 105, 90)).toBeCloseTo(2);
  });
  it("refuses an incoherent plan rather than showing a flattering ratio", () => {
    expect(rewardToRisk(100, 105, 110)).toBeNull();   // stop above a long's target side
    expect(rewardToRisk(100, 95, 90)).toBeNull();     // target on the stop side
    expect(rewardToRisk(100, 100, 110)).toBeNull();   // zero risk
    expect(rewardToRisk(100, null, 110)).toBeNull();
  });
});

describe("ageLabel", () => {
  const now = Date.parse("2026-11-14T15:00:00Z");
  it("reads minutes, hours and days", () => {
    expect(ageLabel("2026-11-14T14:59:40Z", now)).toBe("just now");
    expect(ageLabel("2026-11-14T14:48:00Z", now)).toBe("12m ago");
    expect(ageLabel("2026-11-14T12:00:00Z", now)).toBe("3h ago");
    expect(ageLabel("2026-11-11T15:00:00Z", now)).toBe("3d ago");
  });
  it("is a dash when the timestamp is missing or unparsable", () => {
    expect(ageLabel(null, now)).toBe(DASH);
    expect(ageLabel("not a date", now)).toBe(DASH);
  });
  it("does not go negative for a timestamp slightly in the future", () => {
    expect(ageLabel("2026-11-14T15:00:30Z", now)).toBe("just now");
  });
});

describe("nextTab", () => {
  it("wraps with the arrow keys and jumps with Home/End", () => {
    expect(INSIGHT_TABS.length).toBe(4);
    expect(nextTab("signal", "ArrowRight")).toBe("levels");
    expect(nextTab("catalysts", "ArrowRight")).toBe("signal");
    expect(nextTab("signal", "ArrowLeft")).toBe("catalysts");
    expect(nextTab("levels", "Home")).toBe("signal");
    expect(nextTab("levels", "End")).toBe("catalysts");
    expect(nextTab("levels", "x")).toBeNull();
  });
});
