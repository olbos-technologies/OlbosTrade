import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api/client", async () => {
  // same shape as the real class: (status, message)
  class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } }
  return { ApiError, api: { getPositionTrades: vi.fn() } };
});
import { api, ApiError } from "../../../api/client";
import PositionTrades, { ageLabel } from "../PositionTrades";
import type { PositionTradesResponse } from "../../../types/positionTrades";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const item = (over: Record<string, unknown>) => ({
  symbol: "AMZN", asset_class: "equity", broker_quantity: 7, code: "hidden_from_positions_page", severity: "attention",
  explanation: "An open trade row exists, but it is unattributed.", positions_page_shows: "UNKNOWN",
  trades: [{ id: "t1", status: "open", strategy: "equity", asset_class: "equity", direction: "long", quantity: 7,
    owner: "unattributed (made before ownership existed)", shown_on_positions_page: false,
    entry_date: "2026-11-05T14:30:00+00:00", exit_date: null, approved_by: "autopilot" }],
  claims: [], ...over,
});
const response = (items: unknown[], over: Partial<PositionTradesResponse> = {}): PositionTradesResponse => ({
  viewer_scope: "x", operator_scope: "x", generated_at: new Date().toISOString(),
  broker: { available: true, error: null },
  summary: { holdings: items.length, tracked: 0, attention: items.length, rows_without_position: 0 },
  items: items as never, ...over,
});

describe("PositionTrades", () => {
  it("shows each holding with its reason, what the Positions screen shows, and its rows", async () => {
    vi.mocked(api.getPositionTrades).mockResolvedValue(response([
      item({}),
      item({ symbol: "SPCX", broker_quantity: -18, code: "no_trade_row", explanation: "No trade row was ever recorded.", trades: [], claims: [{ asset_class: "equity", state: "unknown", claimed_at: null, reason: "submit timed out" }] }),
    ]));
    render(<PositionTrades />);
    const amzn = await screen.findByRole("region", { name: "AMZN equity" });
    expect(amzn).toHaveTextContent("Look at this · Hidden from the Positions screen");
    expect(amzn).toHaveTextContent("At broker: +7");
    expect(amzn).toHaveTextContent("The Positions screen shows the strategy as: UNKNOWN");
    expect(amzn).toHaveTextContent("unattributed (made before ownership existed)");
    expect(amzn).toHaveTextContent("2026-11-05");
    const spcx = screen.getByRole("region", { name: "SPCX equity" });
    expect(spcx).toHaveTextContent("At broker: −18");
    expect(spcx).toHaveTextContent("No trade rows recorded for this name.");
    expect(spcx).toHaveTextContent("Entry claim: unknown · submit timed out");
  });

  it("summarises how many holdings need a look", async () => {
    vi.mocked(api.getPositionTrades).mockResolvedValue(response([item({})], { summary: { holdings: 3, tracked: 1, attention: 2, rows_without_position: 1 } }));
    render(<PositionTrades />);
    expect(await screen.findByText(/3 holdings · 1 tracked/)).toHaveTextContent("2 to look at");
    expect(screen.getByText(/1 open row with no position/)).toBeInTheDocument();
  });

  it("marks a tracked holding OK with a check, not only a colour", async () => {
    vi.mocked(api.getPositionTrades).mockResolvedValue(response([item({ code: "tracked", severity: "ok", positions_page_shows: "equity" })]));
    render(<PositionTrades />);
    expect(await screen.findByText(/✓ OK · Tracked/)).toBeInTheDocument();
  });

  it("says the broker could not be read, never that there are no holdings", async () => {
    vi.mocked(api.getPositionTrades).mockResolvedValue(response([], { broker: { available: false, error: "ConnectionError: gateway down" }, summary: null }));
    render(<PositionTrades />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The broker could not be read (ConnectionError: gateway down). Holdings are unknown, which is not the same as none.");
    expect(screen.queryByText(/No holdings at the broker/)).toBeNull();
  });

  it("says so when the page cannot be loaded, and tells the operator-only refusal apart", async () => {
    vi.mocked(api.getPositionTrades).mockRejectedValue(new ApiError(403, "Forbidden"));
    render(<PositionTrades />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load: This view is for the operator account only.");
    expect(screen.queryByText(/No holdings/)).toBeNull();
  });

  it("keeps the last list on screen, marked stale, when a refresh fails", async () => {
    vi.mocked(api.getPositionTrades).mockResolvedValueOnce(response([item({})]));
    render(<PositionTrades />);
    await screen.findByRole("region", { name: "AMZN equity" });
    vi.mocked(api.getPositionTrades).mockRejectedValueOnce(new Error("offline"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Showing the last list, which is stale: offline"));
    expect(screen.getByRole("region", { name: "AMZN equity" })).toBeInTheDocument();
    expect(screen.getByText(/\(stale\)/)).toBeInTheDocument();
  });

  it("says plainly when there is nothing to show", async () => {
    vi.mocked(api.getPositionTrades).mockResolvedValue(response([], { summary: { holdings: 0, tracked: 0, attention: 0, rows_without_position: 0 } }));
    render(<PositionTrades />);
    expect(await screen.findByText("No holdings at the broker and no open trade rows.")).toBeInTheDocument();
  });
});

describe("ageLabel", () => {
  it("words ages and never invents one", () => {
    const now = Date.parse("2026-11-09T12:00:00Z");
    expect(ageLabel("2026-11-09T11:59:30Z", now)).toBe("30s ago");
    expect(ageLabel("2026-11-09T11:30:00Z", now)).toBe("30m ago");
    expect(ageLabel("2026-11-09T09:00:00Z", now)).toBe("3h ago");
    expect(ageLabel(null, now)).toBe("unknown age");
    expect(ageLabel("nonsense", now)).toBe("unknown age");
  });
});
