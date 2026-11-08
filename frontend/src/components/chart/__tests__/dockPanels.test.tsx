import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChartDock from "../ChartDock";
import PositionsTable from "../PositionsTable";
import WatchlistTable from "../WatchlistTable";

afterEach(cleanup);

describe("ChartDock", () => {
  const tabs = [{ key: "a", label: "Alpha" }, { key: "b", label: "Beta", count: 3 }, { key: "c", label: "Gamma" }];

  it("marks the selected tab and puts only it in the tab order", () => {
    render(<ChartDock tabs={tabs} active="b" onChange={() => {}}>body</ChartDock>);
    expect(screen.getByRole("tab", { name: /Beta/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /Beta/ })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("body");
    expect(screen.getByRole("tab", { name: /Beta/ })).toHaveTextContent("3");
  });

  it("moves with the arrow keys, wrapping, and Home / End", () => {
    const onChange = vi.fn();
    render(<ChartDock tabs={tabs} active="c" onChange={onChange}>x</ChartDock>);
    const gamma = screen.getByRole("tab", { name: "Gamma" });
    fireEvent.keyDown(gamma, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("a");           // wraps
    fireEvent.keyDown(gamma, { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("b");
    fireEvent.keyDown(gamma, { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith("a");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Alpha" }), { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("c");
    fireEvent.keyDown(gamma, { key: "x" });
    expect(onChange).toHaveBeenCalledTimes(4);               // other keys ignored
  });

  it("selects on click", () => {
    const onChange = vi.fn();
    render(<ChartDock tabs={tabs} active="a" onChange={onChange}>x</ChartDock>);
    fireEvent.click(screen.getByRole("tab", { name: "Gamma" }));
    expect(onChange).toHaveBeenCalledWith("c");
  });
});

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

