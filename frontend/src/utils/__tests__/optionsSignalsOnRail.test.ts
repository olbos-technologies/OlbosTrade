/**
 * Options signals must be on the sidebar rail, not in Research.
 *
 * The product's second goal names both asset classes: equity AND options
 * signals, with an explicit trade plan. `options:signals` sat in the Research
 * group behind `sidebar: false`, so the navigation could not show that the
 * system trades options at all — you had to already know the destination
 * existed to find it.
 *
 * Checked against BOTH models. navModels.ts exports two and the trade_desk_v2
 * flag decides which renders, so a destination placed in one alone is the same
 * regression with a longer fuse — the bug this file's neighbours record
 * from #78.
 */
import { describe, it, expect } from "vitest";

import { NAV_MODEL_LEGACY, NAV_MODEL_V2 } from "../navModels";
import { filterNavForDisplay } from "../navLabels";

const MODELS = [
  ["legacy", NAV_MODEL_LEGACY],
  ["v2", NAV_MODEL_V2],
] as const;

/** The rail an operator sees on an unrelated page — no active-page exemption. */
function railKeys(model: typeof NAV_MODEL_LEGACY): string[] {
  return filterNavForDisplay(model, false, "dashboard").flatMap((g) =>
    g.children?.length ? g.children.map((c) => c.key) : g.key ? [g.key] : [],
  );
}

describe("options signals are reachable from the rail", () => {
  it.each(MODELS)("%s shows both asset classes without opening anything", (_n, model) => {
    const keys = railKeys(model);
    expect(keys, "equity signals missing from the rail").toContain("equity");
    expect(keys, "options signals missing from the rail — goal 2 names both")
      .toContain("options:signals");
  });

  it.each(MODELS)("%s groups options with signals, not with research", (_n, model) => {
    const group = model.find((g) => g.children?.some((c) => c.key === "options:signals"));
    expect(group?.id, "options:signals should sit in the Signals group").toBe("strat");
  });

  it.each(MODELS)("%s does not hide it behind sidebar:false", (_n, model) => {
    const leaf = model
      .flatMap((g) => g.children ?? [])
      .find((c) => c.key === "options:signals");
    expect(leaf, "options:signals vanished from the model entirely").toBeTruthy();
    expect(leaf?.sidebar, "options:signals is back behind sidebar:false").not.toBe(false);
  });
});
