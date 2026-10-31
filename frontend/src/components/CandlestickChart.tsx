/**
 * CandlestickChart — TradingView lightweight-charts wrapper.
 *
 * Replaces ChartWorkstation.tsx's hand-rolled SVG ChartCanvas (~220 lines)
 * with a real charting engine: proper crosshair, zoom/pan, and price-axis
 * rendering, while keeping the exact same terminal color palette (candle
 * colors, SMA/VWAP colors, dark background) so this is a charting-engine
 * upgrade, not a visual rebrand.
 */
import React, { useEffect, useRef } from "react";
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

// Same palette ChartCanvas used — do not change without updating both.
const COLOR = {
  bg: "#0b1120",
  gridLine: "rgba(255,255,255,0.06)",
  text: "rgba(148,163,184,0.8)",
  upCandle: "#18c37e",
  downCandle: "#ff5f6d",
  upVolume: "rgba(24,195,126,0.32)",
  downVolume: "rgba(255,95,109,0.32)",
  sma: "#f4c64f",
  vwap: "#8e7cfb",  // matches index.css --violet — lightweight-charts takes plain color strings, not var(), so keep these in sync by convention
};

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

function movingAverage(bars: ChartBar[], period: number): (number | null)[] {
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

function barsToSmaData(bars: ChartBar[], period = 20): LineData[] {
  const out: LineData[] = [];
  movingAverage(bars, period).forEach((value, idx) => {
    if (value != null) out.push({ time: toUnixSeconds(bars[idx].timestamp), value });
  });
  return out;
}

function barsToVwapData(bars: ChartBar[]): LineData[] {
  return rollingVwap(bars).map((value, idx) => ({ time: toUnixSeconds(bars[idx].timestamp), value }));
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
  const smaSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const vwapSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  // Chart + series lifecycle — created once per mount.
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

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "rgba(24,195,126,0.9)",
      downColor: "rgba(255,95,109,0.9)",
      borderUpColor: COLOR.upCandle,
      borderDownColor: COLOR.downCandle,
      wickUpColor: COLOR.upCandle,
      wickDownColor: COLOR.downCandle,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

    const smaSeries = chart.addSeries(LineSeries, {
      color: COLOR.sma, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
    });
    const vwapSeries = chart.addSeries(LineSeries, {
      color: COLOR.vwap, lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    smaSeriesRef.current = smaSeries;
    vwapSeriesRef.current = vwapSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      smaSeriesRef.current = null;
      vwapSeriesRef.current = null;
    };
  }, []);

  // Data updates — re-run whenever bars change (new symbol/timeframe).
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !smaSeriesRef.current || !vwapSeriesRef.current) return;
    candleSeriesRef.current.setData(barsToCandlestickData(bars));
    volumeSeriesRef.current.setData(barsToVolumeData(bars));
    smaSeriesRef.current.setData(barsToSmaData(bars));
    vwapSeriesRef.current.setData(barsToVwapData(bars));
    chartRef.current?.timeScale().fitContent();
  }, [bars]);

  // Support/resistance levels — cleared and redrawn on change.
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
        title: level.label,
      }),
    );
    return () => { priceLines.forEach((line) => series.removePriceLine(line)); };
  }, [levels]);

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

  return (
    <div className="candlestick-frame" style={{ position: "relative" }}>
      <details className="chart-legend-disclosure">
        <summary>Chart legend</summary>
      <div
        aria-label="Chart legend"
        style={{

          display: "flex", flexDirection: "column", gap: 4,
          padding: "8px 10px", background: "rgba(6,11,23,0.82)",
          border: "1px solid var(--line-dim)",
        }}
      >
        {[
          { label: "SMA-20", color: COLOR.sma, style: "solid" as const },
          { label: "VWAP", color: COLOR.vwap, style: "dashed" as const },
          { label: "Volume", color: "rgba(148,163,184,0.7)", style: "bar" as const },
          ...levels.map((level) => ({ label: level.label, color: level.color, style: "level" as const })),
        ].map((item) => (
          <div key={item.label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              aria-hidden="true"
              style={{
                width: 18,
                height: item.style === "bar" ? 8 : 2,
                background: item.style === "dashed" ? "transparent" : item.color,
                borderTop: item.style === "dashed" ? `2px dashed ${item.color}` : undefined,
                boxShadow: item.style === "level" ? `0 0 0 1px ${item.color}` : undefined,
              }}
            />
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", letterSpacing: "0.04em" }}>
              {item.label}
            </span>
          </div>
        ))}
      </div>
      </details>
      <div aria-label="Price chart" ref={containerRef} style={{ width: "100%", height }} />
    </div>
  );
}
