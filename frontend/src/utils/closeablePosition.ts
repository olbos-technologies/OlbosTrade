/**
 * Which positions the Trade Desk offers a close button for.
 *
 * This lives outside the component because it is half of a cross-boundary
 * agreement, not a rendering detail. The other half is the allowlist in
 * close_position() (backend/app/api/routes/trade_desk.py), which accepts
 * equity_long, equity_short, put and call.
 *
 * The two drifted: the UI check was written when only equities could be
 * closed, options support landed on the backend, and nothing updated here. The
 * result was an options row with no button at all — which an operator reads as
 * a broken button, not as an unsupported action.
 */

/** Must match close_position()'s spread_type allowlist on the backend. */
export const CLOSEABLE_SPREAD_TYPES = [
  "equity_long",
  "equity_short",
  "put",
  "call",
] as const;

export interface ClosablePositionish {
  spread_type?: string | null;
  id?: string | null;
  /** "db_only" = an open DB Trade row with no matching broker position. */
  source?: string | null;
}

/**
 * True for a row the broker is not actually holding.
 *
 * paper_trade.py emits these for open Trade rows with no matching broker
 * position. They carry a real id and a real spread_type, so every other check
 * here passes — and a "close" for a position that does not exist is an OPENING
 * trade in the opposite direction.
 *
 * The backend refuses these on both paths now (close_equity_trade has since
 * 2026-08-26, close_options_trade as of PR #64), so this is the second line,
 * not the only one. Its job is to not offer an action that can only fail.
 */
export function isDbOnly(p: ClosablePositionish): boolean {
  return (p.source || "").toLowerCase() === "db_only";
}

export function normalizeSpreadType(p: ClosablePositionish): string {
  return (p.spread_type || "").toLowerCase();
}

export function isEquityPosition(p: ClosablePositionish): boolean {
  const t = normalizeSpreadType(p);
  return t === "equity_long" || t === "equity_short";
}

/** True when the backend would accept a close for this spread_type. */
export function isCloseableType(p: ClosablePositionish): boolean {
  return (CLOSEABLE_SPREAD_TYPES as readonly string[]).includes(normalizeSpreadType(p));
}

/**
 * True when the close button should render.
 *
 * The trade id is required separately: close-position is keyed by it, and a
 * broker position with no DB Trade row goes through the untracked path
 * instead.
 */
export function canClosePosition(p: ClosablePositionish): boolean {
  return isCloseableType(p) && !!p.id && !isDbOnly(p);
}
