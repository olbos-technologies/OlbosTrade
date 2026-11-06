import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../client";

afterEach(() => { vi.unstubAllGlobals(); });

describe("api.getOptionsFunnel", () => {
  const stub = () => {
    const f = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}), text: async () => "{}" });
    vi.stubGlobal("fetch", f);
    return f;
  };
  it("asks for the default seven days", async () => {
    const f = stub();
    await api.getOptionsFunnel();
    expect(String(f.mock.calls[0][0])).toContain("/api/options/funnel?days=7");
  });
  it("passes the requested window", async () => {
    const f = stub();
    await api.getOptionsFunnel(30);
    expect(String(f.mock.calls[0][0])).toContain("/api/options/funnel?days=30");
  });
});
