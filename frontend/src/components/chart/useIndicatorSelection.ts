import { useCallback, useState } from "react";
import { DEFAULT_INDICATORS, normaliseSelection, type IndicatorId } from "./indicators";
import { INDICATORS_PREF_KEY, readIndicatorPref, writeIndicatorPref } from "./indicatorPrefs";

function browserStorage(): Storage | null {
  try { return typeof window !== "undefined" ? window.localStorage : null; } catch { return null; }
}

/**
 * The chart's selected indicators, saved in this browser. `set` validates; `reset` restores the default.
 * With no options this is the choice shared by the Chart page and the Workspace; another page can keep its
 * own under a different key and with a different default (the multi-timeframe tiles are small).
 */
export function useIndicatorSelection(
  opts: { key?: string; fallback?: readonly IndicatorId[] } = {},
): [IndicatorId[], (next: readonly IndicatorId[]) => void, () => void] {
  const key = opts.key ?? INDICATORS_PREF_KEY;
  const fallback = opts.fallback ?? DEFAULT_INDICATORS;
  const [selection, setSelection] = useState<IndicatorId[]>(() => readIndicatorPref(browserStorage(), key, fallback));
  const set = useCallback((next: readonly IndicatorId[]) => {
    const clean = normaliseSelection(next);
    writeIndicatorPref(browserStorage(), clean, key);
    setSelection(clean);
  }, [key]);
  // `fallback` is read once per call site (a constant), so it is not a dependency.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reset = useCallback(() => set(fallback), [set]);
  return [selection, set, reset];
}
