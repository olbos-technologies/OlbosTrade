import { describe, expect, it } from "vitest";
import { DEFAULT_INDICATORS, MAX_INDICATORS, INDICATOR_IDS } from "./indicators";
import { INDICATORS_PREF_KEY, readIndicatorPref, writeIndicatorPref } from "./indicatorPrefs";

const store = (initial?: string) => {
  const m = new Map<string, string>(initial === undefined ? [] : [[INDICATORS_PREF_KEY, initial]]);
  return { m, getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } };
};

describe("the saved indicator choice", () => {
  it("is the default when nothing is saved, or there is no storage", () => {
    expect(readIndicatorPref(store())).toEqual([...DEFAULT_INDICATORS]);
    expect(readIndicatorPref(null)).toEqual([...DEFAULT_INDICATORS]);
    expect(readIndicatorPref(undefined)).toEqual([...DEFAULT_INDICATORS]);
  });
  it("round-trips a choice, in registry order", () => {
    const s = store();
    writeIndicatorPref(s, ["rsi", "ma5", "bb"]);
    expect(JSON.parse(s.m.get(INDICATORS_PREF_KEY)!)).toEqual(["ma5", "bb", "rsi"]);
    expect(readIndicatorPref(s)).toEqual(["ma5", "bb", "rsi"]);
  });
  it("keeps a deliberately empty choice (candles only) instead of snapping back to the default", () => {
    const s = store();
    writeIndicatorPref(s, []);
    expect(readIndicatorPref(s)).toEqual([]);
  });
  it.each(["not json", "{", "42", '"ma5"', "null", '{"a":1}', ""])("falls back to the default for %j", (raw) => {
    expect(readIndicatorPref(store(raw))).toEqual([...DEFAULT_INDICATORS]);
  });
  it("falls back to the default when every saved id is unknown (an older or edited value), not a blank chart", () => {
    expect(readIndicatorPref(store('["nope","also-nope"]'))).toEqual([...DEFAULT_INDICATORS]);
  });
  it("drops unknown ids but keeps the known ones, and never exceeds the cap", () => {
    expect(readIndicatorPref(store('["ma5","nope","rsi"]'))).toEqual(["ma5", "rsi"]);
    expect(readIndicatorPref(store(JSON.stringify(INDICATOR_IDS)))).toHaveLength(MAX_INDICATORS);
  });
  it("survives storage that throws on read or write", () => {
    const boom = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(readIndicatorPref(boom)).toEqual([...DEFAULT_INDICATORS]);
    expect(() => writeIndicatorPref(boom, ["ma5"])).not.toThrow();
  });
  it("writes only validated ids even if given junk", () => {
    const s = store();
    writeIndicatorPref(s, ["ma5", "<script>", "__proto__"] as never);
    expect(JSON.parse(s.m.get(INDICATORS_PREF_KEY)!)).toEqual(["ma5"]);
  });
});
