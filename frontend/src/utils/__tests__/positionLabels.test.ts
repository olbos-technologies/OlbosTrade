import { describe, it, expect } from "vitest";
import { ADOPTED_LABEL, ADOPTED_STRATEGY, adoptedLabel, isAdopted } from "../positionLabels";

describe("positionLabels", () => {
  it("recognises the reconciler's marker whatever its case or padding", () => {
    expect(isAdopted(ADOPTED_STRATEGY)).toBe(true);
    expect(isAdopted(" Adopted_Untracked ")).toBe(true);
  });

  it("does not claim an ordinary strategy, an empty one or a near-miss", () => {
    for (const s of ["equity", "bull_put_spread", "", null, undefined, "adopted", "adopted_untracked_2"]) {
      expect(isAdopted(s as string | null | undefined)).toBe(false);
      expect(adoptedLabel(s as string | null | undefined)).toBeNull();
    }
  });

  it("names an adopted position in words instead of the internal string", () => {
    expect(adoptedLabel(ADOPTED_STRATEGY)).toBe(ADOPTED_LABEL);
    expect(ADOPTED_LABEL).not.toMatch(/_/);
  });
});
