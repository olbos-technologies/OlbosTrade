/**
 * What the Signals ledger shows for each idea, and how the list is grouped.
 * Pure, so the rules that decide where a signal appears are testable.
 *
 * The status is a PREVIEW (the same client-side check the why-blocked chip has
 * always used; Evaluate stays authoritative). Two rules keep it honest:
 *   - a signal is only called "clear" when the desk state it depends on was
 *     actually read; if it was not, the status is "unknown", never "clear";
 *   - HOLD and earnings-gated ideas are not candidates at all and are kept out
 *     of the ranked list rather than mixed in with it.
 */
import { deriveSignalBlockReason, type DeskBlockContext, type SignalBlockReason } from "./signalBlockReason";

export type LedgerSignal = {
  id: string;
  generated_at: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  earnings_gated?: boolean;
  trade_plan?: { entry_price?: number; stop_price?: number; target_move_pct?: number };
  opportunity_score?: { score: number } | null;
};

export type LedgerStatus =
  | { kind: "clear" }
  | { kind: "unknown" }
  | { kind: "blocked"; reason: SignalBlockReason };

/** What the desk read could actually see. `capacityAvailable` is the gate's own cap
 * and open count; without it the max-positions check is an estimate. */
export type DeskReadState = { guardrailAvailable: boolean; killStatusAvailable: boolean; capacityAvailable: boolean };

/** The score the row displays: the composite signal score when valid, else the
 * 0-100 confidence score, else null. Mirrors the card, so sort order and the
 * number on screen agree. */
export function signalScore(sig: Pick<LedgerSignal, "confidence" | "opportunity_score" | "action">): number | null {
  const s = sig.opportunity_score?.score;
  if (typeof s === "number" && Number.isFinite(s) && s >= 0 && s <= 100) return s;
  if (sig.action !== "HOLD" && Number.isFinite(sig.confidence) && sig.confidence >= 0 && sig.confidence <= 1) {
    return sig.confidence * 100;
  }
  return null;
}

/** Distance from entry to stop, as a percent of entry. Null when either price is
 * missing or entry is not positive - never a guessed number. */
export function stopDistancePct(tp: LedgerSignal["trade_plan"]): number | null {
  const entry = tp?.entry_price, stop = tp?.stop_price;
  if (typeof entry !== "number" || typeof stop !== "number" || !(entry > 0) || !Number.isFinite(stop)) return null;
  return (Math.abs(entry - stop) / entry) * 100;
}

export function ledgerStatus(
  sig: Pick<LedgerSignal, "action" | "confidence">,
  ctx: DeskBlockContext,
  desk: DeskReadState,
): LedgerStatus {
  const reason = deriveSignalBlockReason(sig, ctx);
  if (reason) return { kind: "blocked", reason };
  // No block found. That only means "clear" if the checks could actually see.
  if (!desk.guardrailAvailable || !desk.killStatusAvailable || !desk.capacityAvailable) return { kind: "unknown" };
  return { kind: "clear" };
}

/** Newest scan cycle first, then highest displayed score. Recency stays primary:
 * a strong signal from several cycles ago is more likely stale than useful. */
const CYCLE_BUCKET_MS = 10 * 60 * 1000;
const cycleBucket = (iso: string) => Math.floor(new Date(iso).getTime() / CYCLE_BUCKET_MS);

export function compareLedger(a: LedgerSignal, b: LedgerSignal): number {
  const cycle = cycleBucket(b.generated_at) - cycleBucket(a.generated_at);
  if (cycle !== 0) return cycle;
  return (signalScore(b) ?? -1) - (signalScore(a) ?? -1);
}

export type DirectionFilter = "all" | "buy" | "sell";

export type LedgerGroups<T extends LedgerSignal> = {
  /** BUY/SELL ideas with no block found (or whose status could not be checked). */
  candidates: Array<{ sig: T; status: LedgerStatus }>;
  /** BUY/SELL ideas the preview says Autopilot would block. */
  blocked: Array<{ sig: T; status: LedgerStatus }>;
  /** HOLD and earnings-gated ideas: not candidates. */
  parked: T[];
};

export function groupLedger<T extends LedgerSignal>(
  signals: T[],
  ctx: DeskBlockContext,
  desk: DeskReadState,
  opts: { direction?: DirectionFilter; minMovePct?: number } = {},
): LedgerGroups<T> {
  const direction = opts.direction ?? "all";
  const minMove = opts.minMovePct ?? 0;
  const out: LedgerGroups<T> = { candidates: [], blocked: [], parked: [] };
  for (const sig of [...signals].sort(compareLedger)) {
    if (sig.action === "HOLD" || sig.earnings_gated) { out.parked.push(sig); continue; }
    if (direction === "buy" && sig.action !== "BUY") continue;
    if (direction === "sell" && sig.action !== "SELL") continue;
    // Same rule as before: a missing move is 0, so it fails any non-zero minimum.
    if ((sig.trade_plan?.target_move_pct ?? 0) < minMove) continue;
    const status = ledgerStatus(sig, ctx, desk);
    (status.kind === "blocked" ? out.blocked : out.candidates).push({ sig, status });
  }
  return out;
}
