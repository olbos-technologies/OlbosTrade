import { describe, expect, it } from "vitest";
import { PNL_FLAT, PNL_NEGATIVE, PNL_POSITIVE, PNL_UNKNOWN, gainSign, pnlColor } from "../pnlTone";
import { KILL_CLEAR, KILL_ENGAGED, killSwitchWord } from "../killSwitchWords";

describe("pnlColor", () => {
  it("green for a gain, red for a loss", () => {
    expect(pnlColor(12.5)).toBe(PNL_POSITIVE);
    expect(pnlColor(-12.5)).toBe(PNL_NEGATIVE);
  });

  it("an exact zero is neutral, not a gain", () => {
    expect(pnlColor(0)).toBe(PNL_FLAT);
    expect(pnlColor(-0)).toBe(PNL_FLAT);
  });

  it("a figure that rounds to $0.00 is neutral too, because that is what the screen shows", () => {
    expect(pnlColor(0.004)).toBe(PNL_FLAT);
    expect(pnlColor(-0.004)).toBe(PNL_FLAT);
    expect(pnlColor(0.01)).toBe(PNL_POSITIVE);
    expect(pnlColor(-0.01)).toBe(PNL_NEGATIVE);
  });

  it("judges zero at the precision it is shown", () => {
    expect(pnlColor(0.4, 0)).toBe(PNL_FLAT);       // a whole-dollar display of 0.4 reads $0
    expect(pnlColor(0.6, 0)).toBe(PNL_POSITIVE);
  });

  it("an unreadable value is unknown, never green", () => {
    for (const v of [null, undefined, NaN, Infinity, -Infinity, "5", {}]) expect(pnlColor(v)).toBe(PNL_UNKNOWN);
  });
});

describe("gainSign", () => {
  it("only a visible gain gets a plus", () => {
    expect(gainSign(5)).toBe("+");
    expect(gainSign(0)).toBe("");
    expect(gainSign(0.004)).toBe("");
    expect(gainSign(-5)).toBe("");
    expect(gainSign(0.4, 0)).toBe("");
  });

  it("no sign for an unreadable value", () => {
    for (const v of [null, undefined, NaN, Infinity]) expect(gainSign(v)).toBe("");
  });
});

describe("killSwitchWord", () => {
  it("one vocabulary: ENGAGED or CLEAR", () => {
    expect(killSwitchWord(true)).toBe(KILL_ENGAGED);
    expect(killSwitchWord(false)).toBe(KILL_CLEAR);
    expect([KILL_ENGAGED, KILL_CLEAR]).toEqual(["ENGAGED", "CLEAR"]);
  });

  it("a failed or missing read is neither, so no caller can fall back to CLEAR", () => {
    expect(killSwitchWord(null)).toBeNull();
    expect(killSwitchWord(undefined)).toBeNull();
  });
});
