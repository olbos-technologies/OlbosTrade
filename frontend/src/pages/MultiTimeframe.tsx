/**
 * Multi-timeframe: one symbol on two to four charts, each at its own bar interval (for example Weekly,
 * Daily, 1h and 15m), so the big trend and the entry chart can be read side by side.
 *
 * Each chart loads its own bars from the same source as the Chart page. They are NOT synchronised in
 * time or zoom (different intervals do not share a time axis). A failed chart says so for itself and
 * never draws made-up bars; the others carry on.
 *
 * The line under the controls says, in words, where price sits against its 20-bar and 50-bar averages
 * on each timeframe. That is descriptive: it reports what has already happened and is not a signal.
 *
 * Presentation only: it never changes the execution mode, risk limits or account, and places no orders.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import CandlestickChart from "../components/CandlestickChart";
import IndicatorPicker from "../components/chart/IndicatorPicker";
import { useIndicatorSelection } from "../components/chart/useIndicatorSelection";
import { useChartBarsMany, type ChartBarsState } from "../components/chart/useChartBarsMany";
import { alignment, trendState, type Side, type TrendState } from "../components/chart/trendState";
import {
  MAX_TILES, MIN_TILES, MTF_INDICATORS_KEY, RANGE_FOR_INTERVAL, readLayout, resizeLayout, writeLayout, type MtfLayout,
} from "../components/chart/multiTimeframePrefs";
import { INTERVAL_OPTIONS, RANGE_OPTIONS, type IntervalKey } from "../components/chart/timeframes";
import { readWatchlist } from "../workspace/useWatchlist";
import { normalizeSymbol } from "../workspace/layout";
import type { IndicatorId } from "../components/chart/indicators";
import "./MultiTimeframe.css";

/** The tiles are small, so they start lighter than the main chart: two averages and volume. */
const TILE_DEFAULT_INDICATORS: readonly IndicatorId[] = ["ma20", "ma50", "volume"];
const MIN_TILE_H = 200;

function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

function initialSymbol(): string {
  try {
    const q = new URLSearchParams(window.location.search).get("symbol");
    return (q && normalizeSymbol(q)) || "AAPL";
  } catch { return "AAPL"; }
}

const GLYPH: Record<Side, string> = { above: "▲", below: "▼", at: "＝" };
const WORD: Record<Side, string> = { above: "above", below: "below", at: "at" };

function Badge({ label, side }: { label: string; side: Side | null }) {
  const text = side ? `${label} ${GLYPH[side]}` : `${label} —`;
  const full = side ? `Price is ${WORD[side]} its ${label.replace("MA", "")}-bar average` : `${label}: not enough bars loaded`;
  return <span className="mtf-badge" title={full} aria-label={full}>{text}</span>;
}

function intervalTitle(k: IntervalKey) { return INTERVAL_OPTIONS.find((o) => o.key === k)?.title ?? k; }
function rangeLabel(k: IntervalKey) { const r = RANGE_FOR_INTERVAL[k]; return RANGE_OPTIONS.find((o) => o.key === r)?.label ?? r; }

function Tile({ index, interval, data, state, indicators, onInterval }: {
  index: number; interval: IntervalKey; data: ChartBarsState; state: TrendState;
  indicators: readonly IndicatorId[]; onInterval: (k: IntervalKey) => void;
}) {
  // Measure the body so the chart fills the tile, whatever the grid gives it.
  const body = useRef<HTMLDivElement>(null);
  const [h, setH] = useState(0);
  useEffect(() => {
    const el = body.current;
    if (!el) return;
    const read = () => setH(Math.max(MIN_TILE_H, Math.floor(el.clientHeight)));
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <section className="mtf-tile" aria-label={`Chart ${index + 1}: ${intervalTitle(interval)}`}>
      <header className="mtf-tile__head">
        <label className="mtf-tile__pick">
          <span className="mtf-sr">Bar interval for chart {index + 1}</span>
          <select value={interval} onChange={(e) => onInterval(e.target.value as IntervalKey)}>
            {INTERVAL_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.title}</option>)}
          </select>
        </label>
        <span className="mtf-tile__range" title="How much history this chart asks for">{rangeLabel(interval)} of {interval === "1M" ? "monthly" : interval} bars</span>
        <span className="mtf-tile__badges">
          <Badge label="MA20" side={data.loading || data.error ? null : state.vsMa20} />
          <Badge label="MA50" side={data.loading || data.error ? null : state.vsMa50} />
        </span>
      </header>
      <div className="mtf-tile__body" ref={body}>
        {data.loading ? (
          <div className="skeleton-block" style={{ height: h || MIN_TILE_H }} aria-busy="true" aria-label={`Loading chart ${index + 1}`}>
            <div className="skeleton-shimmer" />
          </div>
        ) : data.error ? (
          <p className="mtf-note mtf-note--warn" role="status">Chart data unavailable for this timeframe. {data.error}</p>
        ) : data.bars.length === 0 ? (
          <p className="mtf-note" role="status">No bars returned for this timeframe.</p>
        ) : (
          <CandlestickChart bars={data.bars} levels={[]} height={h || MIN_TILE_H} indicators={indicators} />
        )}
      </div>
      {data.note && <p className="mtf-tile__foot" role="status">{data.note}</p>}
    </section>
  );
}

export default function MultiTimeframe() {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [draft, setDraft] = useState(symbol);
  const [layout, setLayout] = useState<MtfLayout>(() => readLayout(browserStorage()));
  const [indicators, setIndicators, resetIndicators] = useIndicatorSelection({ key: MTF_INDICATORS_KEY, fallback: TILE_DEFAULT_INDICATORS });
  const watchlist = useMemo(() => readWatchlist(browserStorage()).slice(0, 8), []);
  const [bad, setBad] = useState(false);

  const specs = useMemo(
    () => layout.intervals.map((interval) => ({ symbol, interval, range: RANGE_FOR_INTERVAL[interval] })),
    [symbol, layout],
  );
  const data = useChartBarsMany(specs);
  const states = useMemo(() => data.map((d) => (d.loading || d.error ? null : trendState(d.bars))), [data]);
  const align = alignment(states, layout.intervals.length);
  const settled = data.every((d) => !d.loading);

  const updateLayout = (next: MtfLayout) => { setLayout(next); writeLayout(browserStorage(), next); };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const s = normalizeSymbol(draft);
    if (!s) { setBad(true); return; }
    setBad(false); setSymbol(s); setDraft(s);
  };
  const choose = (s: string) => { setBad(false); setSymbol(s); setDraft(s); };

  return (
    <div className="mtf" aria-label="Multi-timeframe view">
      <div className="mtf__bar">
        <h2 className="mtf__title">Multi-timeframe</h2>
        <form className="mtf__symbol" onSubmit={submit}>
          <label>
            <span className="mtf-sr">Symbol</span>
            <input value={draft} onChange={(e) => setDraft(e.target.value)} aria-invalid={bad} placeholder="Symbol" maxLength={20} spellCheck={false} />
          </label>
          <button type="submit">Go</button>
          {bad && <span role="alert" className="mtf__bad">Not a valid symbol.</span>}
        </form>
        <div className="mtf__chips" role="group" aria-label="Watchlist symbols">
          {watchlist.map((s) => (
            <button key={s} type="button" aria-pressed={s === symbol} onClick={() => choose(s)}>{s}</button>
          ))}
        </div>
        <label className="mtf__count">
          <span className="mtf-sr">Number of charts</span>
          <select value={layout.intervals.length} onChange={(e) => updateLayout(resizeLayout(layout, Number(e.target.value)))}>
            {Array.from({ length: MAX_TILES - MIN_TILES + 1 }, (_, i) => MIN_TILES + i).map((n) => <option key={n} value={n}>{n} charts</option>)}
          </select>
        </label>
        <IndicatorPicker selected={indicators} onChange={setIndicators} onReset={resetIndicators} buttonClassName="mtf__btn" />
      </div>

      <p className="mtf__align" role="status" aria-live="polite">
        <strong>{symbol}</strong>{" "}
        {settled ? align.summary : "Loading the timeframes…"}
      </p>

      <div className="mtf__grid" style={{ "--mtf-cols": layout.intervals.length === 3 ? 3 : 2 } as React.CSSProperties}>
        {layout.intervals.map((interval, i) => (
          <Tile
            key={i}
            index={i}
            interval={interval}
            data={data[i]}
            state={states[i] ?? trendState([])}
            indicators={indicators}
            onInterval={(k) => updateLayout({ intervals: layout.intervals.map((x, j) => (j === i ? k : x)) })}
          />
        ))}
      </div>

      <p className="mtf__note">
        Each chart loads its own bars and they are not synchronised in time or zoom. The arrows say where price sits against
        its 20-bar and 50-bar averages. That describes what has happened; it is not a signal.
      </p>
    </div>
  );
}
