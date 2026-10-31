/**
 * Shared desk gate snapshot for Why-blocked chips.
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { useTradingStyleFloor } from "./useTradingStyleFloor";
import type { DeskBlockContext } from "../utils/signalBlockReason";

const GLOBAL_MAX_FALLBACK = 5;

export function useDeskBlockContext(): DeskBlockContext & { reload: () => void; guardrailAvailable: boolean; killStatusAvailable: boolean } {
  const { minConfidence, mode } = useTradingStyleFloor();
  const [guardrailAvailable, setGuardrailAvailable] = useState(false);
  const [killStatusAvailable, setKillStatusAvailable] = useState(false);
  const [ctx, setCtx] = useState<Omit<DeskBlockContext, "minConfidence">>({
    killEngaged: false,
    tradingAllowed: true,
    guardrailReason: null,
    guardrailFlags: [],
    consecutiveLosses: 0,
    maxConsecutiveLosses: null,
    tradesToday: 0,
    maxTradesPerDay: null,
    openCount: 0,
    maxConcurrent: GLOBAL_MAX_FALLBACK,
  });

  const reload = useCallback(async () => {
    try {
      const [guard, kill, deskKill, positions, modeSummary] = await Promise.all([
        api.getGuardrailStatus().catch(() => null) as Promise<any>,
        api.getKillSwitchStatus().catch(() => null) as Promise<any>,
        api.getTradeDeskKillSwitch().catch(() => null) as Promise<any>,
        api.getPositions().catch(() => null) as Promise<any>,
        api.getCurrentMode().catch(() => null) as Promise<any>,
      ]);

      const killEngaged = Boolean(
        deskKill?.engaged || kill?.engaged || kill?.is_engaged || kill?.active,
      );
      setGuardrailAvailable(typeof guard?.trading_allowed === "boolean");
      setKillStatusAvailable(killEngaged || (
        typeof deskKill?.engaged === "boolean" &&
        [kill?.engaged, kill?.is_engaged, kill?.active].some(v => typeof v === "boolean")
      ));

      const list = Array.isArray(positions?.positions)
        ? positions.positions
        : Array.isArray(positions)
          ? positions
          : [];
      const openCount = list.filter(
        (p: any) =>
          !p.status ||
          String(p.status).toLowerCase() === "open" ||
          p.status === "OPEN",
      ).length;

      const modeCap =
        typeof modeSummary?.max_concurrent === "number"
          ? modeSummary.max_concurrent
          : null;
      // Gate uses min(global, mode) — we don't have global settings in the UI,
      // so use mode cap when present else fallback 5 (config default).
      const maxConcurrent = modeCap ?? GLOBAL_MAX_FALLBACK;

      setCtx({
        killEngaged,
        tradingAllowed: guard?.trading_allowed !== false,
        guardrailReason: typeof guard?.reason === "string" ? guard.reason : null,
        guardrailFlags: Array.isArray(guard?.flags) ? guard.flags : [],
        consecutiveLosses:
          typeof guard?.consecutive_losses === "number" ? guard.consecutive_losses : 0,
        maxConsecutiveLosses:
          typeof guard?.max_consecutive_losses === "number"
            ? guard.max_consecutive_losses
            : null,
        tradesToday: typeof guard?.trades_today === "number" ? guard.trades_today : 0,
        maxTradesPerDay:
          typeof guard?.max_trades_per_day === "number" ? guard.max_trades_per_day : null,
        openCount,
        maxConcurrent,
      });
    } catch {
      setGuardrailAvailable(false);
      setKillStatusAvailable(false);
    }
  }, []);

  useEffect(() => {
    reload();
    const id = setInterval(reload, 15000);
    return () => clearInterval(id);
  }, [reload, mode]);

  return { ...ctx, minConfidence, reload, guardrailAvailable, killStatusAvailable };
}
