/**
 * Draws a volume profile inside the chart's price pane.
 *
 * It is a lightweight-charts SERIES PRIMITIVE attached to the candle series, so
 * the chart itself calls it on every redraw and hands it the series' own
 * price-to-pixel mapping. The bars therefore stay exactly aligned with the
 * candles and the price axis through zoom, scroll and resize, with no overlay
 * canvas to keep in sync.
 *
 * The bars grow from the right edge toward the left and sit BEHIND the candles.
 * The busiest price (point of control) gets a thin line across the pane, and the
 * value area is drawn slightly stronger. The colours are deliberately neutral: a
 * profile shows where volume traded, not gain or loss, so it does not use the
 * green and red that mean profit and loss elsewhere.
 */

import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { VolumeProfile } from "./volumeProfile";

export interface ProfileColors {
  bar: string;
  valueArea: string;
  poc: string;
}

export const PROFILE_COLORS: ProfileColors = {
  bar: "rgba(148, 163, 184, 0.20)",
  valueArea: "rgba(148, 163, 184, 0.38)",
  poc: "rgba(125, 170, 255, 0.90)",
};

/** The longest bar takes at most this share of the pane's width. */
export const MAX_WIDTH_SHARE = 0.3;

/** The parts of a 2D canvas context the profile uses, so tests can stand in for it. */
export type ProfileContext = Pick<
  CanvasRenderingContext2D,
  "fillRect" | "save" | "restore" | "beginPath" | "moveTo" | "lineTo" | "stroke" | "setLineDash" | "fillStyle" | "strokeStyle" | "lineWidth"
>;

/**
 * Draw `profile` into a pane `width` px wide. `priceToY` maps a price to a pixel
 * row (or null when the price is off the visible scale, in which case that bin is
 * skipped rather than drawn in the wrong place).
 */
export function drawVolumeProfile(
  ctx: ProfileContext,
  profile: VolumeProfile,
  width: number,
  priceToY: (price: number) => number | null,
  colors: ProfileColors = PROFILE_COLORS,
): void {
  if (!profile.rows.length || profile.pocIndex < 0 || !(width > 0)) return;
  const maxTotal = profile.rows[profile.pocIndex].total;
  if (!(maxTotal > 0)) return;

  const maxLen = width * MAX_WIDTH_SHARE;
  ctx.save();
  profile.rows.forEach((row, i) => {
    if (!(row.total > 0)) return;
    const yTop = priceToY(row.high);
    const yBottom = priceToY(row.low);
    if (yTop === null || yBottom === null) return;
    const top = Math.min(yTop, yBottom);
    const height = Math.max(1, Math.abs(yBottom - yTop) - 1); // a 1px gap between bins
    const len = Math.max(1, (row.total / maxTotal) * maxLen);
    const inValueArea = !!profile.valueArea && i >= profile.valueArea.from && i <= profile.valueArea.to;
    ctx.fillStyle = inValueArea ? colors.valueArea : colors.bar;
    ctx.fillRect(width - len, top, len, height);
  });

  const poc = profile.rows[profile.pocIndex];
  const yh = priceToY(poc.high);
  const yl = priceToY(poc.low);
  if (yh !== null && yl !== null) {
    const y = Math.round((yh + yl) / 2) + 0.5;
    ctx.strokeStyle = colors.poc;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  ctx.restore();
}

class ProfileRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly owner: VolumeProfilePrimitive) {}

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    const profile = this.owner.profile;
    const series = this.owner.series;
    if (!profile || !series) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      drawVolumeProfile(context, profile, mediaSize.width, (price) => series.priceToCoordinate(price));
    });
  }
}

class ProfilePaneView implements IPrimitivePaneView {
  private readonly rendererInstance: ProfileRenderer;
  constructor(owner: VolumeProfilePrimitive) { this.rendererInstance = new ProfileRenderer(owner); }
  /** Behind the candles, so the profile never hides a price bar. */
  zOrder(): PrimitivePaneViewZOrder { return "bottom"; }
  renderer(): IPrimitivePaneRenderer { return this.rendererInstance; }
}

export class VolumeProfilePrimitive implements ISeriesPrimitive<Time> {
  profile: VolumeProfile | null = null;
  series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private readonly view = new ProfilePaneView(this);

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  setProfile(profile: VolumeProfile | null): void {
    this.profile = profile;
    this.requestUpdate?.();
  }
}
