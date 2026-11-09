import { describe, expect, it } from "vitest";
import { resolveCapacity } from "../deskCapacity";

const base = { listedOpenCount: 1, styleCap: 5, fallbackCap: 5 };

describe("resolveCapacity", () => {
  it("uses the gate's own cap and open count when the capacity read succeeded", () => {
    // Style cap 5, but the regime tightened it to 3 and the gate counts 3 open.
    expect(resolveCapacity({ ...base, capacity: { effective_cap: 3, open_positions: 3 } }))
      .toEqual({ maxConcurrent: 3, openCount: 3, authoritative: true });
  });

  it("prefers the gate's open count over the browser's positions list", () => {
    const r = resolveCapacity({ ...base, listedOpenCount: 0, capacity: { effective_cap: 3, open_positions: 2 } });
    expect(r.openCount).toBe(2);
  });

  it("falls back to the style cap, and says it is only an estimate, when the read failed", () => {
    for (const capacity of [null, undefined, {}]) {
      expect(resolveCapacity({ ...base, capacity })).toEqual({ maxConcurrent: 5, openCount: 1, authoritative: false });
    }
  });

  it("never treats an unreadable open count as zero room used, nor a partial read as authoritative", () => {
    // The endpoint reports an unreadable open count as null.
    const noOpen = resolveCapacity({ ...base, capacity: { effective_cap: 3, open_positions: null } });
    expect(noOpen.authoritative).toBe(false);
    expect(noOpen.openCount).toBe(1); // the list the browser has, not 0
    const noCap = resolveCapacity({ ...base, capacity: { effective_cap: null, open_positions: 3 } });
    expect(noCap.authoritative).toBe(false);
    expect(noCap.openCount).toBe(3);
  });

  it("ignores junk values instead of trusting them", () => {
    for (const bad of ["3", NaN, -1, Infinity, {}]) {
      expect(resolveCapacity({ ...base, capacity: { effective_cap: bad, open_positions: 2 } }).authoritative).toBe(false);
    }
  });

  it("uses the fallback cap when the style cap is unknown", () => {
    expect(resolveCapacity({ ...base, styleCap: null, capacity: null }).maxConcurrent).toBe(5);
  });
});
