/**
 * Active trading-style floor (frequency-controller min confidence).
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

export type TradingStyleKey = "conservative" | "balanced" | "aggressive" | "scalper";

const FALLBACK_FLOOR: Record<TradingStyleKey, number> = {
  conservative: 0.9,
  balanced: 0.9,
  aggressive: 0.7,
  scalper: 0.6,
};

export function floorForStyle(mode: string | null | undefined): number {
  const key = (mode || "balanced").toLowerCase() as TradingStyleKey;
  return FALLBACK_FLOOR[key] ?? 0.9;
}

export function formatConfidenceFloor(
  confidence: number | null | undefined,
  minConfidence: number,
): { text: string; passes: boolean | null } {
  if (typeof confidence !== "number" || Number.isNaN(confidence)) {
    return { text: `need score ${(minConfidence * 100).toFixed(0)}`, passes: null };
  }
  const pct = Math.round(confidence * 100);
  const need = Math.round(minConfidence * 100);
  const passes = confidence + 1e-9 >= minConfidence;
  return { text: `score ${pct} · need ${need}`, passes };
}

export function useTradingStyleFloor() {
  const [mode, setMode] = useState<TradingStyleKey>("balanced");
  const [minConfidence, setMinConfidence] = useState(0.9);
  const [hardMaxPerDay, setHardMaxPerDay] = useState<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const [current, all] = await Promise.all([
        api.getCurrentMode() as Promise<{ mode?: string }>,
        api.getAllModes() as Promise<Record<string, { min_confidence?: number; hard_max_per_day?: number; is_active?: boolean }>>,
      ]);
      const active = (current.mode || "balanced").toLowerCase() as TradingStyleKey;
      setMode(active);
      const info = all?.[active];
      if (typeof info?.min_confidence === "number") {
        setMinConfidence(info.min_confidence);
      } else {
        setMinConfidence(floorForStyle(active));
      }
      setHardMaxPerDay(
        typeof info?.hard_max_per_day === "number" ? info.hard_max_per_day : null,
      );
    } catch {
      /* keep last known floor */
    }
  }, []);

  useEffect(() => {
    reload();
    const id = setInterval(reload, 15000);
    return () => clearInterval(id);
  }, [reload]);

  return { mode, minConfidence, hardMaxPerDay, reload, setMode };
}
