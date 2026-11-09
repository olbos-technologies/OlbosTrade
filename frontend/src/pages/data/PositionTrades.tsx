/**
 * Positions vs Trades: each holding at the broker next to every trade row recorded for
 * it, and the reason they do or do not line up.
 *
 * Built for the question "why does the Positions screen say UNKNOWN for this one?".
 * That word means the screen found no OPEN trade row it was allowed to show, and there
 * are several different reasons, each needing a different response. This reads across
 * owners (the Positions screen deliberately does not), so it is for the operator only.
 *
 * Read only: it places, cancels and changes nothing. A failed read says so and never
 * looks like an empty list, and a stale list stays on screen marked as stale.
 */
import React, { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../../api/client";
import { Button, Panel } from "../../components/ui";
import type { PositionTradeItem, PositionTradesResponse } from "../../types/positionTrades";

const SEVERITY_WORD: Record<PositionTradeItem["severity"], string> = {
  ok: "OK", info: "Explained", attention: "Look at this",
};

const TITLE: Record<string, string> = {
  tracked: "Tracked",
  quantity_mismatch: "Wrong size",
  direction_mismatch: "Wrong side",
  hidden_from_positions_page: "Hidden from the Positions screen",
  open_row_other_owner: "Open row owned by someone else",
  pending_confirmation: "Waiting for the fill",
  only_closed_rows: "Rows say closed",
  no_trade_row: "No trade row",
  row_without_broker_position: "Row without a position",
};

const qty = (n: number | null) => (n === null ? "none" : n > 0 ? `+${n}` : String(n).replace("-", "−"));
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "—");

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.status === 403 ? "This view is for the operator account only." : err.message;
  return err instanceof Error ? err.message : "unknown error";
}

export function ageLabel(iso: string | null | undefined, now: number): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return "unknown age";
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
}

export default function PositionTrades() {
  const [data, setData] = useState<PositionTradesResponse | null>(null);   // null = never loaded
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getPositionTrades();
      setData(res);
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err));        // keep the last rows on screen, marked stale
    } finally {
      setLoading(false);
      setNow(Date.now());
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(t); }, []);

  const brokerDown = data !== null && !data.broker.available;
  const items = data?.items ?? [];

  return (
    <div className="position-trades" style={{ padding: 16, display: "grid", gap: 12, maxWidth: 1100 }}>
      <Panel
        title="Positions vs Trades"
        action={<Button flat onClick={() => void load()} disabled={loading}>{loading ? "Reading…" : "Refresh"}</Button>}
      >
        <p className="position-trades__lede">
          Each holding at the broker, next to every trade row recorded for it (any owner), and why they do or do not
          line up. Read only: nothing here places, cancels or changes anything.
        </p>

        {loadError && (
          <p role="alert" className="position-trades__error">
            {data ? "Could not refresh. Showing the last list, which is stale: " : "Could not load: "}{loadError}
          </p>
        )}
        {data === null && !loadError && <p role="status">Reading positions and trades…</p>}
        {brokerDown && (
          <p role="alert" className="position-trades__error">
            The broker could not be read ({data!.broker.error}). Holdings are unknown, which is not the same as none.
          </p>
        )}

        {data && !brokerDown && data.summary && (
          <p role="status" className="position-trades__summary">
            {data.summary.holdings} holding{data.summary.holdings === 1 ? "" : "s"} · {data.summary.tracked} tracked ·{" "}
            <strong>{data.summary.attention} to look at</strong>
            {data.summary.rows_without_position > 0 && ` · ${data.summary.rows_without_position} open row${data.summary.rows_without_position === 1 ? "" : "s"} with no position`}
            <small className="position-trades__age"> · read {ageLabel(data.generated_at, now)}{loadError ? " (stale)" : ""}</small>
          </p>
        )}
        {data && !brokerDown && items.length === 0 && <p>No holdings at the broker and no open trade rows.</p>}
      </Panel>

      {items.map((item) => (
        <section key={`${item.symbol}-${item.asset_class}`} className={`pt-card pt-card--${item.severity}`} aria-label={`${item.symbol} ${item.asset_class}`}>
          <header className="pt-card__head">
            <h2 className="pt-card__symbol">{item.symbol}</h2>
            <span className="pt-card__class">{item.asset_class}</span>
            <span className="pt-card__held">At broker: <strong>{qty(item.broker_quantity)}</strong></span>
            <span className={`pt-card__verdict pt-card__verdict--${item.severity}`}>
              {item.severity === "ok" ? "✓ " : item.severity === "attention" ? "⚠ " : "• "}{SEVERITY_WORD[item.severity]} · {TITLE[item.code] ?? item.code}
            </span>
          </header>
          <p className="pt-card__why">{item.explanation}</p>
          <p className="pt-card__shows">The Positions screen shows the strategy as: <strong>{item.positions_page_shows}</strong></p>

          {item.trades.length > 0 ? (
            <table className="dock-table" aria-label={`Trade rows for ${item.symbol}`}>
              <thead><tr><th>Status</th><th>Strategy</th><th>Side</th><th>Qty</th><th>Owner</th><th>Entered</th><th>Closed</th><th>On Positions screen</th></tr></thead>
              <tbody>
                {item.trades.map((t) => (
                  <tr key={t.id}>
                    <td>{t.status}</td><td>{t.strategy}</td><td>{t.direction ?? "—"}</td>
                    <td className="tnum">{t.quantity ?? "—"}</td><td className="pt-owner">{t.owner}</td>
                    <td className="tnum">{day(t.entry_date)}</td><td className="tnum">{day(t.exit_date)}</td>
                    <td>{t.shown_on_positions_page ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="pt-card__none">No trade rows recorded for this name.</p>}

          {item.claims.length > 0 && (
            <ul className="pt-card__claims" aria-label={`Claims on ${item.symbol}`}>
              {item.claims.map((c, i) => (
                <li key={i}>Entry claim: <strong>{c.state}</strong>{c.reason ? ` · ${c.reason}` : ""}{c.claimed_at ? ` · ${ageLabel(c.claimed_at, now)}` : ""}</li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
