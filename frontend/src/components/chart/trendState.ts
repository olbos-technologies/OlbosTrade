/**
 * Where price sits against its own moving averages, per timeframe, in plain words.
 *
 * DESCRIPTIVE ONLY. "Above the 50-bar average" says what has already happened; it is not a signal, a
 * forecast or a reason to trade, and the page that shows it says so. It is worded in words and arrows,
 * never in the green and red that mean profit and loss elsewhere in the app.
 */
import type { ChartBar } from "../CandlestickChart";
import { smaValues } from "./indicators";

export type Side = "above" | "below" | "at";

export interface TrendState {
  close: number | null;
  ma20: number | null;
  ma50: number | null;
  vsMa20: Side | null;
  vsMa50: Side | null;
}

/** Closer than this (as a fraction of the average) counts as "at" it, so a one-cent wobble is not a flip. */
const AT = 1e-4;

export function sideOf(close: number | null, avg: number | null): Side | null {
  if (close == null || avg == null || !Number.isFinite(close) || !Number.isFinite(avg) || avg === 0) return null;
  const rel = (close - avg) / Math.abs(avg);
  return Math.abs(rel) < AT ? "at" : rel > 0 ? "above" : "below";
}

export function trendState(bars: ChartBar[]): TrendState {
  if (!bars.length) return { close: null, ma20: null, ma50: null, vsMa20: null, vsMa50: null };
  const closes = bars.map((b) => b.close);
  const lastClose = closes[closes.length - 1];
  const ma20 = smaValues(closes, 20)[closes.length - 1];
  const ma50 = smaValues(closes, 50)[closes.length - 1];
  return { close: lastClose, ma20, ma50, vsMa20: sideOf(lastClose, ma20), vsMa50: sideOf(lastClose, ma50) };
}

export interface Alignment {
  /** Timeframes with enough bars to say. */
  known: number;
  above: number;
  below: number;
  at: number;
  summary: string;
}

/**
 * Do the timeframes agree about where price sits against the 50-bar average? `total` is how many
 * timeframes are shown, so the sentence can say when some of them could not be compared.
 */
export function alignment(states: (TrendState | null)[], total = states.length): Alignment {
  const sides = states.map((s) => s?.vsMa50 ?? null).filter((s): s is Side => s !== null);
  const known = sides.length;
  const above = sides.filter((s) => s === "above").length;
  const below = sides.filter((s) => s === "below").length;
  const at = known - above - below;
  const some = known < total ? ` on the ${known} of ${total} timeframes with enough bars` : known === 1 ? " on the 1 timeframe" : ` on all ${known} timeframes`;
  let summary: string;
  if (known === 0) summary = "Not enough bars loaded to compare the timeframes.";
  else if (above === known) summary = `Price is above its 50-bar average${some}.`;
  else if (below === known) summary = `Price is below its 50-bar average${some}.`;
  else if (at === known) summary = `Price is at its 50-bar average${some}.`;
  else summary = `Mixed: above the 50-bar average on ${above} of ${known}, below on ${below}${at ? `, at it on ${at}` : ""}${known < total ? ` (${total - known} without enough bars)` : ""}.`;
  return { known, above, below, at, summary };
}
