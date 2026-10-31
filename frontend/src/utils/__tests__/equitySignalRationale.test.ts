import { describe, expect, it } from "vitest";
import {
  EQUITY_RATIONALE_EMPTY,
  equitySignalRationale,
  formatWeightedFactor,
  phrasesFromReasonsMap,
  weightedFactorsFromReasonsMap,
} from "../equitySignalRationale";

describe("phrasesFromReasonsMap", () => {
  it("ranks by absolute weight and maps known keys to plain language", () => {
    expect(
      phrasesFromReasonsMap({
        above_ema200: 0.05,
        rsi_oversold: 0.2,
        macd_above_signal: 0.1,
        orderflow_bull: 0.08,
        noise: 0,
      }),
    ).toEqual([
      "RSI oversold",
      "MACD above signal",
      "Bullish order flow",
      "Price above EMA-200",
    ]);
  });

  it("humanizes unknown keys instead of dropping them", () => {
    expect(phrasesFromReasonsMap({ custom_factor: 1 })).toEqual(["Custom Factor"]);
  });

  it("truncates to four by default so cards stay short", () => {
    const eight = {
      rsi_oversold: 0.2,
      macd_bull_cross: 0.18,
      macd_above_signal: 0.15,
      above_ema200: 0.12,
      volume_confirmed_bull: 0.1,
      above_vwap: 0.08,
      ema_aligned_bull: 0.06,
      orderflow_bull: 0.04,
    };
    expect(phrasesFromReasonsMap(eight)).toHaveLength(4);
    expect(weightedFactorsFromReasonsMap(eight)).toHaveLength(8);
  });
});

describe("weightedFactorsFromReasonsMap", () => {
  it("keeps weights for drawer bullets", () => {
    expect(
      weightedFactorsFromReasonsMap({ rsi_oversold: 0.2, macd_bull_cross: 0.15 }).map(formatWeightedFactor),
    ).toEqual(["RSI oversold (+0.200)", "MACD bullish cross (+0.150)"]);
  });
});

describe("equitySignalRationale", () => {
  it("formats the scanner reasons map even when a legacy reason string is also set", () => {
    expect(
      equitySignalRationale({
        reason: "Earnings bounce setup",
        reasons: { rsi_oversold: 0.2 },
      }),
    ).toBe("RSI oversold");
  });

  it("formats the scanner reasons map when reason is missing", () => {
    expect(
      equitySignalRationale({
        reasons: { rsi_oversold: 0.2, macd_bull_cross: 0.15 },
      }),
    ).toBe("RSI oversold · MACD bullish cross");
  });

  it("falls back to a legacy reason string only when reasons is empty", () => {
    expect(equitySignalRationale({ reason: "Earnings bounce setup" })).toBe("Earnings bounce setup");
  });

  it("returns the shared empty copy when the producer sent nothing usable", () => {
    expect(equitySignalRationale({})).toBe(EQUITY_RATIONALE_EMPTY);
    expect(equitySignalRationale({ reasons: {} })).toBe(EQUITY_RATIONALE_EMPTY);
  });
});
