/**
 * The workspace chart: a slim control strip over a candlestick chart that fills
 * whatever space the panel is given. The size is measured from the panel, not
 * guessed, so dragging a splitter resizes the chart live.
 *
 * Bars come from the same endpoint the Chart page uses. A failed or empty
 * response says so; it never draws a made-up series.
 */

import React, { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import CandlestickChart, { type ChartBar } from "../components/CandlestickChart";
import IntervalStrip from "../components/chart/IntervalStrip";
import RangeBar from "../components/chart/RangeBar";
import {
  DEFAULT_INTERVAL, DEFAULT_RANGE, INTERVAL_OPTIONS, clampNote,
  type IntervalKey, type RangeKey,
} from "../components/chart/timeframes";
import { pnlColor } from "../utils/pnlTone";

const MIN_CHART_H = 160;

const px = (n: number | null | undefined) => (typeof n === "number" && Number.isFinite(n) ? n.toFixed(2) : "—");
const pct = (n: number | null | undefined) =>
  typeof n === "number" && Number.isFinite(n) ? `${n < 0 ? "−" : n > 0 ? "+" : ""}${Math.abs(n).toFixed(2)}%` : "—";

export default function ChartPanel({ symbol }: { symbol: string }) {
  const [interval, setIntervalKey] = useState<IntervalKey>(DEFAULT_INTERVAL);
  const [range, setRange] = useState<RangeKey>(DEFAULT_RANGE);
  const [bars, setBars] = useState<ChartBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [quote, setQuote] = useState<{ last: number | null; chg: number | null } | null>(null);

  // Measure the chart area so the chart fills it.
  const wrap = useRef<HTMLDivElement>(null);
  const [h, setH] = useState(0);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const read = () => setH(Math.max(MIN_CHART_H, Math.floor(el.clientHeight)));
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    let live = true; // a slower, older answer must not overwrite the newest pick
    setLoading(true);
    setError(null);
    (api.getEquityChart(symbol, { timeframe: interval, range, limit: 5000 }) as Promise<any>)
      .then((res) => {
        if (!live) return;
        setBars(res.bars || []);
        const title = INTERVAL_OPTIONS.find((o) => o.key === interval)?.title ?? String(interval);
        setNote(res.range_clamped ? clampNote(title, res.period) : null);
      })
      .catch((e: Error) => { if (live) { setBars([]); setNote(null); setError(e.message || "Chart data unavailable"); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [symbol, interval, range]);

  useEffect(() => {
    let live = true;
    setQuote(null);
    (api.getSnapshot(symbol) as Promise<{ last_close: number | null; change_pct: number | null }>)
      .then((s) => { if (live) setQuote({ last: s.last_close, chg: s.change_pct }); })
      .catch(() => { if (live) setQuote({ last: null, chg: null }); });
    return () => { live = false; };
  }, [symbol]);

  const last = bars.length ? bars[bars.length - 1] : null;

  return (
    <div className="ws-chart">
      <div className="ws-chart__bar">
        <span className="ws-chart__symbol">{symbol}</span>
        <span className="ws-chart__quote" aria-label="Last close snapshot">
          {px(quote?.last)}{" "}
          <span style={{ color: quote?.chg == null ? "var(--ink-faint)" : pnlColor(quote.chg) }}>{pct(quote?.chg)}</span>
        </span>
        {last && (
          <span className="ws-chart__ohlc" aria-label="Latest bar">
            O {px(last.open)} H {px(last.high)} L {px(last.low)} C {px(last.close)}
          </span>
        )}
        <IntervalStrip value={interval} onChange={setIntervalKey} />
      </div>
      <div className="ws-chart__canvas" ref={wrap}>
        {loading ? (
          <div className="skeleton-block" style={{ height: h }} aria-busy="true" aria-label="Loading chart">
            <div className="skeleton-shimmer" />
          </div>
        ) : error ? (
          <p className="ws-note ws-note--warn" role="status">Chart data unavailable for {symbol}. {error}</p>
        ) : bars.length === 0 ? (
          <p className="ws-note" role="status">No bars returned for {symbol} in this range.</p>
        ) : (
          <CandlestickChart bars={bars} levels={[]} height={h} />
        )}
      </div>
      <RangeBar value={range} onChange={setRange} note={note} />
    </div>
  );
}
