/**
 * Whether the Chart page draws the intraday day levels (prior day high/low/close, opening range,
 * session VWAP). Off by default so the chart looks as it always has; once turned on, the choice is
 * kept in this browser. Storage can be missing or blocked, so every access is guarded.
 */

export const DAY_LEVELS_PREF_KEY = "olbos.chart.dayLevels";

export function readDayLevelsPref(storage?: Pick<Storage, "getItem"> | null): boolean {
  try { return storage?.getItem(DAY_LEVELS_PREF_KEY) === "on"; } catch { return false; }
}

export function writeDayLevelsPref(storage: Pick<Storage, "setItem"> | null | undefined, on: boolean): void {
  try { storage?.setItem(DAY_LEVELS_PREF_KEY, on ? "on" : "off"); } catch { /* the choice lasts for this visit */ }
}
