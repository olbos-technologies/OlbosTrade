/**
 * The drawing tools' behaviour: which tool is active, what is selected, and what
 * each mouse press, move, release and key does. No DOM and no chart in here. The
 * caller feeds it pointer positions in pixels and supplies the conversions
 * (pixel to time and price, and the on-screen shapes), so every gesture can be
 * tested exactly.
 *
 * Creating a two-point drawing works both ways: click the first point, move, click
 * the second; or press, drag and release. A drawing is finished when the second
 * point lands, the tool then returns to Select, and the new drawing is selected.
 *
 * Changes are one undo step each. A drag changes the drawing many times a second
 * but records a single step when it ends (see history.ts).
 */

import {
  checkpoint, commit, canRedo, canUndo, createHistory, redo, replacePresent, reset, undo, type History,
} from "./history";
import { hitTest, type Pt, type Shape } from "./geometry";
import {
  DEFAULT_COLOR, DEFAULT_WIDTH, KIND_POINTS, MAX_PER_SYMBOL, makeDrawing,
  type Anchor, type Drawing, type DrawingKind, type LineWidth,
} from "./model";

export type Tool = "select" | DrawingKind;

export interface EngineDeps {
  /** Pixel to a time and price. null when the point is outside the plot or cannot be converted. */
  toAnchor: (p: Pt, snap: boolean) => Anchor | null;
  /** The drawings as pixel shapes right now (for picking). */
  shapes: () => Shape[];
  /** A pixel position for an anchor, or null. */
  toPixel: (a: Anchor) => Pt | null;
  /** Is this pixel inside the plot area (the price pane without its axes)? */
  inPlot: (p: Pt) => boolean;
}

export interface EngineState {
  tool: Tool;
  drawings: readonly Drawing[];
  selectedId: string | null;
  hoverId: string | null;
  /** The drawing being placed, shown while the second point follows the pointer. */
  preview: Drawing | null;
  color: string;
  width: LineWidth;
  magnet: boolean;
  canUndo: boolean;
  canRedo: boolean;
}

export interface KeyInput { key: string; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean }

/** A drag shorter than this (in px) after pressing to start a drawing is treated as a plain click. */
export const DRAG_CREATE_MIN = 6;
/** A second point closer than this to the first would be invisible, so it is ignored. */
export const MIN_SIZE = 2;
/** A fingertip is far less precise than a mouse: touch picks a line within this many px, and a handle within TOUCH_HANDLE. */
export const TOUCH_TOL = 14;
export const TOUCH_HANDLE = 20;

type Pending = { kind: DrawingKind; first: Anchor; firstPx: Pt; pressed: boolean; last: Anchor };
type Drag = { id: string; mode: "handle" | "move"; index: number; base: readonly Drawing[]; startPx: Pt; origPx: Pt[] };

export class DrawingEngine {
  private deps: EngineDeps;
  private history: History<readonly Drawing[]> = createHistory<readonly Drawing[]>([]);
  private tool: Tool = "select";
  private selectedId: string | null = null;
  private hoverId: string | null = null;
  private pending: Pending | null = null;
  private drag: Drag | null = null;
  private color = DEFAULT_COLOR;
  private width: LineWidth = DEFAULT_WIDTH;
  private magnet = false;
  private listeners = new Set<() => void>();
  private commitListeners = new Set<(drawings: readonly Drawing[], key: string) => void>();
  private snapshot: EngineState;
  /** Which symbol the drawings belong to, so a save can never land under another symbol. */
  private owner = "";

  constructor(deps: EngineDeps) {
    this.deps = deps;
    this.snapshot = this.build();
  }

  // ── State ──────────────────────────────────────────────────────────────────

  getState = (): EngineState => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  /** Whether a drawing is half-placed or being dragged, so the caller can claim mouse moves. */
  get busy(): boolean { return this.pending !== null || this.drag !== null; }

  private build(): EngineState {
    const present = this.history.present;
    return {
      tool: this.tool,
      drawings: present,
      selectedId: this.selectedId,
      hoverId: this.hoverId,
      preview: this.previewDrawing(),
      color: this.color,
      width: this.width,
      magnet: this.magnet,
      canUndo: canUndo(this.history),
      canRedo: canRedo(this.history),
    };
  }

  private emit(): void {
    this.snapshot = this.build();
    this.listeners.forEach((l) => l());
  }

  /**
   * Called after the drawings were really EDITED (placed, moved, restyled, deleted,
   * cleared, undone or redone), not on load, hover or a live preview. This is the
   * moment to save, so opening a symbol never writes anything back.
   */
  onCommit = (listener: (drawings: readonly Drawing[], key: string) => void): (() => void) => {
    this.commitListeners.add(listener);
    return () => { this.commitListeners.delete(listener); };
  };

  private committed(): void {
    this.emit();
    this.commitListeners.forEach((l) => l(this.history.present, this.owner));
  }

  private previewDrawing(): Drawing | null {
    const p = this.pending;
    if (!p) return null;
    return { id: "preview", kind: p.kind, points: [p.first, p.last], color: this.color, width: this.width };
  }

  setDeps(deps: EngineDeps): void { this.deps = deps; }

  /** The symbol the current drawings belong to. */
  get key(): string { return this.owner; }

  /** Replace everything with a saved list (a different symbol was opened). */
  load(drawings: readonly Drawing[], key = ""): void {
    this.owner = key;
    this.history = reset(drawings);
    this.selectedId = null;
    this.hoverId = null;
    this.pending = null;
    this.drag = null;
    this.emit();
  }

  // ── Settings ───────────────────────────────────────────────────────────────

  setTool(tool: Tool): void {
    if (this.drag) return;
    this.pending = null;
    this.tool = tool;
    if (tool !== "select") { this.selectedId = null; this.hoverId = null; }
    this.emit();
  }

  setMagnet(on: boolean): void { this.magnet = on; this.emit(); }

  /** Sets the colour for new drawings, and recolours the selected one. */
  setColor(color: string): void {
    this.color = color;
    if (this.restyleSelected({ color })) this.committed(); else this.emit();
  }

  setWidth(width: LineWidth): void {
    this.width = width;
    if (this.restyleSelected({ width })) this.committed(); else this.emit();
  }

  private restyleSelected(style: Partial<Pick<Drawing, "color" | "width">>): boolean {
    if (!this.selectedId) return false;
    const before = this.history;
    const next = this.history.present.map((d) => (d.id === this.selectedId ? { ...d, ...style } : d));
    this.history = commit(this.history, next);
    return this.history !== before;
  }

  // ── Edits ──────────────────────────────────────────────────────────────────

  deleteSelected(): boolean {
    if (!this.selectedId) return false;
    const id = this.selectedId;
    this.history = commit(this.history, this.history.present.filter((d) => d.id !== id));
    this.selectedId = null;
    this.hoverId = null;
    this.committed();
    return true;
  }

  clearAll(): boolean {
    if (!this.history.present.length) return false;
    this.history = commit(this.history, []);
    this.selectedId = null;
    this.hoverId = null;
    this.committed();
    return true;
  }

  undo(): void { this.step(undo(this.history)); }
  redo(): void { this.step(redo(this.history)); }

  private step(next: History<readonly Drawing[]>): void {
    if (next === this.history) return;
    this.history = next;
    this.pending = null;
    this.drag = null;
    if (this.selectedId && !next.present.some((d) => d.id === this.selectedId)) this.selectedId = null;
    if (this.hoverId && !next.present.some((d) => d.id === this.hoverId)) this.hoverId = null;
    this.committed();
  }

  /** Abandon a half-placed drawing and any drag. Returns whether there was anything to cancel. */
  cancel(): boolean {
    const had = this.pending !== null || this.drag !== null;
    if (this.drag) this.history = replacePresent(this.history, this.drag.base);
    this.pending = null;
    this.drag = null;
    if (had) this.emit();
    return had;
  }

  // ── Pointer ────────────────────────────────────────────────────────────────

  /** A press. Returns true when the engine used it, so the caller must keep it from the chart. */
  pointerDown(p: Pt, touch = false): boolean {
    if (this.tool !== "select") return this.placePoint(p, true);
    return this.selectOrGrab(p, touch);
  }

  pointerMove(p: Pt): void {
    if (this.drag) { this.dragTo(p); return; }
    if (this.pending) {
      const a = this.deps.toAnchor(p, this.magnet);
      if (a) { this.pending = { ...this.pending, last: a }; this.emit(); }
      return;
    }
    if (this.tool === "select") {
      const hit = hitTest(this.deps.shapes(), p, { selectedId: this.selectedId });
      const id = hit ? hit.id : null;
      if (id !== this.hoverId) { this.hoverId = id; this.emit(); }
    }
  }

  /** The pointer left the chart: nothing is hovered any more. */
  pointerLeave(): void {
    if (this.hoverId !== null && !this.drag) { this.hoverId = null; this.emit(); }
  }

  pointerUp(p: Pt): void {
    if (this.drag) {
      const before = this.history;
      this.history = checkpoint(this.history, this.drag.base);
      const edited = this.history !== before; // a press-and-release that moved nothing is not an edit
      this.drag = null;
      if (edited) this.committed(); else this.emit();
      return;
    }
    const pend = this.pending;
    if (pend && pend.pressed) {
      // Press-drag-release: if the pointer travelled, the release is the second point.
      const moved = Math.hypot(p.x - pend.firstPx.x, p.y - pend.firstPx.y);
      if (moved >= DRAG_CREATE_MIN) this.placePoint(p, false);
      else this.pending = { ...pend, pressed: false };
    }
  }

  private placePoint(p: Pt, isPress: boolean): boolean {
    if (!this.deps.inPlot(p)) return false;
    const a = this.deps.toAnchor(p, this.magnet);
    if (!a) return false;
    const pend = this.pending;
    const kind = this.tool as DrawingKind;

    if (KIND_POINTS[kind] === 1) return this.finish(kind, [a]);

    if (!pend) {
      this.pending = { kind, first: a, firstPx: p, pressed: isPress, last: a };
      this.emit();
      return true;
    }
    if (Math.hypot(p.x - pend.firstPx.x, p.y - pend.firstPx.y) < MIN_SIZE) return true; // too small to see
    return this.finish(pend.kind, [pend.first, a]);
  }

  private finish(kind: DrawingKind, points: Anchor[]): boolean {
    this.pending = null;
    if (this.history.present.length >= MAX_PER_SYMBOL) { this.emit(); return true; } // at the limit: add nothing
    const d = makeDrawing(kind, points, this.color, this.width);
    this.history = commit(this.history, [...this.history.present, d]);
    this.selectedId = d.id;
    this.hoverId = null;
    this.tool = "select";
    this.committed();
    return true;
  }

  private selectOrGrab(p: Pt, touch: boolean): boolean {
    const hit = hitTest(this.deps.shapes(), p, {
      selectedId: this.selectedId,
      ...(touch ? { tol: TOUCH_TOL, handleRadius: TOUCH_HANDLE } : {}),
    });
    if (!hit) {
      if (this.selectedId) { this.selectedId = null; this.emit(); }
      return false; // empty space: the chart may pan
    }
    this.selectedId = hit.id;
    const d = this.history.present.find((x) => x.id === hit.id);
    if (d) {
      const origPx = d.points.map((a) => this.deps.toPixel(a));
      if (origPx.every((o): o is Pt => o !== null)) {
        this.drag = {
          id: d.id, mode: hit.part === "handle" ? "handle" : "move",
          index: hit.part === "handle" ? hit.index : -1, base: this.history.present, startPx: p, origPx,
        };
      }
    }
    this.emit();
    return true;
  }

  private dragTo(p: Pt): void {
    const d = this.drag;
    if (!d) return;
    const current = this.history.present.find((x) => x.id === d.id);
    if (!current) return;
    let points: Anchor[] | null = null;

    if (d.mode === "handle") {
      const a = this.deps.toAnchor(p, this.magnet);
      if (a) points = current.points.map((q, i) => (i === d.index ? a : q));
    } else {
      const dx = p.x - d.startPx.x, dy = p.y - d.startPx.y;
      const moved: Anchor[] = [];
      for (const o of d.origPx) {
        const a = this.deps.toAnchor({ x: o.x + dx, y: o.y + dy }, false);
        if (!a) break;
        moved.push(a);
      }
      if (moved.length === current.points.length) points = moved; // otherwise it would leave the plot: hold still
    }
    if (!points) return;
    const next = this.history.present.map((x) => (x.id === d.id ? { ...x, points } : x));
    this.history = replacePresent(this.history, next);
    this.emit();
  }

  // ── Keyboard ───────────────────────────────────────────────────────────────

  /** Returns true when the key was used. */
  keyDown(k: KeyInput): boolean {
    const mod = !!(k.ctrlKey || k.metaKey);
    const key = k.key.length === 1 ? k.key.toLowerCase() : k.key;
    if (mod && key === "z") { if (k.shiftKey) this.redo(); else this.undo(); return true; }
    if (mod && key === "y") { this.redo(); return true; }
    if (key === "Escape") {
      if (this.cancel()) return true;
      if (this.tool !== "select") { this.setTool("select"); return true; }
      if (this.selectedId) { this.selectedId = null; this.emit(); return true; }
      return false;
    }
    if (key === "Delete" || key === "Backspace") return this.deleteSelected();
    return false;
  }
}
