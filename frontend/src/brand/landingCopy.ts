/**
 * Landing-page copy that differs by brand.
 *
 * Everything the page says about what the system DOES (the pipeline, the risk
 * controls, the plans and their limits, the track-record placeholders) is the
 * same for every brand, because it is the same platform and those statements
 * are verified against it. Only the framing differs: who the page is for, the
 * headline, and the community section.
 *
 * The truthfulness rules the Olbos page already follows apply to every brand
 * (see the comments in pages/Landing.tsx): say what exists, label what does not,
 * imply no track record, never promise returns. A guard test scans this file's
 * Cimphawng copy for promise-type wording.
 *
 * Olbos's entries below are its original strings, unchanged. A test pins them.
 */

import type { BrandConfig, BrandKey } from "./brands";

export interface CommunityCell { title: string; body: string }

export interface LandingCopy {
  eyebrow: string;
  h1: string;
  lede: string;
  finalHeading: string;
  /** Rendered only when present. Olbos has none. */
  community?: { eyebrow: string; heading: string; lede: string; cells: readonly CommunityCell[] };
}

const heroLede = (systemName: string) =>
  `${systemName} runs a rules-based options and equity workflow — regime detection, a trained signal model, and a fail-closed risk gate — in front of every trade decision. Nothing executes without passing guardrails, and every signal shows where it came from. Crypto is signals-only — scanned and tracked for forward outcomes, with no order path. Currently in paper-trading evaluation; live capital requires a validated track record first.`;

export function landingCopyFor(brand: Pick<BrandConfig, "key" | "systemName">): LandingCopy {
  return COPY[brand.key](brand.systemName);
}

const COPY: Record<BrandKey, (systemName: string) => LandingCopy> = {
  olbos: (systemName) => ({
    eyebrow: "Systematic Options & Equity Execution",
    h1: "Systematic options and equity execution with risk controls built into every decision.",
    lede: heroLede(systemName),
    finalHeading: "Start in paper. Prove the edge before it's live.",
  }),
  cimphawng: (systemName) => ({
    eyebrow: "Systematic Trading for the Zomi Community",
    h1: "A disciplined trading terminal for the Zomi community, with risk controls built into every decision.",
    lede: heroLede(systemName),
    finalHeading: "Start in paper. Learn the system before it's live.",
    community: {
      eyebrow: "For the Zomi Community",
      heading: "Serious tools, built to be learned in the open.",
      lede:
        "Cimphawng Trading is made for Zomi traders and investors who want a clear view of what a trading system is doing and why. It starts with paper trading, so you can learn the tools before any real money is involved.",
      cells: [
        {
          title: "Learn in paper first",
          body: "Accounts open in paper trading. No live order can be placed until the account and environment are explicitly switched to live.",
        },
        {
          title: "English, with Zomi as it is reviewed",
          body: "The interface is in English, with a Zomi language switch. Zomi wording is added only after a Zomi speaker has reviewed it, so many screens are still English. Anything about orders, risk limits or the kill switch stays in English until the exact Zomi sentences are approved.",
        },
        {
          title: "The same engine and the same rules",
          body: "Cimphawng Trading runs on the Olbos Trade platform: the same research, the same risk gate and the same audit trail. The name on the door differs. The protections do not.",
        },
      ],
    },
  }),
};
