import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { WatchRow } from "../components/chart/WatchlistTable";
import { normalizeSymbol } from "./layout";

/**
 * The same watchlist the Chart page uses (same storage key), so a symbol added
 * in one place is there in the other. A symbol whose price cannot be fetched
 * shows "—", never 0 (AGENTS.md rule 5).
 */
export const WATCHLIST_STORAGE_KEY = "olbos.chart.watchlist";
export const DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "SPY", "QQQ", "IWM"];
const MAX_SYMBOLS = 60;

export function readWatchlist(storage?: Pick<Storage, "getItem"> | null): string[] {
  try {
    const raw = storage?.getItem(WATCHLIST_STORAGE_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) {
        const clean = parsed.map((s) => (typeof s === "string" ? normalizeSymbol(s) : null)).filter((s): s is string => !!s);
        if (clean.length) return Array.from(new Set(clean)).slice(0, MAX_SYMBOLS);
      }
    }
  } catch { /* malformed storage: use the default */ }
  return DEFAULT_WATCHLIST;
}

function browserStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

export function useWatchlist() {
  const [symbols, setSymbols] = useState<string[]>(() => readWatchlist(browserStorage()));
  const [snapshots, setSnapshots] = useState<Record<string, WatchRow | undefined>>({});

  const persist = useCallback((next: string[]) => {
    setSymbols(next);
    try { browserStorage()?.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(next)); } catch { /* in-memory still updates */ }
  }, []);

  const add = useCallback((input: string): boolean => {
    const s = normalizeSymbol(input);
    if (!s) return false;
    setSymbols((cur) => {
      if (cur.includes(s) || cur.length >= MAX_SYMBOLS) return cur;
      const next = [...cur, s];
      try { browserStorage()?.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
    return true;
  }, []);

  const remove = useCallback((s: string) => persist(symbols.filter((t) => t !== s)), [persist, symbols]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const entries = await Promise.all(symbols.map(async (ticker) => {
        try {
          const snap = await (api.getSnapshot(ticker) as Promise<WatchRow>);
          return [ticker, { last_close: snap.last_close, change_pct: snap.change_pct }] as const;
        } catch {
          return [ticker, { last_close: null, change_pct: null }] as const;
        }
      }));
      if (alive) setSnapshots(Object.fromEntries(entries));
    };
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [symbols]);

  return { symbols, snapshots, add, remove };
}
