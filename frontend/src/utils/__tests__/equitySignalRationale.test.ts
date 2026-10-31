import { describe, expect, it } from "vitest";
import {
  EQUITY_RATIONALE_EMPTY,
  equitySignalRationale,
  phrasesFromReasonsMap,
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
});

describe("equitySignalRationale", () => {
  it("prefers an explicit reason string when present", () => {
    expect(
      equitySignalRationale({
        reason: "Earnings bounce setup",
        reasons: { rsi_oversold: 0.2 },
      }),
    ).toBe("Earnings bounce setup");
  });

  it("formats the scanner reasons map when reason is missing", () => {
    expect(
      equitySignalRationale({
        reasons: { rsi_oversold: 0.2, macd_bull_cross: 0.15 },
      }),
    ).toBe("RSI oversold · MACD bullish cross");
  });

  it("returns the shared empty copy when the producer sent nothing usable", () => {
    expect(equitySignalRationale({})).toBe(EQUITY_RATIONALE_EMPTY);
    expect(equitySignalRationale({ reasons: {} })).toBe(EQUITY_RATIONALE_EMPTY);
  });
});
