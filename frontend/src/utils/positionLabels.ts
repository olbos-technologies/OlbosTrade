/**
 * How a position the desk did not open itself is named on screen.
 *
 * The reconciler writes a trade row with strategy "adopted_untracked" for a broker
 * position it found with no matching desk trade. That string is an internal
 * marker, not a trading strategy, and printing it raw made it read like one — and
 * made an adopted position look like a normal "Olbos tracked" one.
 */

export const ADOPTED_STRATEGY = "adopted_untracked";

export const ADOPTED_LABEL = "Adopted from broker";
export const ADOPTED_NOTE = "history may be incomplete";

export function isAdopted(strategy: string | null | undefined): boolean {
  return String(strategy ?? "").trim().toLowerCase() === ADOPTED_STRATEGY;
}

/** The display name for an adopted position, or null for anything else so the
 *  caller keeps whatever formatting it already applies. */
export function adoptedLabel(strategy: string | null | undefined): string | null {
  return isAdopted(strategy) ? ADOPTED_LABEL : null;
}
