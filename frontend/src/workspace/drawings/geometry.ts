/**
 * Drawings as pixel shapes, and picking them with the mouse. Pure geometry: the
 * caller supplies functions that convert time to x and price to y (the chart's
 * own scales), so this file has no chart in it and is tested directly.
 *
 * The SAME shapes are used to draw and to hit-test, so what you can click is
 * exactly what is on screen.
 */

import { FIB_LEVELS, type Drawing, type DrawingKind, type LineWidth } from "./model";

export interface Pt { x: number; y: number }
/** The plot area (the price pane without its axes), in pixels. */
export interface Plot { w: number; h: number }

export interface FibLevel { level: number; price: number; y: number; x1: number; x2: number; label: string }

export interface Shape {
  id: string;
  kind: DrawingKind;
  color: string;
  width: LineWidth;
  /** Where the anchor points are in pixels. These are the draggable handles. */
  anchors: Pt[];
  /** Straight lines to stroke. */
  segments: [Pt, Pt][];
  /** Filled rectangle, for the rectangle tool. */
  rect?: { x: number; y: number; w: number; h: number };
  /** Fibonacci level lines. */
  levels?: FibLevel[];
  /** A short text shown with the shape (a horizontal line shows its price). */
  label?: string;
}

export type ToX = (time: number) => number | null;
export type ToY = (price: number) => number | null;

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));
const HANDLE_MARGIN = 8;

export function layoutDrawing(d: Drawing, toX: ToX, toY: ToY, plot: Plot): Shape | null {
  const base = { id: d.id, kind: d.kind, color: d.color, width: d.width };
  const px = d.points.map((p) => ({ x: toX(p.time), y: toY(p.price) }));
  if (px.some((p) => p.x === null || p.y === null || !Number.isFinite(p.x) || !Number.isFinite(p.y))) return null;
  const pts = px as Pt[];

  switch (d.kind) {
    case "trend":
      return { ...base, anchors: pts, segments: [[pts[0], pts[1]]] };
    case "hline": {
      const y = pts[0].y;
      // The handle stays visible even if the anchor's time has scrolled off screen.
      const hx = clamp(pts[0].x, HANDLE_MARGIN, Math.max(HANDLE_MARGIN, plot.w - HANDLE_MARGIN));
      return { ...base, anchors: [{ x: hx, y }], segments: [[{ x: 0, y }, { x: plot.w, y }]], label: d.points[0].price.toFixed(2) };
    }
    case "vline": {
      const x = pts[0].x;
      const hy = clamp(pts[0].y, HANDLE_MARGIN, Math.max(HANDLE_MARGIN, plot.h - HANDLE_MARGIN));
      return { ...base, anchors: [{ x, y: hy }], segments: [[{ x, y: 0 }, { x, y: plot.h }]] };
    }
    case "rect": {
      const x = Math.min(pts[0].x, pts[1].x), y = Math.min(pts[0].y, pts[1].y);
      return { ...base, anchors: pts, segments: [], rect: { x, y, w: Math.abs(pts[1].x - pts[0].x), h: Math.abs(pts[1].y - pts[0].y) } };
    }
    case "fib": {
      const [a, b] = d.points;
      const x1 = Math.min(pts[0].x, pts[1].x), x2 = Math.max(pts[0].x, pts[1].x);
      const levels: FibLevel[] = [];
      for (const level of FIB_LEVELS) {
        const price = a.price + (b.price - a.price) * level;
        const y = toY(price);
        if (y === null || !Number.isFinite(y)) continue;
        levels.push({ level, price, y, x1, x2, label: `${+(level * 100).toFixed(1)}%  ${price.toFixed(2)}` });
      }
      return { ...base, anchors: pts, segments: [[pts[0], pts[1]]], levels };
    }
  }
}

/** Distance from a point to a line segment, in pixels. */
export function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  const t = clamp(((p.x - a.x) * dx + (p.y - a.y) * dy) / len2, 0, 1);
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

export type Hit =
  | { id: string; part: "handle"; index: number }
  | { id: string; part: "body" };

export interface HitOptions {
  /** How close counts as touching a line, in px. */
  tol?: number;
  /** How close counts as touching a handle, in px. */
  handleRadius?: number;
  /** Only the selected drawing's handles can be grabbed. */
  selectedId?: string | null;
}

export const DEFAULT_TOL = 6;
export const DEFAULT_HANDLE_RADIUS = 7;

function onBody(s: Shape, p: Pt, tol: number): boolean {
  for (const [a, b] of s.segments) if (distToSegment(p, a, b) <= tol) return true;
  if (s.rect) {
    const { x, y, w, h } = s.rect;
    const corners: Pt[] = [{ x, y }, { x: x + w, y }, { x: x + w, y: y + h }, { x, y: y + h }];
    for (let i = 0; i < 4; i++) if (distToSegment(p, corners[i], corners[(i + 1) % 4]) <= tol) return true;
  }
  if (s.levels) {
    for (const l of s.levels) if (p.x >= l.x1 - tol && p.x <= l.x2 + tol && Math.abs(p.y - l.y) <= tol) return true;
  }
  return false;
}

/**
 * What is under the pointer, if anything. The selected drawing's handles win, so a
 * handle that sits on another line can still be grabbed; then the topmost body
 * (later in the list is drawn on top).
 */
export function hitTest(shapes: readonly Shape[], p: Pt, opts: HitOptions = {}): Hit | null {
  const tol = opts.tol ?? DEFAULT_TOL;
  const radius = opts.handleRadius ?? DEFAULT_HANDLE_RADIUS;
  if (opts.selectedId) {
    const s = shapes.find((x) => x.id === opts.selectedId);
    if (s) {
      for (let i = 0; i < s.anchors.length; i++) {
        if (Math.hypot(p.x - s.anchors[i].x, p.y - s.anchors[i].y) <= radius) return { id: s.id, part: "handle", index: i };
      }
    }
  }
  for (let i = shapes.length - 1; i >= 0; i--) {
    if (onBody(shapes[i], p, tol)) return { id: shapes[i].id, part: "body" };
  }
  return null;
}
