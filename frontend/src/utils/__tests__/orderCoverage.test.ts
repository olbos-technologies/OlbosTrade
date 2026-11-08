import { describe, expect, it } from "vitest";
import { coverageIsReliable, senderLabel, stopState, type OrderBook } from "../orderCoverage";

const good = (without: string[] = []): OrderBook => ({
  available: true, source: "refreshed", unprotected_is_reliable: true,
  positions_without_stop: without.map(ticker => ({ ticker })),
});

describe("stopState", () => {
  it("is protected only when the read is reliable and the symbol has no gap", () => {
    expect(stopState(good(), "AMZN")).toBe("protected");
  });
  it("names a position the broker shows without a stop", () => {
    expect(stopState(good(["FER"]), "FER")).toBe("unprotected");
    expect(stopState(good(["FER"]), " fer ")).toBe("unprotected");
    expect(stopState(good(["FER"]), "AMZN")).toBe("protected");
  });
  it.each([
    ["no read at all", null],
    ["unavailable", { ...good(), available: false }],
    ["served from cache", { ...good(), source: "cache_after_refresh_failed" }],
    ["not marked reliable", { ...good(), unprotected_is_reliable: false }],
    ["positions errored", { ...good(), positions_error: "timeout" }],
    ["no gap list", { available: true, source: "refreshed", unprotected_is_reliable: true }],
  ])("is unverified, never protected, when the read is %s", (_n, book) => {
    expect(stopState(book as OrderBook | null, "AMZN")).toBe("unverified");
    expect(coverageIsReliable(book as OrderBook | null)).toBe(false);
  });
});

describe("senderLabel", () => {
  it("says this app only for the broker's own this_app label", () => {
    expect(senderLabel({ placed_by: "this_app" })).toEqual({ label: "This app", notable: false });
  });
  it("flags every other sender in words", () => {
    expect(senderLabel({ placed_by: "tws_or_manual" })).toEqual({ label: "TWS / manual entry", notable: true });
    expect(senderLabel({ placed_by: "other_api_client", client_id: 7 }))
      .toEqual({ label: "Other API client (id 7)", notable: true });
    expect(senderLabel({ placed_by: "other_api_client" }).label).toBe("Other API client");
  });
  it("treats a missing or odd label as unknown, not as ours", () => {
    for (const placed_by of [undefined, "", "unknown", "THIS_APP", "mine"]) {
      expect(senderLabel({ placed_by })).toEqual({ label: "Sender unknown", notable: true });
    }
  });
});
