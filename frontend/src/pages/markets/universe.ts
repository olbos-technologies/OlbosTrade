/**
 * Shared market universe for the Markets module (Heatmaps, Watchlists).
 * Grouped so views can render sectioned tables/grids without extra config.
 */
export type Snap = { last_close: number | null; prev_close: number | null; change_pct: number | null };

export interface UniverseGroup {
  label: string;
  symbols: { sym: string; name: string; api?: string }[];
}

export const UNIVERSE: UniverseGroup[] = [
  {
    label: "Index & Broad",
    symbols: [
      { sym: "SPY", name: "S&P 500 ETF" },
      { sym: "QQQ", name: "Nasdaq 100 ETF" },
      { sym: "IWM", name: "Russell 2000 ETF" },
      { sym: "DIA", name: "Dow 30 ETF" },
    ],
  },
  {
    label: "Mega-cap Tech",
    symbols: [
      { sym: "NVDA", name: "NVIDIA" },
      { sym: "AAPL", name: "Apple" },
      { sym: "MSFT", name: "Microsoft" },
      { sym: "AMZN", name: "Amazon" },
      { sym: "META", name: "Meta" },
      { sym: "GOOGL", name: "Alphabet" },
      { sym: "TSLA", name: "Tesla" },
    ],
  },
  {
    label: "Rates, Metals & Energy",
    symbols: [
      { sym: "TLT", name: "20Y Treasury ETF" },
      { sym: "GLD", name: "Gold ETF" },
      { sym: "SLV", name: "Silver ETF" },
      { sym: "USO", name: "Oil futures fund" },
    ],
  },
  {
    label: "Volatility & Dollar",
    symbols: [
      { sym: "DXY", name: "Dollar Index", api: "DX-Y.NYB" },
    ],
  },
];

/** Fetch one snapshot; resolves to a Snap (nulls on failure). */
export function fetchSnap(apiSym: string): Promise<Snap> {
  return fetch(`/api/market/snapshot/${encodeURIComponent(apiSym)}`, { signal: AbortSignal.timeout(12000) })
    .then(r => { if (!r.ok) throw new Error("Snapshot unavailable"); return r.json(); })
    .then(d => {
      const numeric = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v : null;
      return { last_close: numeric(d.last_close), prev_close: numeric(d.prev_close), change_pct: numeric(d.change_pct) };
    })
    .catch(() => ({ last_close: null, prev_close: null, change_pct: null }));
}

/** Color for a % change, mapped onto the terminal palette. */
export function changeColor(pct: number | null): string {
  if (pct === null) return "var(--ink-faint)";
  if (pct >= 1.5) return "var(--green)";
  if (pct > 0) return "var(--green-dim, var(--green))";
  if (pct <= -1.5) return "var(--red)";
  if (pct < 0) return "var(--red-dim, var(--red))";
  return "var(--ink-dim)";
}
