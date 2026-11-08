/**
 * What the broker's resting orders say about each position - read, never guessed.
 *
 * Two questions the Positions screen could not answer before:
 *   1. Is this position protected by a stop? ("unverified" is its own answer: a
 *      stale or failed read must not look like "protected".)
 *   2. Who sent this order? IBKR stamps every order with the API client id of its
 *      sender, so the label comes from the broker's record (placed_by), not from
 *      what the app remembers.
 */

export type RestingOrder = {
  order_id: number;
  symbol: string;
  action?: string;
  order_type?: string;
  quantity?: number;
  limit_price?: number | null;
  stop_price?: number | null;
  status?: string;
  is_protective?: boolean;
  client_id?: number | null;
  placed_by?: string;
};

export type OrderBook = {
  available?: boolean;
  source?: string;
  unprotected_is_reliable?: boolean;
  positions_error?: string;
  positions_without_stop?: { ticker: string }[];
  protected_tickers?: string[];
  orders?: RestingOrder[];
  app_client_id?: number | null;
};

export type StopState = "protected" | "unprotected" | "unverified";

/** True only when the read was fresh from the broker and says it can be trusted. */
export function coverageIsReliable(book: OrderBook | null | undefined): boolean {
  return !!book &&
    book.available === true &&
    book.source === "refreshed" &&
    book.unprotected_is_reliable === true &&
    !book.positions_error &&
    Array.isArray(book.positions_without_stop);
}

export function stopState(book: OrderBook | null | undefined, symbol: string): StopState {
  if (!coverageIsReliable(book)) return "unverified";
  const want = String(symbol ?? "").trim().toUpperCase();
  const bare = (book!.positions_without_stop ?? []).some(
    p => String(p.ticker ?? "").trim().toUpperCase() === want,
  );
  return bare ? "unprotected" : "protected";
}

export const STOP_LABEL: Record<StopState, string> = {
  protected: "Stop in place",
  unprotected: "No stop at broker",
  unverified: "Stop unverified",
};

export type Sender = { label: string; notable: boolean };

/** `notable` marks senders that are not this app, so the screen can flag them in
 *  words, not colour alone. */
export function senderLabel(order: Pick<RestingOrder, "placed_by" | "client_id">): Sender {
  switch (order.placed_by) {
    case "this_app":
      return { label: "This app", notable: false };
    case "tws_or_manual":
      return { label: "TWS / manual entry", notable: true };
    case "other_api_client":
      return {
        label: typeof order.client_id === "number" ? `Other API client (id ${order.client_id})` : "Other API client",
        notable: true,
      };
    default:
      return { label: "Sender unknown", notable: true };
  }
}
