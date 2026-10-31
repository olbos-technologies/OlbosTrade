/**
 * Command Overview — Trade Desk landing. Read-only queues from existing APIs.
 * Density: one readout strip + primary pending/blocked/submitted queues + signal feeds.
 */

import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import type { TradeDeskTab } from "./TradeDeskTabs";
import { formatConfidenceFloor } from "../hooks/useTradingStyleFloor";
import { useDeskBlockContext } from "../hooks/useDeskBlockContext";
import { deriveSignalBlockReason } from "../utils/signalBlockReason";
import { deskNextAction } from "../utils/deskNextAction";
import { lifecycleFromExecution } from "./executionStatus";

interface QueueCardProps {
  title: string;
  count: number | string;
  items: string[];
  empty: string;
  tone?: "ok" | "warn" | "crit" | "muted";
  onOpen?: () => void;
  actionLabel?: string;
  /** Larger primary queue column */
  primary?: boolean;
}

function QueueCard({
  title, count, items, empty, tone = "muted", onOpen, actionLabel, primary,
}: QueueCardProps) {
  const border =
    tone === "crit" ? "rgba(239,68,68,0.35)" :
    tone === "warn" ? "rgba(245,158,11,0.35)" :
    tone === "ok" ? "rgba(34,197,94,0.3)" :
    "var(--line-dim)";

  return (
    <div
      className="instrument-card"
      style={{
        border: `1px solid ${border}`,
        padding: primary ? "16px 18px" : "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minHeight: primary ? 220 : 160,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: primary ? 12 : 11,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--ink-dim)",
          }}
        >
          {title}
        </span>
        <span
          style={{
            fontFamily: "var(--mono)",
            fontSize: primary ? 22 : 18,
            fontWeight: 700,
            color: "var(--ink)",
          }}
        >
          {count}
        </span>
      </div>
      {items.length === 0 ? (
        <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)", flex: 1 }}>
          {empty}
        </p>
      ) : (
        <ul
          style={{
            margin: 0,
            padding: 0,
            listStyle: "none",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            flex: 1,
          }}
        >
          {items.slice(0, primary ? 8 : 5).map((line, i) => (
            <li
              key={i}
              style={{
                fontFamily: "var(--mono)",
                fontSize: primary ? 12 : 11,
                color: "var(--ink)",
                borderBottom: "1px solid var(--line-dim)",
                paddingBottom: 4,
              }}
            >
              {line}
            </li>
          ))}
        </ul>
      )}
      {onOpen && (
        <button
          type="button"
          className="btn-t"
          onClick={onOpen}
          style={{ alignSelf: "flex-start", fontSize: 10, letterSpacing: "0.08em" }}
        >
          {actionLabel || "Open"}
        </button>
      )}
    </div>
  );
}

interface ScoreCardProps {
  title: string;
  score: number | null;
  scoreSuffix?: string;
  scoreLabel: string;
  stats: { label: string; value: string; tone?: string }[];
  items: string[];
  empty: string;
  onOpen?: () => void;
  actionLabel?: string;
}

function ScoreCard({
  title, score, scoreSuffix = "%", scoreLabel, stats, items, empty, onOpen, actionLabel,
}: ScoreCardProps) {
  const scoreColor =
    score == null ? "var(--ink-faint)" :
    score >= 70 ? "var(--green)" :
    score >= 40 ? "var(--amber)" : "var(--red)";

  return (
    <div
      className="instrument-card"
      style={{
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minHeight: 180,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <span
          style={{
            fontFamily: "var(--mono)", fontSize: 11, letterSpacing: "0.1em",
            textTransform: "uppercase", color: "var(--ink-dim)",
          }}
        >
          {title}
        </span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink-faint)" }}>
          {scoreLabel}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 24, fontWeight: 700, color: scoreColor }}>
          {score == null ? "—" : score.toFixed(0)}
        </span>
        {score != null && (
          <span style={{ fontFamily: "var(--mono)", fontSize: 13, color: scoreColor }}>{scoreSuffix}</span>
        )}
      </div>

      {stats.length > 0 && (
        <div style={{ display: "flex", gap: 0, border: "1px solid var(--line-dim)" }}>
          {stats.map((s, i) => (
            <div
              key={s.label}
              style={{
                flex: 1, padding: "6px 8px",
                borderRight: i < stats.length - 1 ? "1px solid var(--line-dim)" : undefined,
              }}
            >
              <div style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--ink-faint)", marginBottom: 2 }}>
                {s.label}
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 600, color: s.tone || "var(--ink)" }}>
                {s.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {items.length === 0 ? (
        <p style={{ margin: 0, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)", flex: 1 }}>
          {empty}
        </p>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
          {items.slice(0, 5).map((line, i) => (
            <li
              key={i}
              style={{
                fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink)",
                borderBottom: "1px solid var(--line-dim)", paddingBottom: 4,
              }}
            >
              {line}
            </li>
          ))}
        </ul>
      )}

      {onOpen && (
        <button
          type="button"
          className="btn-t"
          onClick={onOpen}
          style={{ alignSelf: "flex-start", fontSize: 10, letterSpacing: "0.08em" }}
        >
          {actionLabel || "Open"}
        </button>
      )}
    </div>
  );
}

function ReadoutCell({
  label,
  value,
  tone = "muted",
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "crit" | "muted";
}) {
  const color =
    tone === "ok" ? "var(--green)" :
    tone === "warn" ? "var(--amber)" :
    tone === "crit" ? "var(--red)" :
    "var(--ink)";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "10px 14px",
        borderRight: "1px solid var(--line-dim)",
        minWidth: 100,
      }}
    >
      <span
        style={{
          fontFamily: "var(--mono)",
          fontSize: 9,
          letterSpacing: "0.1em",
          textTransform: "uppercase",
          color: "var(--ink-faint)",
        }}
      >
        {label}
      </span>
      <span style={{ fontFamily: "var(--mono)", fontSize: 14, fontWeight: 700, color }}>
        {value}
      </span>
    </div>
  );
}

export default function CommandOverview({
  onNavigateTab,
}: {
  onNavigateTab: (tab: TradeDeskTab) => void;
}) {
  const [pending, setPending] = useState<any[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [execLog, setExecLog] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [heat, setHeat] = useState<string>("—");
  const [heatTone, setHeatTone] = useState<"ok" | "warn" | "crit" | "muted">("muted");
  const [riskBudget, setRiskBudget] = useState("—");
  const [equitySignals, setEquitySignals] = useState<any[]>([]);
  const [optionsSignals, setOptionsSignals] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState({ pending: false, log: false, positions: false, equity: false, options: false });
  const blockCtx = useDeskBlockContext();
  const { minConfidence } = blockCtx;

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [pend, log, pos, risk, eqSig, optSig, heatResp] = await Promise.all([
          api.getPendingApprovals().catch(() => null),
          api.getExecutionLog().catch(() => null),
          api.getPositions().catch(() => null),
          api.getPortfolioState().catch(() => ({})),
          api.getEquitySignals(20).catch(() => null),
          api.getOptionsSignals(20).catch(() => null),
          fetch("/api/portfolio/heat").then((r) => { if (!r.ok) throw new Error("Heat unavailable"); return r.json(); }).catch(() => ({})),
        ]);
        if (!alive) return;
        const p = (pend as any)?.pending || [];
        setAvailable({ pending: Array.isArray((pend as any)?.pending), log: Array.isArray((log as any)?.events) || Array.isArray((log as any)?.log), positions: Array.isArray((pos as any)?.positions) || Array.isArray(pos), equity: Array.isArray((eqSig as any)?.signals), options: Array.isArray((optSig as any)?.signals) });
        setPending(p);
        setPendingCount((pend as any)?.count ?? p.length);
        setExecLog((log as any)?.events || (log as any)?.log || []);
        setPositions((pos as any)?.positions || (Array.isArray(pos) ? pos : []));

        const s = (risk as any).state || risk;
        if (typeof s.daily_loss_pct === "number" && typeof s.max_daily_loss_pct === "number") {
          const lossUsed = Math.max(0, -s.daily_loss_pct);
          const rem = Math.max(0, s.max_daily_loss_pct - lossUsed);
          setRiskBudget(`${(rem * 100).toFixed(1)}%`);
        } else {
          setRiskBudget("—");
        }

        const h = heatResp as any;
        if (typeof h.portfolio_heat_pct === "number") {
          setHeat(`${h.portfolio_heat_pct.toFixed(1)}%`);
          setHeatTone(
            h.heat_status === "high" ? "crit" :
            h.heat_status === "elevated" ? "warn" : "ok",
          );
        } else if (typeof h.heat_pct === "number") {
          const pct = h.heat_pct <= 1 ? h.heat_pct * 100 : h.heat_pct;
          setHeat(`${pct.toFixed(1)}%`);
          setHeatTone(pct >= 70 ? "crit" : pct >= 40 ? "warn" : "ok");
        } else {
          setHeat(h.error ? "Unavailable" : "—");
          setHeatTone("muted");
        }

        setEquitySignals((eqSig as any)?.signals || []);
        setOptionsSignals((optSig as any)?.signals || []);
        setError(null);
      } catch (e: any) {
        if (alive) setError(e?.message || "Failed to load overview");
      }
    };
    load();
    const t = setInterval(load, 20000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const openCount = positions.filter(
    (p: any) =>
      !p.status ||
      String(p.status).toLowerCase() === "open" ||
      p.status === "OPEN",
  ).length;
  const maxOpen = blockCtx.maxConcurrent ?? 5;
  const consecLim = blockCtx.maxConsecutiveLosses;
  const consecLabel =
    consecLim != null
      ? `${blockCtx.consecutiveLosses}/${consecLim}`
      : String(blockCtx.consecutiveLosses);

  const pendingLines = pending.slice(0, 8).map((p) => {
    const t = p.ticker || p.signal?.ticker || "?";
    const a = p.action || p.signal?.action || "";
    const src = p.source || p.signal?.source || "pending";
    return `${t} ${a} · ${src}`.trim();
  });

  const actionableEquity = equitySignals.filter((s) => s.action === "BUY" || s.action === "SELL");
  const blockedSignalLines = actionableEquity
    .map((s) => {
      const block = deriveSignalBlockReason(s, blockCtx);
      if (!block) return null;
      const conf = formatConfidenceFloor(
        typeof s.confidence === "number" ? s.confidence : null,
        minConfidence,
      ).text;
      return `${s.ticker} ${s.action} · ${block.label} · ${conf}`;
    })
    .filter(Boolean) as string[];

  const execBlocked = execLog.filter((e) => {
    const life = lifecycleFromExecution(e);
    return life === "blocked" || life === "rejected" || life === "error";
  });
  const execBlockedLines = execBlocked.slice(0, 8).map((e) => {
    const t = e.ticker || e.signal_id || e.kind || "event";
    const life = lifecycleFromExecution(e);
    const reason = e.reason || e.result?.reason || e.status || life;
    return `${t} · ${reason}`;
  });

  // Prefer live signal blocks; fall back to recent exec failures.
  const blockedLines =
    blockedSignalLines.length > 0
      ? blockedSignalLines.slice(0, 8)
      : execBlockedLines;

  const submitted = execLog.filter((e) => lifecycleFromExecution(e) === "submitted");
  const submittedLines = submitted.slice(0, 8).map((e) => {
    const t = e.ticker || e.signal_id || "?";
    const a = e.action || e.signal?.action || "";
    return `${t} ${a} · submitted`.trim();
  });

  const equityAvgConfidence =
    actionableEquity.length > 0
      ? (actionableEquity.reduce((sum, s) => sum + (s.confidence || 0), 0) / actionableEquity.length) * 100
      : null;
  const equityBuyCount = actionableEquity.filter((s) => s.action === "BUY").length;
  const equitySellCount = actionableEquity.filter((s) => s.action === "SELL").length;
  const equityLines = actionableEquity.slice(0, 5).map((s) => {
    const conf = formatConfidenceFloor(
      typeof s.confidence === "number" ? s.confidence : null,
      minConfidence,
    ).text;
    const block = deriveSignalBlockReason(s, blockCtx);
    const suffix = block ? ` · blocked: ${block.label}` : "";
    return `${s.ticker} ${s.action} · ${conf}${suffix}`;
  });

  const optionsAvgConfidence =
    optionsSignals.length > 0
      ? (optionsSignals.reduce((sum, s) => sum + (s.confidence || 0), 0) / optionsSignals.length) * 100
      : null;
  const optionsCreditCount = optionsSignals.filter((s) => (s.spread?.net_credit ?? 0) > 0).length;
  const optionsDebitCount = optionsSignals.filter((s) => (s.spread?.net_credit ?? 0) < 0).length;
  const optionsLines = optionsSignals.slice(0, 5).map((s) => {
    const action = s.action ? s.action.replace(/_/g, " ") : "—";
    const side = s.action === "BUY_SPREAD" ? "LONG" : s.action === "SELL_SPREAD" ? "SHORT" : "";
    const strat = s.strategy ? s.strategy.replace(/_/g, " ") : "";
    const conf = formatConfidenceFloor(
      typeof s.confidence === "number" ? s.confidence : null,
      minConfidence,
    ).text;
    const block = deriveSignalBlockReason(
      { action: s.action || "BUY", confidence: s.confidence },
      blockCtx,
    );
    const suffix = block ? ` · blocked: ${block.label}` : "";
    const sideLabel = side ? ` ${side}` : "";
    const stratLabel = strat ? ` · ${strat}` : "";
    return `${s.ticker} ${action}${sideLabel}${stratLabel} · ${conf}${suffix}`;
  });

  const openTone: "ok" | "warn" | "crit" | "muted" =
    openCount >= maxOpen ? "warn" : openCount > 0 ? "ok" : "muted";
  const consecTone: "ok" | "warn" | "crit" | "muted" =
    !blockCtx.tradingAllowed ? "crit" :
    consecLim != null && blockCtx.consecutiveLosses >= Math.max(1, consecLim - 2) ? "warn" :
    "muted";
  const ksTone: "ok" | "warn" | "crit" | "muted" = blockCtx.killEngaged ? "crit" : "ok";
  const nextAction = available.positions && blockCtx.guardrailAvailable && blockCtx.killStatusAvailable ? deskNextAction(blockCtx, openCount) : null;

  const pendingEmpty = nextAction && pendingCount === 0
    ? nextAction
    : "No Copilot approvals waiting.";
  const blockedEmpty = nextAction && !blockCtx.tradingAllowed
    ? nextAction
    : "No live signal blocks or recent rejects.";
  const submittedEmpty =
    openCount <= 0 && blockCtx.tradingAllowed
      ? "Flat · nothing submitted yet — queue a signal that clears the style floor"
      : "No recent submitted events in the execution log.";

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14, width: "100%", boxSizing: "border-box" }}>
      <div className="instrument-card page-header" style={{ margin: 0 }}>
        <div>
          <div className="page-header__title">Command Overview</div>
          <p className="page-header__sub">
            Pending · blocked · submitted. Read-only — execution stays on existing Trade Desk paths.
          </p>
        </div>
      </div>

      {error && (
        <div
          style={{
            border: "1px solid rgba(239,68,68,0.35)",
            padding: "10px 12px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            color: "var(--red)",
          }}
        >
          {error}
        </div>
      )}

      {/* #5 Positions / risk readout strip — always visible */}
      <div
        className="instrument-rail"
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "stretch",
          overflow: "hidden",
        }}
        aria-label="Desk positions and risk readout"
      >
        <ReadoutCell label="Open" value={available.positions ? `${openCount}/${maxOpen}` : "Unavailable"} tone={available.positions ? openTone : "muted"} />
        <ReadoutCell label="Heat" value={heat} tone={heatTone} />
        <ReadoutCell label="Consec losses" value={blockCtx.guardrailAvailable ? consecLabel : "Unavailable"} tone={blockCtx.guardrailAvailable ? consecTone : "muted"} />
        <ReadoutCell label="Risk budget" value={riskBudget} tone={riskBudget === "—" ? "muted" : "ok"} />
        <ReadoutCell
          label="Kill switch"
          value={!blockCtx.killStatusAvailable ? "Unavailable" : blockCtx.killEngaged ? "ENGAGED" : "Clear"}
          tone={blockCtx.killStatusAvailable ? ksTone : "muted"}
        />
        <ReadoutCell
          label="Trading"
          value={!blockCtx.guardrailAvailable ? "Unavailable" : blockCtx.tradingAllowed ? "Allowed" : "Suspended"}
          tone={!blockCtx.guardrailAvailable ? "muted" : blockCtx.tradingAllowed ? "ok" : "crit"}
        />
        <div style={{ flex: 1, minWidth: 8 }} />
        <div style={{ display: "flex", alignItems: "center", padding: "8px 12px", gap: 8 }}>
          <button
            type="button"
            className="btn-t"
            onClick={() => onNavigateTab("positions")}
            style={{ fontSize: 10, letterSpacing: "0.08em" }}
          >
            Positions
          </button>
        </div>
      </div>

      {nextAction && (
        <div
          style={{
            border: "1px solid rgba(245,158,11,0.35)",
            background: "rgba(245,158,11,0.06)",
            padding: "10px 14px",
            fontFamily: "var(--mono)",
            fontSize: 11,
            color: "var(--amber)",
            letterSpacing: "0.02em",
          }}
          role="status"
        >
          Next · {nextAction}
        </div>
      )}

      {/* #4 Primary queues — pending / blocked / submitted */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 12,
        }}
      >
        <QueueCard
          primary
          title="Pending"
          count={available.pending ? pendingCount : "—"}
          items={pendingLines}
          empty={available.pending ? pendingEmpty : "Approval queue unavailable."}
          tone={pendingCount > 0 ? "warn" : "muted"}
          onOpen={() => onNavigateTab("copilot")}
          actionLabel="Review queue"
        />
        <QueueCard
          primary
          title={blockedSignalLines.length > 0 ? "Blocked signals" : "Recent execution failures"}
          count={blockedSignalLines.length > 0 ? blockedSignalLines.length : available.log ? execBlocked.length : "—"}
          items={blockedLines}
          empty={available.log && available.equity ? blockedEmpty : "Some signal or execution data is unavailable."}
          tone={(blockedLines.length || execBlocked.length) > 0 ? "crit" : "muted"}
          onOpen={() => onNavigateTab("execution")}
          actionLabel="Execution log"
        />
        <QueueCard
          primary
          title="Submitted"
          count={available.log ? submitted.length : "—"}
          items={submittedLines}
          empty={available.log ? submittedEmpty : "Execution log unavailable."}
          tone={submitted.length > 0 ? "ok" : "muted"}
          onOpen={() => onNavigateTab("execution")}
          actionLabel="Execution log"
        />
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          gap: 12,
        }}
      >
        <ScoreCard
          title="Equities"
          score={equityAvgConfidence}
          scoreLabel="avg confidence"
          stats={[
            { label: "BUY", value: available.equity ? String(equityBuyCount) : "—", tone: "var(--green)" },
            { label: "SELL", value: available.equity ? String(equitySellCount) : "—", tone: "var(--red)" },
            { label: "SIGNALS", value: available.equity ? String(actionableEquity.length) : "—" },
          ]}
          items={equityLines}
          empty={available.equity ? "No actionable equity signals right now." : "Equity signals unavailable."}
          onOpen={() => onNavigateTab("equities")}
          actionLabel="Desk signals"
        />
        <ScoreCard
          title="Options"
          score={optionsAvgConfidence}
          scoreLabel="avg confidence"
          stats={[
            { label: "CREDIT", value: available.options ? String(optionsCreditCount) : "—", tone: "var(--green)" },
            { label: "DEBIT", value: available.options ? String(optionsDebitCount) : "—", tone: "var(--amber)" },
            { label: "SIGNALS", value: available.options ? String(optionsSignals.length) : "—" },
          ]}
          items={optionsLines}
          empty={available.options ? "No recent options spread signals." : "Options signals unavailable."}
          onOpen={() => onNavigateTab("options")}
          actionLabel="Spread Scanner"
        />
      </div>
    </div>
  );
}
