import { coverageIsReliable, senderLabel, STOP_LABEL, type OrderBook } from "../utils/orderCoverage";

type Props = {
  /** The broker's order book if the last read is fresh and succeeded; otherwise null. */
  book: OrderBook | null;
  loading: boolean;
  /** When the displayed read was taken, to say how old it is. */
  at: number | null;
  now: number;
};

const num = (n: unknown) => (typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 4 }) : "—");

/**
 * Resting orders at the broker, with who sent each and whether every open
 * position has a stop. A failed or stale read says so; it never shows the last
 * good answer as if it were current.
 */
export default function OrderCoveragePanel({ book, loading, at, now }: Props) {
  if (!book) {
    return (
      <section className="order-coverage" aria-label="Orders at the broker">
        <h2 className="order-coverage__title">Orders at the broker</h2>
        <p role="status" className="order-coverage__unverified">
          {loading ? "Reading the broker's orders…" : "Broker orders unavailable. Stop coverage is unverified, not confirmed."}
        </p>
      </section>
    );
  }
  const reliable = coverageIsReliable(book);
  const bare = reliable ? book.positions_without_stop! : [];
  const orders = book.orders ?? [];
  const age = at ? Math.max(0, Math.round((now - at) / 1000)) : null;
  return (
    <section className="order-coverage" aria-label="Orders at the broker">
      <h2 className="order-coverage__title">Orders at the broker</h2>
      <p role="status" className={bare.length || !reliable ? "order-coverage__warn" : undefined}>
        {!reliable
          ? `${STOP_LABEL.unverified}: the broker's position list could not be confirmed just now.`
          : bare.length
            ? `⚠ ${bare.length} position${bare.length === 1 ? "" : "s"} with ${STOP_LABEL.unprotected.toLowerCase()}: ${bare.map(p => p.ticker).join(", ")}`
            : "Every open position has a stop at the broker."}
        {age !== null && <small className="order-coverage__age"> · read {age}s ago</small>}
      </p>
      {orders.length === 0 ? (
        <p className="order-coverage__empty">No resting orders.</p>
      ) : (
        <div className="positions-table-scroll" role="region" aria-label="Resting orders table" tabIndex={0}>
          <table className="t-table order-coverage__table">
            <thead><tr>{["Symbol", "Side", "Type", "Qty", "Price", "Status", "Placed by"].map(h => <th key={h}>{h}</th>)}</tr></thead>
            <tbody>
              {orders.map(o => {
                const who = senderLabel(o);
                return (
                  <tr key={o.order_id}>
                    <td data-label="Symbol" className="mono">{o.symbol}</td>
                    <td data-label="Side">{o.action ?? "—"}</td>
                    <td data-label="Type">{o.order_type ?? "—"}{o.is_protective ? " (stop)" : ""}</td>
                    <td data-label="Qty" className="mono">{num(o.quantity)}</td>
                    <td data-label="Price" className="mono">{num(o.stop_price ?? o.limit_price)}</td>
                    <td data-label="Status">{o.status ?? "—"}</td>
                    <td data-label="Placed by" className={who.notable ? "order-coverage__notable" : undefined}>
                      {who.notable ? "⚠ " : ""}{who.label}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
