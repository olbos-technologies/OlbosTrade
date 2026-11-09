import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PositionsTable from "../PositionsTable";
import WatchlistTable from "../WatchlistTable";

afterEach(cleanup);

describe("WatchlistTable", () => {
  const base = {
    watchlist: ["AAPL", "MSFT", "ZZZ"],
    snapshots: { AAPL: { last_close: 342.36, change_pct: -0.52 }, MSFT: { last_close: 530.5, change_pct: 1 }, ZZZ: { last_close: null, change_pct: null } },
    symbol: "AAPL", onSelect: vi.fn(), onRemove: vi.fn(),
  };

  it("shows prices and signed moves, and a dash, never zero, for a missing quote", () => {
    render(<WatchlistTable {...base} />);
    expect(screen.getByText("342.36")).toBeInTheDocument();
    expect(screen.getByText("−0.52%")).toBeInTheDocument();
    expect(screen.getByText("+1.00%")).toBeInTheDocument();
    const row = screen.getByRole("button", { name: "ZZZ" }).closest("tr")!;
    expect(row.textContent).toContain("—");
    expect(row.textContent).not.toContain("0.00");
  });

  it("flags the current symbol and selects and removes", () => {
    const fns = { onSelect: vi.fn(), onRemove: vi.fn() };
    render(<WatchlistTable {...base} {...fns} />);
    expect(screen.getByRole("button", { name: "AAPL" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "MSFT" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "MSFT" }));
    expect(fns.onSelect).toHaveBeenCalledWith("MSFT");
    fireEvent.click(screen.getByRole("button", { name: "Remove ZZZ from Market Watch" }));
    expect(fns.onRemove).toHaveBeenCalledWith("ZZZ");
  });

  it("has no add box: symbols are added from the instrument bar", () => {
    render(<WatchlistTable {...base} />);
    expect(screen.queryByLabelText("Add ticker to Market Watch")).toBeNull();
  });
});

describe("PositionsTable", () => {
  it("lists positions with signed P&L and never turns a missing value into zero", () => {
    render(<PositionsTable error={false} positions={[
      { symbol: "FER", quantity: -337, avg_cost: 58.57, unrealized_pnl: -46, hold_days: 2 },
      { symbol: "AMZN", quantity: 7, avg_cost: null, unrealized_pnl: null, hold_days: null },
    ]} />);
    const fer = screen.getByText("FER").closest("tr")!;
    expect(fer.textContent).toContain("−$46.00");
    expect(fer.textContent).toContain("$58.57");
    const amzn = screen.getByText("AMZN").closest("tr")!;
    expect(amzn.textContent).not.toMatch(/\$0\.00/);
    expect(amzn.textContent).toContain("—");
  });

  it("says unavailable on an error and none when there are none", () => {
    const { rerender } = render(<PositionsTable error positions={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("Positions unavailable");
    expect(screen.queryByRole("table")).toBeNull();
    rerender(<PositionsTable error={false} positions={[]} />);
    expect(screen.getByText("No open positions.")).toBeInTheDocument();
  });
});

describe("PositionsTable narrow", () => {
  it("drops the secondary columns in a narrow panel but keeps symbol, quantity and P&L", () => {
    render(<PositionsTable narrow error={false} positions={[{ symbol: "FER", quantity: -337, avg_cost: 58.57, unrealized_pnl: -46, hold_days: 2 }]} />);
    const head = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(head).toEqual(["Symbol", "Qty", "P&L"]);
    const row = screen.getByText("FER").closest("tr")!;
    expect(row.textContent).toContain("−$46.00");
    expect(row.textContent).not.toContain("58.57");
  });
});
