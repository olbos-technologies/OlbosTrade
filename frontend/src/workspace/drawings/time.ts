/**
 * Converting between a chart's bar positions (its "logical" index: 0 is the first
 * bar, 1.5 is halfway between the second and third) and real time.
 *
 * The chart only knows the times of bars that exist. A drawing needs more: a point
 * between two bars, and a point in the future past the last bar. So positions are
 * interpolated between bars, and extended beyond either end using the typical gap
 * between bars (the median, so a weekend or holiday gap does not distort it).
 *
 * `times` must be ascending UTC seconds, one per bar.
 */

const FALLBACK_STEP = 60;

function typicalStep(times: readonly number[], fromEnd: boolean): number {
  const n = times.length;
  if (n < 2) return FALLBACK_STEP;
  const gaps: number[] = [];
  if (fromEnd) for (let i = n - 1; i >= 1 && gaps.length < 30; i--) gaps.push(times[i] - times[i - 1]);
  else for (let i = 1; i < n && gaps.length < 30; i++) gaps.push(times[i] - times[i - 1]);
  const positive = gaps.filter((g) => g > 0).sort((a, b) => a - b);
  if (!positive.length) return FALLBACK_STEP;
  return positive[Math.floor(positive.length / 2)];
}

/** Bar position for a time. Fractional between bars; beyond either end it is extrapolated. */
export function timeToLogical(times: readonly number[], time: number): number | null {
  const n = times.length;
  if (!n || !Number.isFinite(time)) return null;
  if (n === 1) return (time - times[0]) / FALLBACK_STEP;
  if (time >= times[n - 1]) return n - 1 + (time - times[n - 1]) / typicalStep(times, true);
  if (time <= times[0]) return (time - times[0]) / typicalStep(times, false);
  let lo = 0, hi = n - 1; // largest index whose time is <= `time`
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= time) lo = mid; else hi = mid;
  }
  const span = times[lo + 1] - times[lo];
  return span > 0 ? lo + (time - times[lo]) / span : lo;
}

/** The time at a bar position. The inverse of timeToLogical. */
export function logicalToTime(times: readonly number[], logical: number): number | null {
  const n = times.length;
  if (!n || !Number.isFinite(logical)) return null;
  if (n === 1) return times[0] + logical * FALLBACK_STEP;
  if (logical >= n - 1) return times[n - 1] + (logical - (n - 1)) * typicalStep(times, true);
  if (logical <= 0) return times[0] + logical * typicalStep(times, false);
  const i = Math.floor(logical);
  return times[i] + (logical - i) * (times[i + 1] - times[i]);
}
