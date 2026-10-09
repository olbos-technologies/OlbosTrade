/**
 * The Profile screen must state the plan the product actually sells.
 *
 * This file exists because the first version of that screen did not. It
 * carried a hand-written summary, under a comment claiming it mirrored
 * backend/app/services/tier_limits.py, that told Pro users they had 25
 * watchlist symbols and Elite users they had full history. The real limits are
 * an uncapped watchlist and five years, for both. Two wrong claims about what
 * someone is paying for, in the one screen whose job is to answer that, and
 * the comment made it look verified.
 *
 * The fix was to stop copying: Profile renders Landing.tsx's exported PLANS.
 * The chain is then enforced end to end, because backend test_tier_limits.py
 * parses that same array and fails if it disagrees with tier_limits.py.
 *
 * So these tests assert the JOIN rather than any particular number. Writing
 * "Pro gets a full watchlist" here would just be the same copy in a third
 * place, drifting on the same day the table changed.
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const getMe = vi.fn();

vi.mock("../../api/client", () => ({
  api: { getMe: (...a: unknown[]) => getMe(...a) },
  ApiError: class extends Error {},
}));

import AccountProfile from "../account/AccountProfile";
import { PLANS } from "../Landing";

beforeEach(() => { vi.clearAllMocks(); });

function planFor(tier: string) {
  const p = PLANS.find(x => x.name.toLowerCase() === tier);
  if (!p) throw new Error(`no published plan named ${tier}`);
  return p;
}

describe("AccountProfile renders the published plan, not a copy of it", () => {
  for (const tier of ["free", "pro", "elite"]) {
    it(`shows every published limit for ${tier}`, async () => {
      getMe.mockResolvedValue({ user: { id: "u1", email: "t@example.com", tier } });
      render(<AccountProfile />);
      await screen.findByText("t@example.com");

      for (const row of planFor(tier).limits) {
        // Both halves: the feature name and the value the pricing page states.
        expect(screen.getByText(row.feature), `${tier}: ${row.feature} missing`).toBeInTheDocument();
        expect(
          screen.getAllByText(row.limit).length,
          `${tier}: "${row.feature}" should read "${row.limit}"`
        ).toBeGreaterThan(0);
      }
    });
  }

  it("does not invent a number the pricing page never published", async () => {
    // The exact wrong claims this screen shipped with.
    getMe.mockResolvedValue({ user: { id: "u1", email: "t@example.com", tier: "pro" } });
    render(<AccountProfile />);
    await screen.findByText("t@example.com");

    expect(screen.queryByText(/25 watchlist symbols/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/full history/i)).not.toBeInTheDocument();
  });

  it("would catch a Profile that stopped matching the table", async () => {
    // The mutation, expressed as data: if Profile rendered a limit string that
    // is not in the published plan, this comparison would find it.
    getMe.mockResolvedValue({ user: { id: "u1", email: "t@example.com", tier: "elite" } });
    render(<AccountProfile />);
    await screen.findByText("t@example.com");

    const published = planFor("elite").limits.map(r => r.limit);
    expect(published.length).toBeGreaterThan(0);
    for (const limit of published) {
      expect(screen.getAllByText(limit).length).toBeGreaterThan(0);
    }
    // ...and a string the table does NOT contain is genuinely absent, so the
    // assertion above is not satisfied by some catch-all render.
    expect(published).not.toContain("25 symbols");
    expect(screen.queryByText("25 symbols")).not.toBeInTheDocument();
  });

  it("renders no plan block for a tier the pricing page does not publish", async () => {
    // "unlimited" is the auth-disabled install's internal tier. It has no
    // pricing card, and inventing one would be the same bug again.
    getMe.mockResolvedValue({ user: { id: "u1", email: "t@example.com", tier: "unlimited" } });
    render(<AccountProfile />);
    await screen.findByText("t@example.com");
    expect(screen.queryByText(/your plan includes/i)).not.toBeInTheDocument();
  });
});
