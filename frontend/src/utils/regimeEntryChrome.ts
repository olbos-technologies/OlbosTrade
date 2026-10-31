/**
 * Shell chrome for regime entry allowance.
 *
 * Autopilot / trading-style preferences can stay selected while the regime
 * engine refuses new risk-increasing entries. The status strip must say that
 * explicitly — otherwise CRISIS + AGGRESSIVE + AUTOPILOT reads as live autonomy.
 */

export type RegimeEntryPayload = {
  equity_allowed?: boolean | null;
  regime?: string | null;
} | null | undefined;

export type RegimeEntryAllowance = "allowed" | "blocked" | "unknown";

export function regimeEntryAllowance(payload: RegimeEntryPayload): RegimeEntryAllowance {
  if (!payload || typeof payload.equity_allowed !== "boolean") return "unknown";
  return payload.equity_allowed ? "allowed" : "blocked";
}

/** Chip for the capital-at-risk row. Null when entries are allowed (no noise). */
export function regimeEntriesStatusChip(allowance: RegimeEntryAllowance): {
  label: string;
  value: string;
  tone: string;
  title: string;
} | null {
  if (allowance === "allowed") return null;
  if (allowance === "blocked") {
    return {
      label: "ENTRIES",
      value: "BLOCKED",
      tone: "var(--red)",
      title:
        "Regime engine reports equity_allowed=false — new risk-increasing entries are refused. Autopilot and trading style preferences remain selected but do not authorize entries.",
    };
  }
  return {
    label: "ENTRIES",
    value: "UNKNOWN",
    tone: "var(--amber)",
    title: "Regime entry allowance could not be read — treat new entries as unverified.",
  };
}

/** Short ticker cue shown beside REGIME when entries are not allowed. */
export function regimeEntriesTickerLabel(allowance: RegimeEntryAllowance): string | null {
  if (allowance === "blocked") return "ENTRIES PAUSED";
  if (allowance === "unknown") return "ENTRIES UNVERIFIED";
  return null;
}
