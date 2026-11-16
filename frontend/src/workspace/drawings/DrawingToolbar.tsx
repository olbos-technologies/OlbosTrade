/**
 * The drawing tools' rail: pick a tool, a colour and a line width, turn the magnet
 * on, and undo, redo, delete or clear. Every control has a name and a pressed
 * state for assistive technology.
 *
 * Colour and width live behind a "Style" button that opens them in place. They are
 * used far less often than the tools, and keeping them folded is what lets the
 * whole rail fit on a short laptop window without scrolling.
 */

import React, { useState } from "react";
import type { DrawingEngine, EngineState, Tool } from "./engine";
import "./DrawingToolbar.css";
import { KIND_TITLES, LINE_WIDTHS, PALETTE, type DrawingKind } from "./model";

type IconProps = { children: React.ReactNode };
const Icon = ({ children }: IconProps) => (
  <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);

const TOOL_ICONS: Record<Tool, React.ReactNode> = {
  select: <Icon><path d="M4 3l9 4.5-3.8 1.2L7.6 12.6z" /></Icon>,
  trend: <Icon><path d="M3 14L15 4" /><circle cx="3" cy="14" r="1.4" /><circle cx="15" cy="4" r="1.4" /></Icon>,
  hline: <Icon><path d="M2 9h14" /><circle cx="9" cy="9" r="1.4" /></Icon>,
  vline: <Icon><path d="M9 2v14" /><circle cx="9" cy="9" r="1.4" /></Icon>,
  rect: <Icon><rect x="3" y="5" width="12" height="8" rx="1" /></Icon>,
  fib: <Icon><path d="M2 4h14M2 8h14M2 11h14M2 15h14" /></Icon>,
};

const TOOLS: { tool: Tool; title: string }[] = [
  { tool: "select", title: "Select and edit" },
  ...(["trend", "hline", "vline", "rect", "fib"] as DrawingKind[]).map((k) => ({ tool: k as Tool, title: KIND_TITLES[k] })),
];

export default function DrawingToolbar({ state, engine, orientation = "vertical" }: {
  state: EngineState;
  engine: DrawingEngine;
  /** "horizontal" is the finger-sized bar the Chart page shows on a phone. */
  orientation?: "vertical" | "horizontal";
}) {
  const hasSelection = state.selectedId !== null;
  const [styleOpen, setStyleOpen] = useState(false);
  return (
    <div className={`ws-drawtools${orientation === "horizontal" ? " ws-drawtools--bar" : ""}`} role="toolbar" aria-label="Drawing tools" aria-orientation={orientation}>
      <div className="ws-drawtools__group" role="group" aria-label="Tool">
        {TOOLS.map(({ tool, title }) => (
          <button key={tool} type="button" className="ws-drawtools__btn" aria-label={title} title={title}
            aria-pressed={state.tool === tool} onClick={() => engine.setTool(tool)}>
            {TOOL_ICONS[tool]}
          </button>
        ))}
        <button type="button" className="ws-drawtools__btn" aria-label="Magnet" aria-pressed={state.magnet}
          title="Magnet: snap new points to the nearest bar's open, high, low or close" onClick={() => engine.setMagnet(!state.magnet)}>
          <Icon><path d="M5 3v6a4 4 0 008 0V3M5 6h3M10 6h3" /></Icon>
        </button>
        <button type="button" className="ws-drawtools__btn" aria-label="Style" aria-expanded={styleOpen} aria-controls="ws-drawtools-style"
          title="Colour and line width" onClick={() => setStyleOpen((o) => !o)}>
          <span className="ws-drawtools__dot" style={{ background: state.color }} aria-hidden="true" />
        </button>
      </div>

      {styleOpen && (
        <div id="ws-drawtools-style" className="ws-drawtools__style">
          <div className="ws-drawtools__group ws-drawtools__swatches" role="group" aria-label="Colour">
            {PALETTE.map((c) => (
              <button key={c.value} type="button" className="ws-drawtools__swatch" aria-label={`${c.name} colour`} title={c.name}
                aria-pressed={state.color === c.value} style={{ background: c.value }} onClick={() => engine.setColor(c.value)} />
            ))}
          </div>
          <div className="ws-drawtools__group ws-drawtools__widths" role="group" aria-label="Line width">
            {LINE_WIDTHS.map((w) => (
              <button key={w} type="button" className="ws-drawtools__width" aria-label={`Line width ${w}`} title={`Line width ${w}`}
                aria-pressed={state.width === w} onClick={() => engine.setWidth(w)}>
                <span style={{ height: w + 1 }} />
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="ws-drawtools__group" role="group" aria-label="Edit">
        <button type="button" className="ws-drawtools__btn" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!state.canUndo} onClick={() => engine.undo()}>
          <Icon><path d="M6 5L3 8l3 3M3 8h7a4 4 0 010 8H8" /></Icon>
        </button>
        <button type="button" className="ws-drawtools__btn" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!state.canRedo} onClick={() => engine.redo()}>
          <Icon><path d="M12 5l3 3-3 3M15 8H8a4 4 0 000 8h2" /></Icon>
        </button>
        <button type="button" className="ws-drawtools__btn" aria-label="Delete selected drawing" title="Delete selected (Delete)" disabled={!hasSelection} onClick={() => engine.deleteSelected()}>
          <Icon><path d="M4 5h10M7 5V3.5h4V5M5.5 5l.6 9h5.8l.6-9" /></Icon>
        </button>
        <button type="button" className="ws-drawtools__btn" aria-label="Clear all drawings" title="Clear all drawings on this symbol (can be undone)" disabled={state.drawings.length === 0} onClick={() => engine.clearAll()}>
          <Icon><path d="M3 3l12 12M15 3L3 15" /></Icon>
        </button>
      </div>
    </div>
  );
}
