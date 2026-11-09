import { describe, expect, it } from "vitest";
import { floorForStyle, formatConfidenceFloor } from "../../hooks/useTradingStyleFloor";

describe("formatConfidenceFloor", () => {
  it("marks below-floor confidence as failing", () => {
    const r = formatConfidenceFloor(0.78, 0.9);
    expect(r.text).toBe("score 78 · need 90");
    expect(r.passes).toBe(false);
  });

  it("marks at-or-above floor as passing", () => {
    const r = formatConfidenceFloor(0.7, 0.7);
    expect(r.text).toBe("score 70 · need 70");
    expect(r.passes).toBe(true);
  });

  it("handles missing confidence", () => {
    const r = formatConfidenceFloor(null, 0.7);
    expect(r.text).toBe("need score 70");
    expect(r.passes).toBe(null);
  });
});

describe("floorForStyle", () => {
  it("maps known styles", () => {
    expect(floorForStyle("balanced")).toBe(0.9);
    expect(floorForStyle("aggressive")).toBe(0.7);
    expect(floorForStyle("scalper")).toBe(0.6);
  });
});
