import { describe, expect, it } from "vitest";
import { pnlContributors, portfolioDataCoverage } from "../ProPortfolioInsights";

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
});
