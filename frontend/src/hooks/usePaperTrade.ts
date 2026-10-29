import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";

export function usePaperTrade() {
  const [positions, setPositions]   = useState<any[]>([]);
  const [portfolio, setPortfolio]   = useState<any>(null);
  const [greeks, setGreeks]         = useState<any>(null);
  const [lastSignal, setLastSignal] = useState<any>(null);
  const [cycleLog, setCycleLog]     = useState<any[]>([]);
  const [loading, setLoading]       = useState(true);
  // Distinguishes "portfolio is genuinely unfetched/failed" from "not yet
  // loaded" — a fetch failure must never be indistinguishable from a
  // legitimate absence of data, so callers can show an explicit
  // unavailable state instead of a stale or fabricated value.
  const [portfolioError, setPortfolioError] = useState(false);
  const [positionsError, setPositionsError] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [p, port, g, hist] = await Promise.allSettled([
        api.getPositions() as Promise<any>,
        api.getPortfolio() as Promise<any>,
        api.getGreeksSummary() as Promise<any>,
        api.getTradeHistory() as Promise<any>,
      ]);
      if (p.status === "fulfilled" && Array.isArray(p.value?.positions)) {
        setPositions(p.value.positions);
        setPositionsError(false);
      } else {
        setPositionsError(true);
      }
      if (port.status === "fulfilled" && port.value?.portfolio) {
        setPortfolio(port.value.portfolio);
        setPortfolioError(false);
      } else {
        setPortfolioError(true);
      }
      if (g.status === "fulfilled") setGreeks(g.value);
      if (hist.status === "fulfilled") {
        const trades = hist.value?.trades ?? [];
        setCycleLog(trades);
        if (trades.length > 0) setLastSignal(trades[0]);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const runCycle = useCallback(async () => {
    setLoading(true);
    try {
      // Trigger a manual signal cycle via the paper-trade toggle endpoint
      await (api as any).runSignalCycle?.();
      await refresh();
    } catch {
      await refresh();
    }
  }, [refresh]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000); // re-poll every 30s
    return () => clearInterval(interval);
  }, [refresh]);

  return { positions, portfolio, portfolioError, positionsError, greeks, lastSignal, cycleLog, loading, refresh, runCycle };
}
