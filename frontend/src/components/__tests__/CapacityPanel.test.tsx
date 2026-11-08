import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CapacityPanel, { type Capacity } from "../CapacityPanel";

const base = (over: Partial<Capacity> = {}): Capacity => ({
  global_cap: 5,
  style: { name: "aggressive", cap: 6 },
  base_cap: 5,
  limited_by: "global",
  regime: { name: "high_vol_trending", known: true, multiplier: 0.75, tightened: true },
  effective_cap: 3,
  open_positions: 3,
  room: 0,
  steps: ["Global limit 5", "Trading style (aggressive) allows 6", "Tighter of the two: 5",
          "Market regime (high_vol_trending) scales that by 0.75: 5 → 3"],
  recent_blocks: [{ reason: "Max concurrent positions reached", count: 12 }],
  blocks_available: true,
  blocks_window_hours: 24,
  ...over,
});

describe("CapacityPanel", () => {
  it("explains why five becomes three", () => {
    render(<CapacityPanel data={base()} loading={false} />);
    expect(screen.getByTestId("capacity-headline").textContent).toBe("At capacity: 3 of 3 open");
    expect(screen.getByText("Market regime (high_vol_trending) scales that by 0.75: 5 → 3")).toBeTruthy();
    expect(screen.getByText("Limit today: 3")).toBeTruthy();
    expect(screen.getByText(/Max concurrent positions reached/).textContent).toContain("×12");
  });

  it("says how much room is left", () => {
    render(<CapacityPanel data={base({ open_positions: 1, room: 2 })} loading={false} />);
    expect(screen.getByTestId("capacity-headline").textContent).toBe("1 of 3 open · room for 2 more");
  });

  it("never shows an unreadable open count as zero", () => {
    render(<CapacityPanel data={base({ open_positions: null, room: null })} loading={false} />);
    const text = screen.getByTestId("capacity-headline").textContent!;
    expect(text).toContain("open count unavailable");
    expect(text).not.toMatch(/\b0 of\b/);
  });

  it("flags an unclassified regime", () => {
    render(<CapacityPanel data={base({ regime: { name: "unknown", known: false, multiplier: 0.5, tightened: true } })} loading={false} />);
    expect(screen.getByText(/regime is unclassified/i)).toBeTruthy();
  });

  it("distinguishes 'nothing blocked' from 'could not read the history'", () => {
    const { rerender } = render(<CapacityPanel data={base({ recent_blocks: [] })} loading={false} />);
    expect(screen.getByText("Nothing blocked.")).toBeTruthy();
    rerender(<CapacityPanel data={base({ recent_blocks: [], blocks_available: false })} loading={false} />);
    expect(screen.queryByText("Nothing blocked.")).toBeNull();
    expect(screen.getByText("Blocked-entry history unavailable.")).toBeTruthy();
  });

  it("says unavailable, not a number, when there is no fresh read", () => {
    const { rerender } = render(<CapacityPanel data={null} loading={true} />);
    expect(screen.getByTestId("capacity-headline").textContent).toContain("Reading capacity");
    rerender(<CapacityPanel data={null} loading={false} />);
    expect(screen.getByTestId("capacity-headline").textContent).toContain("Capacity unavailable");
  });
});
