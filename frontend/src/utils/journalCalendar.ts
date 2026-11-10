/** Pure helpers for the Journal's monthly P&L calendar. */

export interface DayPnl { date: string; pnl: number; trades: number }

export interface CalendarCell {
  /** 1-based day of month, or null for the blank cells padding the first week. */
  day: number | null;
  iso: string | null;
  pnl: number | null;
  trades: number;
}

/** "YYYY-MM" shifted by `delta` months. */
export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Weeks of cells, Sunday first, padded with blanks so every week has 7. */
export function buildMonthGrid(month: string, days: DayPnl[]): CalendarCell[][] {
  const [y, m] = month.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const byDate = new Map(days.map(d => [d.date, d]));
  const cells: CalendarCell[] = Array.from({ length: first }, () => ({ day: null, iso: null, pnl: null, trades: 0 }));
  for (let day = 1; day <= count; day++) {
    const iso = `${month}-${String(day).padStart(2, "0")}`;
    const hit = byDate.get(iso);
    cells.push({ day, iso, pnl: hit ? hit.pnl : null, trades: hit ? hit.trades : 0 });
  }
  while (cells.length % 7 !== 0) cells.push({ day: null, iso: null, pnl: null, trades: 0 });
  const weeks: CalendarCell[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

/** Compact signed money for a small cell: +665.53, -1.52K. */
export function compactPnl(n: number): string {
  if (Math.round(n * 100) === 0) return "0.00";
  const sign = n < 0 ? "-" : "+";
  const a = Math.abs(n);
  return a >= 1000 ? `${sign}${(a / 1000).toFixed(2)}K` : `${sign}${a.toFixed(2)}`;
}
