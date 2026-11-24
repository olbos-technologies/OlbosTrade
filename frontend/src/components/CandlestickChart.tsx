/**
 * CandlestickChart — TradingView lightweight-charts wrapper.
 *
 * Multi-pane layout inspired by common mobile desks (price + volume + MACD)
 * while keeping Olbos navy terminal colors — not a retail blue rebrand.
 * Live MA/MACD readouts sit above the canvas so the key numbers stay visible
 * without covering candles.
 */
import React, { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  type CandlestickData,
  type HistogramData,
  type LineData,
} from "lightweight-charts";
import { useIsMobile } from "../hooks/useIsMobile";
import { VolumeProfilePrimitive } from "./chart/VolumeProfilePrimitive";
import { computeVolumeProfile, summarizeProfile } from "./chart/volumeProfile";
import { attachIndicators, type AttachedIndicators } from "./chart/indicatorSeries";
import {
  computeMacd, DEFAULT_INDICATORS, emaSeries, legendFor, normaliseSelection, readoutFor, selectionKey, toUnixSeconds,
  toVolumeData, type IndicatorId,
} from "./chart/indicators";
import "./CandlestickChart.css";

// These live in chart/indicators.ts now; re-exported so everything that imports them from here is unchanged.
export { computeMacd, emaSeries, toUnixSeconds };

export interface ChartBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/**
 * What a caller gets to draw on the chart. `onDispose` registers cleanup that runs
 * BEFORE the chart is removed, so a primitive is detached from a chart that still
 * exists (detaching afterwards makes the library log "Object is disposed").
 */
export interface ChartHandle {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  container: HTMLElement;
  onDispose: (fn: () => void) => () => void;
}

export interface ChartLevel {
  label: string;
  value: number;
  color: string;
}

const COLOR = {
  bg: "#0b1120",
  gridLine: "rgba(255,255,255,0.06)",
  text: "rgba(148,163,184,0.8)",
  upCandle: "#18c37e",
  downCandle: "#ff5f6d",
  upVolume: "rgba(24,195,126,0.45)",
  downVolume: "rgba(255,95,109,0.45)",
  ma5: "#5b9fd4",
  ma10: "#f4a261",
  ma20: "#f4c64f",
  vwap: "#8e7cfb",
  macd: "#5b9fd4",
  macdSignal: "#f4a261",
  macdHistUp: "rgba(24,195,126,0.55)",
  macdHistDown: "rgba(255,95,109,0.55)",
};

/** Short axis titles so level labels do not cover candles on a phone. */
export function axisTitleForLevel(label: string, compact: boolean): string {
  if (!compact) return label;
  const key = label.trim().toUpperCase();
  if (key.startsWith("RES")) return "R";
  if (key.startsWith("SUP")) return "S";
  if (key.startsWith("ENT")) return "E";
  if (/^(PD[HLC]|~?OR[HL])$/.test(key)) return key;   // day levels: one letter would make PDH, PDL and PDC read alike
  return key.slice(0, 1) || label;
}

export function barsToCandlestickData(bars: ChartBar[]): CandlestickData[] {
  return bars.map((b) => ({
    time: toUnixSeconds(b.timestamp),
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }));
}

/** Volume bars, coloured by the bar's direction. */
export const barsToVolumeData = toVolumeData;

export function movingAverage(bars: ChartBar[], period: number): (number | null)[] {
  return bars.map((_, idx) => {
    if (idx < period - 1) return null;
    const slice = bars.slice(idx - period + 1, idx + 1);
    return slice.reduce((sum, item) => sum + item.close, 0) / period;
  });
}

function rollingVwap(bars: ChartBar[]): number[] {
  let cumulativePV = 0;
  let cumulativeVolume = 0;
  return bars.map((bar) => {
    const typical = (bar.high + bar.low + bar.close) / 3;
    cumulativePV += typical * Math.max(bar.volume, 1);
    cumulativeVolume += Math.max(bar.volume, 1);
    return cumulativePV / cumulativeVolume;
  });
}

export function barsToSmaData(bars: ChartBar[], period: number): LineData[] {
  const out: LineData[] = [];
  movingAverage(bars, period).forEach((value, idx) => {
    if (value != null) out.push({ time: toUnixSeconds(bars[idx].timestamp), value });
  });
  return out;
}

export function barsToVwapData(bars: ChartBar[]): LineData[] {
  return rollingVwap(bars).map((value, idx) => ({ time: toUnixSeconds(bars[idx].timestamp), value }));
}

export function lastIndicatorReadout(bars: ChartBar[]): {
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  vwap: number | null;
  macd: number | null;
  signal: number | null;
  hist: number | null;
} {
  if (!bars.length) {
    return { ma5: null, ma10: null, ma20: null, vwap: null, macd: null, signal: null, hist: null };
  }
  const ma = (period: number) => {
    const series = movingAverage(bars, period);
    return series[series.length - 1];
  };
  const vwapSeries = rollingVwap(bars);
  const { macd, signal, histogram } = computeMacd(bars);
  return {
    ma5: ma(5),
    ma10: ma(10),
    ma20: ma(20),
    vwap: vwapSeries[vwapSeries.length - 1] ?? null,
    macd: macd.length ? macd[macd.length - 1].value : null,
    signal: signal.length ? signal[signal.length - 1].value : null,
    hist: histogram.length ? histogram[histogram.length - 1].value : null,
  };
}

const LEGEND_KEY = "olbos.chart.legend";
function readLegendPreference(): boolean {
  try { return window.localStorage.getItem(LEGEND_KEY) === "open"; } catch { return false; }
}
function writeLegendPreference(open: boolean): void {
  try { window.localStorage.setItem(LEGEND_KEY, open ? "open" : "closed"); } catch { /* storage unavailable: fine */ }
}

export default function CandlestickChart({
  bars,
  levels,
  height = 460,
  volumeProfile = false,
  onProfileSummary,
  onReady,
  indicators,
}: {
  bars: ChartBar[];
  levels: ChartLevel[];
  height?: number;
  /**
   * Which indicators to draw. Left out, the chart draws exactly what it always has (MA5, MA10, MA20,
   * VWAP, volume, MACD), so a chart that is not given a selection is unchanged. A different selection
   * rebuilds the chart (the caller re-attaches through `onReady`); the zoom position is kept.
   */
  indicators?: readonly IndicatorId[];
  /**
   * Draw a volume profile for the bars currently in view, at the right edge of the
   * price pane. Off by default, so a chart that does not ask for it is unchanged.
   */
  volumeProfile?: boolean;
  /**
   * Where to show the profile's summary text (bars in view, point of control,
   * value area). When given, the chart reports the text here and does NOT add a
   * line to its own readout, so the caller can place it without the chart
   * changing size when the profile is switched on or off.
   */
  onProfileSummary?: (summary: string | null) => void;
  /**
   * Called with a handle once the chart exists, and with null when it is about to be
   * removed. The chart is rebuilt whenever it is re-mounted (for example while new
   * bars load), so a caller must re-attach each time it receives a handle.
   */
  onReady?: (handle: ChartHandle | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const attachedRef = useRef<AttachedIndicators | null>(null);
  // The visible range just before a rebuild for a new selection, so the zoom survives the change.
  const savedRangeRef = useRef<{ from: number; to: number; bars: number } | null>(null);
  const barsLenRef = useRef(0);
  const selection = indicators ? normaliseSelection(indicators) : (DEFAULT_INDICATORS as IndicatorId[]);
  const selKey = selectionKey(selection);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  const isMobile = useIsMobile();
  // The readout text for the volume profile (bars in view, point of control, value area).
  const [profileSummary, setProfileSummary] = useState<string | null>(null);
  const onSummaryRef = useRef(onProfileSummary);
  onSummaryRef.current = onProfileSummary;
  // The profile's teardown, so the chart's own cleanup can run it BEFORE the chart
  // is removed. React cleans effects up in the order they were declared, and the
  // chart is created (and removed) by the first one, so without this the primitive
  // would be detached from a chart that no longer exists.
  const profileTeardownRef = useRef<(() => void) | null>(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const disposersRef = useRef(new Set<() => void>());
  // Collapsed by default on every screen size: the key is 13 rows tall and sat
  // over the first part of the price line. The indicator readout above stays
  // visible, and the chart's own right-axis chips name entry / stop / targets.
  // The choice is remembered per viewer; storage can be unavailable, so it is
  // read and written defensively and the chart works without it.
  const [legendOpen, setLegendOpen] = useState<boolean>(readLegendPreference);
  const toggleLegend = () =>
    setLegendOpen((open) => {
      writeLegendPreference(!open);
      return !open;
    });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: { background: { color: COLOR.bg }, textColor: COLOR.text, fontFamily: "var(--mono)", fontSize: 10 },
      grid: {
        vertLines: { color: COLOR.gridLine, style: 2 },
        horzLines: { color: COLOR.gridLine, style: 2 },
      },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
      timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    });

    const panes = chart.panes();
    const pricePane = panes[0];
    pricePane.setStretchFactor(3);

    const candleSeries = pricePane.addSeries(CandlestickSeries, {
      upColor: "rgba(24,195,126,0.9)",
      downColor: "rgba(255,95,109,0.9)",
      borderUpColor: COLOR.upCandle,
      borderDownColor: COLOR.downCandle,
      wickUpColor: COLOR.upCandle,
      wickDownColor: COLOR.downCandle,
    });

    const attached = attachIndicators(chart, pricePane, selectionRef.current);

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    const disposers = disposersRef.current;
    onReadyRef.current?.({
      chart,
      series: candleSeries,
      container,
      onDispose: (fn) => { disposers.add(fn); return () => { disposers.delete(fn); }; },
    });
    attachedRef.current = attached;

    return () => {
      // Remember where the chart was scrolled and zoomed, in case this is a rebuild for a new selection.
      try {
        const r = chart.timeScale().getVisibleLogicalRange?.();
        savedRangeRef.current = r ? { from: r.from, to: r.to, bars: barsLenRef.current } : null;
      } catch { savedRangeRef.current = null; }
      profileTeardownRef.current?.();
      // Anything a caller attached (drawings) lets go while the chart still exists.
      disposers.forEach((fn) => { try { fn(); } catch { /* keep tearing down */ } });
      disposers.clear();
      onReadyRef.current?.(null);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      attachedRef.current = null;
    };
    // A new selection is a new chart. Nothing else rebuilds it.
  }, [selKey]);

  useEffect(() => {
    if (!candleSeriesRef.current || !attachedRef.current) return;
    barsLenRef.current = bars.length;
    candleSeriesRef.current.setData(barsToCandlestickData(bars));
    attachedRef.current.update(bars);
    // After a rebuild for a new selection, put the chart back where it was; otherwise fit everything.
    const saved = savedRangeRef.current;
    savedRangeRef.current = null;
    if (saved && saved.bars === bars.length) chartRef.current?.timeScale().setVisibleLogicalRange({ from: saved.from, to: saved.to });
    else chartRef.current?.timeScale().fitContent();
  }, [bars, selKey]);

  // Volume profile of the bars in view. It is recomputed when the visible range
  // changes (zoom, scroll) at most once per frame, and drawn by a primitive on the
  // candle series so it shares the candles' own price scale.
  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!volumeProfile || !chart || !series || !bars.length) {
      setProfileSummary(null);
      onSummaryRef.current?.(null);
      return;
    }
    const primitive = new VolumeProfilePrimitive();
    series.attachPrimitive(primitive);
    let frame = 0;
    const recompute = () => {
      frame = 0;
      const range = chart.timeScale().getVisibleLogicalRange();
      const from = range ? Math.max(0, Math.ceil(range.from)) : 0;
      const to = range ? Math.min(bars.length - 1, Math.floor(range.to)) : bars.length - 1;
      const profile = computeVolumeProfile(bars, { from, to });
      primitive.setProfile(profile);
      const text = summarizeProfile(profile);
      setProfileSummary(text);
      onSummaryRef.current?.(text);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(recompute); };
    chart.timeScale().subscribeVisibleLogicalRangeChange(schedule);
    recompute();
    const teardown = () => {
      if (frame) { cancelAnimationFrame(frame); frame = 0; }
      try { chart.timeScale().unsubscribeVisibleLogicalRangeChange(schedule); } catch { /* chart already disposed */ }
      try { series.detachPrimitive(primitive); } catch { /* chart already disposed */ }
      profileTeardownRef.current = null;
    };
    profileTeardownRef.current = teardown;
    return () => {
      teardown();
      setProfileSummary(null);
      onSummaryRef.current?.(null);
    };
  }, [volumeProfile, bars, selKey]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    const priceLines = levels.map((level) =>
      series.createPriceLine({
        price: level.value,
        color: level.color,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: axisTitleForLevel(level.label, isMobile),
      }),
    );
    return () => { priceLines.forEach((line) => series.removePriceLine(line)); };
  }, [levels, isMobile, selKey]);

  if (!bars.length) {
    return (
      <div style={{
        height, display: "flex", alignItems: "center", justifyContent: "center",
        color: "var(--ink-faint)", fontFamily: "var(--mono)", fontSize: 11,
      }}>
        NO CHART DATA
      </div>
    );
  }

  const readout = readoutFor(bars, selection);
  const legendItems = [
    ...legendFor(selection),
    ...levels.map((level) => ({ label: level.label, color: level.color, style: "level" as const })),
  ];

  return (
    <div className={`candlestick-chart${isMobile ? " candlestick-chart--mobile" : ""}`} style={{ height }}>
      <div className="candlestick-chart__readout" aria-label="Indicator readout">
        {readout.map((item) => (
          <span key={item.text.split(" ")[0] + item.color} style={{ color: item.color }}>{item.text}</span>
        ))}
        {volumeProfile && profileSummary && !onProfileSummary && (
          <span
            className="candlestick-chart__profile"
            aria-label="Volume profile"
            title="Approximate: built from bars, with each bar's volume spread evenly across its high-low range. It shows where volume traded in the bars in view. It is not a signal."
          >
            Volume profile · {profileSummary}
          </span>
        )}
      </div>
      <div className="candlestick-chart__stage">
      <div className="candlestick-chart__legend-chrome">
        <button
          type="button"
          className="candlestick-chart__legend-toggle"
          aria-expanded={legendOpen}
          aria-controls="candlestick-chart-legend"
          onClick={toggleLegend}
        >
          {legendOpen ? "Hide legend" : "Legend"}
        </button>
        {legendOpen && (
          <div
            id="candlestick-chart-legend"
            className="candlestick-chart__legend"
            aria-label="Chart legend"
          >
            {legendItems.map((item) => (
              <div key={item.label} className="candlestick-chart__legend-row">
                <span
                  aria-hidden="true"
                  className={`candlestick-chart__swatch candlestick-chart__swatch--${item.style}`}
                  style={{
                    background: item.style === "dashed" ? "transparent" : item.color,
                    borderTop: item.style === "dashed" ? `2px dashed ${item.color}` : undefined,
                    boxShadow: item.style === "level" ? `0 0 0 1px ${item.color}` : undefined,
                  }}
                />
                <span>{item.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div ref={containerRef} className="candlestick-chart__canvas" aria-label="Price chart" />
      </div>
    </div>
  );
}
