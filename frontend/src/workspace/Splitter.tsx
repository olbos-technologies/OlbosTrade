/**
 * A draggable, keyboard-operable divider between two regions.
 *
 * It is a WAI-ARIA window splitter: role="separator" with a value, focusable,
 * moved by the arrow keys (Shift for a larger step), reset by double-click or
 * Enter. Drag uses pointer capture, so it works with mouse, pen and touch and
 * keeps tracking when the pointer leaves the thin handle.
 *
 * The splitter does not know about layout. It reports a new fraction through
 * `onChange`; the caller clamps it (see layout.ts) and decides what it means.
 */

import React, { useRef } from "react";
import { KEY_STEP, KEY_STEP_LARGE, fracAfterDrag, type Frac, clampFrac } from "./layout";

export interface SplitterProps {
  orientation: "vertical" | "horizontal";
  /** What this divider resizes, for the screen-reader label. */
  label: string;
  which: Frac;
  /** The current fraction. */
  value: number;
  /** The region's length in px along the drag axis, read at drag start. */
  getTotalPx: () => number;
  onChange: (next: number) => void;
  onReset: () => void;
}

export default function Splitter({ orientation, label, which, value, getTotalPx, onChange, onReset }: SplitterProps) {
  const drag = useRef<{ start: number; origin: number; total: number } | null>(null);
  const vertical = orientation === "vertical"; // a vertical bar moves left/right

  const down = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { start: value, origin: vertical ? e.clientX : e.clientY, total: getTotalPx() };
    e.preventDefault();
  };
  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const now = vertical ? e.clientX : e.clientY;
    onChange(fracAfterDrag(d.start, now - d.origin, d.total, which));
  };
  const up = (e: React.PointerEvent<HTMLDivElement>) => {
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  };

  const key = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
    const less = vertical ? "ArrowLeft" : "ArrowUp";
    const more = vertical ? "ArrowRight" : "ArrowDown";
    if (e.key === less) onChange(clampFrac(which, value - step));
    else if (e.key === more) onChange(clampFrac(which, value + step));
    else if (e.key === "Enter" || e.key === "Home") onReset();
    else return;
    e.preventDefault();
  };

  return (
    <div
      className={`ws-splitter ws-splitter--${orientation}`}
      role="separator"
      aria-orientation={orientation}
      aria-label={label}
      aria-valuemin={Math.round(clampFrac(which, 0) * 100)}
      aria-valuemax={Math.round(clampFrac(which, 1) * 100)}
      aria-valuenow={Math.round(value * 100)}
      tabIndex={0}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onKeyDown={key}
      onDoubleClick={onReset}
      title={`${label}: drag, or use the arrow keys. Double-click to reset.`}
    />
  );
}
