/**
 * A guardrail cap from /api/guardrails/status, or null when it was not reported.
 *
 * Meters used to hardcode their caps ("Trades Today" showed 4 / 3 while the
 * desk's real cap was 30), so a normal day looked like a breach. A cap the
 * server did not send must stay unknown, never become a plausible-looking
 * number — only a finite positive value is a cap.
 */
export function guardrailCap(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * A loss/drawdown limit the server reports as a fraction (0.05) as a percent (5),
 * or null when it was not reported. Rounded so 0.07 reads as 7, not 7.000000000000001.
 */
export function guardrailCapPct(value: unknown): number | null {
  const cap = guardrailCap(value);
  return cap === null ? null : Number((cap * 100).toFixed(4));
}
