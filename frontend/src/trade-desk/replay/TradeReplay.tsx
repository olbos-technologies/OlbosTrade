/**
 * Trade Replay MVP — closed/open trade tape with source + environment labels.
 * Read-only. Not a Journal rename; journal notes remain under Research.
 * No order submission.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { pnlColor } from "../../utils/pnlTone";
import { api } from "../../api/client";
import { Badge } from "../../components/ui";

type StatusFilter = "all" | "open" | "closed";

interface ReplayTrade {
  id: string;
  strategy?: string;
  underlying?: string;
  status?: string;
  entry_date?: string | null;
  exit_date?: string | null;
  quantity?: number;
  credit_received?: number;
  pnl?: number;
  pnl_pct?: number;
  mfe?: number | null;
  mae?: number | null;
  exit_reason?: string | null;
  signal_score?: number;
  hold_days?: number | null;
  short_strike?: number;
  long_strike?: number;
  trading_mode?: string | null;
  approved_by?: string | null;
  source?: string | null;
}

function fmtPnl(n: number | null | undefined) {
  if (n == null) return "—";
  const sign = n >= 0 ? "+" : "";
  return `${sign}$${n.toFixed(2)}`;
}

function envLabel(trade: ReplayTrade, accountEnv: string): { text: string; color: string } {
  // Account broker mode is authoritative for paper vs live book separation.
  if (accountEnv === "Live") return { text: "LIVE BOOK", color: "var(--red)" };
  if (accountEnv === "Paper") return { text: "PAPER BOOK", color: "var(--cyan)" };
  const mode = (trade.trading_mode || "").toLowerCase();
  if (mode.includes("live")) return { text: "LIVE BOOK", color: "var(--red)" };
  if (mode.includes("paper")) return { text: "PAPER BOOK", color: "var(--cyan)" };
  return { text: "ENV UNKNOWN", color: "var(--amber)" };
}

function sourceLabel(trade: ReplayTrade): string {
  if (trade.source) return trade.source;
  // approved_by is the dedicated field (added alongside the fix that
  // restored trading_mode to its actual conservative/balanced/aggressive/
  // scalper meaning). Rows recorded before that fix have no approved_by,
  // so fall back to the old trading_mode-as-approver heuristic for them.
  const a = (trade.approved_by || trade.trading_mode || "").toLowerCase();
  if (a === "user" || a === "copilot") return "Copilot / manual approve";
  if (a === "manual") return "Manual trade";
  if (a === "autopilot") return "Autopilot";
  if ((trade.strategy || "").toLowerCase() === "equity") return "Equity desk / scanner";
  return "Trade recorder";
}

export default function TradeReplay() {
  const [trades, setTrades] = useState<ReplayTrade[]>([]);
  const [journalByTrade, setJournalByTrade] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [accountEnv, setAccountEnv] = useState<"Paper" | "Live" | "Unknown">("Unknown");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      (api.getTradeHistory({ limit: 100, status: status === "all" ? undefined : status }) as any).catch(
        () => ({ trades: [] }),
      ),
      (api.getJournalEntries() as any).catch(() => ({ entries: [] })),
      fetch("/api/market/broker")
        .then((r) => r.json())
        .catch(() => ({})),
    ])
      .then(([h, j, broker]) => {
        const list: ReplayTrade[] = h.trades || [];
        setTrades(list);
        const map: Record<string, any> = {};
        for (const e of j.entries || j || []) {
          if (e?.trade_id) map[String(e.trade_id)] = e;
        }
        setJournalByTrade(map);
        if (broker?.paper_mode === false) setAccountEnv("Live");
        else if (broker?.paper_mode === true) setAccountEnv("Paper");
        else setAccountEnv("Unknown");
        if (!selectedId && list.length) setSelectedId(list[0].id);
      })
      .catch((e) => setError(e?.message || "Failed to load replay"))
      .finally(() => setLoading(false));
  }, [status, selectedId]);

  useEffect(() => {
    refresh();
  }, [status]); // eslint-disable-line react-hooks/exhaustive-deps — refresh on filter only

  const selected = useMemo(
    () => trades.find((t) => t.id === selectedId) || null,
    [trades, selectedId],
  );
  const journal = selected ? journalByTrade[selected.id] : null;
  const env = selected ? envLabel(selected, accountEnv) : null;

  return (
    <div
      className="trade-replay"
      style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}
    >
      <div
        style={{
          padding: "10px 16px",
          borderBottom: "1px solid var(--line-dim)",
          background: "var(--bg-3)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <span className="panel-title">TRADE REPLAY</span>
        <Badge kind="tag" tone={accountEnv === "Live" ? "var(--red)" : "var(--cyan)"}>
          {accountEnv === "Live" ? "ACCOUNT LIVE" : accountEnv === "Paper" ? "ACCOUNT PAPER" : "ACCOUNT ?"}
        </Badge>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", flex: 1 }}>
          Read-only tape from trade history. Journal editing stays under Research → Journal.
        </span>
        {(["all", "open", "closed"] as StatusFilter[]).map((s) => (
          <button
            key={s}
            type="button"
            className={`btn-t ${status === s ? "active" : ""}`}
            style={{ fontSize: 10 }}
            aria-pressed={status === s}
            onClick={() => setStatus(s)}
          >
            {s.toUpperCase()}
          </button>
        ))}
        <button type="button" className="btn-t" style={{ fontSize: 10 }} onClick={refresh}>
          Refresh
        </button>
      </div>

      {error && (
        <div role="alert" style={{ padding: "8px 16px", fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)" }}>
          {error}
        </div>
      )}

      <div
        className="trade-replay-body"
        style={{
          flex: 1,
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: "minmax(0, 1.1fr) minmax(260px, 0.9fr)",
        }}
      >
        <div
          role="listbox"
          aria-label="Trades for replay"
          style={{ overflowY: "auto", borderRight: "1px solid var(--line-dim)" }}
        >
          {loading ? (
            <div style={{ padding: 40, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              Loading…
            </div>
          ) : trades.length === 0 ? (
            <div style={{ padding: 40, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              No trades to replay
            </div>
          ) : (
            <table className="t-table">
              <thead>
                <tr>
                  {["Entry", "Symbol", "Strategy", "Status", "P&L", "Hold"].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const on = t.id === selectedId;
                  const pnl = t.pnl ?? 0;
                  return (
                    <tr
                      key={t.id}
                      role="option"
                      aria-selected={on}
                      tabIndex={0}
                      onClick={() => setSelectedId(t.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setSelectedId(t.id);
                        }
                      }}
                      style={{
                        cursor: "pointer",
                        background: on ? "var(--bg-3)" : undefined,
                        outline: on ? "1px solid var(--line)" : undefined,
                      }}
                    >
                      <td className="mono" style={{ fontSize: 10, color: "var(--ink-dim)" }}>
                        {t.entry_date?.slice(0, 10) || "—"}
                      </td>
                      <td className="mono" style={{ fontWeight: 600, color: "var(--ink)" }}>
                        {t.underlying || "—"}
                      </td>
                      <td className="mono" style={{ fontSize: 10 }}>
                        {(t.strategy || "—").replace(/_/g, " ").toUpperCase()}
                      </td>
                      <td>
                        <Badge kind="tag" tone={t.status === "open" ? "var(--cyan)" : "var(--ink-dim)"}>
                          {(t.status || "—").toUpperCase()}
                        </Badge>
                      </td>
                      <td
                        className="mono"
                        style={{
                          color: t.status === "open" ? "var(--ink-dim)" : pnlColor(pnl),
                          fontWeight: 600,
                        }}
                      >
                        {t.status === "open" ? "OPEN" : fmtPnl(t.pnl)}
                      </td>
                      <td className="mono" style={{ fontSize: 10 }}>
                        {t.hold_days != null ? `${t.hold_days}d` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <aside
          aria-label="Selected trade detail"
          style={{ overflowY: "auto", padding: 16, background: "var(--bg-2)" }}
        >
          {!selected ? (
            <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              Select a trade to inspect entry, exit, and source labels.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 18, fontWeight: 700, color: "var(--cyan)" }}>
                  {selected.underlying}
                </span>
                {env && <Badge kind="tag" tone={env.color}>{env.text}</Badge>}
                <Badge kind="tag" tone="var(--ink-dim)">{(selected.status || "—").toUpperCase()}</Badge>
              </div>

              <div>
                <div className="kicker" style={{ marginBottom: 6 }}>
                  SOURCE
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink)" }}>
                  {sourceLabel(selected)}
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", marginTop: 4 }}>
                  trading_mode field: {selected.trading_mode || "not stored on this row"}
                </div>
              </div>

              <dl
                style={{
                  margin: 0,
                  display: "grid",
                  gridTemplateColumns: "120px 1fr",
                  gap: "6px 10px",
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                }}
              >
                {(
                  [
                    ["Strategy", (selected.strategy || "—").replace(/_/g, " ")],
                    ["Qty", String(selected.quantity ?? "—")],
                    ["Entry", selected.entry_date?.slice(0, 19) || "—"],
                    ["Exit", selected.exit_date?.slice(0, 19) || "—"],
                    ["Hold", selected.hold_days != null ? `${selected.hold_days}d` : "—"],
                    ["Credit", `$${(selected.credit_received ?? 0).toFixed(2)}`],
                    ["P&L", fmtPnl(selected.pnl)],
                    ["MFE", selected.mfe != null ? fmtPnl(selected.mfe) : "—"],
                    ["MAE", selected.mae != null ? fmtPnl(selected.mae) : "—"],
                    ["Score", selected.signal_score != null ? selected.signal_score.toFixed(3) : "—"],
                    ["Exit reason", (selected.exit_reason || "—").replace(/_/g, " ")],
                    [
                      "Strikes",
                      selected.short_strike != null
                        ? `${selected.short_strike} / ${selected.long_strike}`
                        : "—",
                    ],
                  ] as [string, string][]
                ).map(([k, v]) => (
                  <React.Fragment key={k}>
                    <dt style={{ color: "var(--ink-faint)", margin: 0 }}>{k}</dt>
                    <dd style={{ color: "var(--ink)", margin: 0 }}>{v}</dd>
                  </React.Fragment>
                ))}
              </dl>

              <div>
                <div className="kicker" style={{ marginBottom: 6 }}>
                  JOURNAL SNAPSHOT
                </div>
                {journal ? (
                  <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.6 }}>
                    <div>Thesis: {journal.pre_trade_thesis || "—"}</div>
                    <div>Notes: {journal.post_trade_notes || "—"}</div>
                    <div>
                      Rules followed:{" "}
                      {journal.followed_rules == null ? "unset" : journal.followed_rules ? "yes" : "no"}
                    </div>
                  </div>
                ) : (
                  <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
                    No journal entry linked. Add notes in Research → Journal.
                  </p>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
