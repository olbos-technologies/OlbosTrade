import { describe, expect, it } from "vitest";
import { guardrailCap, guardrailCapPct } from "../guardrailCap";

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

describe("guardrailCapPct", () => {
  it("turns a reported fraction into a clean percent", () => {
    expect(guardrailCapPct(0.05)).toBe(5);
    expect(guardrailCapPct(0.07)).toBe(7);
    expect(guardrailCapPct(0.1)).toBe(10);
    expect(guardrailCapPct(0.025)).toBe(2.5);
  });
  it.each([undefined, null, "0.05", NaN, Infinity, 0, -0.05])("treats %s as not reported", (v) => {
    expect(guardrailCapPct(v)).toBeNull();
  });
});
