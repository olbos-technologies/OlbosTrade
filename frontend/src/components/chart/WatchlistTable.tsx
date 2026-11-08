import { useState } from "react";
import { pnlColor } from "../../utils/pnlTone";

export type WatchRow = { last_close: number | null; change_pct: number | null };

const price = (n: number | null | undefined) =>
  typeof n === "number" && Number.isFinite(n) ? n.toFixed(2) : "—";
const change = (n: number | null | undefined) =>
  typeof n === "number" && Number.isFinite(n) ? `${n < 0 ? "−" : n > 0 ? "+" : ""}${Math.abs(n).toFixed(2)}%` : "—";

/** Dense watchlist: one 28px row per symbol. A missing price shows "—", never 0. */
export default function WatchlistTable({
  watchlist, snapshots, symbol, onSelect, onAdd, onRemove,
}: {
  watchlist: string[];
  snapshots: Record<string, WatchRow | undefined>;
  symbol: string;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => void;
  onRemove: (ticker: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const t = draft.trim().toUpperCase();
    if (t) onAdd(t);
    setDraft("");
  };
  return (
    <div className="dock-table-wrap">
      <div className="dock-add">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") add(); }}
          placeholder="Add ticker…"
          aria-label="Add ticker to Market Watch"
        />
        <button type="button" className="btn-t btn-t--flat" onClick={add} aria-label="Add ticker">+</button>
      </div>
      <table className="dock-table" aria-label="Watchlist">
        <thead><tr><th>Symbol</th><th>Last</th><th>Chg</th><th aria-label="Remove" /></tr></thead>
        <tbody>
          {watchlist.map((ticker) => {
            const snap = snapshots[ticker];
            const on = ticker === symbol;
            return (
              <tr key={ticker} className={on ? "dock-row--current" : undefined}>
                <td>
                  <button type="button" className="dock-link" aria-pressed={on} onClick={() => onSelect(ticker)}>{ticker}</button>
                </td>
                <td className="tnum">{price(snap?.last_close)}</td>
                <td className="tnum" style={{ color: snap?.change_pct == null ? "var(--ink-faint)" : pnlColor(snap.change_pct) }}>{change(snap?.change_pct)}</td>
                <td>
                  <button type="button" className="dock-x" aria-label={`Remove ${ticker} from Market Watch`} onClick={() => onRemove(ticker)}>×</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
