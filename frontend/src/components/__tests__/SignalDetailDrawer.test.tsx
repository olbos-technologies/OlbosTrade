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

describe("SignalDetailDrawer with no signal", () => {
  const base = { open: true, onClose: () => {}, symbol: "QQQ", timeframe: "1d", latestBar: null, signal: null } as const;

  it("says the feed is unavailable when it could not be read, not that nothing was selected", () => {
    render(<SignalDetailDrawer {...base} loadState="unavailable" />);
    expect(screen.getByText("Signals are unavailable. The signal feed could not be verified.")).toBeTruthy();
    expect(screen.queryByText(/No signal payload/)).toBeNull();
  });

  it("says it is loading while the first read is out", () => {
    render(<SignalDetailDrawer {...base} loadState="loading" />);
    expect(screen.getByText("Loading signal evidence…")).toBeTruthy();
  });

  it("keeps the original empty message for a successful empty read (and by default)", () => {
    render(<SignalDetailDrawer {...base} />);
    expect(screen.getByText("No signal payload is selected for this symbol yet.")).toBeTruthy();
  });
});

