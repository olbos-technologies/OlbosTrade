/**
 * Connects the drawing engine to a live chart.
 *
 *  - attaches the painting primitive when the chart exists and lets go of it
 *    BEFORE the chart is removed (the chart is rebuilt whenever bars reload);
 *  - turns mouse events on the chart into engine gestures, claiming a press only
 *    when the engine used it, so panning and zooming keep working everywhere else;
 *  - loads a symbol's saved drawings, and saves after every real edit.
 *
 * Touch works the same way as the mouse: a one-finger press the engine uses is
 * kept from the chart (capture phase, non-passive, so it can be stopped before the
 * chart's own pan handler sees it); a press on empty space is left alone so one
 * finger still pans, and a second finger cancels any gesture so pinch-zoom works.
 */

import { useEffect, useRef, useSyncExternalStore } from "react";
import { toUnixSeconds, type ChartBar, type ChartHandle } from "../../components/CandlestickChart";
import { DrawingEngine, type EngineState } from "./engine";
import { DrawingsPrimitive, type PaintState } from "./DrawingsPrimitive";
import { loadDrawings, saveDrawings } from "./model";
import type { Pt } from "./geometry";

function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

export function toPaintState(s: EngineState): PaintState {
  return { drawings: s.drawings, selectedId: s.selectedId, hoverId: s.hoverId, preview: s.preview };
}

function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable === true;
}

export function useDrawings(opts: {
  handle: ChartHandle | null;
  bars: readonly ChartBar[];
  symbol: string;
  /** Mouse, touch and keys on. Off on the phone layout until Draw mode is switched on; saved drawings still show. */
  interactive: boolean;
  /** The element that receives the keyboard shortcuts (Delete, Escape, Ctrl+Z). */
  keyTarget: HTMLElement | null;
}) {
  const { handle, bars, symbol, interactive, keyTarget } = opts;
  const primitiveRef = useRef<DrawingsPrimitive | null>(null);
  const engineRef = useRef<DrawingEngine | null>(null);
  if (!engineRef.current) {
    engineRef.current = new DrawingEngine({
      toAnchor: (p, snap) => primitiveRef.current?.toAnchor(p, snap) ?? null,
      shapes: () => primitiveRef.current?.shapes() ?? [],
      toPixel: (a) => primitiveRef.current?.toPixel(a) ?? null,
      inPlot: (p) => primitiveRef.current?.inPlot(p) ?? false,
    });
  }
  const engine = engineRef.current;
  const state = useSyncExternalStore(engine.subscribe, engine.getState, engine.getState);

  const barsRef = useRef(bars);
  barsRef.current = bars;

  // Load this symbol's saved drawings. Opening a symbol writes nothing back.
  useEffect(() => {
    engine.load(loadDrawings(browserStorage(), symbol), symbol);
  }, [engine, symbol]);

  // Save after every real edit, under the symbol the drawings belong to.
  useEffect(() => engine.onCommit((drawings, key) => { if (key) saveDrawings(browserStorage(), key, drawings); }), [engine]);

  // Put the painting primitive on the chart, and take it off before the chart goes.
  useEffect(() => {
    if (!handle) { primitiveRef.current = null; return; }
    const prim = new DrawingsPrimitive();
    handle.series.attachPrimitive(prim);
    primitiveRef.current = prim;
    prim.setBars(barsRef.current.map((b) => ({ timeSec: toUnixSeconds(b.timestamp), open: b.open, high: b.high, low: b.low, close: b.close })));
    prim.setState(toPaintState(engine.getState()));
    const unsubscribe = engine.subscribe(() => prim.setState(toPaintState(engine.getState())));
    let done = false;
    const dispose = () => {
      if (done) return;
      done = true;
      unsubscribe();
      try { handle.series.detachPrimitive(prim); } catch { /* chart already disposed */ }
      if (primitiveRef.current === prim) primitiveRef.current = null;
    };
    const off = handle.onDispose(dispose);
    return () => { off(); dispose(); };
  }, [handle, engine]);

  // Keep the primitive's bars current (a new interval or symbol brings new bars).
  useEffect(() => {
    primitiveRef.current?.setBars(bars.map((b) => ({ timeSec: toUnixSeconds(b.timestamp), open: b.open, high: b.high, low: b.low, close: b.close })));
  }, [bars, handle]);

  // Mouse: claim a press only when the engine used it.
  useEffect(() => {
    if (!handle || !interactive) return;
    const el = handle.container;
    const rel = (e: MouseEvent): Pt => {
      const r = el.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const onDocMove = (e: MouseEvent) => engine.pointerMove(rel(e));
    const stopDoc = () => {
      document.removeEventListener("mousemove", onDocMove);
      document.removeEventListener("mouseup", onDocUp);
    };
    const onDocUp = (e: MouseEvent) => { engine.pointerUp(rel(e)); stopDoc(); };
    const onDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      if (!engine.pointerDown(rel(e))) return;
      // The engine used this press. Keep it from the chart so the chart does not pan.
      e.stopPropagation();
      e.preventDefault();
      keyTarget?.focus({ preventScroll: true });
      stopDoc();
      document.addEventListener("mousemove", onDocMove);
      document.addEventListener("mouseup", onDocUp);
    };
    const onMove = (e: MouseEvent) => { if (!engine.busy) engine.pointerMove(rel(e)); else if (!e.buttons) engine.pointerMove(rel(e)); };
    const onLeave = () => engine.pointerLeave();
    el.addEventListener("mousedown", onDown, true); // capture: before the chart's own handler
    el.addEventListener("mousemove", onMove);
    el.addEventListener("mouseleave", onLeave);
    let removed = false; // the chart's disposer and React's cleanup can both call this
    const remove = () => {
      if (removed) return;
      removed = true;
      el.removeEventListener("mousedown", onDown, true);
      el.removeEventListener("mousemove", onMove);
      el.removeEventListener("mouseleave", onLeave);
      stopDoc();
    };
    const off = handle.onDispose(remove);
    return () => { off(); remove(); };
  }, [handle, interactive, engine, keyTarget]);

  // Touch: one finger draws or drags when the engine uses the press; anything else is the chart's.
  useEffect(() => {
    if (!handle || !interactive) return;
    const el = handle.container;
    let active: number | null = null;
    const rel = (t: Touch): Pt => {
      const r = el.getBoundingClientRect();
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    };
    const find = (list: TouchList, id: number): Touch | null => {
      for (let i = 0; i < list.length; i++) if (list[i].identifier === id) return list[i];
      return null;
    };
    const release = () => { active = null; };
    const onStart = (e: TouchEvent) => {
      if (e.touches.length > 1) { // a second finger is a pinch: hand the gesture back to the chart
        if (active !== null) { engine.cancel(); release(); }
        return;
      }
      const t = e.changedTouches[0];
      if (!t || !engine.pointerDown(rel(t), true)) return;
      active = t.identifier;
      e.stopPropagation();
      if (e.cancelable) e.preventDefault();
      keyTarget?.focus({ preventScroll: true });
    };
    const onMove = (e: TouchEvent) => {
      if (active === null) return;
      const t = find(e.changedTouches, active);
      if (!t) return;
      e.stopPropagation();
      if (e.cancelable) e.preventDefault();
      engine.pointerMove(rel(t));
    };
    const onEnd = (e: TouchEvent) => {
      if (active === null) return;
      const t = find(e.changedTouches, active);
      if (!t) return;
      e.stopPropagation();
      if (e.cancelable) e.preventDefault();
      engine.pointerUp(rel(t));
      release();
    };
    const onCancel = () => { if (active !== null) { engine.cancel(); release(); } };
    const opts = { capture: true, passive: false } as const;
    el.addEventListener("touchstart", onStart, opts);
    el.addEventListener("touchmove", onMove, opts);
    el.addEventListener("touchend", onEnd, opts);
    el.addEventListener("touchcancel", onCancel, opts);
    let removed = false;
    const remove = () => {
      if (removed) return;
      removed = true;
      el.removeEventListener("touchstart", onStart, opts);
      el.removeEventListener("touchmove", onMove, opts);
      el.removeEventListener("touchend", onEnd, opts);
      el.removeEventListener("touchcancel", onCancel, opts);
    };
    const off = handle.onDispose(remove);
    return () => { off(); remove(); };
  }, [handle, interactive, engine, keyTarget]);

  // Keyboard: only the keys the engine uses are taken, and never while typing in a field.
  useEffect(() => {
    if (!keyTarget || !interactive) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return;
      if (engine.keyDown({ key: e.key, ctrlKey: e.ctrlKey, metaKey: e.metaKey, shiftKey: e.shiftKey })) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    keyTarget.addEventListener("keydown", onKey);
    return () => keyTarget.removeEventListener("keydown", onKey);
  }, [keyTarget, interactive, engine]);

  return { engine, state };
}
