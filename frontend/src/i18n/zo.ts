/**
 * Zomi interface strings: English text -> Zomi.
 *
 * ONLY reviewed terms belong here. Every entry below was taken from the
 * Cimphawng community app's Zomi dictionary (cimphawng/index.html, `ZO`), which
 * a Zomi speaker has reviewed and which is live. Do not add a translation that a
 * Zomi speaker has not checked: a wrong word in a finance interface is worse
 * than an English one. Anything missing here simply shows in English.
 *
 * To add terms: put the exact English string on the left, exactly as it appears
 * at its `tr()` call site, and the reviewed Zomi on the right. The list of words
 * still needed is in docs/ZOMI_GLOSSARY.md.
 */
export const ZO: Readonly<Record<string, string>> = {
  "Home": "Inn",
  "Markets": "Market",
  "Sign out": "Pusuak",
};

/**
 * Wording that must stay in English until a Zomi speaker has approved the exact
 * Zomi sentence. These are the phrases a person acts on or relies on when money
 * (even paper money) is involved: order confirmation, risk and loss limits, the
 * kill switch, execution modes, paper/live labels and disclosures. A guard test
 * fails if any ZO key matches, unless the key is also in ZO_SAFETY_REVIEWED.
 */
export const ZO_SAFETY_BLOCKLIST =
  /kill switch|autopilot|copilot|submit|place (an )?order|confirm|loss limit|daily loss|drawdown|margin|live trading|real money|paper|not financial advice|disclos|risk limit|stop loss|liquidat/i;

/** Safety-wording keys a Zomi speaker has explicitly approved. Empty until someone does. */
export const ZO_SAFETY_REVIEWED: ReadonlySet<string> = new Set<string>([]);
