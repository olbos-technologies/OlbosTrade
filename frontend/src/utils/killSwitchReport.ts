/**
 * How a kill-switch engage result should read to an operator.
 *
 * Extracted from RiskMonitor so it can be tested. The rendering it drives is
 * safety-critical and was going out untested — a regression in the filled/
 * working split, or a quiet return to static success wording, would have
 * passed every existing test. Caught in review on PR #64.
 *
 * The rule it encodes: `positions_flattened` counts every order the service
 * did not see rejected, so `submitted` (accepted, no fill yet), `partial`
 * (residual exposure the caller MUST handle) and `cancelled` (terminated with
 * no fill) are all inside it. Only `filled` means a position is gone. Saying
 * "Flattened N" off that number tells an operator the book is flat when it may
 * not be — during the one action where that claim matters most.
 */

export interface KillSwitchReport {
  already_engaged?: boolean;
  positions_flattened?: number;
  orders_cancelled?: number;
  flatten_statuses?: Record<string, number> | null;
  errors?: string[] | null;
}

export interface ReportSummary {
  /** Nothing was flattened — the switch was already armed. */
  alreadyEngaged: boolean;
  /** Closing orders submitted. */
  sent: number;
  /** Orders that actually filled. Only these closed a position. */
  filled: number;
  /** ["1 submitted", "2 partial"] — orders still carrying exposure. */
  working: string[];
  /** True when every order sent came back filled. */
  allClosed: boolean;
  errors: string[];
}

export function summarizeKillSwitchReport(r: KillSwitchReport | null | undefined): ReportSummary {
  const st = (r?.flatten_statuses || {}) as Record<string, number>;
  const filled = st.filled ?? 0;
  const sent = r?.positions_flattened ?? 0;
  const working = Object.entries(st)
    .filter(([k]) => k !== "filled")
    .map(([k, v]) => `${v} ${k}`);
  return {
    alreadyEngaged: !!r?.already_engaged,
    sent,
    filled,
    working,
    // sent === 0 is NOT "all closed" — there was nothing to close, or nothing
    // was attempted. Reporting green there would be the same overstatement in
    // a different place.
    allClosed: sent > 0 && filled === sent && working.length === 0,
    errors: Array.isArray(r?.errors) ? r!.errors! : [],
  };
}
