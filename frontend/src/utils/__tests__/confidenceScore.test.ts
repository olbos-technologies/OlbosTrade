import { describe, expect, it } from "vitest";
import { CONFIDENCE_NOTE, confidenceScore, confidenceScoreText } from "../confidenceScore";

describe("confidenceScore", () => {
  it("words a 0-1 confidence as a score out of 100, never as a percent", () => {
    expect(confidenceScoreText(0.72)).toBe("72/100");
    expect(confidenceScoreText(0.725)).toBe("73/100");
    expect(confidenceScoreText(0)).toBe("0/100");
    expect(confidenceScoreText(1)).toBe("100/100");
    expect(confidenceScoreText(0.72)).not.toContain("%");
  });

  it("never invents a score when there is no usable value", () => {
    for (const bad of [null, undefined, NaN, Infinity]) {
      expect(confidenceScore(bad as number | null | undefined)).toBeNull();
      expect(confidenceScoreText(bad as number | null | undefined)).toBe("—");
    }
    expect(confidenceScoreText(null, "Not provided")).toBe("Not provided");
  });

  it("states plainly that it is not a win probability", () => {
    expect(CONFIDENCE_NOTE).toMatch(/not the probability/i);
  });
});
