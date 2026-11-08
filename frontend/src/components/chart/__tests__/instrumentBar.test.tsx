import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import InstrumentBar from "../InstrumentBar";

afterEach(cleanup);

const make = (over: Record<string, unknown> = {}) => ({
  symbols: ["AAPL", "MSFT"], symbol: "AAPL", onSelect: vi.fn(), onGoTo: vi.fn(() => true), onUnpin: undefined as undefined | (() => void),
  price: 342.36 as number | null, changePct: -0.52 as number | undefined,
  bar: { open: 343.1, high: 344.4, low: 340.9, close: 342.36, volume: 12_100_000 } as null | { open: number; high: number; low: number; close: number; volume: number },
  freshness: "Last close snapshot · Chart bar: 11/6/2026",
  timeframes: [{ key: "5m", label: "5M" }, { key: "15m", label: "15M" }],
  timeframe: "15m", onTimeframe: vi.fn(), signalBadge: <span>badge</span>, onSignalDetail: vi.fn(),
  ...over,
});

describe("InstrumentBar", () => {
  it("shows the price, move, last bar and freshness once", () => {
    render(<InstrumentBar {...make()} />);
    const quote = screen.getByLabelText("Selected instrument quote");
    expect(quote).toHaveTextContent("342.36");
    expect(quote).toHaveTextContent("−0.52%");
    expect(quote).toHaveTextContent("Last close snapshot");
    expect(screen.getByLabelText("Last bar")).toHaveTextContent("Vol 12.1M");
    expect(screen.getByRole("heading", { name: "Market workspace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AAPL" })).toHaveAttribute("aria-pressed", "true");
  });

  it("has no Focus chart control: the page is the chart", () => {
    render(<InstrumentBar {...make()} />);
    expect(screen.queryByRole("button", { name: /focus chart|show research/i })).toBeNull();
  });

  it("drives the timeframe, signal detail and symbol controls", () => {
    const p = make();
    render(<InstrumentBar {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "5M" }));
    expect(p.onTimeframe).toHaveBeenCalledWith("5m");
    fireEvent.click(screen.getByRole("button", { name: "Signal detail" }));
    expect(p.onSignalDetail).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "MSFT" }));
    expect(p.onSelect).toHaveBeenCalledWith("MSFT");
  });

  it("loads any symbol typed in the box and clears it, or flags one it refused", () => {
    const p = make();
    render(<InstrumentBar {...p} />);
    const input = screen.getByLabelText("Go to symbol") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "nvda" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(p.onGoTo).toHaveBeenCalledWith("nvda");
    expect(input.value).toBe("");
    expect(input).not.toHaveAttribute("aria-invalid");

    p.onGoTo.mockReturnValue(false);
    fireEvent.change(input, { target: { value: "not a symbol" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.value).toBe("not a symbol");            // kept so it can be corrected
    fireEvent.change(input, { target: { value: "AMD" } });
    expect(input).not.toHaveAttribute("aria-invalid");

    p.onGoTo.mockClear();
    fireEvent.change(input, { target: { value: "  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(p.onGoTo).not.toHaveBeenCalled();             // blank does nothing
  });

  it("offers to remove the current symbol only when it can be removed", () => {
    const onUnpin = vi.fn();
    const { rerender } = render(<InstrumentBar {...make()} />);
    expect(screen.queryByRole("button", { name: /Remove AAPL/ })).toBeNull();
    rerender(<InstrumentBar {...make({ onUnpin })} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove AAPL from Market Watch" }));
    expect(onUnpin).toHaveBeenCalled();
  });

  it("shows dashes, not zeros, when the quote and the bar are missing", () => {
    render(<InstrumentBar {...make({ price: null, changePct: undefined, bar: null })} />);
    const quote = screen.getByLabelText("Selected instrument quote");
    expect(quote.textContent).toContain("—");
    expect(quote.textContent).not.toMatch(/\b0\.00\b/);
    expect(screen.getByLabelText("Last bar")).toHaveTextContent("No bar data");
  });
});
