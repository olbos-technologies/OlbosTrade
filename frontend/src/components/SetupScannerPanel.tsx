/**
 * Setup Readiness Scanner — Chart Intelligence Phase 2.
 *
 * Ranks a watchlist by trade-confirmation score. Blocked candidates are never
 * hidden — the reason and unblock condition are always shown. Readiness only;
 * produces no orders and bypasses no gates.
 */

import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { Panel, Button } from "./ui";

interface SetupRow {
  symbol: string; bias: string; confirmation_score: number;
  structure_state: string; timeframe_alignment: string; macro_risk: string;
  status: "eligible" | "caution" | "blocked";
  blocked_reasons: string[]; unblock_condition: string | null;
  modes_allowed: string[]; strategy_fit: string[];
}

const STATUS_TONE: Record<string, string> = {
  eligible: "var(--green)", caution: "var(--amber)", blocked: "var(--red)",
};
const BIAS_TONE: Record<string, string> = {
  bullish: "var(--green)", bearish: "var(--red)", neutral: "var(--ink-dim)",
};

export default function SetupScannerPanel() {
  const [rows, setRows] = useState<SetupRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const run = () => {
    setLoading(true);
    setError(false);
    (api.getSetupScanner() as Promise<{ results: SetupRow[] }>)
      .then(d => setRows(d.results || []))
      .catch(() => { setRows([]); setError(true); })
      .finally(() => setLoading(false));
  };
  useEffect(() => { run(); }, []);

  return (
    <Panel
      padding={8}
      title="Setup Readiness"
      action={<Button style={{ fontSize: 11 }} onClick={run} disabled={loading}>{loading ? "Scanning…" : "Rescan"}</Button>}
    >
        {error ? (
          <div role="status" style={{ fontSize: 12, color: "var(--amber)", padding: 8 }}>Setup readiness unavailable. Rescan to retry.</div>
        ) : loading && rows.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--ink-dim)", padding: 8 }}>Scanning watchlist…</div>
        ) : rows.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--ink-faint)", padding: 8 }}>No results.</div>
        ) : rows.map(r => (
          <div key={r.symbol} style={{ borderBottom: "1px solid var(--line-dim)", padding: "6px 4px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
              onClick={() => setOpen(open === r.symbol ? null : r.symbol)}>
              <span className="mono" style={{ fontWeight: 600, width: 44 }}>{r.symbol}</span>
              <span style={{ color: BIAS_TONE[r.bias], fontSize: 12, textTransform: "capitalize", width: 56 }}>{r.bias}</span>
              <span className="tnum" style={{ fontSize: 13, fontWeight: 600, width: 30, color: r.confirmation_score >= 60 ? "var(--green)" : "var(--ink-dim)" }}>{r.confirmation_score}</span>
              <span style={{ marginLeft: "auto", fontSize: 11, color: STATUS_TONE[r.status], border: `1px solid ${STATUS_TONE[r.status]}`, borderRadius: 3, padding: "0 6px", textTransform: "capitalize" }}>{r.status}</span>
            </div>
            {open === r.symbol && (
              <div style={{ padding: "6px 4px 2px", fontSize: 12, color: "var(--ink-dim)" }}>
                <div>{r.timeframe_alignment} · structure {r.structure_state.replace(/_/g, " ")} · macro {r.macro_risk}</div>
                {r.blocked_reasons.length > 0 ? (
                  <div style={{ color: "var(--red)", marginTop: 4 }}>
                    {r.blocked_reasons.map((b, i) => <div key={i}>■ {b}</div>)}
                    {r.unblock_condition && <div style={{ color: "var(--ink-faint)" }}>→ {r.unblock_condition}</div>}
                  </div>
                ) : (
                  <div style={{ marginTop: 4 }}>
                    <span style={{ color: "var(--ink-faint)" }}>Fit:</span> {r.strategy_fit.join(", ")} ·{" "}
                    <span style={{ color: "var(--ink-faint)" }}>modes:</span> {r.modes_allowed.join(", ")}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        <div style={{ fontSize: 10, color: "var(--ink-faint)", padding: "6px 4px 2px" }}>
          Readiness evidence only — not a trade signal. Blocked setups show why.
        </div>
    </Panel>
  );
}
