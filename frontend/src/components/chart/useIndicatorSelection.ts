import { useCallback, useState } from "react";
import { DEFAULT_INDICATORS, normaliseSelection, type IndicatorId } from "./indicators";
import { readIndicatorPref, writeIndicatorPref } from "./indicatorPrefs";

function browserStorage(): Storage | null {
  try { return typeof window !== "undefined" ? window.localStorage : null; } catch { return null; }
}

/** The chart's selected indicators, saved in this browser. `set` validates; `reset` restores the default. */
export function useIndicatorSelection(): [IndicatorId[], (next: readonly IndicatorId[]) => void, () => void] {
  const [selection, setSelection] = useState<IndicatorId[]>(() => readIndicatorPref(browserStorage()));
  const set = useCallback((next: readonly IndicatorId[]) => {
    const clean = normaliseSelection(next);
    writeIndicatorPref(browserStorage(), clean);
    setSelection(clean);
  }, []);
  const reset = useCallback(() => set(DEFAULT_INDICATORS), [set]);
  return [selection, set, reset];
}
