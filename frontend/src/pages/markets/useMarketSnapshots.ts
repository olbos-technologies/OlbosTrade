import { useCallback, useEffect, useState } from "react";
import { UNIVERSE, Snap, fetchSnap } from "./universe";

export function useMarketSnapshots() {
  const [snaps, setSnaps] = useState<Record<string, Snap>>({});
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    let alive = true, busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      const pairs = await Promise.all(UNIVERSE.flatMap(g => g.symbols.map(async s =>
        [s.sym, await fetchSnap(s.api ?? s.sym)] as const)));
      if (alive) { setSnaps(Object.fromEntries(pairs)); setUpdatedAt(Date.now()); setLoading(false); }
      busy = false;
    };
    load();
    const t = setInterval(load, 60000);
    const clock = setInterval(() => setNow(Date.now()), 5000);
    return () => { alive = false; clearInterval(t); clearInterval(clock); };
  }, [revision]);
  const stale = updatedAt !== null && now - updatedAt > 120000;
  const failed = Object.values(snaps).filter(s => s.last_close === null).length;
  return { snaps, loading, updatedAt, stale, failed, refresh: useCallback(() => setRevision(n => n + 1), []) };
}
