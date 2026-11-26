/**
 * Intraday reference levels for the Chart page: where the prior session ended, how today opened
 * against it, the opening range (the first 30 minutes), and where price sits against session VWAP.
 *
 * Pure arithmetic over the bars the chart already has. It DESCRIBES the day; it is not a signal and
 * suggests no trade. Any rule built on these levels is a hypothesis for the Edge Lab ledger to test,
 * not something this file should be read as recommending.
 *
 * Fail closed: when the bars cannot support a level (daily bars, a missing opening bar, one session,
 * an incomplete prior day) that level is simply absent and the reason is said in words. It never
 * draws a level from partial data and never turns a gap into zero.
 *
 * Sessions are New York regular-hours days (09:30-16:00 ET). Pre- and after-hours bars are ignored,
 * so a feed that includes them cannot move the opening range or the prior close.
 */

import type { IntervalKey } from "./timeframes";

export interface BarLike { timestamp: string; open: number; high: number; low: number; close: number; volume: number }

export type RangeSide = "above" | "below" | "inside";

export interface IntradayLevels {
  ready: boolean;
  /** Why nothing is drawn, in words (set when `ready` is false). */
  reason?: string;
  sessionDate?: string;
  prior?: { date: string; high: number; low: number; close: number };
  /** Today's first regular-hours open. */
  open?: number;
  /** Today's open against the prior close, in percent. */
  gapPct?: number;
  /** The first 30 minutes of today. `complete` is true once a bar at or after 10:00 exists. */
  openingRange?: { high: number; low: number; complete: boolean };
  /** Volume-weighted average price of today's regular-hours bars so far. Absent when there is no volume. */
  vwap?: number;
  last?: number;
  vsOpeningRange?: RangeSide;
  vsPriorRange?: RangeSide;
  vsVwap?: "above" | "below" | "at";
}

const OPEN_MIN = 9 * 60 + 30;
const OR_END_MIN = 10 * 60;
const CLOSE_MIN = 16 * 60;
/** A prior session whose last bar started before this did not run to its close (a half day ends 13:00). */
const PRIOR_LAST_BAR_AT_LEAST = 12 * 60 + 30;

const INTERVAL_MINUTES: Partial<Record<IntervalKey, number>> = { "1m": 1, "5m": 5, "15m": 15, "30m": 30 };

const fmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function etParts(ts: string): { date: string; minute: number } | null {
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return null;
  const p: Record<string, string> = {};
  for (const part of fmt.formatToParts(new Date(t))) p[part.type] = part.value;
  const minute = Number(p.hour) * 60 + Number(p.minute);
  if (!Number.isFinite(minute)) return null;
  return { date: `${p.year}-${p.month}-${p.day}`, minute };
}

const none = (reason: string): IntradayLevels => ({ ready: false, reason });
const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

function side(price: number, low: number, high: number): RangeSide {
  return price > high ? "above" : price < low ? "below" : "inside";
}

export function computeIntradayLevels(bars: readonly BarLike[], interval: IntervalKey): IntradayLevels {
  const step = INTERVAL_MINUTES[interval];
  if (!step) return none("Day levels need 1, 5, 15 or 30 minute bars.");
  if (!bars.length) return none("No bars to read day levels from.");

  type Row = { minute: number; b: BarLike };
  const days = new Map<string, Row[]>();
  for (const b of bars) {
    if (![b.open, b.high, b.low, b.close].every(finite) || b.high < b.low) continue;     // a broken bar is skipped, not guessed at
    const p = etParts(b.timestamp);
    if (!p || p.minute < OPEN_MIN || p.minute >= CLOSE_MIN) continue;                    // regular hours only
    (days.get(p.date) ?? days.set(p.date, []).get(p.date)!).push({ minute: p.minute, b });
  }
  const dates = [...days.keys()].sort();
  if (dates.length < 2) return none("Day levels need the prior session as well as today: load more history (try the 5D range).");

  const today = dates[dates.length - 1];
  const priorDate = dates[dates.length - 2];
  const todayRows = days.get(today)!.sort((a, b) => a.minute - b.minute);
  const priorRows = days.get(priorDate)!.sort((a, b) => a.minute - b.minute);

  const out: IntradayLevels = { ready: false, sessionDate: today };

  // Prior session: needs to have run to (about) the close, or its "close" is just where the data stopped.
  const priorLast = priorRows[priorRows.length - 1];
  if (priorRows.length >= 3 && priorLast.minute >= PRIOR_LAST_BAR_AT_LEAST) {
    out.prior = {
      date: priorDate,
      high: Math.max(...priorRows.map((r) => r.b.high)),
      low: Math.min(...priorRows.map((r) => r.b.low)),
      close: priorLast.b.close,
    };
  }

  const first = todayRows[0];
  const last = todayRows[todayRows.length - 1].b.close;
  out.last = last;

  // Today's opening bar must really be the 09:30 bar, or "open" and "opening range" would be wrong.
  const hasOpeningBar = first.minute < OPEN_MIN + step;
  if (hasOpeningBar) {
    out.open = first.b.open;
    if (out.prior) out.gapPct = ((first.b.open - out.prior.close) / out.prior.close) * 100;
    const orRows = todayRows.filter((r) => r.minute < OR_END_MIN);
    out.openingRange = {
      high: Math.max(...orRows.map((r) => r.b.high)),
      low: Math.min(...orRows.map((r) => r.b.low)),
      complete: todayRows.some((r) => r.minute >= OR_END_MIN),
    };
    out.vsOpeningRange = side(last, out.openingRange.low, out.openingRange.high);
  }

  // Session VWAP only means something from the open: a day whose first bars are missing has no VWAP.
  let pv = 0, vol = 0;
  for (const r of hasOpeningBar ? todayRows : []) {
    if (!finite(r.b.volume) || r.b.volume <= 0) continue;
    pv += ((r.b.high + r.b.low + r.b.close) / 3) * r.b.volume;
    vol += r.b.volume;
  }
  if (vol > 0) {
    out.vwap = pv / vol;
    const eps = Math.abs(out.vwap) * 1e-6;
    out.vsVwap = last > out.vwap + eps ? "above" : last < out.vwap - eps ? "below" : "at";
  }

  if (out.prior) out.vsPriorRange = side(last, out.prior.low, out.prior.high);

  out.ready = Boolean(out.prior || out.openingRange || out.vwap !== undefined);
  if (!out.ready) out.reason = "Not enough regular-hours bars for day levels.";
  return out;
}

const n2 = (v: number) => v.toFixed(2);

/** The levels as chart price lines. Neutral colours: green, red and amber keep their meaning (profit, loss, caution). */
export function levelLines(l: IntradayLevels): { label: string; value: number; color: string }[] {
  const lines: { label: string; value: number; color: string }[] = [];
  if (l.prior) {
    lines.push({ label: "PDH", value: l.prior.high, color: "#8aa4d6" });
    lines.push({ label: "PDL", value: l.prior.low, color: "#8aa4d6" });
    lines.push({ label: "PDC", value: l.prior.close, color: "#6f7f9f" });
  }
  if (l.openingRange) {
    const f = l.openingRange.complete ? "" : "~";                     // "~" marks a range still forming
    lines.push({ label: `${f}ORH`, value: l.openingRange.high, color: "#b48cff" });
    lines.push({ label: `${f}ORL`, value: l.openingRange.low, color: "#b48cff" });
  }
  if (l.vwap !== undefined) lines.push({ label: "VWAP", value: l.vwap, color: "#7fd1d1" });
  return lines;
}

/** One line of words, for the chart footer. Never says to buy or sell. */
export function describeLevels(l: IntradayLevels): string {
  if (!l.ready) return l.reason ?? "Day levels unavailable.";
  const parts: string[] = [];
  if (l.prior) parts.push(`prior day H ${n2(l.prior.high)} L ${n2(l.prior.low)} C ${n2(l.prior.close)}`);
  if (l.gapPct !== undefined) parts.push(`opened ${l.gapPct >= 0 ? "+" : "−"}${Math.abs(l.gapPct).toFixed(2)}% vs prior close`);
  if (l.openingRange) {
    const tag = l.openingRange.complete ? "" : " (still forming)";
    parts.push(`opening range ${n2(l.openingRange.low)}–${n2(l.openingRange.high)}${tag}`);
  }
  const where: string[] = [];
  if (l.vsOpeningRange) where.push(`${l.vsOpeningRange === "inside" ? "inside" : l.vsOpeningRange === "above" ? "above" : "below"} the opening range`);
  if (l.vsVwap && l.vwap !== undefined) where.push(l.vsVwap === "at" ? `at VWAP ${n2(l.vwap)}` : `${l.vsVwap} VWAP ${n2(l.vwap)}`);
  if (where.length) parts.push(`price is ${where.join(" and ")}`);
  return parts.join(" · ");
}
