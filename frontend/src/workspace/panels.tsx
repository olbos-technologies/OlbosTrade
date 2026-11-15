/**
 * What can sit in a workspace slot. Each panel reuses the component the
 * terminal already has for that job; nothing here re-implements a data source.
 * Every panel is wrapped in an error boundary, so one failing panel shows its
 * own message and leaves the rest of the workspace working.
 */

import React, { useState } from "react";
import ErrorBoundary from "../components/ErrorBoundary";
import PositionsTable from "../components/chart/PositionsTable";
import WatchlistTable from "../components/chart/WatchlistTable";
import SetupScannerPanel from "../components/SetupScannerPanel";
import BacktestCenter from "../pages/BacktestCenter";
import NewsCatalysts from "../pages/markets/NewsCatalysts";
import OptionsFlow from "../pages/OptionsFlow";
import ScanCenter from "../pages/ScanCenter";
import { usePaperTrade } from "../hooks/usePaperTrade";
import { PANEL_TITLES, type PanelId } from "./layout";
import { useWatchlist } from "./useWatchlist";

function WatchlistPanel({ symbol, onSelect }: { symbol: string; onSelect: (s: string) => void }) {
  const { symbols, snapshots, add, remove } = useWatchlist();
  const [draft, setDraft] = useState("");
  const [bad, setBad] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    const ok = add(draft);
    setBad(!ok);
    if (ok) setDraft("");
  };
  return (
    <div className="ws-watchlist">
      <form className="ws-addsymbol" onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => { setDraft(e.target.value); setBad(false); }}
          placeholder="Add symbol"
          aria-label="Add a symbol to the watchlist"
          aria-invalid={bad}
          maxLength={20}
          autoCapitalize="characters"
          spellCheck={false}
        />
        <button type="submit">Add</button>
      </form>
      {bad && <p className="ws-note ws-note--warn" role="alert">That is not a valid symbol.</p>}
      <WatchlistTable watchlist={symbols} snapshots={snapshots} symbol={symbol} onSelect={onSelect} onRemove={remove} />
    </div>
  );
}

function PositionsPanel() {
  const { positions, positionsError } = usePaperTrade();
  return <PositionsTable positions={positions} error={positionsError} narrow />;
}

export function renderPanel(id: PanelId, ctx: { symbol: string; onSelect: (s: string) => void }): React.ReactNode {
  let body: React.ReactNode;
  switch (id) {
    case "watchlist": body = <WatchlistPanel symbol={ctx.symbol} onSelect={ctx.onSelect} />; break;
    case "positions": body = <PositionsPanel />; break;
    case "flow": body = <OptionsFlow />; break;
    case "news": body = <NewsCatalysts />; break;
    case "scanner": body = <ScanCenter />; break;
    case "setups": body = <SetupScannerPanel />; break;
    case "backtest": body = <BacktestCenter />; break;
  }
  return <ErrorBoundary label={PANEL_TITLES[id]}>{body}</ErrorBoundary>;
}
