/**
 * Which indicators the chart draws, remembered in this browser and shared by the Chart page and
 * the Pro Workspace (turn one on in either and it is on in both). Storage can be missing, blocked
 * or edited by hand, so every read is validated and every access guarded; anything unusable falls
 * back to the default, which is what the chart drew before the picker existed.
 */
import { DEFAULT_INDICATORS, normaliseSelection, type IndicatorId } from "./indicators";

export const INDICATORS_PREF_KEY = "olbos.chart.indicators";

export function readIndicatorPref(storage?: Pick<Storage, "getItem"> | null): IndicatorId[] {
  try {
    const raw = storage?.getItem(INDICATORS_PREF_KEY);
    if (raw == null) return [...DEFAULT_INDICATORS];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...DEFAULT_INDICATORS];
    const clean = normaliseSelection(parsed);
    // An empty list the person chose ("candles only") is valid. A non-empty list of ids this
    // version has never heard of is corruption or an older build: use the default, not a blank chart.
    if (clean.length === 0 && parsed.length > 0) return [...DEFAULT_INDICATORS];
    return clean;
  } catch {
    return [...DEFAULT_INDICATORS];
  }
}

export function writeIndicatorPref(storage: Pick<Storage, "setItem"> | null | undefined, selection: readonly IndicatorId[]): void {
  try {
    storage?.setItem(INDICATORS_PREF_KEY, JSON.stringify(normaliseSelection(selection)));
  } catch { /* storage unavailable: the choice lasts for this visit */ }
}
