/**
 * Every hub tab must be reachable from the sidebar.
 *
 * This guards a bug that shipped in #78. My Brokers was added to
 * SystemCenter's TABS, to TAB_PAGE_KEYS and to the page registry in App.tsx —
 * three of the four places — and not to navModels.ts. Everything resolved, the
 * URL worked, tabRouteTable.test.tsx passed, and the page was reachable only
 * by typing its address or by finding the tab strip inside another page. It
 * was invisible in the nav for the entire life of that PR.
 *
 * tabRouteTable checks tab -> page key -> registry. Nothing checked
 * page key -> NAV. That asymmetry is the hole, and it is exactly the shape of
 * miss this codebase keeps repeating: the mechanism is covered thoroughly and
 * one of the places it must be applied is left out.
 *
 * THE CHECK RUNS AGAINST BOTH NAV MODELS. navModels.ts exports
 * NAV_MODEL_LEGACY and NAV_MODEL_V2 and the trade_desk_v2 flag decides which
 * renders, so a key present in one and missing from the other is the same bug
 * with a longer fuse — it appears only once someone flips a flag.
 */

import { describe, it, expect } from "vitest";

import { BASE_PAGES, tradeDeskPages } from "../../App";
import { NAV_MODEL_LEGACY, NAV_MODEL_V2 } from "../../utils/navModels";
import { filterNavForDisplay } from "../../utils/navLabels";

import { TAB_PAGE_KEYS as RISK_KEYS }     from "../../pages/RiskCenter";
import { TAB_PAGE_KEYS as SYSTEM_KEYS }   from "../../pages/SystemCenter";
import { TAB_PAGE_KEYS as RESEARCH_KEYS } from "../../pages/ResearchCenter";
import { TAB_PAGE_KEYS as SIGNALS_KEYS }  from "../../pages/SignalsCenter";
import { TAB_PAGE_KEYS as ACCOUNT_KEYS }  from "../../pages/AccountCenter";

/** Every page key named in a nav model, at any depth. */
function navKeys(model: Array<{ key?: string; children?: Array<{ key: string }> }>): Set<string> {
  const out = new Set<string>();
  for (const group of model) {
    if (group.key) out.add(group.key);
    for (const child of group.children || []) out.add(child.key);
  }
  return out;
}

const HUBS: Array<[string, Readonly<Record<string, string>>]> = [
  ["RiskCenter", RISK_KEYS],
  ["SystemCenter", SYSTEM_KEYS],
  ["ResearchCenter", RESEARCH_KEYS],
  ["SignalsCenter", SIGNALS_KEYS],
  ["AccountCenter", ACCOUNT_KEYS],
];

/**
 * Keys a hub exposes that deliberately have no nav entry of their own.
 *
 * Empty on purpose. An exception belongs here only with a reason, so that
 * "it is not in the nav" is always either a stated decision or a failure —
 * never an oversight nobody notices.
 */
const INTENTIONALLY_NOT_IN_NAV = new Set<string>([]);

describe("every hub tab is reachable from the sidebar", () => {
  for (const [modelName, model] of [
    ["NAV_MODEL_LEGACY", NAV_MODEL_LEGACY],
    ["NAV_MODEL_V2", NAV_MODEL_V2],
  ] as const) {
    const keys = navKeys(model as never);

    for (const [hubName, tabKeys] of HUBS) {
      it(`${hubName}: every tab appears in ${modelName}`, () => {
        const missing = Object.values(tabKeys)
          .filter(k => !keys.has(k) && !INTENTIONALLY_NOT_IN_NAV.has(k));
        expect(
          missing,
          `${hubName} exposes ${missing.join(", ")} but ${modelName} never names ` +
          `it, so the page is reachable only by URL — the #78 bug. Add it to ` +
          `navModels.ts, or to INTENTIONALLY_NOT_IN_NAV with a reason.`
        ).toEqual([]);
      });
    }
  }

  it("the account group is in both models, not just one", () => {
    // The specific trap this file's docstring describes: adding a group to the
    // model you happened to be looking at.
    for (const key of Object.values(ACCOUNT_KEYS)) {
      expect(navKeys(NAV_MODEL_LEGACY as never).has(key), `${key} missing from LEGACY`).toBe(true);
      expect(navKeys(NAV_MODEL_V2 as never).has(key), `${key} missing from V2`).toBe(true);
    }
  });

  it("would actually catch the regression", () => {
    // A guard that cannot fail is decoration. Strip the account group from a
    // copy and confirm the reachability check notices.
    const stripped = NAV_MODEL_V2.filter(g => g.id !== "account");
    const keys = navKeys(stripped as never);
    const missing = Object.values(ACCOUNT_KEYS).filter(k => !keys.has(k));
    expect(missing.length).toBe(Object.values(ACCOUNT_KEYS).length);

    // ...and that it is not vacuous: the real model does contain them.
    const real = navKeys(NAV_MODEL_V2 as never);
    expect(Object.values(ACCOUNT_KEYS).filter(k => !real.has(k))).toEqual([]);
  });
});

describe("the Account group only exists for a signed-in user", () => {
  /**
   * AUTH_ENABLED=false renders the whole terminal with no identity at all —
   * AuthGate's "disabled" phase, the original single-operator install. Every
   * account route 401s there because there is no user to be. Shipping the
   * group unconditionally added four dead sidebar destinations to exactly the
   * deployments that never had accounts. Raised in review on #79.
   */
  it("is marked requiresAuth in both models", () => {
    for (const model of [NAV_MODEL_LEGACY, NAV_MODEL_V2]) {
      const account = (model as Array<{ id: string; requiresAuth?: boolean }>)
        .find(g => g.id === "account");
      expect(account, "the account group is missing").toBeDefined();
      expect(account!.requiresAuth, "the account group is not gated").toBe(true);
    }
  });

  it("is filtered out when nobody is signed in, and kept when someone is", () => {
    const signedOut = filterNavForDisplay(NAV_MODEL_V2, true, "dashboard", false);
    expect(signedOut.some(g => g.id === "account")).toBe(false);

    const signedIn = filterNavForDisplay(NAV_MODEL_V2, true, "dashboard", true);
    expect(signedIn.some(g => g.id === "account")).toBe(true);
  });

  it("stays hidden even when an account page is somehow the active one", () => {
    // The deep-link escape hatch keeps the active page's group visible. That
    // must not resurrect a group whose every route 401s — the page is a dead
    // end either way, and showing it makes the dead end look navigable.
    const signedOut = filterNavForDisplay(NAV_MODEL_V2, true, "account:profile", false);
    expect(signedOut.some(g => g.id === "account")).toBe(false);
  });

  it("does not hide anything else", () => {
    // The gate must be narrow: only groups that opt in.
    const signedOut = filterNavForDisplay(NAV_MODEL_V2, true, "dashboard", false);
    const signedIn = filterNavForDisplay(NAV_MODEL_V2, true, "dashboard", true);
    const lost = signedIn.filter(g => !signedOut.some(x => x.id === g.id)).map(g => g.id);
    expect(lost).toEqual(["account"]);
  });

  it("defaults to showing the group, so existing callers are unaffected", () => {
    // The parameter is optional; every pre-existing call site passes three
    // arguments and must keep its behaviour.
    const legacyCall = filterNavForDisplay(NAV_MODEL_V2, true, "dashboard");
    expect(legacyCall.some(g => g.id === "account")).toBe(true);
  });
});

/**
 * The inverse of the check above, and the other half of the same hole.
 *
 * navReachability's original tests go tab -> nav: they catch a page that exists
 * but is invisible. Nothing went nav -> registry, so the opposite mistake — a
 * sidebar entry whose key resolves to no page — was uncovered. That one is
 * worse from the user's side: the menu item is right there, they click it, and
 * they get whatever App.tsx does with an unknown key. Added while registering
 * "crypto:signals", which is exactly the change that could have introduced it.
 */
describe("every nav key resolves to a real page", () => {
  const V2_ONLY = new Set(Object.keys(tradeDeskPages(true)));

  for (const [modelName, model] of [
    ["NAV_MODEL_LEGACY", NAV_MODEL_LEGACY],
    ["NAV_MODEL_V2", NAV_MODEL_V2],
  ] as const) {
    it(`${modelName}: no dead sidebar entries`, () => {
      const dead = [...navKeys(model as any)].filter(
        (key) => !(key in BASE_PAGES) && !V2_ONLY.has(key),
      );
      expect(dead).toEqual([]);
    });
  }

  it("crypto:signals is registered in both models and in the page registry", () => {
    expect(navKeys(NAV_MODEL_LEGACY as any).has("crypto:signals")).toBe(true);
    expect(navKeys(NAV_MODEL_V2 as any).has("crypto:signals")).toBe(true);
    expect("crypto:signals" in BASE_PAGES).toBe(true);
  });
});
