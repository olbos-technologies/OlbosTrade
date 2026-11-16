/**
 * Draws the selected indicators on a lightweight-charts chart: lines on the price pane for the
 * overlays, and one pane each for the panels. Called when the chart is built, so a different
 * selection means a rebuilt chart (CandlestickChart does that), not series added and removed in place.
 *
 * With the default selection this creates exactly the series the chart always had, in the same panes
 * and stretch factors, so a chart that is not given a selection looks and behaves as before.
 */
import { HistogramSeries, LineSeries, type IChartApi, type IPaneApi, type Time } from "lightweight-charts";
import type { ChartBar } from "../CandlestickChart";
import {
  atrValues, bollingerValues, COLOR, computeMacd, emaValues, INDICATOR_BY_ID, rsiValues, smaValues, toLineData,
  toVolumeData, vwapValues, type IndicatorDef, type IndicatorId,
} from "./indicators";

export interface AttachedIndicators {
  /** Give every drawn indicator the bars to compute from. */
  update(bars: ChartBar[]): void;
}

const QUIET = { priceLineVisible: false, lastValueVisible: false } as const;
/** Heavier lines for the slow averages, as the 20-bar average always was. */
const THICK: ReadonlySet<string> = new Set(["ma20", "ma50", "ma200", "ema50"]);
const DASHED = 2;

export function attachIndicators(chart: IChartApi, pricePane: IPaneApi<Time>, selection: readonly IndicatorId[]): AttachedIndicators {
  const updaters: Array<(bars: ChartBar[]) => void> = [];
  const defs = selection.map((id) => INDICATOR_BY_ID.get(id)).filter((d): d is IndicatorDef => !!d);

  for (const def of defs.filter((d) => d.group === "overlay")) {
    const line = (color: string, extra: { lineStyle?: number; lineWidth?: 1 | 2 } = {}) =>
      pricePane.addSeries(LineSeries, { color, lineWidth: extra.lineWidth ?? (THICK.has(def.id) ? 2 : 1), lineStyle: extra.lineStyle, ...QUIET });
    switch (def.kind) {
      case "sma": { const s = line(def.color); updaters.push((b) => s.setData(toLineData(b, smaValues(b.map((x) => x.close), def.period!)))); break; }
      case "ema": { const s = line(def.color); updaters.push((b) => s.setData(toLineData(b, emaValues(b.map((x) => x.close), def.period!)))); break; }
      case "vwap": { const s = line(def.color, { lineStyle: DASHED }); updaters.push((b) => s.setData(toLineData(b, vwapValues(b)))); break; }
      case "bb": {
        const upper = line(def.color, { lineStyle: DASHED });
        const mid = line(def.color);
        const lower = line(def.color, { lineStyle: DASHED });
        updaters.push((b) => {
          const bands = bollingerValues(b.map((x) => x.close));
          upper.setData(toLineData(b, bands.upper));
          mid.setData(toLineData(b, bands.mid));
          lower.setData(toLineData(b, bands.lower));
        });
        break;
      }
    }
  }

  for (const def of defs.filter((d) => d.group === "panel")) {
    const pane = chart.addPane(true);
    switch (def.kind) {
      case "volume": {
        pane.setStretchFactor(0.85);
        const s = pane.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, ...QUIET });
        updaters.push((b) => s.setData(toVolumeData(b)));
        break;
      }
      case "macd": {
        pane.setStretchFactor(1.1);
        const hist = pane.addSeries(HistogramSeries, QUIET);
        const line = pane.addSeries(LineSeries, { color: COLOR.macd, lineWidth: 1, ...QUIET });
        const signal = pane.addSeries(LineSeries, { color: COLOR.macdSignal, lineWidth: 1, ...QUIET });
        updaters.push((b) => {
          const m = computeMacd(b);
          line.setData(m.macd);
          signal.setData(m.signal);
          hist.setData(m.histogram);
        });
        break;
      }
      case "rsi": {
        pane.setStretchFactor(0.9);
        // Always 0 to 100, so the guides at 30 and 70 sit where they always do whatever the data does.
        const s = pane.addSeries(LineSeries, {
          color: def.color, lineWidth: 1, ...QUIET,
          priceFormat: { type: "price", precision: 1, minMove: 0.1 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        });
        for (const price of [30, 70]) {
          s.createPriceLine({ price, color: "rgba(148,163,184,0.5)", lineWidth: 1, lineStyle: DASHED, axisLabelVisible: false, title: "" });
        }
        updaters.push((b) => s.setData(toLineData(b, rsiValues(b.map((x) => x.close)))));
        break;
      }
      case "atr": {
        pane.setStretchFactor(0.8);
        const s = pane.addSeries(LineSeries, { color: def.color, lineWidth: 1, ...QUIET, priceFormat: { type: "price", precision: 2, minMove: 0.01 } });
        updaters.push((b) => s.setData(toLineData(b, atrValues(b.map((x) => x.high), b.map((x) => x.low), b.map((x) => x.close)))));
        break;
      }
    }
  }

  return { update: (bars) => updaters.forEach((fn) => fn(bars)) };
}
