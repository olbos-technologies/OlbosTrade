import { pnlColor } from "../../utils/pnlTone";

type Pos = {
  symbol?: string; underlying?: string; quantity?: number | null; avg_cost?: number | null;
  unrealized_pnl?: number | null; hold_days?: number | null; entry_date?: string | null;
};

const num = (n: unknown) => (typeof n === "number" && Number.isFinite(n) ? String(n) : "—");
const usd = (n: unknown) => (typeof n === "number" && Number.isFinite(n) ? `$${n.toFixed(2)}` : "—");
const pnl = (n: unknown) =>
  typeof n === "number" && Number.isFinite(n) ? `${n < 0 ? "−" : n > 0 ? "+" : ""}$${Math.abs(n).toFixed(2)}` : "—";

/** Open positions as a dense table. An unreadable list says so; it is never an empty table. */
export default function PositionsTable({ positions, error }: { positions: Pos[]; error: boolean }) {
  if (error) return <p className="dock-note dock-note--warn" role="status">Positions unavailable. Check the broker connection; holdings are not confirmed.</p>;
  if (positions.length === 0) return <p className="dock-note">No open positions.</p>;
  return (
    <div className="dock-table-wrap">
      <table className="dock-table" aria-label="Open positions">
        <thead><tr><th>Symbol</th><th>Qty</th><th>Avg cost</th><th>Unrealized P&amp;L</th><th>Held</th></tr></thead>
        <tbody>
          {positions.map((p, i) => {
            const sym = p.symbol || p.underlying || "?";
            return (
              <tr key={`${sym}-${p.entry_date || i}`}>
                <td className="mono">{sym}</td>
                <td className="tnum">{num(p.quantity)}</td>
                <td className="tnum">{usd(p.avg_cost)}</td>
                <td className="tnum" style={{ color: p.unrealized_pnl == null ? "var(--ink-faint)" : pnlColor(p.unrealized_pnl) }}>{pnl(p.unrealized_pnl)}</td>
                <td className="tnum">{typeof p.hold_days === "number" ? `${p.hold_days}d` : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
