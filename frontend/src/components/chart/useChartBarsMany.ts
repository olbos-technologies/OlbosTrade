/**
 * Bars for several (symbol, interval, range) charts at once, each fetched on its own.
 *
 * - Each chart's request is independent: one that fails says so for that chart only, and never
 *   draws a made-up series.
 * - A newly requested chart is fetched fresh (a symbol change refetches all of them; an interval
 *   change refetches only that chart), and a slower, older answer never overwrites the newest pick.
 * - Two charts asking for the same thing share one request.
 *
 * It uses the same endpoint, with the same parameters, as the Chart page.
 */
import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { ChartBar } from "../CandlestickChart";
import { clampNote, INTERVAL_OPTIONS, type IntervalKey, type RangeKey } from "./timeframes";

export interface ChartSpec { symbol: string; interval: IntervalKey; range: RangeKey }
export interface ChartBarsState { bars: ChartBar[]; loading: boolean; error: string | null; note: string | null }

const keyOf = (s: ChartSpec) => `${s.symbol}|${s.interval}|${s.range}`;
const PENDING: ChartBarsState = { bars: [], loading: true, error: null, note: null };

export function useChartBarsMany(specs: ChartSpec[]): ChartBarsState[] {
  const [store, setStore] = useState<Record<string, ChartBarsState>>({});
  const wanted = useRef<Set<string>>(new Set());
  const inflight = useRef<Set<string>>(new Set());
  // Charts that have an answer (data or an error) and are still on screen: not fetched again.
  const answered = useRef<Set<string>>(new Set());
  const signature = specs.map(keyOf).join(",");

  useEffect(() => {
    const keys = new Set(specs.map(keyOf));
    wanted.current = keys;
    for (const k of [...answered.current]) if (!keys.has(k)) answered.current.delete(k);
    // Forget what is no longer on screen; what is still wanted keeps its answer.
    setStore((cur) => {
      const next: Record<string, ChartBarsState> = {};
      for (const k of keys) if (cur[k]) next[k] = cur[k];
      return next;
    });
    for (const spec of specs) {
      const k = keyOf(spec);
      if (inflight.current.has(k) || answered.current.has(k)) continue;
      inflight.current.add(k);
      setStore((cur) => (cur[k] ? cur : { ...cur, [k]: PENDING }));
      (api.getEquityChart(spec.symbol, { timeframe: spec.interval, range: spec.range, limit: 5000 }) as Promise<any>)
        .then((res) => {
          const title = INTERVAL_OPTIONS.find((o) => o.key === spec.interval)?.title ?? String(spec.interval);
          const done: ChartBarsState = {
            bars: res?.bars || [], loading: false, error: null,
            note: res?.range_clamped ? clampNote(title, res.period) : null,
          };
          if (wanted.current.has(k)) { answered.current.add(k); setStore((cur) => ({ ...cur, [k]: done })); }
        })
        .catch((e: Error) => {
          if (wanted.current.has(k)) {
            answered.current.add(k);
            setStore((cur) => ({ ...cur, [k]: { bars: [], loading: false, error: e?.message || "Chart data unavailable", note: null } }));
          }
        })
        .finally(() => { inflight.current.delete(k); });
    }
    // `specs` is rebuilt every render; its content is what matters, and that is `signature`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  return specs.map((s) => store[keyOf(s)] ?? PENDING);
}
