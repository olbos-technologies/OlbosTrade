import { describe, expect, it } from "vitest";
import { guardrailCap } from "../guardrailCap";

describe("guardrailCap", () => {
  it("passes a real positive cap through", () => {
    expect(guardrailCap(30)).toBe(30);
    expect(guardrailCap(0.5)).toBe(0.5);
  });
  it.each([undefined, null, "30", NaN, Infinity, -Infinity, 0, -3, {}, []])(
    "treats %s as not reported, never as a cap", (v) => {
      expect(guardrailCap(v)).toBeNull();
    });
});
