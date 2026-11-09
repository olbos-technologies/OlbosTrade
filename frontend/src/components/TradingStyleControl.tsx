/**
 * Compact Trading Style switcher for header rails.
 */

import React, { useState } from "react";
import { api } from "../api/client";
import type { TradingStyleKey } from "../hooks/useTradingStyleFloor";
import { floorForStyle } from "../hooks/useTradingStyleFloor";

const OPTIONS: { key: TradingStyleKey; label: string; onColor: string }[] = [
  { key: "conservative", label: "CONS", onColor: "var(--green)" },
  { key: "balanced", label: "BAL", onColor: "var(--accent)" },
  { key: "aggressive", label: "AGG", onColor: "var(--orange)" },
  { key: "scalper", label: "SCALP", onColor: "var(--red)" },
];

export default function TradingStyleControl({
  mode,
  onChanged,
}: {
  mode: string;
  onChanged?: (mode: TradingStyleKey) => void;
}) {
  const [busy, setBusy] = useState(false);
  const active = (mode || "balanced").toLowerCase() as TradingStyleKey;

  const activate = async (key: TradingStyleKey) => {
    if (key === active || busy) return;
    if (key === "scalper") {
      const ok = window.confirm(
        "Scalper requires active monitoring and allows more frequent trades. Continue?",
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      await api.setTradingMode({ mode: key, confirmed: key === "scalper" });
      onChanged?.(key);
    } catch {
      /* keep prior */
    } finally {
      setBusy(false);
    }
  };

  const floorPct = Math.round(floorForStyle(active) * 100);

  return (
    <div
      role="group"
      aria-label="Trading style"
      title={`Trading style (minimum confidence score ${floorPct}). Conservative/Balanced 90, Aggressive 70, Scalper 60.`}
      className="segmented-group"
      style={{ height: 22 }}
    >
      {OPTIONS.map((opt) => {
        const on = active === opt.key;
        return (
          <button
            key={opt.key}
            type="button"
            disabled={busy}
            onClick={() => activate(opt.key)}
            aria-pressed={on}
            style={{
              height: 18,
              padding: "0 7px",
              background: on ? "var(--cyan-dim)" : "transparent",
              border: `1px solid ${on ? opt.onColor : "transparent"}`,
              color: on ? opt.onColor : "var(--ink-faint)",
              fontFamily: "var(--mono)",
              fontSize: 9,
              letterSpacing: "0.06em",
              cursor: busy ? "wait" : "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {opt.label}
          </button>
        );
      })}
      <span
        style={{
          fontFamily: "var(--mono)",
          fontSize: 9,
          color: "var(--ink-faint)",
          padding: "0 6px 0 2px",
          borderLeft: "1px solid var(--line-dim)",
          marginLeft: 2,
        }}
      >
        ≥{floorPct}%
      </span>
    </div>
  );
}
