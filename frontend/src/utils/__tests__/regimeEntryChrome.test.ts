import { describe, it, expect } from "vitest";
import {
  regimeEntryAllowance,
  regimeEntriesStatusChip,
  regimeEntriesTickerLabel,
} from "../regimeEntryChrome";

describe("regimeEntryChrome", () => {
  it("treats missing or non-boolean equity_allowed as unknown", () => {
    expect(regimeEntryAllowance(null)).toBe("unknown");
    expect(regimeEntryAllowance({})).toBe("unknown");
    expect(regimeEntryAllowance({ equity_allowed: null })).toBe("unknown");
  });

  it("maps equity_allowed booleans", () => {
    expect(regimeEntryAllowance({ equity_allowed: true })).toBe("allowed");
    expect(regimeEntryAllowance({ equity_allowed: false, regime: "crisis" })).toBe("blocked");
  });

  it("only emits a status chip when entries are not clearly allowed", () => {
    expect(regimeEntriesStatusChip("allowed")).toBeNull();
    expect(regimeEntriesStatusChip("blocked")).toMatchObject({
      label: "ENTRIES",
      value: "BLOCKED",
      tone: "var(--red)",
    });
    expect(regimeEntriesStatusChip("unknown")).toMatchObject({
      label: "ENTRIES",
      value: "UNKNOWN",
      tone: "var(--amber)",
    });
  });

  it("exposes ticker cues for blocked and unverified allowance", () => {
    expect(regimeEntriesTickerLabel("allowed")).toBeNull();
    expect(regimeEntriesTickerLabel("blocked")).toBe("ENTRIES PAUSED");
    expect(regimeEntriesTickerLabel("unknown")).toBe("ENTRIES UNVERIFIED");
  });
});
