import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import SignalAttribution from "../SignalAttribution";

describe("SignalAttribution", () => {
  it("shows an explicit no-signal state instead of a blank card", () => {
    render(<SignalAttribution data={null} />);
    expect(screen.getByText(/no signal available/i)).toBeInTheDocument();
  });

  it("shows a loading state", () => {
    render(<SignalAttribution data={null} loading />);
    expect(screen.getByText(/loading signal/i)).toBeInTheDocument();
  });

  it("renders direction, timeframe, and confidence in the compact summary", () => {
    render(
      <SignalAttribution
        data={{ direction: "BUY", timeframe: "Weekly", confidence: 0.78, source: "Equity Scan Engine" }}
      />
    );
    expect(screen.getByText("BUY · Weekly · score 78")).toBeInTheDocument();
  });

  it("never omits missing attribution — unknown fields say so explicitly in the disclosure", () => {
    const { container } = render(<SignalAttribution data={{ direction: "SELL" }} />);
    const summary = container.querySelector("summary");
    expect(summary).toHaveTextContent("SELL");
    // The disclosure body still states every field explicitly rather than omitting it.
    expect(screen.getByText("Source unavailable")).toBeInTheDocument();
    expect(screen.getByText("Timeframe unknown")).toBeInTheDocument();
    expect(screen.getByText("Timestamp unavailable")).toBeInTheDocument();
    expect(screen.getByText("Execution authority is not available from the current frontend data.")).toBeInTheDocument();
  });

  it("flags a stale signal instead of showing an old value as current", () => {
    render(
      <SignalAttribution
        data={{ direction: "BUY", updatedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString() }}
      />
    );
    expect(screen.getByText("STALE")).toBeInTheDocument();
  });

  it("is keyboard operable via a native disclosure (no click-only div)", () => {
    const { container } = render(<SignalAttribution data={{ direction: "HOLD" }} />);
    const summary = container.querySelector("summary");
    expect(summary).not.toBeNull();
    expect(summary).toHaveTextContent("HOLD");
  });

  it("renders SHAP evidence factors when present", () => {
    render(
      <SignalAttribution
        data={{
          direction: "SELL_SPREAD",
          topPositiveFactors: [{ feature: "iv_rank", value: 45.2, impact: 0.12 }],
          topNegativeFactors: [{ feature: "days_to_expiry", value: 3.0, impact: -0.08 }],
        }}
      />
    );
    expect(screen.getByText("Supporting factors")).toBeInTheDocument();
    expect(screen.getByText("Deteriorating factors")).toBeInTheDocument();
    expect(screen.getByText("iv rank")).toBeInTheDocument();
    expect(screen.getByText("days to expiry")).toBeInTheDocument();
    expect(screen.getByText("+0.120")).toBeInTheDocument();
    expect(screen.getByText("-0.080")).toBeInTheDocument();
  });

  it("renders no evidence section at all when factors are absent (not an 'unknown' row)", () => {
    render(<SignalAttribution data={{ direction: "BUY" }} />);
    expect(screen.queryByText("Supporting factors")).not.toBeInTheDocument();
    expect(screen.queryByText("Deteriorating factors")).not.toBeInTheDocument();
  });
});
