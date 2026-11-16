/**
 * Which indicators the chart draws, remembered in this browser and shared by the Chart page and
 * the Pro Workspace (turn one on in either and it is on in both). Storage can be missing, blocked
 * or edited by hand, so every read is validated and every access guarded; anything unusable falls
 * back to the default, which is what the chart drew before the picker existed.
 */
import { DEFAULT_INDICATORS, normaliseSelection, type IndicatorId } from "./indicators";

export const INDICATORS_PREF_KEY = "olbos.chart.indicators";

export function readIndicatorPref(
  storage?: Pick<Storage, "getItem"> | null,
  key: string = INDICATORS_PREF_KEY,
  fallback: readonly IndicatorId[] = DEFAULT_INDICATORS,
): IndicatorId[] {
  try {
    const raw = storage?.getItem(key);
    if (raw == null) return [...fallback];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...fallback];
    const clean = normaliseSelection(parsed);
    // An empty list the person chose ("candles only") is valid. A non-empty list of ids this
    // version has never heard of is corruption or an older build: use the default, not a blank chart.
    if (clean.length === 0 && parsed.length > 0) return [...fallback];
    return clean;
  } catch {
    return [...fallback];
  }
}

export function writeIndicatorPref(
  storage: Pick<Storage, "setItem"> | null | undefined,
  selection: readonly IndicatorId[],
  key: string = INDICATORS_PREF_KEY,
): void {
  try {
    storage?.setItem(key, JSON.stringify(normaliseSelection(selection)));
  } catch { /* storage unavailable: the choice lasts for this visit */ }
}
