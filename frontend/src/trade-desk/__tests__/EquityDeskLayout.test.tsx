import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import EquityDesk from "../equity/EquityDesk";
vi.mock("../../pages/ChartWorkstation", () => ({ default: ({ symbol }: { symbol: string }) => <div>Chart {symbol}</div> }));
vi.mock("../equity/EquityDiscoveryRail", () => ({ default: ({ onSelect }: { onSelect: (s: string) => void }) => <button onClick={() => onSelect("MSFT")}>Select MSFT</button> }));
vi.mock("../equity/EquityIntelligenceRail", () => ({ default: ({ symbol }: { symbol: string }) => <div>Intelligence {symbol}</div> }));
vi.mock("../equity/EquityOrderComposer", () => ({ default: ({ symbol }: { symbol: string }) => <div>Composer {symbol}</div> }));
afterEach(() => { cleanup(); localStorage.clear(); });
describe("Equity Desk layout", () => {
  it("keeps chart, composer and intelligence linked to the selected symbol", () => {
    render(<EquityDesk />);
    fireEvent.click(screen.getByRole("button", { name: "Select MSFT" }));
    expect(screen.getByText("Chart MSFT")).toBeInTheDocument();
    expect(screen.getByText("Composer MSFT")).toBeInTheDocument();
    expect(screen.getByText("Intelligence MSFT")).toBeInTheDocument();
  });
  it("retains the composer's visibility control", () => {
    render(<EquityDesk />);
    const toggle = screen.getByRole("button", { name: "HIDE COMPOSER" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(screen.queryByText("Composer AAPL")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SHOW COMPOSER" })).toHaveAttribute("aria-expanded", "false");
  });
});
