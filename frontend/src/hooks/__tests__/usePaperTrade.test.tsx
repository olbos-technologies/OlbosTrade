import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { usePaperTrade } from "../usePaperTrade";
import { api } from "../../api/client";
vi.mock("../../api/client", () => ({ api: {
  getPositions: vi.fn(), getPortfolio: vi.fn(), getGreeksSummary: vi.fn(), getTradeHistory: vi.fn(),
} }));
beforeEach(() => {
  vi.mocked(api.getPositions).mockResolvedValue({positions:[{symbol:"SPY",unrealized_pnl:12}]});
  vi.mocked(api.getPortfolio).mockResolvedValue({portfolio:{account_value:1000}});
  vi.mocked(api.getGreeksSummary).mockResolvedValue({});
  vi.mocked(api.getTradeHistory).mockResolvedValue({trades:[]});
});
describe("independent account reads", () => {
  it("keeps confirmed holdings available when the portfolio request fails", async () => {
    vi.mocked(api.getPortfolio).mockRejectedValue(new Error("offline"));
    const {result} = renderHook(usePaperTrade);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.positions[0].symbol).toBe("SPY");
    expect(result.current.positionsError).toBe(false);
    expect(result.current.portfolioError).toBe(true);
  });
  it("flags failed refreshes so last-known holdings cannot look confirmed", async () => {
    const {result} = renderHook(usePaperTrade);
    await waitFor(() => expect(result.current.loading).toBe(false));
    vi.mocked(api.getPositions).mockRejectedValue(new Error("offline"));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.positionsError).toBe(true);
    expect(result.current.portfolioError).toBe(false);
  });
});
