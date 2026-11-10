import { describe, it, expect } from "vitest";
import { buildMonthGrid, compactPnl, shiftMonth } from "../journalCalendar";

describe("journal calendar helpers", () => {
  it("lays November 2026 out Sunday-first, with the 1st on a Sunday", () => {
    const weeks = buildMonthGrid("2026-11", [{ date: "2026-11-05", pnl: -1520, trades: 2 }]);
    expect(weeks[0][0].day).toBe(1);
    expect(weeks.every(w => w.length === 7)).toBe(true);
    expect(weeks.flat().filter(c => c.day !== null)).toHaveLength(30);
    const fifth = weeks.flat().find(c => c.day === 5)!;
    expect(fifth.pnl).toBe(-1520);
    expect(fifth.trades).toBe(2);
  });

  it("pads the first week with blanks when the month starts mid-week", () => {
    const weeks = buildMonthGrid("2026-12", []);
    expect(weeks[0].slice(0, 2).every(c => c.day === null)).toBe(true); // Dec 1 2026 is a Tuesday
    expect(weeks[0][2].day).toBe(1);
  });

  it("a day with no closed trade has null P&L, never zero", () => {
    const cell = buildMonthGrid("2026-11", []).flat().find(c => c.day === 9)!;
    expect(cell.pnl).toBeNull();
    expect(cell.trades).toBe(0);
  });

  it("shifts months across a year boundary", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2026-11", 0)).toBe("2026-11");
  });

  it("prints a sign and compacts thousands, and calls a net-zero day flat", () => {
    expect(compactPnl(665.53)).toBe("+665.53");
    expect(compactPnl(-203.45)).toBe("-203.45");
    expect(compactPnl(-1520)).toBe("-1.52K");
    expect(compactPnl(0.004)).toBe("0.00");
  });
});
