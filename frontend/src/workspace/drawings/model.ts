/**
 * Chart drawings: the data model, validation and browser storage. No React, no
 * charting library, so every rule can be tested directly.
 *
 * A drawing is anchored to TIME and PRICE, not to pixels or bar numbers, so it
 * stays on the same candle and price when the chart is zoomed, scrolled, resized
 * or switched to another bar interval. Times are UTC seconds and may be
 * fractional (a point between two bars) or beyond the last bar (the future).
 *
 * Drawings are PRESENTATION only. They are notes on a chart: they place no
 * orders, set no stops and feed no risk or execution logic. They are saved in
 * this browser only (not to the account), and stored data is validated on load
 * because browser storage can be edited by anyone.
 */

export const DRAWING_KINDS = ["trend", "hline", "vline", "rect", "fib"] as const;
export type DrawingKind = (typeof DRAWING_KINDS)[number];

export const KIND_TITLES: Readonly<Record<DrawingKind, string>> = {
  trend: "Trend line",
  hline: "Horizontal line",
  vline: "Vertical line",
  rect: "Rectangle",
  fib: "Fib retracement",
};

/** How many anchor points each kind needs. */
export const KIND_POINTS: Readonly<Record<DrawingKind, 1 | 2>> = {
  trend: 2, hline: 1, vline: 1, rect: 2, fib: 2,
};

export interface Anchor {
  /** UTC seconds. May fall between bars or after the last one. */
  time: number;
  price: number;
}

export type LineWidth = 1 | 2 | 3;
export const LINE_WIDTHS: readonly LineWidth[] = [1, 2, 3];

export interface Drawing {
  id: string;
  kind: DrawingKind;
  points: Anchor[];
  color: string;
  width: LineWidth;
}

/**
 * The colours on offer. A drawing's colour is the user's own mark, not a profit
 * or loss signal, so pure green and red are left out to keep them meaning P&L.
 */
export const PALETTE: readonly { name: string; value: string }[] = [
  { name: "Blue", value: "#7daaff" },
  { name: "Amber", value: "#f2b84b" },
  { name: "Violet", value: "#a98bff" },
  { name: "Cyan", value: "#4fd1e6" },
  { name: "White", value: "#e8edf6" },
  { name: "Pink", value: "#f28bc0" },
];
export const DEFAULT_COLOR = PALETTE[0].value;
export const DEFAULT_WIDTH: LineWidth = 2;

/** Fibonacci retracement levels, as a fraction of the move from point 1 to point 2. */
export const FIB_LEVELS: readonly number[] = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

export const MAX_PER_SYMBOL = 200;
export const MAX_SYMBOLS = 40;
export const DRAWINGS_STORAGE_KEY = "olbos.workspace.drawings.v1";

const SYMBOL_KEY = /^[A-Z0-9.^=:-]{1,20}$/;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;
const COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;
const MAX_TIME = 4.2e9; // about the year 2103, in seconds
const MAX_PRICE = 1e9;

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

export function isKind(value: unknown): value is DrawingKind {
  return typeof value === "string" && (DRAWING_KINDS as readonly string[]).includes(value);
}

let counter = 0;
export function newId(): string {
  try {
    const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
    if (c?.randomUUID) return c.randomUUID();
  } catch { /* fall through */ }
  counter += 1;
  return `d${Date.now().toString(36)}${counter.toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

function parseAnchor(raw: unknown): Anchor | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (!finite(r.time) || !finite(r.price)) return null;
  if (r.time < 0 || r.time > MAX_TIME || Math.abs(r.price) > MAX_PRICE) return null;
  return { time: r.time, price: r.price };
}

export function parseDrawing(raw: unknown): Drawing | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string" || !ID_PATTERN.test(r.id)) return null;
  if (!isKind(r.kind)) return null;
  if (!Array.isArray(r.points) || r.points.length !== KIND_POINTS[r.kind]) return null;
  const points: Anchor[] = [];
  for (const p of r.points) {
    const a = parseAnchor(p);
    if (!a) return null;
    points.push(a);
  }
  const color = typeof r.color === "string" && COLOR_PATTERN.test(r.color) ? r.color.toLowerCase() : DEFAULT_COLOR;
  const width = r.width === 1 || r.width === 2 || r.width === 3 ? r.width : DEFAULT_WIDTH;
  return { id: r.id, kind: r.kind, points, color, width };
}

/** Anything -> a clean list: invalid entries dropped, duplicate ids dropped, capped. */
export function parseDrawings(raw: unknown): Drawing[] {
  if (!Array.isArray(raw)) return [];
  const out: Drawing[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const d = parseDrawing(item);
    if (!d || seen.has(d.id)) continue;
    seen.add(d.id);
    out.push(d);
    if (out.length >= MAX_PER_SYMBOL) break;
  }
  return out;
}

export type DrawingStore = Record<string, Drawing[]>;

/** Anything -> a clean store. Only well-formed symbol keys are kept (so `__proto__` cannot appear). */
export function parseStore(raw: unknown): DrawingStore {
  const store: DrawingStore = Object.create(null);
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return store;
  let kept = 0;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!SYMBOL_KEY.test(key)) continue;
    const list = parseDrawings(value);
    if (!list.length) continue;
    store[key] = list;
    if (++kept >= MAX_SYMBOLS) break;
  }
  return store;
}

type ReadStorage = Pick<Storage, "getItem">;
type WriteStorage = Pick<Storage, "getItem" | "setItem">;

function readStore(storage?: ReadStorage | null): DrawingStore {
  try {
    const text = storage?.getItem(DRAWINGS_STORAGE_KEY);
    return text ? parseStore(JSON.parse(text)) : parseStore(null);
  } catch {
    return parseStore(null); // private mode, blocked storage or corrupt JSON
  }
}

export function loadDrawings(storage: ReadStorage | null | undefined, symbol: string): Drawing[] {
  if (!SYMBOL_KEY.test(symbol)) return [];
  return readStore(storage)[symbol] ?? [];
}

/** Save one symbol's drawings, keeping the others. An empty list removes the symbol. */
export function saveDrawings(storage: WriteStorage | null | undefined, symbol: string, drawings: readonly Drawing[]): void {
  if (!SYMBOL_KEY.test(symbol)) return;
  try {
    const store = readStore(storage);
    delete store[symbol];
    const list = parseDrawings(drawings);
    const entries = Object.entries(store);
    if (list.length) entries.push([symbol, list]); // most recently saved goes last
    const kept = entries.slice(-MAX_SYMBOLS); // when over the limit, the oldest symbols go first
    storage?.setItem(DRAWINGS_STORAGE_KEY, JSON.stringify(Object.fromEntries(kept)));
  } catch { /* storage unavailable or full: the drawings last for this visit */ }
}

/** A drawing from its anchors, with the chosen style. */
export function makeDrawing(kind: DrawingKind, points: Anchor[], color = DEFAULT_COLOR, width: LineWidth = DEFAULT_WIDTH): Drawing {
  return { id: newId(), kind, points: points.map((p) => ({ ...p })), color, width };
}
