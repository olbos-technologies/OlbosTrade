import { describe, it, expect } from "vitest";
import {
  describeSignalReasons,
  rankedReasons,
  reasonList,
  phraseFor,
} from "../signalRationale";

describe("signalRationale", () => {
  it("describes the scanner's factor dict, which is what broke: the card read a string", () => {
    const text = describeSignalReasons({ macd_bull_cross: 10, above_ema200: 6, above_vwap: 3 });
    expect(text).toBe(
      "MACD crossed up through its signal line, price is above the 200-period EMA and price is above VWAP.",
    );
  });

  it("orders by contribution size, not by key order", () => {
    const ranked = rankedReasons({ above_vwap: 3, macd_bull_cross: 10, below_ema200: -6 });
    expect(ranked.map((r) => r.key)).toEqual(["macd_bull_cross", "below_ema200", "above_vwap"]);
  });

  it("counts a negative contribution by its magnitude — a strong bearish factor is strong evidence", () => {
    const ranked = rankedReasons({ rsi_oversold: 2, macd_bear_cross: -10 });
    expect(ranked[0].key).toBe("macd_bear_cross");
  });

  it("drops zero-weight factors rather than overstating the evidence", () => {
    expect(rankedReasons({ above_vwap: 0, macd_bull_cross: 10 })).toEqual([
      { key: "macd_bull_cross", points: 10 },
    ]);
  });

  it("caps the sentence and says how many factors it left out", () => {
    const text = describeSignalReasons({ a1: 9, a2: 8, a3: 7, a4: 6, a5: 5 });
    expect(text).toContain(", plus 2 more factors.");
  });

  it("uses the singular for exactly one omitted factor", () => {
    expect(describeSignalReasons({ a1: 9, a2: 8, a3: 7, a4: 6 })).toContain(", plus 1 more factor.");
  });

  it("returns null when there is genuinely nothing to say, so the placeholder survives", () => {
    expect(describeSignalReasons(undefined)).toBeNull();
    expect(describeSignalReasons({})).toBeNull();
    expect(describeSignalReasons({ above_vwap: 0 })).toBeNull();
  });

  it("still honours a plain string, so a producer that does supply prose is not discarded", () => {
    expect(describeSignalReasons("Gap-and-go continuation.")).toBe("Gap-and-go continuation.");
    expect(reasonList("Gap-and-go continuation.")).toEqual(["Gap-and-go continuation."]);
  });

  it("surfaces an unmapped factor instead of silently dropping it", () => {
    expect(phraseFor("some_new_factor")).toBe("some new factor");
    expect(describeSignalReasons({ some_new_factor: 4 })).toBe("Some new factor.");
  });

  it("gives the drawer one signed phrase per factor", () => {
    expect(reasonList({ macd_bull_cross: 10, below_vwap: -4 })).toEqual([
      "MACD crossed up through its signal line (+10)",
      "price is below VWAP (-4)",
    ]);
  });

  it("ignores a non-numeric weight rather than printing NaN", () => {
    expect(reasonList({ above_vwap: Number.NaN, macd_bull_cross: 10 })).toEqual([
      "MACD crossed up through its signal line (+10)",
    ]);
  });
});
