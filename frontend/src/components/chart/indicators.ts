/**
 * The indicators a chart can draw, and the maths behind them.
 *
 * Pure: no chart library, no DOM. `indicatorSeries.ts` draws them; this file says what exists,
 * what each one is called and how it is computed. The maths is checked against published reference
 * values in `indicators.test.ts`.
 *
 * Colours never use the green and red that mean profit and loss elsewhere in the app, so a line on
 * the chart cannot be mistaken for a gain or a loss. (MACD's histogram is the one exception and is
 * unchanged: it is a signed quantity, positive or negative, not a trading result.)
 */
import type { HistogramData, LineData, UTCTimestamp } from "lightweight-charts";
import type { ChartBar } from "../CandlestickChart";

export function toUnixSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

export function fmtNum(n: number | null | undefined, digits = 2): string {
  return n == null || !Number.isFinite(n) ? "—" : n.toFixed(digits);
}

export const COLOR = {
  upCandle: "#18c37e",
  downCandle: "#ff5f6d",
  ma5: "#5b9fd4",
  ma10: "#f4a261",
  ma20: "#f4c64f",
  ma50: "#e879f9",
  ma200: "#e2e8f0",
  ema9: "#22d3ee",
  ema21: "#d8b4fe",
  ema50: "#94a3b8",
  vwap: "#8e7cfb",
  bollinger: "rgba(125, 170, 255, 0.85)",
  volume: "rgba(148,163,184,0.7)",
  macd: "#5b9fd4",
  macdSignal: "#f4a261",
  macdHistUp: "rgba(24,195,126,0.55)",
  macdHistDown: "rgba(255,95,109,0.55)",
  rsi: "#c4b5fd",
  atr: "#38bdf8",
};

// ── maths ────────────────────────────────────────────────────────────────────

/**
 * Simple moving average of `period` values; null until there are enough. Each value is the mean of
 * its own window (not a running sum), so it matches the chart's existing moving averages to the digit.
 */
export function smaValues(values: number[], period: number): (number | null)[] {
  return values.map((_, i) => {
    if (period < 1 || i < period - 1) return null;
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j];
    return sum / period;
  });
}

/** EMA seeded with the SMA of the first `period` values, then the standard recursion. */
export function emaValues(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = values.map(() => null);
  if (period < 1 || values.length < period) return out;
  const k = 2 / (period + 1);
  let ema = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out[period - 1] = ema;
  for (let i = period; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
    out[i] = ema;
  }
  return out;
}

/** The chart's existing name for EMA, kept so nothing that imports it changes. */
export const emaSeries = emaValues;

/**
 * Bollinger bands: the SMA of `period` closes, and bands `mult` standard deviations either side.
 * The deviation is the population one (divide by N), as in the original definition.
 */
export function bollingerValues(values: number[], period = 20, mult = 2): {
  mid: (number | null)[]; upper: (number | null)[]; lower: (number | null)[];
} {
  const mid = smaValues(values, period);
  const upper: (number | null)[] = values.map(() => null);
  const lower: (number | null)[] = values.map(() => null);
  for (let i = period - 1; i < values.length; i++) {
    const m = mid[i];
    if (m == null) continue;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) sq += (values[j] - m) ** 2;
    const sd = Math.sqrt(sq / period);
    upper[i] = m + mult * sd;
    lower[i] = m - mult * sd;
  }
  return { mid, upper, lower };
}

/**
 * Wilder's RSI. The first value (at index `period`) uses the simple mean of the first `period`
 * gains and losses; after that each average is smoothed as (previous * (period-1) + current) / period.
 * No losses at all reads 100; no movement at all reads 50.
 */
export function rsiValues(values: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = values.map(() => null);
  if (period < 1 || values.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = values[i] - values[i - 1];
    if (d >= 0) gain += d; else loss -= d;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  const rsi = () => (avgGain === 0 && avgLoss === 0 ? 50 : avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
  out[period] = rsi();
  for (let i = period + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    avgGain = (avgGain * (period - 1) + (d > 0 ? d : 0)) / period;
    avgLoss = (avgLoss * (period - 1) + (d < 0 ? -d : 0)) / period;
    out[i] = rsi();
  }
  return out;
}

/**
 * Wilder's ATR. True range is the largest of high-low, |high - previous close| and
 * |low - previous close| (the first bar has no previous close, so it is high-low). The first ATR, at
 * index `period - 1`, is the simple mean of the first `period` true ranges; then Wilder smoothing.
 */
export function atrValues(highs: number[], lows: number[], closes: number[], period = 14): (number | null)[] {
  const n = closes.length;
  const out: (number | null)[] = closes.map(() => null);
  if (period < 1 || n < period || highs.length !== n || lows.length !== n) return out;
  const tr = closes.map((_, i) => i === 0
    ? highs[0] - lows[0]
    : Math.max(highs[i] - lows[i], Math.abs(highs[i] - closes[i - 1]), Math.abs(lows[i] - closes[i - 1])));
  let atr = tr.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out[period - 1] = atr;
  for (let i = period; i < n; i++) {
    atr = (atr * (period - 1) + tr[i]) / period;
    out[i] = atr;
  }
  return out;
}

/** Cumulative volume-weighted average price over every bar loaded (not reset each session). */
export function vwapValues(bars: ChartBar[]): number[] {
  let pv = 0;
  let vol = 0;
  return bars.map((bar) => {
    const typical = (bar.high + bar.low + bar.close) / 3;
    pv += typical * Math.max(bar.volume, 1);
    vol += Math.max(bar.volume, 1);
    return pv / vol;
  });
}

export function toLineData(bars: ChartBar[], values: (number | null)[]): LineData[] {
  const out: LineData[] = [];
  values.forEach((v, i) => { if (v != null && Number.isFinite(v)) out.push({ time: toUnixSeconds(bars[i].timestamp), value: v }); });
  return out;
}

export function toVolumeData(bars: ChartBar[]): HistogramData[] {
  return bars.map((b) => ({
    time: toUnixSeconds(b.timestamp), value: b.volume,
    color: b.close >= b.open ? "rgba(24,195,126,0.45)" : "rgba(255,95,109,0.45)",
  }));
}

/** MACD (fast, slow, signal) as chart data. Moved here from CandlestickChart.tsx unchanged. */
export function computeMacd(bars: ChartBar[], fast = 12, slow = 26, signal = 9): {
  macd: LineData[];
  signal: LineData[];
  histogram: HistogramData[];
} {
  const closes = bars.map((b) => b.close);
  const fastEma = emaValues(closes, fast);
  const slowEma = emaValues(closes, slow);
  const macdRaw: (number | null)[] = closes.map((_, i) =>
    fastEma[i] != null && slowEma[i] != null ? (fastEma[i] as number) - (slowEma[i] as number) : null,
  );
  // Signal EMA only over defined MACD points: seed at the first full MACD index.
  const firstMacd = macdRaw.findIndex((v) => v != null);
  const signalRaw: (number | null)[] = closes.map(() => null);
  if (firstMacd >= 0 && closes.length - firstMacd >= signal) {
    const seedEnd = firstMacd + signal;
    let sig = 0;
    for (let i = firstMacd; i < seedEnd; i++) sig += macdRaw[i] as number;
    sig /= signal;
    signalRaw[seedEnd - 1] = sig;
    const k = 2 / (signal + 1);
    for (let i = seedEnd; i < closes.length; i++) {
      if (macdRaw[i] == null) continue;
      sig = (macdRaw[i] as number) * k + sig * (1 - k);
      signalRaw[i] = sig;
    }
  }

  const macd: LineData[] = [];
  const signalLine: LineData[] = [];
  const histogram: HistogramData[] = [];
  bars.forEach((bar, i) => {
    const t = toUnixSeconds(bar.timestamp);
    if (macdRaw[i] != null) macd.push({ time: t, value: macdRaw[i] as number });
    if (signalRaw[i] != null) signalLine.push({ time: t, value: signalRaw[i] as number });
    if (macdRaw[i] != null && signalRaw[i] != null) {
      const hist = (macdRaw[i] as number) - (signalRaw[i] as number);
      histogram.push({ time: t, value: hist, color: hist >= 0 ? COLOR.macdHistUp : COLOR.macdHistDown });
    }
  });
  return { macd, signal: signalLine, histogram };
}

const closesOf = (bars: ChartBar[]) => bars.map((b) => b.close);
const last = <T,>(xs: (T | null)[]): T | null => (xs.length ? xs[xs.length - 1] : null);
const lastOf = <T,>(xs: T[]): T | undefined => xs[xs.length - 1];

// ── what exists ──────────────────────────────────────────────────────────────

export type IndicatorId =
  | "ma5" | "ma10" | "ma20" | "ma50" | "ma200"
  | "ema9" | "ema21" | "ema50"
  | "vwap" | "bb"
  | "volume" | "macd" | "rsi" | "atr";

export type IndicatorGroup = "overlay" | "panel";
export type IndicatorKind = "sma" | "ema" | "vwap" | "bb" | "volume" | "macd" | "rsi" | "atr";

export interface IndicatorDef {
  id: IndicatorId;
  label: string;
  group: IndicatorGroup;
  /** What it is computed as. Dispatch on this, never on the id's spelling ("macd" starts with "ma"). */
  kind: IndicatorKind;
  /** One plain sentence for the picker: what it is. It does not say what it predicts. Read by assistive technology, and shown on phones. */
  description: string;
  /** The same in a few words, for the compact desktop row. */
  short: string;
  color: string;
  swatch: "solid" | "dashed" | "bar";
  /** Moving-average period, for the ones that have one. */
  period?: number;
}

export const INDICATORS: readonly IndicatorDef[] = [
  { id: "ma5", kind: "sma", label: "MA5", group: "overlay", period: 5, color: COLOR.ma5, swatch: "solid", short: "Average of the last 5 closes", description: "Simple moving average of the last 5 closes." },
  { id: "ma10", kind: "sma", label: "MA10", group: "overlay", period: 10, color: COLOR.ma10, swatch: "solid", short: "Average of the last 10 closes", description: "Simple moving average of the last 10 closes." },
  { id: "ma20", kind: "sma", label: "MA20", group: "overlay", period: 20, color: COLOR.ma20, swatch: "solid", short: "Average of the last 20 closes", description: "Simple moving average of the last 20 closes." },
  { id: "ma50", kind: "sma", label: "MA50", group: "overlay", period: 50, color: COLOR.ma50, swatch: "solid", short: "50-close average · needs 50 bars", description: "Simple moving average of the last 50 closes. Needs 50 bars before it starts." },
  { id: "ma200", kind: "sma", label: "MA200", group: "overlay", period: 200, color: COLOR.ma200, swatch: "solid", short: "200-close average · needs 200 bars", description: "Simple moving average of the last 200 closes. Needs 200 bars before it starts, so it only shows on long ranges." },
  { id: "ema9", kind: "ema", label: "EMA9", group: "overlay", period: 9, color: COLOR.ema9, swatch: "solid", short: "Recent closes weigh more · 9 bars", description: "Exponential moving average, 9 bars: weights recent closes more than a simple average." },
  { id: "ema21", kind: "ema", label: "EMA21", group: "overlay", period: 21, color: COLOR.ema21, swatch: "solid", short: "Recent closes weigh more · 21 bars", description: "Exponential moving average, 21 bars." },
  { id: "ema50", kind: "ema", label: "EMA50", group: "overlay", period: 50, color: COLOR.ema50, swatch: "solid", short: "Recent closes weigh more · 50 bars", description: "Exponential moving average, 50 bars." },
  { id: "vwap", kind: "vwap", label: "VWAP", group: "overlay", color: COLOR.vwap, swatch: "dashed", short: "Volume-weighted, all bars loaded", description: "Volume-weighted average price over all the bars loaded. It is not reset each session." },
  { id: "bb", kind: "bb", label: "Bollinger", group: "overlay", color: COLOR.bollinger, swatch: "dashed", short: "20-bar average ± 2 std deviations", description: "20-bar average with bands 2 standard deviations either side: how far price has stretched from its recent average." },
  { id: "volume", kind: "volume", label: "Volume", group: "panel", color: COLOR.volume, swatch: "bar", short: "Shares traded in each bar", description: "Shares traded in each bar." },
  { id: "macd", kind: "macd", label: "MACD", group: "panel", color: COLOR.macd, swatch: "solid", short: "12/26 EMA gap with a 9-bar signal", description: "12/26 EMA difference with a 9-bar signal line and a histogram of the gap between them." },
  { id: "rsi", kind: "rsi", label: "RSI", group: "panel", color: COLOR.rsi, swatch: "solid", short: "Momentum 0–100 · guides at 30 and 70", description: "Relative strength index over 14 bars, 0 to 100, with guides at 30 and 70. It measures recent momentum; it does not predict." },
  { id: "atr", kind: "atr", label: "ATR", group: "panel", color: COLOR.atr, swatch: "solid", short: "Typical one-bar range · 14 bars", description: "Average true range over 14 bars: how much price typically moves in one bar, in price units." },
];

export const INDICATOR_IDS: readonly IndicatorId[] = INDICATORS.map((d) => d.id);
const ORDER = new Map(INDICATOR_IDS.map((id, i) => [id, i]));
export const INDICATOR_BY_ID: ReadonlyMap<IndicatorId, IndicatorDef> = new Map(INDICATORS.map((d) => [d.id, d]));

/** What a chart draws when it has not been told otherwise: exactly what it drew before the picker. */
export const DEFAULT_INDICATORS: readonly IndicatorId[] = ["ma5", "ma10", "ma20", "vwap", "volume", "macd"];

/** Most indicators on one chart. A phone has little room, and every panel takes a slice of the chart. */
export const MAX_INDICATORS = 10;

export function isIndicatorId(v: unknown): v is IndicatorId {
  return typeof v === "string" && ORDER.has(v as IndicatorId);
}

/**
 * Any input turned into a valid selection: known ids only, no duplicates, in registry order (so the
 * panels always stack in the same order whatever order they were switched on), at most MAX_INDICATORS.
 */
export function normaliseSelection(input: unknown): IndicatorId[] {
  if (!Array.isArray(input)) return [];
  const seen = new Set<IndicatorId>();
  for (const v of input) if (isIndicatorId(v)) seen.add(v);
  return [...seen].sort((a, b) => ORDER.get(a)! - ORDER.get(b)!).slice(0, MAX_INDICATORS);
}

export const selectionKey = (sel: readonly IndicatorId[]) => sel.join(",");

// ── readout and legend text ──────────────────────────────────────────────────

export interface ReadoutItem { text: string; color: string }

/** The last value of each selected indicator, as the line of text above the chart. */
export function readoutFor(bars: ChartBar[], selection: readonly IndicatorId[]): ReadoutItem[] {
  if (!bars.length) return [];
  const closes = closesOf(bars);
  const items: ReadoutItem[] = [];
  for (const id of selection) {
    const def = INDICATOR_BY_ID.get(id);
    if (!def) continue;
    switch (def.kind) {
      case "sma": items.push({ text: `${def.label} ${fmtNum(last(smaValues(closes, def.period!)))}`, color: def.color }); break;
      case "ema": items.push({ text: `${def.label} ${fmtNum(last(emaValues(closes, def.period!)))}`, color: def.color }); break;
      case "vwap": items.push({ text: `VWAP ${fmtNum(lastOf(vwapValues(bars)))}`, color: COLOR.vwap }); break;
      case "bb": {
        const b = bollingerValues(closes);
        items.push({ text: `BB ${fmtNum(last(b.upper))} / ${fmtNum(last(b.mid))} / ${fmtNum(last(b.lower))}`, color: COLOR.bollinger });
        break;
      }
      case "macd": {
        const m = computeMacd(bars);
        const macd = lastOf(m.macd)?.value ?? null;
        const signal = lastOf(m.signal)?.value ?? null;
        const hist = lastOf(m.histogram)?.value ?? null;
        items.push({ text: `MACD ${fmtNum(macd, 3)}`, color: COLOR.macd });
        items.push({ text: `Sig ${fmtNum(signal, 3)}`, color: COLOR.macdSignal });
        items.push({ text: `Hist ${fmtNum(hist, 3)}`, color: (hist ?? 0) >= 0 ? COLOR.upCandle : COLOR.downCandle });
        break;
      }
      case "rsi": items.push({ text: `RSI ${fmtNum(last(rsiValues(closes)), 1)}`, color: COLOR.rsi }); break;
      case "atr": items.push({ text: `ATR ${fmtNum(last(atrValues(bars.map((b) => b.high), bars.map((b) => b.low), closes)))}`, color: COLOR.atr }); break;
      case "volume": break;   // a panel of bars: nothing to read out as a number
    }
  }
  return items;
}

export interface LegendItem { label: string; color: string; style: "solid" | "dashed" | "bar" }

export function legendFor(selection: readonly IndicatorId[]): LegendItem[] {
  return selection.flatMap((id) => {
    const d = INDICATOR_BY_ID.get(id);
    return d ? [{ label: d.label, color: d.color, style: d.swatch }] : [];
  });
}
