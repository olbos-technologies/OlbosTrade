import { describe, expect, it } from "vitest";
import { compareLedger, groupLedger, ledgerStatus, signalScore, stopDistancePct, type LedgerSignal } from "../signalLedger";
import type { DeskBlockContext } from "../signalBlockReason";

const ctx = (over: Partial<DeskBlockContext> = {}): DeskBlockContext => ({
  killEngaged: false, tradingAllowed: true, guardrailReason: null, guardrailFlags: [],
  consecutiveLosses: 0, maxConsecutiveLosses: null, tradesToday: 0, maxTradesPerDay: null,
  openCount: 0, maxConcurrent: 3, minConfidence: 0.7, ...over,
});
const seen = { guardrailAvailable: true, killStatusAvailable: true, capacityAvailable: true };
const sig = (id: string, over: Partial<LedgerSignal> = {}): LedgerSignal => ({
  id, generated_at: "2026-11-09T14:00:00Z", action: "BUY", confidence: 0.8,
  trade_plan: { entry_price: 100, stop_price: 95, target_move_pct: 5 }, ...over,
});

describe("signalScore", () => {
  it("prefers the composite score, falls back to confidence, and never invents one", () => {
    expect(signalScore({ action: "BUY", confidence: 0.6, opportunity_score: { score: 75 } })).toBe(75);
    expect(signalScore({ action: "BUY", confidence: 0.6, opportunity_score: null })).toBeCloseTo(60);
    expect(signalScore({ action: "BUY", confidence: 0.6, opportunity_score: { score: 400 } })).toBeCloseTo(60);
    expect(signalScore({ action: "HOLD", confidence: 0.6, opportunity_score: null })).toBeNull();
    expect(signalScore({ action: "BUY", confidence: NaN, opportunity_score: null })).toBeNull();
  });
});

describe("stopDistancePct", () => {
  it("is a percent of entry, and null when it cannot be computed", () => {
    expect(stopDistancePct({ entry_price: 100, stop_price: 95 })).toBe(5);
    expect(stopDistancePct({ entry_price: 100, stop_price: 105 })).toBe(5);
    expect(stopDistancePct({ entry_price: 100 })).toBeNull();
    expect(stopDistancePct({ entry_price: 0, stop_price: 1 })).toBeNull();
    expect(stopDistancePct(undefined)).toBeNull();
  });
});

describe("ledgerStatus", () => {
  it("is blocked with the reason when the preview finds a block", () => {
    const s = ledgerStatus(sig("a"), ctx({ openCount: 3, maxConcurrent: 3 }), seen);
    expect(s.kind).toBe("blocked");
    expect(s.kind === "blocked" && s.reason.code).toBe("max_positions");
  });
  it("is clear only when the desk state was actually read", () => {
    expect(ledgerStatus(sig("a"), ctx(), seen).kind).toBe("clear");
  });
  it("is UNKNOWN, never clear, when the desk state could not be read", () => {
    expect(ledgerStatus(sig("a"), ctx(), { ...seen, guardrailAvailable: false }).kind).toBe("unknown");
    expect(ledgerStatus(sig("a"), ctx(), { ...seen, killStatusAvailable: false }).kind).toBe("unknown");
  });
  it("is UNKNOWN when the gate's own capacity could not be read, because the cap is then only an estimate", () => {
    expect(ledgerStatus(sig("a"), ctx(), { ...seen, capacityAvailable: false }).kind).toBe("unknown");
  });
  it("still reports a block it can see even when part of the desk state is unreadable", () => {
    expect(ledgerStatus(sig("a", { confidence: 0.5 }), ctx(), { guardrailAvailable: false, killStatusAvailable: false, capacityAvailable: false }).kind).toBe("blocked");
  });
});

describe("groupLedger", () => {
  it("ranks candidates by displayed score, highest first, within a scan cycle", () => {
    const g = groupLedger([sig("lo", { confidence: 0.75 }), sig("hi", { confidence: 0.95 }), sig("mid", { confidence: 0.85 })], ctx(), seen);
    expect(g.candidates.map(c => c.sig.id)).toEqual(["hi", "mid", "lo"]);
  });
  it("keeps the newest cycle ahead of an older, higher score", () => {
    const old = sig("old", { confidence: 0.99, generated_at: "2026-11-09T12:00:00Z" });
    const fresh = sig("fresh", { confidence: 0.75 });
    expect(groupLedger([old, fresh], ctx(), seen).candidates.map(c => c.sig.id)).toEqual(["fresh", "old"]);
    expect(compareLedger(old, fresh)).toBeGreaterThan(0);
  });
  it("splits blocked ideas from candidates, so a full book does not bury the list", () => {
    const g = groupLedger([sig("a"), sig("b")], ctx({ openCount: 3, maxConcurrent: 3 }), seen);
    expect(g.candidates).toHaveLength(0);
    expect(g.blocked.map(c => c.sig.id).sort()).toEqual(["a", "b"]);
  });
  it("keeps HOLD and earnings-gated ideas out of the candidates", () => {
    const g = groupLedger([sig("h", { action: "HOLD" }), sig("e", { earnings_gated: true }), sig("ok")], ctx(), seen);
    expect(g.candidates.map(c => c.sig.id)).toEqual(["ok"]);
    expect(g.parked.map(s => s.id).sort()).toEqual(["e", "h"]);
  });
  it("filters by direction and minimum move without touching the parked group", () => {
    const sigs = [sig("b1"), sig("s1", { action: "SELL" }), sig("b2", { trade_plan: { entry_price: 100, stop_price: 95, target_move_pct: 1 } }), sig("h", { action: "HOLD" })];
    expect(groupLedger(sigs, ctx(), seen, { direction: "sell" }).candidates.map(c => c.sig.id)).toEqual(["s1"]);
    expect(groupLedger(sigs, ctx(), seen, { direction: "buy", minMovePct: 3 }).candidates.map(c => c.sig.id)).toEqual(["b1"]);
    expect(groupLedger(sigs, ctx(), seen, { direction: "sell" }).parked.map(s => s.id)).toEqual(["h"]);
  });
  it("loses nothing: every signal lands in exactly one group when no filter applies", () => {
    const sigs = [sig("a"), sig("b", { confidence: 0.3 }), sig("c", { action: "HOLD" }), sig("d", { action: "SELL" })];
    const g = groupLedger(sigs, ctx(), seen);
    expect(g.candidates.length + g.blocked.length + g.parked.length).toBe(sigs.length);
  });
});
