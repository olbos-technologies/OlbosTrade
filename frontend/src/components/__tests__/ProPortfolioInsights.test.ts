import { describe, expect, it } from "vitest";
import { pnlContributors, portfolioDataCoverage, normalizeCorrelationMatrix } from "../ProPortfolioInsights";

describe("portfolio intelligence contributors", () => {
  it("sorts observed unrealized P&L by absolute size", () => {
    expect(pnlContributors([
      { symbol: "A", unrealized_pnl: 8 },
      { symbol: "B", unrealized_pnl: -20, tracked: false },
      { symbol: "C", unrealized_pnl: null },
    ])).toEqual([
      { symbol: "B", pnl: -20, tracked: false },
      { symbol: "A", pnl: 8, tracked: true },
    ]);
  });
  it("excludes unavailable and nonfinite values", () => {
    expect(pnlContributors([{ symbol: "A", unrealized_pnl: Number.NaN }, { symbol: "B" }])).toEqual([]);
  });
  it("reports unknown unrealized P&L instead of replacing it with zero", () => {
    expect(portfolioDataCoverage([
      { symbol: "A", unrealized_pnl: 10 },
      { symbol: "B", unrealized_pnl: null, tracked: false },
    ])).toEqual({ total: 2, pnlKnown: 1, missingPnl: 1, untracked: 1 });
  });
  it("converts the portfolio endpoint's ticker-keyed correlation matrix", () => {
    expect(normalizeCorrelationMatrix(["AAPL", "MSFT"], {
      AAPL: { AAPL: 1, MSFT: 0.72 },
      MSFT: { AAPL: 0.72, MSFT: 1 },
    })).toEqual([[1, 0.72], [0.72, 1]]);
  });
  it("rejects incomplete or invalid correlations", () => {
    expect(normalizeCorrelationMatrix(["AAPL", "MSFT"], { AAPL: { AAPL: 1 }, MSFT: { MSFT: 1 } })).toBeNull();
    expect(normalizeCorrelationMatrix(["AAPL", "MSFT"], { AAPL: { AAPL: 1, MSFT: NaN }, MSFT: { AAPL: 0, MSFT: 1 } })).toBeNull();
  });

});
