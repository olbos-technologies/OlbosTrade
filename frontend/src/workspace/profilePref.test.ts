import { describe, expect, it } from "vitest";
import { PROFILE_PREF_KEY, readProfilePref, writeProfilePref } from "./profilePref";

const store = (initial: Record<string, string> = {}) => {
  const m = new Map(Object.entries(initial));
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
};

describe("volume profile preference", () => {
  it("is on by default, including with no or broken storage", () => {
    expect(readProfilePref(store())).toBe(true);
    expect(readProfilePref(null)).toBe(true);
    expect(readProfilePref(undefined)).toBe(true);
    expect(readProfilePref({ getItem: () => { throw new Error("denied"); } })).toBe(true);
  });
  it("only an explicit 'off' turns it off; anything else keeps it on", () => {
    expect(readProfilePref(store({ [PROFILE_PREF_KEY]: "off" }))).toBe(false);
    for (const v of ["on", "", "OFF", "false", "0", "garbage"]) expect(readProfilePref(store({ [PROFILE_PREF_KEY]: v })), v).toBe(true);
  });
  it("round-trips, and a failing write does not throw", () => {
    const s = store();
    writeProfilePref(s, false);
    expect(readProfilePref(s)).toBe(false);
    writeProfilePref(s, true);
    expect(readProfilePref(s)).toBe(true);
    expect(() => writeProfilePref({ setItem: () => { throw new Error("quota"); } }, false)).not.toThrow();
    expect(() => writeProfilePref(null, false)).not.toThrow();
  });
});
