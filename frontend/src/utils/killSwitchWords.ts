/**
 * The words for the kill switch's state, in one place.
 *
 * The same state read "NOT ARMED" in the status strip, "CLEAR" on the Command Center card,
 * "Clear" in the Trade Desk header and "OFF" (hardcoded, whatever the state) in the Risk
 * rules list; engaged read "HALTED" in one place and "ENGAGED" in the rest. "NOT ARMED" was
 * the worst of them: for a safety control it can be read as "the protection is not ready".
 *
 * Two states, named after the API's own `engaged` field: ENGAGED (new orders halted) and
 * CLEAR (not engaged, trading may proceed). A failed read is neither, so callers keep their
 * own UNKNOWN / UNVERIFIED / Unavailable wording for it and never fall back to CLEAR.
 */

export const KILL_ENGAGED = "ENGAGED";
export const KILL_CLEAR = "CLEAR";

export function killSwitchWord(engaged: boolean | null | undefined): typeof KILL_ENGAGED | typeof KILL_CLEAR | null {
  if (engaged === true) return KILL_ENGAGED;
  if (engaged === false) return KILL_CLEAR;
  return null;
}
