/**
 * Research Lab — the strategy promotion funnel.
 *
 * Create an experiment (hypothesis + strategy), then walk it through the gates:
 *   DRAFT → BACKTESTED → PAPER → PROMOTED   (or ARCHIVED).
 * A move to PAPER needs a passing backtest; to PROMOTED a passing paper record.
 * On promotion a real StrategyBaseline is derived and fed to the Strategy Health
 * Monitor. Backed by /api/research/lab/*.
 */
import React, { useEffect, useState } from "react";
import { api } from "../api/client";

interface Experiment {
  id: string; name: string; strategy: string; hypothesis: string | null;
  stage: "draft" | "backtested" | "walk_forward" | "paper" | "promoted" | "archived";
  backtest_metrics: Record<string, number> | null;
  paper_perf: Record<string, number> | null;
  baseline: Record<string, number> | null;
}

const STAGES = ["draft", "backtested", "walk_forward", "paper", "promoted"] as const;
const STAGE_COLOR: Record<string, string> = {
  draft: "var(--ink-dim)", backtested: "var(--cyan)", walk_forward: "var(--cyan)",
  paper: "var(--amber)", promoted: "var(--green)", archived: "var(--ink-faint)",
};
const NEXT: Record<string, string | null> = {
  draft: "backtested", backtested: "walk_forward", walk_forward: "paper",
  paper: "promoted", promoted: null, archived: "draft",
};

const STRATEGIES = ["bull_put_spread", "bear_call_spread", "iron_condor", "bull_call_debit_spread"];

// Same polling interval/cap as the established precedent (useBacktest.ts) —
// a real backtest is a background job, not an instant response.
const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 180_000;
// Backtest.tsx's own hardcoded default date range, reused rather than
// inventing a new convention or adding date-picker UI to this page.
const RESEARCH_BACKTEST_RANGE = { start_date: "2022-01-01", end_date: "2024-12-31" };

export default function ResearchLab() {
  const [exps, setExps] = useState<Experiment[]>([]);
  const [name, setName] = useState("");
  const [strategy, setStrategy] = useState(STRATEGIES[0]);
  const [hypothesis, setHypothesis] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [msgKind, setMsgKind] = useState<"error" | "gate" | "info">("info");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  // Keyed per-experiment (not the shared busy flag) — a real backtest can
  // run for up to 3 minutes and shouldn't freeze every other experiment's
  // buttons on the page while it's in flight.
  const [backtestRuns, setBacktestRuns] = useState<Record<string, { status: string; runId?: string }>>({});

  // AI Research Assistant
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ answer?: string; provider?: string; error?: string; hint?: string } | null>(null);
  const [asking, setAsking] = useState(false);

  const ask = async () => {
    if (!question.trim()) return;
    setAsking(true); setAnswer(null);
    const r = await fetch("/api/research/assistant", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }).then(r => r.json()).catch(() => ({ error: "request failed" }));
    setAsking(false); setAnswer(r);
  };

  const load = async () => {
    setLoadErr(null);
    const r = await fetch("/api/research/lab/experiments").then(r => r.json()).catch(() => null);
    if (r?.experiments) setExps(r.experiments);
    else setLoadErr("Could not load experiments — check the connection and retry.");
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!name.trim()) { setMsg("Name required"); return; }
    setBusy(true); setMsg(null);
    const r = await fetch("/api/research/lab/experiments", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, strategy, hypothesis }),
    }).then(r => r.json()).catch(() => null);
    setBusy(false);
    if (!r) { setMsg("Request failed — the server didn't respond. Try again."); return; }
    if (r.error) { setMsg(r.error); return; }
    setName(""); setHypothesis(""); load();
  };

  const advance = async (e: Experiment, target: string) => {
    setBusy(true); setMsg(null);
    // Structural moves only — archive, reopen, demote, and the walk-forward
    // step that carries no new evidence.
    //
    // This used to attach literal metrics (sharpe 1.0, oos_sharpe 0.9) when
    // advancing to BACKTESTED or PAPER, and the server's gates, which
    // evaluate whatever dict they are handed, cleared them. A strategy could
    // reach a validated stage without anything having been measured. Evidence
    // is now built server-side from a stored run; advancing to BACKTESTED
    // goes through advanceToBacktested(), which runs a real backtest first.
    const body: any = { target };
    const r = await fetch(`/api/research/lab/experiments/${e.id}/transition`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(r => r.json()).catch(() => null);
    setBusy(false);
    // Surface every failure mode rather than hanging silently: network error,
    // an error payload, or a gate rejection (ok === false).
    if (!r) { setMsg(`${e.name}: request failed — the server didn't respond.`); return; }
    if (r.error) { setMsg(`${e.name}: ${r.error}`); return; }
    if (r.ok === false) { setMsg(`${e.name}: ${r.reason || "transition rejected by gate"}`); }
    load();
  };

  // Runs a real backtest (POST /run, poll /results) and returns the
  // completed result, or throws — on backtest failure, a poll timeout, or
  // a network error. Never falls back to fabricated metrics; the caller
  // only proceeds to /transition after this resolves successfully.
  const runRealBacktest = async (exp: Experiment) => {
    const run = await (api.runBacktest({
      strategy: exp.strategy, ...RESEARCH_BACKTEST_RANGE,
    }) as Promise<any>);
    const runId = run?.run_id;
    if (!runId) throw new Error("backtest did not return a run id");
    setBacktestRuns(prev => ({ ...prev, [exp.id]: { status: run.status ?? "queued", runId } }));

    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));
      const res = await (api.getBacktestResults(runId) as Promise<any>);
      setBacktestRuns(prev => ({ ...prev, [exp.id]: { status: res.status, runId } }));
      if (res.status === "completed") return { ...res, run_id: runId };
      if (res.status === "failed") throw new Error(res.error || "backtest failed");
    }
    throw new Error("backtest timed out after 3 minutes — check server logs");
  };

  const advanceToBacktested = async (e: Experiment) => {
    setMsg(null);
    try {
      const result = await runRealBacktest(e);
      // Send the RUN ID, not the numbers. The server loads that run, reads its
      // stored metrics, checks the strategy matches, and records where they
      // came from. Posting metrics from here — even these real ones — would
      // leave the route unable to tell them from typed ones, which is the
      // hole this closes. Key mapping (sharpe_ratio → sharpe) now happens
      // server-side too.
      const r = await (api.transitionExperiment(e.id, {
        target: "backtested", backtest_run_id: result.run_id,
      }) as Promise<any>);
      if (r?.error) { setMsgKind("error"); setMsg(`${e.name}: ${r.error}`); return; }
      if (r?.ok === false) { setMsgKind("gate"); setMsg(`${e.name}: ${r.reason || "gate rejected"}`); return; }
      setMsgKind("info");
      setMsg(`${e.name}: backtest complete (sharpe ${result.sharpe_ratio.toFixed(2)}) — advanced to BACKTESTED.`);
    } catch (err: any) {
      setMsgKind("error");
      setMsg(`${e.name}: backtest error — ${err.message}`);
    } finally {
      setBacktestRuns(prev => { const { [e.id]: _drop, ...rest } = prev; return rest; });
      load();
    }
  };

  return (
    <div className="page-shell">
      <div className="instrument-card page-header">
        <div>
          <div className="page-header__title">Research Lab</div>
          <p className="page-header__sub">Hypothesis → backtest → paper → promoted</p>
        </div>
      </div>

      {/* AI Research Assistant */}
      <div className="instrument-card" style={{ padding: 16 }}>
        <div className="panel-title" style={{ marginBottom: 12 }}>AI Research Assistant</div>
        <div style={{ display: "flex", gap: 10 }}>
          <input value={question} onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") ask(); }}
            placeholder="e.g. Which strategy performs best in high IV? Why is bull put declining?"
            className="mono control-input" style={{ flex: 1 }} />
          <button onClick={ask} disabled={asking} className="btn-primary">
            {asking ? "…" : "Ask"}
          </button>
        </div>
        {answer && (
          <div className="mono" style={{ marginTop: 12, fontSize: 12, lineHeight: 1.6,
            color: answer.error ? "var(--amber)" : "var(--ink)", whiteSpace: "pre-wrap" }}>
            {answer.error
              ? <>{answer.error}{answer.hint ? <div style={{ color: "var(--ink-dim)", marginTop: 6 }}>{answer.hint}</div> : null}</>
              : <>{answer.answer}
                  <div style={{ color: "var(--ink-faint)", fontSize: 9.5, marginTop: 8 }}>via {answer.provider}</div>
                </>}
          </div>
        )}
      </div>

      {/* New experiment */}
      <div className="instrument-card" style={{ padding: 16 }}>
        <div className="panel-title" style={{ marginBottom: 12 }}>New Experiment</div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ flex: 2, minWidth: 180 }}>
            <div className="kicker" style={{ marginBottom: 4 }}>Name</div>
            <input value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. SPY mean-revert condor" className="mono"
              style={inp} />
          </label>
          <label style={{ flex: 1, minWidth: 160 }}>
            <div className="kicker" style={{ marginBottom: 4 }}>Strategy</div>
            <select value={strategy} onChange={e => setStrategy(e.target.value)} className="mono" style={inp}>
              {STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label style={{ flex: 3, minWidth: 200 }}>
            <div className="kicker" style={{ marginBottom: 4 }}>Hypothesis</div>
            <input value={hypothesis} onChange={e => setHypothesis(e.target.value)}
              placeholder="Why should this have an edge?" className="mono" style={inp} />
          </label>
          <button onClick={create} disabled={busy} className="mono" style={btn}>
            {busy ? "…" : "Create"}
          </button>
        </div>
        {msg && <div className="mono" style={{ fontSize: 11, marginTop: 10,
          color: msgKind === "error" ? "var(--red)" : "var(--amber)" }}>{msg}</div>}
      </div>

      {/* Experiments */}
      {loadErr && (
        <div className="mono" style={{ fontSize: 11, color: "var(--red)", marginBottom: 12,
          padding: "8px 12px", border: "1px solid var(--red)", background: "rgba(239,68,68,0.06)" }}>
          ⚠ {loadErr}{" "}
          <button onClick={load} className="mono"
            style={{ marginLeft: 8, background: "none", border: "1px solid var(--red)",
              color: "var(--red)", cursor: "pointer", padding: "1px 8px", fontSize: 10 }}>
            Retry
          </button>
        </div>
      )}
      {exps.length === 0
        ? <div className="instrument-card instrument-card--flat empty-chassis">
            <p className="empty-chassis__title">No experiments yet</p>
            <p className="empty-chassis__hint">Create a hypothesis above to start the promotion funnel.</p>
          </div>
        : exps.map(e => (
          <div key={e.id} className="instrument-card" style={{ padding: 14, marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
                <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>{e.name}</span>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-dim)" }}>{e.strategy}</span>
              </div>
              <Pipeline stage={e.stage} />
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {NEXT[e.stage] && (
                  e.stage === "draft" ? (
                    backtestRuns[e.id] ? (
                      <span className="mono" style={{ fontSize: 10.5, color: "var(--amber)" }}>
                        running backtest… ({backtestRuns[e.id].status})
                      </span>
                    ) : (
                      <button onClick={() => advanceToBacktested(e)} disabled={busy || !!backtestRuns[e.id]}
                        className="mono" style={btnSm}>
                        → backtested
                      </button>
                    )
                  ) : (
                    <button onClick={() => advance(e, NEXT[e.stage]!)} disabled={busy} className="mono" style={btnSm}>
                      → {NEXT[e.stage]}
                      {NEXT[e.stage] === "paper" && <DemoBadge />}
                    </button>
                  )
                )}
                {e.stage !== "archived" && e.stage !== "promoted" && (
                  <button onClick={() => advance(e, "archived")} disabled={busy} className="mono"
                    style={{ ...btnSm, color: "var(--ink-dim)" }}>archive</button>
                )}
              </div>
            </div>
            {e.hypothesis && <div className="mono" style={{ fontSize: 11, color: "var(--ink-dim)", marginTop: 8 }}>{e.hypothesis}</div>}
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginTop: 10, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--ink-dim)" }}>
              {e.backtest_metrics && <span>backtest sharpe <b style={{ color: "var(--cyan)" }}>{e.backtest_metrics.sharpe}</b> · DD {e.backtest_metrics.max_drawdown_pct}%</span>}
              {e.paper_perf && <span>paper win <b style={{ color: "var(--ink)" }}>{Math.round((e.paper_perf.win_rate || 0) * 100)}%</b> · exp {e.paper_perf.expectancy}</span>}
              {e.baseline && <span style={{ color: "var(--green)" }}>baseline win {Math.round((e.baseline.win_rate || 0) * 100)}% · exp {e.baseline.expectancy}</span>}
            </div>
          </div>
        ))}
    </div>
  );
}

function DemoBadge() {
  return (
    <span title="Uses placeholder walk-forward metrics — real out-of-sample evaluation not built yet."
      style={{ marginLeft: 6, color: "var(--amber)", fontSize: 9, fontWeight: 700 }}>
      (demo)
    </span>
  );
}

function Pipeline({ stage }: { stage: string }) {
  const idx = STAGES.indexOf(stage as any);
  if (stage === "archived")
    return <span className="mono" style={{ fontSize: 10, color: STAGE_COLOR.archived }}>ARCHIVED</span>;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      {STAGES.map((s, i) => (
        <React.Fragment key={s}>
          <span className="mono" style={{ fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
            padding: "2px 8px", borderRadius: 10,
            color: i <= idx ? STAGE_COLOR[s] : "var(--ink-faint)",
            background: i <= idx ? "var(--bg-2)" : "transparent",
            border: `1px solid ${i <= idx ? STAGE_COLOR[s] : "var(--line-dim)"}` }}>
            {s.toUpperCase()}
          </span>
          {i < STAGES.length - 1 && <span style={{ color: i < idx ? STAGE_COLOR[STAGES[i + 1]] : "var(--ink-faint)" }}>›</span>}
        </React.Fragment>
      ))}
    </div>
  );
}

const inp: React.CSSProperties = {
  width: "100%", padding: "8px 10px", fontSize: 12, color: "var(--ink)",
  background: "var(--bg-2)", border: "1px solid var(--line-dim)", borderRadius: 4,
};
const btn: React.CSSProperties = {
  padding: "9px 18px", fontSize: 12, fontWeight: 600, color: "var(--bg-0)",
  background: "var(--cyan)", border: "none", borderRadius: 4, cursor: "pointer",
};
const btnSm: React.CSSProperties = {
  padding: "5px 12px", fontSize: 10.5, color: "var(--ink)",
  background: "var(--bg-2)", border: "1px solid var(--line-dim)", borderRadius: 4, cursor: "pointer",
};
