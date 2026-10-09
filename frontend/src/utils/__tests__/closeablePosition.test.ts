/**
 * The close gate's own behaviour.
 *
 * The bug this pins: close_position() has accepted put and call for a while,
 * routing them through close_options_trade(). The Trade Desk's gate was still
 * equity-only, so an options row rendered a "close via broker" placeholder and
 * no button. Reported as "hold to close is not functioning" — correctly, from
 * the outside, since an absent button and a dead button look the same.
 *
 * These tests alone would NOT have caught that: the helper can be perfectly
 * consistent with itself and still disagree with the backend. The test that
 * catches it compares both allowlists, and lives in the backend suite —
 * backend/tests/test_close_gate_matches_the_route.py — because reading across
 * the boundary is already the idiom there and needs no node types here.
 */

import { describe, it, expect } from "vitest";
import {
  CLOSEABLE_SPREAD_TYPES,
  canClosePosition,
  isCloseableType,
  isDbOnly,
  isEquityPosition,
} from "../closeablePosition";

describe("canClosePosition", () => {
  it("offers a button for equity positions with a trade id", () => {
    expect(canClosePosition({ spread_type: "equity_long", id: "t1" })).toBe(true);
    expect(canClosePosition({ spread_type: "equity_short", id: "t1" })).toBe(true);
  });

  it("offers a button for put and call positions — the case that was missing", () => {
    expect(canClosePosition({ spread_type: "put", id: "t1" })).toBe(true);
    expect(canClosePosition({ spread_type: "call", id: "t1" })).toBe(true);
  });

  it("withholds it without a trade id, whatever the type", () => {
    for (const t of CLOSEABLE_SPREAD_TYPES) {
      expect(canClosePosition({ spread_type: t, id: null })).toBe(false);
      expect(canClosePosition({ spread_type: t })).toBe(false);
    }
  });

  it("withholds it for a type the backend would reject", () => {
    // close_position() 400s on anything outside its allowlist, so rendering a
    // button here would promise an action that cannot succeed.
    expect(canClosePosition({ spread_type: "iron_condor", id: "t1" })).toBe(false);
    expect(canClosePosition({ spread_type: "", id: "t1" })).toBe(false);
    expect(canClosePosition({ id: "t1" })).toBe(false);
  });

  it("is case-insensitive — the backend lowercases before matching", () => {
    expect(canClosePosition({ spread_type: "PUT", id: "t1" })).toBe(true);
    expect(canClosePosition({ spread_type: "Equity_Long", id: "t1" })).toBe(true);
  });
});

describe("isEquityPosition", () => {
  it("separates equities from options, which close by different broker paths", () => {
    expect(isEquityPosition({ spread_type: "equity_long" })).toBe(true);
    expect(isEquityPosition({ spread_type: "put" })).toBe(false);
    // Still closeable — just not an equity.
    expect(isCloseableType({ spread_type: "put" })).toBe(true);
  });
});

describe("DB-only rows are never offered a close", () => {
  // paper_trade.py emits these for an open Trade row with no matching broker
  // position. They carry a real id and a real spread_type, so every other
  // check passes — and "closing" a position that is not held is an OPENING
  // trade in the other direction. close_options_trade() read its strikes
  // straight from the DB row and submitted blind until PR #64 added a
  // live-position guard; this is the second line of that defence.
  it("withholds the button for a db_only options row", () => {
    expect(canClosePosition({ spread_type: "put", id: "t1", source: "db_only" })).toBe(false);
    expect(canClosePosition({ spread_type: "call", id: "t1", source: "db_only" })).toBe(false);
  });

  it("withholds it for a db_only equity row too", () => {
    expect(canClosePosition({ spread_type: "equity_long", id: "t1", source: "db_only" })).toBe(false);
  });

  it("still allows rows the broker actually holds", () => {
    expect(canClosePosition({ spread_type: "put", id: "t1" })).toBe(true);
    expect(canClosePosition({ spread_type: "put", id: "t1", source: "broker" })).toBe(true);
    expect(canClosePosition({ spread_type: "put", id: "t1", source: null })).toBe(true);
  });

  it("identifies db_only regardless of case", () => {
    expect(isDbOnly({ source: "DB_ONLY" })).toBe(true);
    expect(isDbOnly({ source: "db_only" })).toBe(true);
    expect(isDbOnly({ source: "" })).toBe(false);
    expect(isDbOnly({})).toBe(false);
  });

  it("leaves the TYPE check alone — db_only is about holding, not type", () => {
    // The placeholder text distinguishes these: "not at broker" vs the
    // type-based "close via broker", so the two must stay separable.
    expect(isCloseableType({ spread_type: "put", source: "db_only" })).toBe(true);
  });
});
