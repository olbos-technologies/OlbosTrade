/**
 * The expanded signal "card": what the idea is in one glance, then the numbers in a small table.
 *
 *   kicker          small caps line: BUY IDEA · EQUITY SIGNAL
 *   symbol, sub     the big name (ticker, or ticker and strikes) and one line under it
 *   headline        ONE number, with what it is called and what it is not (never a probability)
 *   stats           the table on the right: label, value, optional tone
 *
 * Presentation only. It shows what the caller gives it and never fills a gap: a missing number is a
 * dash, an unavailable headline says so. Colour is a tone on a value (loss, profit, caution as the
 * rest of the app uses them) and never the only cue: every row has its label in words.
 */

import React from "react";
import "./SignalHero.css";

export interface HeroStat {
  label: string;
  /** Already formatted. Undefined, null or "" renders a dash. */
  value?: string | number | null;
  /** A CSS colour for the value (var(--red) for a stop, for example). Omitted = normal text. */
  tone?: string;
}

export interface SignalHeroProps {
  kicker: string;
  symbol: React.ReactNode;
  subtitle?: React.ReactNode;
  headline: { label: string; value: string; note?: string };
  stats: readonly HeroStat[];
  /** Words for when there are no stats to show (no trade plan, for example). */
  emptyStats?: string;
  /** Extra class on the stats table, for tests and layout hooks that already name it. */
  statsClassName?: string;
  /** Shown under the headline: when it was generated, what it is not. */
  footnote?: React.ReactNode;
  /** The one action, such as "Review idea". */
  children?: React.ReactNode;
}

const dash = "—";
const show = (v: HeroStat["value"]) => (v === undefined || v === null || v === "" ? dash : String(v));

export default function SignalHero({
  kicker, symbol, subtitle, headline, stats, emptyStats, statsClassName, footnote, children,
}: SignalHeroProps) {
  return (
    <section className="signal-hero" aria-label={`${typeof symbol === "string" ? symbol : "Signal"} signal`}>
      <div className="signal-hero__main">
        <small className="signal-hero__kicker">{kicker}</small>
        <h3 className="signal-hero__symbol">{symbol}</h3>
        {subtitle && <p className="signal-hero__sub">{subtitle}</p>}
        <div className="signal-hero__headline">
          <small>{headline.label}</small>
          <strong className="tnum">{headline.value}</strong>
          {headline.note && <span>{headline.note}</span>}
        </div>
        {footnote && <p className="signal-hero__foot">{footnote}</p>}
      </div>
      {stats.length > 0 ? (
        <dl className={`signal-hero__stats${statsClassName ? ` ${statsClassName}` : ""}`}>
          {stats.map((s) => (
            <div key={s.label} className="signal-hero__row">
              <dt>{s.label}</dt>
              <dd className="tnum financial-value" style={s.tone ? { color: s.tone } : undefined}>{show(s.value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="signal-hero__empty" role="status">{emptyStats ?? "No details for this idea."}</p>
      )}
      {children && <div className="signal-hero__action">{children}</div>}
    </section>
  );
}
