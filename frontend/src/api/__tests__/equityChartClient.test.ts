import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../client";

afterEach(() => { vi.unstubAllGlobals(); });

const stub = () => {
  const f = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}), text: async () => "{}" });
  vi.stubGlobal("fetch", f);
  return f;
};
const url = (f: ReturnType<typeof stub>) => new URL(String(f.mock.calls[0][0]), "http://x");

describe("api.getEquityChart", () => {
  it("sends the interval, the range and the limit", async () => {
    const f = stub();
    await api.getEquityChart("AAPL", { timeframe: "4h", range: "3m", limit: 5000 });
    const u = url(f);
    expect(u.pathname).toBe("/api/equity/chart/AAPL");
    expect(u.searchParams.get("timeframe")).toBe("4h");
    expect(u.searchParams.get("range")).toBe("3m");
    expect(u.searchParams.get("limit")).toBe("5000");
  });

  it("still works without a range, as the compact desk charts call it", async () => {
    const f = stub();
    await api.getEquityChart("MSFT", { timeframe: "15m", limit: 96 });
    const u = url(f);
    expect(u.searchParams.get("range")).toBeNull();
    expect(u.searchParams.get("timeframe")).toBe("15m");
  });

  it("sends nothing extra when no parameters are given", async () => {
    const f = stub();
    await api.getEquityChart("SPY");
    expect(String(f.mock.calls[0][0])).toMatch(/\/api\/equity\/chart\/SPY$/);
  });
});
