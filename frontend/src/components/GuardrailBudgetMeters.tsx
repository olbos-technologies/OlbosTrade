import React from "react";
import { guardrailCap, guardrailCapPct } from "../utils/guardrailCap";

type Meter = { label: string; val: number; max: number | null; unit: string };

/**
 * The Dashboard's four guardrail budget bars.
 *
 * Every cap comes from the server (`max_*` fields of /api/guardrails/status), so the
 * bars match the limits actually in force — including ones changed in the environment.
 * They used to be fixed numbers (2% / 5% / 3) that drifted from the real limits. When a
 * cap is unknown the bar is omitted and the figure reads "n / —" in amber, so a missing limit is
 * visible as missing rather than shown as a number that looks safe or alarming.
 */
export default function GuardrailBudgetMeters({
  guard, hintFor,
}: { guard: any; hintFor: (label: string) => React.ReactNode }) {
  const meters: Meter[] = [
    // daily/weekly_loss_pct are signed P&L ratios (positive on a gain day) —
    // clamp to the negative portion only, so a gain shows 0% of the loss budget
    // used instead of its magnitude.
    { label: "Daily Loss",    val: Math.max(0, -(guard?.daily_loss_pct  || 0)) * 100, max: guardrailCapPct(guard?.max_daily_loss_pct), unit: "%" },
    { label: "Weekly Loss",   val: Math.max(0, -(guard?.weekly_loss_pct || 0)) * 100, max: guardrailCapPct(guard?.max_weekly_loss_pct), unit: "%" },
    { label: "Trades Today",  val: guard?.trades_today || 0, max: guardrailCap(guard?.max_trades_per_day), unit: "" },
    { label: "Consecutive losses", val: guard?.consecutive_losses || 0, max: guardrailCap(guard?.max_consecutive_losses), unit: "" },
  ];
  return (
    <>
      {meters.map(g => {
        const known = g.max !== null && g.max > 0;
        const pct = known ? Math.min((g.val / (g.max as number)) * 100, 100) : null;
        const color = pct === null ? "var(--amber)" : pct < 60 ? "var(--green)" : pct < 85 ? "var(--amber)" : "var(--red)";
        return (
          <div key={g.label} data-testid={`guardrail-meter-${g.label}`}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                {hintFor(g.label)}
              </span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color }}>
                {g.val.toFixed(1)}{g.unit} / {known ? `${g.max}${g.unit}` : "—"}
              </span>
            </div>
            {pct !== null && (
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
