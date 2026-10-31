import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import MarketData from "../data/MarketData";

afterEach(() => { vi.unstubAllGlobals(); });

describe("market status availability", () => {
  it("does not present failed reads as a closed market or unrestricted hours", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<MarketData />);
    await waitFor(() => expect(screen.getAllByText("UNAVAILABLE")).toHaveLength(2));
    expect(screen.queryByText("CLOSED")).not.toBeInTheDocument();
    expect(screen.queryByText("always")).not.toBeInTheDocument();
  });
  it("shows a verified closed session and configured hours policy", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve({ ok: true, json: () => Promise.resolve(url.endsWith("status") ? { is_open: false, execution_gated: true } : { regime: "crisis" }) })));
    render(<MarketData />);
    expect(await screen.findByText("CLOSED")).toBeInTheDocument();
    expect(screen.getByText("market hours")).toBeInTheDocument();
  });
});
