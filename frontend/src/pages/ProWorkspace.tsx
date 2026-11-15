/**
 * Pro Workspace: a dockable, resizable multi-panel view of the terminal.
 *
 * A chart in the middle, a side column and a bottom dock. Each slot holds one
 * panel the terminal already has (watchlist, positions, unusual options, news,
 * scanners, backtest), chosen from a menu. Splitters resize the regions by mouse,
 * touch or keyboard, and the layout is remembered in this browser.
 *
 * It is an ADDITIONAL page. Every existing page is unchanged, and the same
 * workspace serves every brand. It is presentation only: it never alters the
 * execution mode, risk limits or account in use, and it places no orders.
 *
 * On a phone (900px and under) the regions stack into one panel at a time,
 * chosen from a strip, because three resizable regions do not fit.
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import ErrorBoundary from "../components/ErrorBoundary";
import ChartPanel from "../workspace/ChartPanel";
import Splitter from "../workspace/Splitter";
import { renderPanel } from "../workspace/panels";
import {
  DEFAULT_LAYOUT, PANEL_IDS, PANEL_TITLES, PRESET_IDS, PRESET_TITLES, loadLayout, normalizeSymbol,
  presetLayout, saveLayout, type DockLayout, type Frac, type PanelId, type PresetId,
} from "../workspace/layout";
import "../workspace/workspace.css";

function initialSymbol(): string {
  try {
    const q = new URLSearchParams(window.location.search).get("symbol");
    return (q && normalizeSymbol(q)) || "AAPL";
  } catch { return "AAPL"; }
}

function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

function Slot({ label, value, onPick, children }: {
  label: string; value: PanelId; onPick: (id: PanelId) => void; children: React.ReactNode;
}) {
  return (
    <section className="ws-slot" aria-label={PANEL_TITLES[value]}>
      <header className="ws-slot__head">
        <select
          className="ws-slot__pick"
          aria-label={`Panel shown in the ${label}`}
          value={value}
          onChange={(e) => onPick(e.target.value as PanelId)}
        >
          {PANEL_IDS.map((id) => <option key={id} value={id}>{PANEL_TITLES[id]}</option>)}
        </select>
      </header>
      <div className="ws-slot__body">{children}</div>
    </section>
  );
}

export default function ProWorkspace() {
  const isMobile = useIsMobile(900);
  const [layout, setLayout] = useState<DockLayout>(() => loadLayout(browserStorage()));
  const [symbol, setSymbol] = useState<string>(initialSymbol);
  const [draft, setDraft] = useState("");
  const [badSymbol, setBadSymbol] = useState(false);
  const [mobilePane, setMobilePane] = useState<"chart" | PanelId>("chart");

  useEffect(() => { saveLayout(browserStorage(), layout); }, [layout]);

  const set = (patch: Partial<DockLayout>) => setLayout((cur) => ({ ...cur, ...patch }));
  const setFrac = (which: Frac) => (next: number) => set({ [which]: next } as Partial<DockLayout>);
  const resetFrac = (which: Frac) => () => set({ [which]: DEFAULT_LAYOUT[which] } as Partial<DockLayout>);

  const topRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLDivElement>(null);
  const sideRef = useRef<HTMLDivElement>(null);

  const go = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    const s = normalizeSymbol(draft);
    setBadSymbol(!s);
    if (s) { setSymbol(s); setDraft(""); }
  };

  const ctx = useMemo(() => ({ symbol, onSelect: setSymbol }), [symbol]);

  const toolbar = (
    <div className="ws-toolbar" role="toolbar" aria-label="Workspace controls">
      <form className="ws-symbolform" onSubmit={go}>
        <label className="ws-symbolform__label" htmlFor="ws-symbol">Symbol</label>
        <input
          id="ws-symbol"
          value={draft}
          onChange={(e) => { setDraft(e.target.value); setBadSymbol(false); }}
          placeholder={symbol}
          aria-invalid={badSymbol}
          maxLength={20}
          autoCapitalize="characters"
          spellCheck={false}
        />
        <button type="submit">Go</button>
        {badSymbol && <span className="ws-note ws-note--warn" role="alert">Not a valid symbol</span>}
      </form>
      {!isMobile && (
        <>
          <div className="ws-toolbar__group" role="group" aria-label="Layout presets">
            {PRESET_IDS.map((id: PresetId) => (
              <button key={id} type="button" onClick={() => setLayout(presetLayout(id))}>{PRESET_TITLES[id]}</button>
            ))}
          </div>
          <div className="ws-toolbar__group" role="group" aria-label="Show or hide regions">
            <button type="button" aria-pressed={layout.sideOpen} onClick={() => set({ sideOpen: !layout.sideOpen })}>Side panels</button>
            <button type="button" aria-pressed={layout.bottomOpen} onClick={() => set({ bottomOpen: !layout.bottomOpen })}>Bottom dock</button>
            <button type="button" onClick={() => setLayout({ ...DEFAULT_LAYOUT })}>Reset layout</button>
          </div>
        </>
      )}
    </div>
  );

  if (isMobile) {
    return (
      <div className="ws-root ws-root--stacked">
        {toolbar}
        <div className="ws-mobilestrip" role="group" aria-label="Workspace panels">
          <button type="button" aria-pressed={mobilePane === "chart"} onClick={() => setMobilePane("chart")}>Chart</button>
          {PANEL_IDS.map((id) => (
            <button key={id} type="button" aria-pressed={mobilePane === id} onClick={() => setMobilePane(id)}>{PANEL_TITLES[id]}</button>
          ))}
        </div>
        <section className="ws-slot ws-slot--mobile" aria-label={mobilePane === "chart" ? "Chart" : PANEL_TITLES[mobilePane]}>
          <div className="ws-slot__body">
            {mobilePane === "chart"
              ? <div className="ws-mobilechart"><ErrorBoundary label="Chart"><ChartPanel symbol={symbol} /></ErrorBoundary></div>
              : renderPanel(mobilePane, ctx)}
          </div>
        </section>
      </div>
    );
  }

  const { sideOpen, bottomOpen, chartFrac, topFrac, sideSplit } = layout;
  const topCols = sideOpen ? `minmax(0, ${chartFrac}fr) 6px minmax(0, ${1 - chartFrac}fr)` : "minmax(0, 1fr)";
  const mainRows = bottomOpen ? `minmax(0, ${topFrac}fr) 6px minmax(0, ${1 - topFrac}fr)` : "minmax(0, 1fr)";
  const sideRows = `minmax(0, ${sideSplit}fr) 6px minmax(0, ${1 - sideSplit}fr)`;

  return (
    <div className="ws-root">
      {toolbar}
      <div className="ws-main" ref={mainRef} style={{ gridTemplateRows: mainRows }}>
        <div className="ws-top" ref={topRef} style={{ gridTemplateColumns: topCols }}>
          <section className="ws-slot ws-slot--chart" aria-label="Chart">
            <div className="ws-slot__body">
              <ErrorBoundary label="Chart"><ChartPanel symbol={symbol} /></ErrorBoundary>
            </div>
          </section>
          {sideOpen && (
            <>
              <Splitter orientation="vertical" label="Resize chart and side panels" which="chartFrac" value={chartFrac}
                getTotalPx={() => topRef.current?.clientWidth ?? 0} onChange={setFrac("chartFrac")} onReset={resetFrac("chartFrac")} />
              <div className="ws-side" ref={sideRef} style={{ gridTemplateRows: sideRows }}>
                <Slot label="top side slot" value={layout.sideTop} onPick={(id) => set({ sideTop: id })}>
                  {renderPanel(layout.sideTop, ctx)}
                </Slot>
                <Splitter orientation="horizontal" label="Resize the two side panels" which="sideSplit" value={sideSplit}
                  getTotalPx={() => sideRef.current?.clientHeight ?? 0} onChange={setFrac("sideSplit")} onReset={resetFrac("sideSplit")} />
                <Slot label="bottom side slot" value={layout.sideBottom} onPick={(id) => set({ sideBottom: id })}>
                  {renderPanel(layout.sideBottom, ctx)}
                </Slot>
              </div>
            </>
          )}
        </div>
        {bottomOpen && (
          <>
            <Splitter orientation="horizontal" label="Resize chart area and bottom dock" which="topFrac" value={topFrac}
              getTotalPx={() => mainRef.current?.clientHeight ?? 0} onChange={setFrac("topFrac")} onReset={resetFrac("topFrac")} />
            <Slot label="bottom dock" value={layout.bottom} onPick={(id) => set({ bottom: id })}>
              {renderPanel(layout.bottom, ctx)}
            </Slot>
          </>
        )}
      </div>
    </div>
  );
}
