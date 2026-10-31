import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import SignalDetailDrawer from "../SignalDetailDrawer";

vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));

describe("SignalDetailDrawer rationale", () => {
  it("lists every contributing factor with its weight, not a truncated card line", () => {
    render(
      <SignalDetailDrawer
        open
        onClose={() => {}}
        symbol="QQQ"
        timeframe="1d"
        latestBar={null}
        signal={{
          ticker: "QQQ",
          action: "BUY",
          confidence: 0.7,
          reasons: {
            rsi_oversold: 0.2,
            macd_bull_cross: 0.18,
            macd_above_signal: 0.15,
            above_ema200: 0.12,
            volume_confirmed_bull: 0.1,
            above_vwap: 0.08,
            ema_aligned_bull: 0.06,
            orderflow_bull: 0.04,
          },
        }}
      />,
    );

    expect(screen.getByText("Why it appeared")).toBeTruthy();
    expect(screen.getByText("1. RSI oversold (+0.200)")).toBeTruthy();
    expect(screen.getByText("5. Volume confirmed above VWAP (+0.100)")).toBeTruthy();
    expect(screen.getByText("8. Bullish order flow (+0.040)")).toBeTruthy();
  });
});
