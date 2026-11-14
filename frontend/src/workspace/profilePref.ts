/**
 * Whether the workspace chart shows its volume profile. On by default (it is part
 * of what the workspace is for); a viewer can turn it off and the choice is kept
 * in this browser. Storage can be missing or blocked, so every access is guarded
 * and the default applies.
 */

export const PROFILE_PREF_KEY = "olbos.workspace.volumeProfile";

export function readProfilePref(storage?: Pick<Storage, "getItem"> | null): boolean {
  try {
    return storage?.getItem(PROFILE_PREF_KEY) !== "off";
  } catch {
    return true;
  }
}

export function writeProfilePref(storage: Pick<Storage, "setItem"> | null | undefined, on: boolean): void {
  try {
    storage?.setItem(PROFILE_PREF_KEY, on ? "on" : "off");
  } catch { /* storage unavailable: the choice lasts for this visit */ }
}
