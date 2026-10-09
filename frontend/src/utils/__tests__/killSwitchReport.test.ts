/**
 * The kill switch must not tell an operator the book is flat when it is not.
 *
 * This is the one action where an overstatement is unrecoverable: the operator
 * reads "flattened", stops watching, and the residual sits open.
 */

import { describe, it, expect } from "vitest";
import { summarizeKillSwitchReport } from "../killSwitchReport";

describe("summarizeKillSwitchReport", () => {
  it("separates orders SENT from positions actually CLOSED", () => {
    const s = summarizeKillSwitchReport({
      positions_flattened: 3,
      flatten_statuses: { filled: 1, submitted: 1, partial: 1 },
    });
    expect(s.sent).toBe(3);
    expect(s.filled).toBe(1);        // the number that means "gone"
    expect(s.working).toEqual(expect.arrayContaining(["1 submitted", "1 partial"]));
    expect(s.allClosed).toBe(false);
  });

  it("reports all-clear only when every order filled", () => {
    const s = summarizeKillSwitchReport({
      positions_flattened: 2,
      flatten_statuses: { filled: 2 },
    });
    expect(s.allClosed).toBe(true);
    expect(s.working).toEqual([]);
  });

  it("does NOT read zero orders as all-clear", () => {
    // Nothing attempted is not the same as everything closed.
    const s = summarizeKillSwitchReport({ positions_flattened: 0, flatten_statuses: {} });
    expect(s.allClosed).toBe(false);
  });

  it("treats cancelled as still-open, not as closed", () => {
    // OrderResult: cancelled = terminated with no fill.
    const s = summarizeKillSwitchReport({
      positions_flattened: 1,
      flatten_statuses: { cancelled: 1 },
    });
    expect(s.filled).toBe(0);
    expect(s.allClosed).toBe(false);
    expect(s.working).toEqual(["1 cancelled"]);
  });

  it("surfaces already_engaged — that press flattened nothing", () => {
    const s = summarizeKillSwitchReport({ already_engaged: true, positions_flattened: 0 });
    expect(s.alreadyEngaged).toBe(true);
    expect(s.allClosed).toBe(false);
  });

  it("survives a response with no statuses at all", () => {
    expect(summarizeKillSwitchReport({ positions_flattened: 2 }).filled).toBe(0);
    expect(summarizeKillSwitchReport(null).sent).toBe(0);
    expect(summarizeKillSwitchReport(undefined).errors).toEqual([]);
  });

  it("passes errors through as a list", () => {
    const s = summarizeKillSwitchReport({ errors: ["flatten_SPY", "get_positions"] });
    expect(s.errors).toEqual(["flatten_SPY", "get_positions"]);
  });
});
