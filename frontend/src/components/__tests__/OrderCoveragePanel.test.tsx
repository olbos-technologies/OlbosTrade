import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import OrderCoveragePanel from "../OrderCoveragePanel";
import type { OrderBook } from "../../utils/orderCoverage";

const NOW = 1_000_000;
const book = (over: Partial<OrderBook> = {}): OrderBook => ({
  available: true, source: "refreshed", unprotected_is_reliable: true,
  positions_without_stop: [],
  orders: [
    { order_id: 1, symbol: "AMZN", action: "SELL", order_type: "STP", quantity: 12, stop_price: 243.82, status: "PreSubmitted", is_protective: true, placed_by: "this_app", client_id: 2 },
    { order_id: 2, symbol: "FER", action: "BUY", order_type: "STP", quantity: 337, stop_price: 61.57, status: "PreSubmitted", is_protective: true, placed_by: "tws_or_manual", client_id: 0 },
  ],
  ...over,
});
const show = (b: OrderBook | null, loading = false) =>
  render(<OrderCoveragePanel book={b} loading={loading} at={NOW - 7000} now={NOW} />);

describe("OrderCoveragePanel", () => {
  it("says every position has a stop only when the read is reliable", () => {
    show(book());
    expect(screen.getByRole("status").textContent).toContain("Every open position has a stop at the broker.");
    expect(screen.getByRole("status").textContent).toContain("read 7s ago");
  });

  it("names positions with no stop, in words", () => {
    show(book({ positions_without_stop: [{ ticker: "FER" }] }));
    expect(screen.getByRole("status").textContent).toContain("1 position with no stop at broker: FER");
  });

  it("does not claim protection when the read cannot be trusted", () => {
    show(book({ source: "cache_after_refresh_failed" }));
    const text = screen.getByRole("status").textContent!;
    expect(text).toContain("Stop unverified");
    expect(text).not.toContain("Every open position has a stop");
  });

  it("shows no orders, and says unverified, when there is no fresh read", () => {
    show(null);
    expect(screen.getByRole("status").textContent).toContain("Stop coverage is unverified");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says it is reading while the first read is in flight", () => {
    show(null, true);
    expect(screen.getByRole("status").textContent).toContain("Reading the broker's orders");
  });

  it("labels who sent each order and flags senders that are not this app in words", () => {
    show(book());
    expect(screen.getByText("This app")).toBeTruthy();
    expect(screen.getByText(/⚠ TWS \/ manual entry/)).toBeTruthy();
    expect(screen.getByText("AMZN").closest("tr")!.textContent).not.toContain("⚠");
  });

  it("says when there are no resting orders", () => {
    show(book({ orders: [] }));
    expect(screen.getByText("No resting orders.")).toBeTruthy();
  });
});
