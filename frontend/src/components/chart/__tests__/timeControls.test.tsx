import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import IntervalStrip from "../IntervalStrip";
import RangeBar from "../RangeBar";
import SidePanel from "../SidePanel";
import { INTERVAL_OPTIONS, RANGE_DEFAULT_INTERVAL, RANGE_OPTIONS, clampNote } from "../timeframes";

afterEach(cleanup);

describe("IntervalStrip", () => {
  it("offers every interval, marks the current one and reports a pick", () => {
    const onChange = vi.fn();
    render(<IntervalStrip value="15m" onChange={onChange} />);
    expect(screen.getByRole("group", { name: "Chart timeframe" })).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(INTERVAL_OPTIONS.length);
    expect(screen.getByRole("button", { name: "15 minutes" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "4 hours" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "4 hours" }));
    expect(onChange).toHaveBeenCalledWith("4h");
  });

  it("never lets a minute read like a month", () => {
    const labels = INTERVAL_OPTIONS.map((o) => o.label);
    expect(new Set(labels.map((l) => l.toLowerCase())).size).toBe(labels.length);   // no two differ only by case
    expect(INTERVAL_OPTIONS.find((o) => o.key === "1M")!.label).toBe("M");
    expect(INTERVAL_OPTIONS.find((o) => o.key === "1m")!.label).toBe("1m");
  });
});

describe("RangeBar", () => {
  it("offers every range, marks the current one and reports a pick", () => {
    const onChange = vi.fn();
    render(<RangeBar value="5d" onChange={onChange} />);
    expect(screen.getByRole("group", { name: "Chart range" })).toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(RANGE_OPTIONS.length);
    expect(screen.getByRole("button", { name: "5 days" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Year to date" }));
    expect(onChange).toHaveBeenCalledWith("ytd");
  });

  it("says so, in words, when the provider could not cover the range", () => {
    const { rerender } = render(<RangeBar value="1y" onChange={() => {}} />);
    expect(screen.queryByRole("status")).toBeNull();
    rerender(<RangeBar value="1y" onChange={() => {}} note={clampNote("1 minute", "7d")} />);
    expect(screen.getByRole("status")).toHaveTextContent("Showing the last 7 days: 1 minute bars don't go back further.");
  });

  it("can show no range as selected", () => {
    render(<RangeBar value={null} onChange={() => {}} />);
    for (const b of screen.getAllByRole("button")) expect(b).toHaveAttribute("aria-pressed", "false");
  });
});

describe("range -> interval pairing", () => {
  it("every range picks a known interval", () => {
    const intervals = new Set(INTERVAL_OPTIONS.map((o) => o.key));
    for (const r of RANGE_OPTIONS) expect(intervals.has(RANGE_DEFAULT_INTERVAL[r.key])).toBe(true);
    expect(RANGE_DEFAULT_INTERVAL["1d"]).toBe("5m");
    expect(RANGE_DEFAULT_INTERVAL.all).toBe("1M");
  });
  it("words a clamp without a number when none is given", () => {
    expect(clampNote("1 hour", undefined)).toBe("Showing less history: 1 hour bars don't go back further.");
  });
});

describe("SidePanel", () => {
  it("titles the panel with its count and renders its body", () => {
    render(<SidePanel title="Positions" count={2} label="Open positions panel"><p>body</p></SidePanel>);
    expect(screen.getByRole("region", { name: "Open positions panel" })).toHaveTextContent("Positions2");
    expect(screen.getByText("body")).toBeInTheDocument();
  });
  it("shows a dash count as given", () => {
    render(<SidePanel title="Positions" count="—" label="p">x</SidePanel>);
    expect(screen.getByRole("heading", { name: /Positions/ })).toHaveTextContent("—");
  });
});
