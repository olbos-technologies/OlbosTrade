import React from "react";
import { pnlColor } from "../utils/pnlTone";
import { buildMonthGrid, compactPnl, shiftMonth, type DayPnl } from "../utils/journalCalendar";
import "./JournalCalendar.css";

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

/**
 * Month calendar of realized P&L by the day a trade closed. A day with no closed
 * trade shows only its number; a day is never shown as 0.00 unless trades netted
 * to zero. The sign is printed, so colour is never the only cue.
 */
export default function JournalCalendar({ month, days, onMonthChange }: {
  month: string;
  days: DayPnl[];
  onMonthChange: (m: string) => void;
}) {
  const weeks = buildMonthGrid(month, days);
  const total = days.reduce((a, d) => a + d.pnl, 0);
  const trades = days.reduce((a, d) => a + d.trades, 0);
  return (
    <section className="journal-cal" aria-label={`P&L calendar ${month}`}>
      <div className="journal-cal__nav">
        <button type="button" aria-label="Previous month" onClick={() => onMonthChange(shiftMonth(month, -1))}>‹</button>
        <h3 className="journal-cal__month">{month}</h3>
        <button type="button" aria-label="Next month" onClick={() => onMonthChange(shiftMonth(month, 1))}>›</button>
      </div>
      <div className="journal-cal__summary">
        <span>Realized P&amp;L (USD) · by close date</span>
        <strong className="tnum" style={{ color: days.length ? pnlColor(total) : "var(--ink-faint)" }}>
          {days.length ? compactPnl(total) : "—"}
        </strong>
      </div>
      <div className="journal-cal__sub">{trades} closed trade{trades === 1 ? "" : "s"} on {days.length} day{days.length === 1 ? "" : "s"}</div>
      <div className="journal-cal__grid" role="grid" aria-label="Daily realized P&L">
        <div className="journal-cal__row journal-cal__row--head" role="row">
          {WEEKDAYS.map((d, i) => <span key={i} role="columnheader">{d}</span>)}
        </div>
        {weeks.map((week, wi) => (
          <div className="journal-cal__row" role="row" key={wi}>
            {week.map((c, ci) => c.day === null ? <span key={ci} className="journal-cal__blank" role="gridcell" /> : (
              <span
                key={ci}
                role="gridcell"
                className={`journal-cal__cell${c.pnl === null ? "" : c.pnl > 0 ? " journal-cal__cell--up" : c.pnl < 0 ? " journal-cal__cell--down" : " journal-cal__cell--flat"}`}
                aria-label={c.pnl === null ? `${c.iso}: no closed trades` : `${c.iso}: ${compactPnl(c.pnl)} from ${c.trades} closed trade${c.trades === 1 ? "" : "s"}`}
                title={c.pnl === null ? undefined : `${c.trades} closed trade${c.trades === 1 ? "" : "s"}`}
              >
                <b>{c.day}</b>
                {c.pnl !== null && <small className="tnum" style={{ color: pnlColor(c.pnl) }}>{compactPnl(c.pnl)}</small>}
              </span>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
