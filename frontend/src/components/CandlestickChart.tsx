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
import "./CandlestickChart.css";

export interface ChartBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
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
  return key.slice(0, 1) || label;
}

export function toUnixSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
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

export function barsToVolumeData(bars: ChartBar[]): HistogramData[] {
  return bars.map((b) => ({
    time: toUnixSeconds(b.timestamp),
    value: b.volume,
    color: b.close >= b.open ? COLOR.upVolume : COLOR.downVolume,
  }));
}

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

/** EMA seed = SMA of the first `period` closes, then standard EMA. */
export function emaSeries(closes: number[], period: number): (number | null)[] {
  const out: (number | null)[] = closes.map(() => null);
  if (closes.length < period) return out;
  const k = 2 / (period + 1);
  let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out[period - 1] = ema;
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k);
    out[i] = ema;
  }
  return out;
}

export function computeMacd(bars: ChartBar[], fast = 12, slow = 26, signal = 9): {
  macd: LineData[];
  signal: LineData[];
  histogram: HistogramData[];
} {
  const closes = bars.map((b) => b.close);
  const fastEma = emaSeries(closes, fast);
  const slowEma = emaSeries(closes, slow);
  const macdRaw: (number | null)[] = closes.map((_, i) =>
    fastEma[i] != null && slowEma[i] != null ? (fastEma[i] as number) - (slowEma[i] as number) : null,
  );
  const macdValues = macdRaw.map((v) => (v == null ? 0 : v));
  // Signal EMA only over defined MACD points — seed at first full MACD index.
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
      histogram.push({
        time: t,
        value: hist,
        color: hist >= 0 ? COLOR.macdHistUp : COLOR.macdHistDown,
      });
    }
  });
  return { macd, signal: signalLine, histogram };
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

const LEGEND_ITEMS_BASE = [
  { label: "MA5", color: COLOR.ma5, style: "solid" as const },
  { label: "MA10", color: COLOR.ma10, style: "solid" as const },
  { label: "MA20", color: COLOR.ma20, style: "solid" as const },
  { label: "VWAP", color: COLOR.vwap, style: "dashed" as const },
  { label: "Volume", color: "rgba(148,163,184,0.7)", style: "bar" as const },
  { label: "MACD", color: COLOR.macd, style: "solid" as const },
];

function fmtNum(n: number | null, digits = 2): string {
  return n == null || !Number.isFinite(n) ? "—" : n.toFixed(digits);
}

export default function CandlestickChart({
  bars,
  levels,
  height = 460,
}: {
  bars: ChartBar[];
  levels: ChartLevel[];
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ma5Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma10Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma20Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdLineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdSignalRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdHistRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const isMobile = useIsMobile();
  const [legendOpen, setLegendOpen] = useState(!isMobile);

  useEffect(() => {
    setLegendOpen(!isMobile);
  }, [isMobile]);

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

    const ma5 = pricePane.addSeries(LineSeries, {
      color: COLOR.ma5, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    const ma10 = pricePane.addSeries(LineSeries, {
      color: COLOR.ma10, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    const ma20 = pricePane.addSeries(LineSeries, {
      color: COLOR.ma20, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    });
    const vwapSeries = pricePane.addSeries(LineSeries, {
      color: COLOR.vwap, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
    });

    const volumePane = chart.addPane(true);
    volumePane.setStretchFactor(0.85);
    const volumeSeries = volumePane.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const macdPane = chart.addPane(true);
    macdPane.setStretchFactor(1.1);
    const macdHist = macdPane.addSeries(HistogramSeries, {
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const macdLine = macdPane.addSeries(LineSeries, {
      color: COLOR.macd, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });
    const macdSignal = macdPane.addSeries(LineSeries, {
      color: COLOR.macdSignal, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ma5Ref.current = ma5;
    ma10Ref.current = ma10;
    ma20Ref.current = ma20;
    vwapSeriesRef.current = vwapSeries;
    macdLineRef.current = macdLine;
    macdSignalRef.current = macdSignal;
    macdHistRef.current = macdHist;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      ma5Ref.current = null;
      ma10Ref.current = null;
      ma20Ref.current = null;
      vwapSeriesRef.current = null;
      macdLineRef.current = null;
      macdSignalRef.current = null;
      macdHistRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (
      !candleSeriesRef.current || !volumeSeriesRef.current ||
      !ma5Ref.current || !ma10Ref.current || !ma20Ref.current || !vwapSeriesRef.current ||
      !macdLineRef.current || !macdSignalRef.current || !macdHistRef.current
    ) return;
    candleSeriesRef.current.setData(barsToCandlestickData(bars));
    volumeSeriesRef.current.setData(barsToVolumeData(bars));
    ma5Ref.current.setData(barsToSmaData(bars, 5));
    ma10Ref.current.setData(barsToSmaData(bars, 10));
    ma20Ref.current.setData(barsToSmaData(bars, 20));
    vwapSeriesRef.current.setData(barsToVwapData(bars));
    const macd = computeMacd(bars);
    macdLineRef.current.setData(macd.macd);
    macdSignalRef.current.setData(macd.signal);
    macdHistRef.current.setData(macd.histogram);
    chartRef.current?.timeScale().fitContent();
  }, [bars]);

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
  }, [levels, isMobile]);

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

  const readout = lastIndicatorReadout(bars);
  const legendItems = [
    ...LEGEND_ITEMS_BASE,
    ...levels.map((level) => ({ label: level.label, color: level.color, style: "level" as const })),
  ];

  return (
    <div className={`candlestick-chart${isMobile ? " candlestick-chart--mobile" : ""}`} style={{ height }}>
      <div className="candlestick-chart__readout" aria-label="Indicator readout">
        <span style={{ color: COLOR.ma5 }}>MA5 {fmtNum(readout.ma5)}</span>
        <span style={{ color: COLOR.ma10 }}>MA10 {fmtNum(readout.ma10)}</span>
        <span style={{ color: COLOR.ma20 }}>MA20 {fmtNum(readout.ma20)}</span>
        <span style={{ color: COLOR.vwap }}>VWAP {fmtNum(readout.vwap)}</span>
        <span style={{ color: COLOR.macd }}>MACD {fmtNum(readout.macd, 3)}</span>
        <span style={{ color: COLOR.macdSignal }}>Sig {fmtNum(readout.signal, 3)}</span>
        <span style={{ color: (readout.hist ?? 0) >= 0 ? COLOR.upCandle : COLOR.downCandle }}>
          Hist {fmtNum(readout.hist, 3)}
        </span>
      </div>
      <div className="candlestick-chart__legend-chrome">
        {isMobile && (
          <button
            type="button"
            className="candlestick-chart__legend-toggle"
            aria-expanded={legendOpen}
            aria-controls="candlestick-chart-legend"
            onClick={() => setLegendOpen((open) => !open)}
          >
            {legendOpen ? "Hide legend" : "Legend"}
          </button>
        )}
        {(legendOpen || !isMobile) && (
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
  );
}
