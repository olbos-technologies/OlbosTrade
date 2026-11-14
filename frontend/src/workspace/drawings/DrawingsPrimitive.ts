/**
 * Paints the drawings inside the chart's price pane and answers "where is this
 * time and price on screen" using the chart's OWN scales.
 *
 * It is a lightweight-charts series primitive attached to the candle series, so
 * the chart calls it on every redraw and the drawings stay on the same candle and
 * price through zoom, scroll and resize, with no overlay to keep in sync.
 *
 * The painting itself is a pure function (`paintShape`) that takes a canvas
 * context, so it can be checked without a real canvas.
 */

import type {
  IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, PrimitivePaneViewZOrder,
  SeriesAttachedParameter, SeriesType, Time, IChartApi,
} from "lightweight-charts";
import { layoutDrawing, type Plot, type Pt, type Shape } from "./geometry";
import { snapPoint, type SnapBar } from "./snap";
import { logicalToTime, timeToLogical } from "./time";
import type { Anchor, Drawing } from "./model";

/** The parts of a 2D canvas context painting uses, so a test can stand in for it. */
export type PaintContext = Pick<
  CanvasRenderingContext2D,
  "save" | "restore" | "beginPath" | "moveTo" | "lineTo" | "stroke" | "fill" | "fillRect" | "rect" | "clip" | "arc" |
  "fillText" | "setLineDash" | "fillStyle" | "strokeStyle" | "lineWidth" | "globalAlpha" | "font" | "textAlign" | "textBaseline" | "lineCap"
>;

export interface PaintStyle {
  selected: boolean;
  hover: boolean;
  /** The drawing still being placed: dashed and lighter. */
  preview: boolean;
}

const HANDLE_FILL = "#0b1120";
const LABEL_FONT = "11px ui-monospace, SFMono-Regular, Menlo, monospace";

export function paintShape(ctx: PaintContext, s: Shape, style: PaintStyle): void {
  ctx.save();
  ctx.lineCap = "round";
  ctx.strokeStyle = s.color;
  ctx.fillStyle = s.color;
  ctx.lineWidth = s.width + (style.hover && !style.selected ? 1 : 0);
  ctx.globalAlpha = style.preview ? 0.8 : 1;
  ctx.setLineDash(style.preview ? [6, 4] : []);

  if (s.rect) {
    ctx.globalAlpha = style.preview ? 0.1 : 0.14;
    ctx.fillRect(s.rect.x, s.rect.y, s.rect.w, s.rect.h);
    ctx.globalAlpha = style.preview ? 0.8 : 1;
    ctx.beginPath();
    ctx.rect(s.rect.x, s.rect.y, s.rect.w, s.rect.h);
    ctx.stroke();
  }

  for (const [a, b] of s.segments) {
    if (s.kind === "fib") ctx.setLineDash([3, 4]); // the connector between the two points
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }

  if (s.levels) {
    ctx.setLineDash([]);
    ctx.font = LABEL_FONT;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    for (const l of s.levels) {
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(l.x1, l.y);
      ctx.lineTo(l.x2, l.y);
      ctx.stroke();
      ctx.fillText(l.label, l.x1 + 4, l.y - 2);
    }
  }

  if (s.label) {
    ctx.font = LABEL_FONT;
    ctx.textAlign = "right";
    ctx.textBaseline = "bottom";
    ctx.fillText(s.label, s.segments[0][1].x - 6, s.segments[0][0].y - 3);
  }

  if (style.selected) {
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ctx.fillStyle = HANDLE_FILL;
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    for (const a of s.anchors) {
      ctx.beginPath();
      ctx.arc(a.x, a.y, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }
  ctx.restore();
}

export interface PaintState {
  drawings: readonly Drawing[];
  selectedId: string | null;
  hoverId: string | null;
  preview: Drawing | null;
}

/** Paint every drawing, clipped to the plot so nothing spills over the price or time axis. */
export function paintAll(
  ctx: PaintContext, state: PaintState, plot: Plot, layout: (d: Drawing) => Shape | null,
): void {
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, plot.w, plot.h);
  ctx.clip();
  for (const d of state.drawings) {
    const s = layout(d);
    if (s) paintShape(ctx, s, { selected: d.id === state.selectedId, hover: d.id === state.hoverId, preview: false });
  }
  if (state.preview) {
    const s = layout(state.preview);
    if (s) paintShape(ctx, s, { selected: false, hover: false, preview: true });
  }
  ctx.restore();
}

export interface BarLike extends SnapBar { timeSec: number }

class DrawingsRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly owner: DrawingsPrimitive) {}
  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    const o = this.owner;
    if (!o.series || !o.chart) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const plot = { w: mediaSize.width, h: mediaSize.height };
      paintAll(context, o.state, plot, (d) => layoutDrawing(d, o.toX, o.toY, plot));
    });
  }
}

class DrawingsPaneView implements IPrimitivePaneView {
  private readonly rendererInstance: DrawingsRenderer;
  constructor(owner: DrawingsPrimitive) { this.rendererInstance = new DrawingsRenderer(owner); }
  /** On top of the candles, like marks drawn on paper over a chart. */
  zOrder(): PrimitivePaneViewZOrder { return "top"; }
  renderer(): IPrimitivePaneRenderer { return this.rendererInstance; }
}

export class DrawingsPrimitive implements ISeriesPrimitive<Time> {
  state: PaintState = { drawings: [], selectedId: null, hoverId: null, preview: null };
  series: ISeriesApi<SeriesType, Time> | null = null;
  chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private times: number[] = [];
  private snapBars: SnapBar[] = [];
  private readonly view = new DrawingsPaneView(this);

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series;
    this.chart = param.chart as IChartApi;
    this.requestUpdate = param.requestUpdate;
  }
  detached(): void {
    this.series = null;
    this.chart = null;
    this.requestUpdate = null;
  }
  paneViews(): readonly IPrimitivePaneView[] { return [this.view]; }

  setState(state: PaintState): void {
    this.state = state;
    this.requestUpdate?.();
  }

  /** The bars on the chart, so times can be mapped to and from bar positions. */
  setBars(bars: readonly BarLike[]): void {
    this.times = bars.map((b) => b.timeSec);
    this.snapBars = bars.map((b) => ({ open: b.open, high: b.high, low: b.low, close: b.close }));
    this.requestUpdate?.();
  }

  // Arrow functions so they can be passed around as plain converters.
  toX = (time: number): number | null => {
    const chart = this.chart;
    if (!chart) return null;
    const logical = timeToLogical(this.times, time);
    if (logical === null) return null;
    return chart.timeScale().logicalToCoordinate(logical as never);
  };

  toY = (price: number): number | null => this.series?.priceToCoordinate(price) ?? null;

  /** The plot area in pixels (the price pane without its axes), or null if the chart is not ready. */
  plot(): Plot | null {
    const chart = this.chart;
    if (!chart) return null;
    const w = chart.timeScale().width();
    const h = chart.panes()[0]?.getHeight() ?? 0;
    return w > 0 && h > 0 ? { w, h } : null;
  }

  inPlot = (p: Pt): boolean => {
    const plot = this.plot();
    return !!plot && p.x >= 0 && p.x <= plot.w && p.y >= 0 && p.y <= plot.h;
  };

  toPixel = (a: Anchor): Pt | null => {
    const x = this.toX(a.time), y = this.toY(a.price);
    return x === null || y === null ? null : { x, y };
  };

  /** Pixels to a time and price, optionally pulled onto the nearest bar and price (the magnet). */
  toAnchor = (p: Pt, snap: boolean): Anchor | null => {
    const chart = this.chart, series = this.series;
    if (!chart || !series || !this.inPlot(p) || !this.times.length) return null;
    const ts = chart.timeScale();
    const logical = ts.coordinateToLogical(p.x);
    const price = series.coordinateToPrice(p.y);
    if (logical === null || price === null) return null;
    let l: number = logical, pr: number = price;
    if (snap) {
      const s = snapPoint({
        x: p.x, y: p.y, logical: l, price: pr, bars: this.snapBars,
        xOfLogical: (v) => ts.logicalToCoordinate(v as never),
        yOfPrice: (v) => series.priceToCoordinate(v),
      });
      l = s.logical; pr = s.price;
    }
    const time = logicalToTime(this.times, l);
    return time === null ? null : { time, price: pr };
  };

  /** The drawings as pixel shapes now, for picking. */
  shapes = (): Shape[] => {
    const plot = this.plot();
    if (!plot) return [];
    const out: Shape[] = [];
    for (const d of this.state.drawings) {
      const s = layoutDrawing(d, this.toX, this.toY, plot);
      if (s) out.push(s);
    }
    return out;
  };
}
