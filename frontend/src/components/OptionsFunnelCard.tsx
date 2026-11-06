import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { FunnelStage, OptionsFunnel } from "../types/optionsFunnel";

const WINDOWS = [1, 7, 30] as const;

/** A short label for each verdict, so the state never rests on colour alone. */
const VERDICT_LABEL: Record<string, string> = {
  trading: "TRADING",
  no_data: "NO DATA",
  nothing_qualifies: "NOTHING QUALIFIES",
  mode_manual: "MANUAL MODE",
  waiting_for_approval: "WAITING FOR APPROVAL",
  qualified_but_never_dispatched: "NOT DISPATCHED",
  blocked_at_execution: "BLOCKED AT EXECUTION",
};

const pretty = (s: string) => s.replace(/_/g, " ");

function verdictTone(code: string): string {
  if (code === "trading") return "var(--green)";
  if (code === "nothing_qualifies" || code === "blocked_at_execution") return "var(--amber)";
  return "var(--ink-dim)";
}

function StageRow({ stage, max }: { stage: FunnelStage; max: number }) {
  const width = max > 0 ? Math.max(stage.count > 0 ? 2 : 0, (stage.count / max) * 100) : 0;
  const tone = stage.key === "submitted" ? "var(--green)" : stage.key === "qualified" ? "var(--accent)" : "var(--amber)";
  return (
    <div data-testid={`funnel-stage-${stage.key}`} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
        <span style={{ color: "var(--ink)" }}>{stage.label}</span>
        <span className="tnum" style={{ color: "var(--ink)" }}>{stage.count.toLocaleString()}</span>
      </div>
      <div className="bar-track" style={{ height: 4 }}>
        <div className="bar-fill" style={{ width: `${width}%`, background: tone }} />
      </div>
      {stage.reasons.length > 0 && stage.count > 0 && (
        <ul style={{ listStyle: "none", margin: "2px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 2 }}>
          {stage.reasons.slice(0, 5).map(r => (
            <li key={r.reason} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--ink-dim)" }}>
              <span>{pretty(r.reason)}</span>
              <span className="tnum">{r.count.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * "Why aren't options trading?" — the funnel from the scan to an order, with the biggest reason
 * at each stage and a one-sentence verdict. A failed read is shown as unavailable, never as a
 * funnel of zeros: "nothing was rejected" and "we could not look" must not read the same.
 */
export default function OptionsFunnelCard() {
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<OptionsFunnel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    (api.getOptionsFunnel(days) as Promise<OptionsFunnel>)
      .then(d => { setData(d); setError(null); })
      .catch(e => { setData(null); setError(String(e?.message ?? e)); })
      .finally(() => setLoading(false));
  }, [days]);

  useEffect(() => {
    load();
    const t = setInterval(load, 120000);
    return () => clearInterval(t);
  }, [load]);

  const max = data ? Math.max(0, ...data.stages.map(s => s.count)) : 0;
  const code = data?.verdict.code ?? "";

  return (
    <section className="instrument-card" aria-label="Why options are not trading" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="panel-title">Why options aren't trading</span>
        <span style={{ flex: 1 }} />
        <div role="group" aria-label="Window" style={{ display: "flex", gap: 6 }}>
          {WINDOWS.map(w => (
            <button key={w} onClick={() => setDays(w)} aria-pressed={days === w}
              className={days === w ? "btn-primary" : "btn-ghost"} style={{ padding: "2px 10px", fontSize: 11 }}>
              {w}D
            </button>
          ))}
        </div>
      </div>

      {loading && !data && !error && <div style={{ color: "var(--ink-dim)", fontSize: 12 }}>Loading the options funnel…</div>}

      {error && (
        <div role="alert" style={{ color: "var(--amber)", fontSize: 12 }}>
          UNAVAILABLE — the options funnel could not be read ({error}). This is not the same as zero rejections.
        </div>
      )}

      {data && (
        <>
          <div data-testid="funnel-verdict" style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
            <span style={{ color: verdictTone(code), fontWeight: 700, fontSize: 11, letterSpacing: "0.06em", whiteSpace: "nowrap" }}>
              {VERDICT_LABEL[code] ?? code.toUpperCase()}
            </span>
            <span style={{ color: "var(--ink)", fontSize: 13 }}>{data.verdict.text}</span>
          </div>

          <div data-testid="funnel-context" style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 11, color: "var(--ink-dim)" }}>
            <span>Execution mode: <strong style={{ color: "var(--ink)" }}>{data.execution_mode.toUpperCase()}</strong></span>
            <span>
              Regime:{" "}
              {data.regime
                ? <strong style={{ color: "var(--ink)" }}>{pretty(data.regime.name)} · options {data.regime.options_allowed ? "allowed" : "NOT allowed"}
                    {data.regime.strategies_allowed.length > 0 ? ` (${data.regime.strategies_allowed.map(pretty).join(", ")})` : ""}</strong>
                : <strong style={{ color: "var(--ink)" }}>not available</strong>}
            </span>
            <span>
              Frequency floors:{" "}
              {data.frequency_rule
                ? <strong className="tnum" style={{ color: "var(--ink)" }}>{data.frequency_rule.trading_mode} · confidence ≥ {data.frequency_rule.min_confidence.toFixed(2)}
                    {" "}· POP ≥ {Math.max(data.frequency_rule.min_pop, data.frequency_rule.credit_spread_min_pop ?? 0).toFixed(2)}
                    {" "}· risk score ≤ {data.frequency_rule.max_risk_score}</strong>
                : <strong style={{ color: "var(--ink)" }}>not available</strong>}
            </span>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {data.stages.map(s => <StageRow key={s.key} stage={s} max={max} />)}
          </div>

          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 10, color: "var(--ink-faint)", display: "flex", flexDirection: "column", gap: 2 }}>
            {data.notes.map(n => <li key={n}>{n}</li>)}
          </ul>
        </>
      )}
    </section>
  );
}
