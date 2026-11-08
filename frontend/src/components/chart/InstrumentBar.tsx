import React from "react";
import { Button } from "../ui";
import { pnlColor } from "../../utils/pnlTone";

type Bar = { open: number; high: number; low: number; close: number; volume: number } | null;
type TF = { key: string; label: string };

const px = (n: number | null | undefined) => (typeof n === "number" && Number.isFinite(n) ? n.toFixed(2) : "—");
const vol = (n: number | null | undefined) => {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(Math.round(n));
};

/**
 * One row that replaces the page heading, the symbol tabs, the KPI cards, the
 * snapshot card and the chart title: which instrument, its price and move, the
 * last bar's numbers, how fresh they are, and the chart controls.
 */
export default function InstrumentBar({
  symbols, symbol, onSelect, price, changePct, bar, freshness,
  timeframes, timeframe, onTimeframe, signalBadge, onSignalDetail, focused, onToggleFocus,
}: {
  symbols: string[];
  symbol: string;
  onSelect: (s: string) => void;
  price: number | null | undefined;
  changePct: number | null | undefined;
  bar: Bar;
  freshness: string;
  timeframes: TF[];
  timeframe: string;
  onTimeframe: (k: string) => void;
  signalBadge: React.ReactNode;
  onSignalDetail: () => void;
  focused: boolean;
  onToggleFocus: () => void;
}) {
  const move = typeof changePct === "number" && Number.isFinite(changePct)
    ? `${changePct < 0 ? "−" : changePct > 0 ? "+" : ""}${Math.abs(changePct).toFixed(2)}%` : "—";
  return (
    <header className="instrument-bar" aria-label="Selected instrument">
      <div className="instrument-bar__lead">
        <h1 className="instrument-bar__title">Market workspace</h1>
        <div className="chart-symbol-picker instrument-bar__symbols" aria-label="Chart instruments">
          {symbols.map((s) => (
            <button key={s} type="button" aria-pressed={symbol === s} onClick={() => onSelect(s)}>{s}</button>
          ))}
        </div>
      </div>
      <div className="instrument-bar__quote" aria-label="Selected instrument quote">
        <div className="instrument-bar__price">
          <strong className="tnum">{px(price)}</strong>
          <span className="tnum" style={{ color: typeof changePct === "number" ? pnlColor(changePct) : "var(--ink-faint)" }}>{move}</span>
        </div>
        <span className="instrument-bar__fresh">{freshness}</span>
      </div>
      <div className="instrument-bar__ohlc tnum" aria-label="Last bar">
        {bar ? <>O {px(bar.open)} &nbsp;H {px(bar.high)} &nbsp;L {px(bar.low)} &nbsp;C {px(bar.close)} &nbsp;Vol {vol(bar.volume)}</> : "No bar data"}
      </div>
      <div className="instrument-bar__signal">{signalBadge}</div>
      <div className="instrument-bar__controls">
        <div className="chart-timeframes" role="group" aria-label="Chart timeframe">
          {timeframes.map((t) => (
            <Button selector key={t.key} active={timeframe === t.key} onClick={() => onTimeframe(t.key)}>{t.label}</Button>
          ))}
        </div>
        <Button flat onClick={onSignalDetail}>Signal detail</Button>
        <Button flat aria-pressed={focused} onClick={onToggleFocus}>
          {focused ? "Show research panels" : "Focus chart"}
        </Button>
      </div>
    </header>
  );
}
