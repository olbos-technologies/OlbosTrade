/**
 * The Pro Workspace layout: a model with no React in it, so every rule can be
 * tested directly.
 *
 * The workspace is three regions around the chart:
 *
 *     +----------------------+-----------+
 *     |                      |  side top |
 *     |        chart         +-----------+
 *     |                      | side bot. |
 *     +----------------------+-----------+
 *     |            bottom dock           |
 *     +----------------------------------+
 *
 * Which panel sits in each slot, how the regions are sized, and whether the side
 * column and bottom dock are open make up a layout. A layout is PRESENTATION
 * only. It never changes what a panel can do, which account or environment is
 * active, or any risk or execution setting (AGENTS.md rule 3).
 *
 * A saved layout comes from browser storage, which anyone can edit, so it is
 * validated on the way in: unknown panels, out-of-range sizes and the wrong
 * version all fall back to safe values rather than rendering something broken.
 */

export const PANEL_IDS = ["watchlist", "positions", "flow", "news", "scanner", "setups", "backtest"] as const;
export type PanelId = (typeof PANEL_IDS)[number];

export const PANEL_TITLES: Readonly<Record<PanelId, string>> = {
  watchlist: "Watchlist",
  positions: "Positions",
  flow: "Unusual options",
  news: "News & catalysts",
  scanner: "Scanner",
  setups: "Setup scanner",
  backtest: "Backtest",
};

export type PresetId = "trader" | "research" | "focus";
export const PRESET_IDS: readonly PresetId[] = ["trader", "research", "focus"];
export const PRESET_TITLES: Readonly<Record<PresetId, string>> = {
  trader: "Trader",
  research: "Research",
  focus: "Chart only",
};

export interface DockLayout {
  v: 1;
  /** Share of the width the chart column takes when the side column is open. */
  chartFrac: number;
  /** Share of the height the chart/side area takes when the bottom dock is open. */
  topFrac: number;
  /** Share of the side column's height the top slot takes. */
  sideSplit: number;
  sideOpen: boolean;
  bottomOpen: boolean;
  sideTop: PanelId;
  sideBottom: PanelId;
  bottom: PanelId;
}

/** Hard limits, so no region can be dragged to nothing and lost. */
export const LIMITS = {
  chartFrac: { min: 0.4, max: 0.85 },
  topFrac: { min: 0.35, max: 0.8 },
  sideSplit: { min: 0.2, max: 0.8 },
} as const;

export type Frac = keyof typeof LIMITS;

export const LAYOUT_STORAGE_KEY = "olbos.workspace.layout.v1";

export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

export function clampFrac(which: Frac, value: number): number {
  return clamp(value, LIMITS[which].min, LIMITS[which].max);
}

export function isPanelId(value: unknown): value is PanelId {
  return typeof value === "string" && (PANEL_IDS as readonly string[]).includes(value);
}

export function presetLayout(id: PresetId): DockLayout {
  switch (id) {
    case "research":
      return { v: 1, chartFrac: 0.6, topFrac: 0.55, sideSplit: 0.5, sideOpen: true, bottomOpen: true,
        sideTop: "news", sideBottom: "flow", bottom: "backtest" };
    case "focus":
      return { ...presetLayout("trader"), sideOpen: false, bottomOpen: false };
    case "trader":
    default:
      return { v: 1, chartFrac: 0.72, topFrac: 0.68, sideSplit: 0.5, sideOpen: true, bottomOpen: true,
        sideTop: "watchlist", sideBottom: "flow", bottom: "scanner" };
  }
}

export const DEFAULT_LAYOUT: DockLayout = presetLayout("trader");

/** Anything -> a valid layout. Never throws; bad input becomes the default. */
export function parseLayout(raw: unknown): DockLayout {
  if (typeof raw !== "object" || raw === null) return { ...DEFAULT_LAYOUT };
  const r = raw as Record<string, unknown>;
  if (r.v !== 1) return { ...DEFAULT_LAYOUT };
  const num = (key: Frac, fallback: number) =>
    typeof r[key] === "number" ? clampFrac(key, r[key] as number) : fallback;
  const panel = (value: unknown, fallback: PanelId): PanelId => (isPanelId(value) ? value : fallback);
  return {
    v: 1,
    chartFrac: num("chartFrac", DEFAULT_LAYOUT.chartFrac),
    topFrac: num("topFrac", DEFAULT_LAYOUT.topFrac),
    sideSplit: num("sideSplit", DEFAULT_LAYOUT.sideSplit),
    sideOpen: typeof r.sideOpen === "boolean" ? r.sideOpen : DEFAULT_LAYOUT.sideOpen,
    bottomOpen: typeof r.bottomOpen === "boolean" ? r.bottomOpen : DEFAULT_LAYOUT.bottomOpen,
    sideTop: panel(r.sideTop, DEFAULT_LAYOUT.sideTop),
    sideBottom: panel(r.sideBottom, DEFAULT_LAYOUT.sideBottom),
    bottom: panel(r.bottom, DEFAULT_LAYOUT.bottom),
  };
}

type ReadStorage = Pick<Storage, "getItem">;
type WriteStorage = Pick<Storage, "setItem">;

export function loadLayout(storage?: ReadStorage | null): DockLayout {
  try {
    const text = storage?.getItem(LAYOUT_STORAGE_KEY);
    if (!text) return { ...DEFAULT_LAYOUT };
    return parseLayout(JSON.parse(text));
  } catch {
    return { ...DEFAULT_LAYOUT }; // private mode, blocked storage or corrupt JSON
  }
}

export function saveLayout(storage: WriteStorage | null | undefined, layout: DockLayout): void {
  try {
    storage?.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* storage unavailable: the layout just lasts for this visit */
  }
}

/** A drag: the fraction after moving `deltaPx` along a region `totalPx` long. */
export function fracAfterDrag(start: number, deltaPx: number, totalPx: number, which: Frac): number {
  if (!(totalPx > 0)) return clampFrac(which, start);
  return clampFrac(which, start + deltaPx / totalPx);
}

/** One keyboard step on a splitter. */
export const KEY_STEP = 0.02;
export const KEY_STEP_LARGE = 0.1;

/** The symbols the workspace accepts: the same rule as the chart page. */
export const SYMBOL_PATTERN = /^[A-Z0-9.^=:-]{1,20}$/;

export function normalizeSymbol(input: string): string | null {
  const s = input.trim().toUpperCase();
  return SYMBOL_PATTERN.test(s) ? s : null;
}
