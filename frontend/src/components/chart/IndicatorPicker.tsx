/**
 * The "Indicators" button and the panel it opens: which overlays and panels the chart draws.
 *
 * The panel is rendered in a portal and positioned with `position: fixed`, so the chart toolbar (which
 * scrolls sideways on a phone and would clip anything inside it) cannot cut it off. On a phone it is a
 * sheet above the bottom navigation. It is a dialog that is not modal: Escape or a click outside closes
 * it, Tab stays inside it while it is open, and focus returns to the button.
 *
 * It only chooses. What each choice draws is `indicatorSeries.ts`; what it is called and computed as is
 * `indicators.ts`.
 */
import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { INDICATORS, MAX_INDICATORS, type IndicatorDef, type IndicatorId } from "./indicators";
import "./IndicatorPicker.css";

const FOCUSABLE = 'input:not([disabled]), button:not([disabled])';

function Swatch({ def }: { def: IndicatorDef }) {
  return (
    <span
      aria-hidden="true"
      className={`indicator-picker__swatch indicator-picker__swatch--${def.swatch}`}
      style={def.swatch === "dashed" ? { borderTopColor: def.color } : { background: def.color }}
    />
  );
}

export default function IndicatorPicker({
  selected, onChange, onReset, buttonClassName,
}: {
  selected: readonly IndicatorId[];
  onChange: (next: IndicatorId[]) => void;
  onReset: () => void;
  /** The button takes the look of whichever toolbar it sits in. */
  buttonClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const uid = useId();
  const atCap = selected.length >= MAX_INDICATORS;

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) button.current?.focus();
  }, []);

  const toggleOpen = () => {
    if (open) { close(false); return; }
    const r = button.current?.getBoundingClientRect();
    setAnchor(r ? { top: Math.round(r.bottom + 6), right: Math.max(8, Math.round(window.innerWidth - r.right)) } : null);
    setOpen(true);
  };

  // While open: Escape closes, a press outside closes, a resize closes (the anchor would be stale).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); close(true); return; }
      if (e.key !== "Tab" || !panel.current) return;
      const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) return;
      const first = items[0];
      const lastItem = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panel.current)) { e.preventDefault(); lastItem.focus(); }
      else if (!e.shiftKey && active === lastItem) { e.preventDefault(); first.focus(); }
    };
    const onPointer = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panel.current?.contains(t) || button.current?.contains(t)) return;
      close(false);
    };
    const onResize = () => close(false);
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onPointer, true);
    window.addEventListener("resize", onResize);
    panel.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onPointer, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open, close]);

  const toggle = (id: IndicatorId) => {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  };

  const group = (title: string, defs: IndicatorDef[]) => (
    <fieldset className="indicator-picker__group">
      <legend>{title}</legend>
      <ul>
        {defs.map((def) => {
          const on = selected.includes(def.id);
          const locked = !on && atCap;
          return (
            <li key={def.id} className={`indicator-picker__row${locked ? " indicator-picker__row--locked" : ""}`}>
              <label>
                <input
                  type="checkbox"
                  checked={on}
                  disabled={locked}
                  aria-describedby={`${uid}-${def.id}`}
                  onChange={() => toggle(def.id)}
                />
                <Swatch def={def} />
                <span className="indicator-picker__name">{def.label}</span>
              </label>
              <span className="indicator-picker__short" aria-hidden="true" title={def.description}>{def.short}</span>
              <span id={`${uid}-${def.id}`} className="indicator-picker__desc">{def.description}</span>
            </li>
          );
        })}
      </ul>
    </fieldset>
  );

  return (
    <>
      <button
        ref={button}
        type="button"
        className={buttonClassName}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Choose the indicators drawn on the chart"
        onClick={toggleOpen}
      >
        Indicators
      </button>
      {open && createPortal(
        <div
          ref={panel}
          role="dialog"
          aria-label="Chart indicators"
          tabIndex={-1}
          className="indicator-picker__panel"
          style={anchor ? ({ "--ip-top": `${anchor.top}px`, "--ip-right": `${anchor.right}px` } as React.CSSProperties) : undefined}
        >
          <div className="indicator-picker__head">
            <strong>Indicators</strong>
            <span aria-live="polite">{selected.length} of {MAX_INDICATORS}</span>
          </div>
          {group("On the price chart", INDICATORS.filter((d) => d.group === "overlay"))}
          {group("Panels below", INDICATORS.filter((d) => d.group === "panel"))}
          {atCap && <p role="status" className="indicator-picker__cap">Most {MAX_INDICATORS} at once. Switch one off to add another.</p>}
          <p className="indicator-picker__note">Indicators describe past prices. They are not signals.</p>
          <div className="indicator-picker__foot">
            <button type="button" onClick={onReset}>Reset to default</button>
            <button type="button" onClick={() => close(true)}>Done</button>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
