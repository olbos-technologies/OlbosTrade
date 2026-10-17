/**
 * ExecutiveSummary — Bloomberg-style dashboard header.
 *
 *   KPI cards (Total Equity / Total P&L / Day P&L)
 *   Performance panel (Sharpe, Sortino, Calmar, win rate, profit factor, …)
 *   Portfolio Heat gauge (capital at risk + exposures + concentration flags)
 *   System Health checklist
 *
 * Data: /api/dashboard/summary, /api/analytics/performance, /api/portfolio/heat
 * (polled every 15s).
 */
import React, { useEffect, useState } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import MetricHint, { resolveMetricHint } from "./MetricHint";

// ── Inline icon set ─────────────────────────────────────────────────────────
const ICON: Record<string, string> = {
  dollar:   "M12 1v22 M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6",
  pnl:      "M3 3v18h18 M7 16v2 M12 11v7 M17 7v11",
  trend:    "M23 6l-9.5 9.5-5-5L1 18 M17 6h6v6",
  activity: "M22 12h-4l-3 9L9 3l-3 9H2",
  gauge:    "M12 14a2 2 0 100-4 2 2 0 000 4z M12 14l4-4 M3.5 18a9 9 0 1117 0",
  perf:     "M3 3v18h18 M7 14l3-3 3 3 5-6",
  check:    "M22 11.08V12a10 10 0 11-5.93-9.14 M22 4L12 14.01l-3-3",
  warn:     "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z M12 9v4 M12 17h.01",
  cross:    "M12 22a10 10 0 100-20 10 10 0 000 20z M15 9l-6 6 M9 9l6 6",
  strategy: "M12 2L2 7l10 5 10-5-10-5z M2 17l10 5 10-5 M2 12l10 5 10-5",
  risk:     "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
};

const Icon = ({ name, size = 14, color = "currentColor" }: { name: string; size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
    strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
    {/* Defensive: an unknown icon name must never crash the dashboard (falls back
        to the activity glyph instead of throwing on undefined.split). */}
    {(ICON[name] || ICON.activity).split(" M").map((seg, i) => <path key={i} d={(i ? "M" : "") + seg} />)}
  </svg>
);

// ── Types ───────────────────────────────────────────────────────────────────
type Health = { name: string; status: "ok" | "warn" | "error"; detail: string };
interface Summary {
  // null when the broker read failed — never substitute a computed value.
  total_equity: number | null; total_equity_source?: "broker" | "unavailable";
  total_pnl: number; total_pnl_pct: number;
  day_pnl: number; day_pnl_pct: number; health: Health[]; issues: number;
}
interface Performance {
  total_trades: number; win_rate: number; profit_factor: number; expectancy: number;
  payoff_ratio: number; avg_hold_days: number; sharpe: number; sortino: number;
  calmar: number; max_drawdown_pct: number; cagr_pct: number;
  current_drawdown_pct: number; rolling_max_dd_30_pct: number; sample_size_warning: boolean;
}
interface Heat {
  portfolio_heat_pct: number; heat_status: "ok" | "elevated" | "high";
  total_risk_dollars: number; position_count: number;
  largest_underlying: string | null; largest_underlying_pct: number;
  largest_sector: string | null; largest_sector_pct: number;
  exposure_by_underlying: Record<string, number>; concentration_flags: string[];
}
interface StratHealth {
  strategy: string; status: "healthy" | "watch" | "degraded" | "insufficient_data";
  action: "none" | "reduce" | "suspend"; score: number; sample_size: number;
  win_rate: number; expectancy: number; drawdown_pct: number;
  expectancy_ratio: number | null; reasons: string[];
}
interface StratHealthResp { strategies: StratHealth[]; suspended: string[]; total_strategies: number; }
interface MetaDecision {
  strategy: string; active: boolean; tilt: number; health_status: string;
  health_score: number; regime_sanctioned: boolean; reason: string;
}
interface MetaResp { regime: string | null; decisions: MetaDecision[]; active_strategies: string[]; }
interface AllocResp { method: string; weights: Record<string, number>; cash_weight: number; regime?: string; }
interface ScenarioRow { scenario: string; portfolio_pnl: number; portfolio_pnl_pct: number | null; }
interface ScenariosResp {
  scenarios: ScenarioRow[]; worst_scenario: string | null; worst_pnl: number;
  excluded_symbols?: { ticker: string; reason: string }[];
}
interface VarResp {
  available?: boolean; reason?: string;
  var: number | null; expected_shortfall: number | null; var_pct: number | null; es_pct: number | null;
  confidence: number; spot_price?: number; spot_source?: string; spot_data_status?: string;
}
interface HealthDetail {
  scanner: { alive: boolean; last_tick_age_seconds: number | null };
  observability: { counters: Record<string, number>; uptime_seconds: number };
}

// ── Formatters ──────────────────────────────────────────────────────────────
const money = (n: number) =>
  (n >= 0 ? "+" : "-") + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const plain = (n: number) =>
  "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const STATUS = { ok: "var(--green)", warn: "var(--amber)", error: "var(--red)" } as const;
const HEAT = { ok: "var(--green)", elevated: "var(--amber)", high: "var(--red)" } as const;
const STRAT_STATUS: Record<string, { color: string; tint: string; icon: string; label: string }> = {
  healthy:           { color: "var(--green)", tint: "rgba(34,197,94,0.05)", icon: "check", label: "HEALTHY" },
  watch:             { color: "var(--amber)", tint: "rgba(245,158,11,0.06)", icon: "warn",  label: "WATCH" },
  degraded:          { color: "var(--red)",   tint: "rgba(239,68,68,0.06)",  icon: "cross", label: "SUSPENDED" },
  insufficient_data: { color: "var(--ink-dim)", tint: "transparent",         icon: "activity", label: "NO DATA" },
};

// ── KPI card ────────────────────────────────────────────────────────────────
// Existing rgba tint literals already used elsewhere in this app for the
// same 3 colors (.mode-badge.conservative / .signal-badge.bullish / .mode-badge.balanced)
// — reused here rather than introducing color-mix() or new tokens.
const BADGE_TINT: Record<string, string> = {
  "var(--green)":  "rgba(34,197,94,0.15)",
  "var(--red)":    "rgba(239,68,68,0.15)",
  "var(--accent)": "var(--cyan-dim)",
};

function Kpi({ label, value, sub, color, icon }:
  { label: string; value: string; sub?: string; color?: string; icon: string }) {
  const badgeColor = color || "var(--accent)";
  return (
    <div
      className="instrument-card exec-card"
      style={{ flex: 1, minWidth: 210, padding: "16px 18px", ["--tab-color" as any]: badgeColor }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <span className="kicker">{label}</span>
        <span style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
          background: BADGE_TINT[badgeColor] ?? BADGE_TINT["var(--accent)"],
        }}>
          <Icon name={icon} size={15} color={badgeColor} />
        </span>
      </div>
      <div className="mono" style={{ fontSize: 27, fontWeight: 700, color: color || "var(--ink)", lineHeight: 1, letterSpacing: "-0.02em" }}>
        {value}
      </div>
      {sub && <div className="mono" style={{ fontSize: 11, color: "var(--ink-dim)", marginTop: 8 }}>{sub}</div>}
    </div>
  );
}

// ── Small metric tile ───────────────────────────────────────────────────────
function Tile({ label, value, color }: { label: string; value: string; color?: string }) {
  const hinted = !!resolveMetricHint(label);
  return (
    <div className="instrument-card--flat" style={{ padding: "11px 13px" }}>
      <div className="kicker" style={{ fontSize: 8.5, marginBottom: 5 }}>
        {hinted ? <MetricHint id={label} /> : label}
      </div>
      <div className="mono" style={{ fontSize: 15, fontWeight: 600, color: color || "var(--ink)" }}>{value}</div>
    </div>
  );
}

function PanelHead({ icon, title, right }: { icon: string; title: string; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "9px 14px", borderBottom: "1px solid var(--line-dim)" }}>
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ color: "var(--cyan)" }}><Icon name={icon} size={13} /></span>
        <span className="panel-title">{title}</span>
      </span>
      {right}
    </div>
  );
}

function HealthRow({ h }: { h: Health }) {
  const color = STATUS[h.status];
  const tint = h.status === "ok" ? "rgba(34,197,94,0.05)"
    : h.status === "warn" ? "rgba(245,158,11,0.06)" : "rgba(239,68,68,0.06)";
  const icon = h.status === "ok" ? "check" : h.status === "warn" ? "warn" : "cross";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 14px",
      background: tint, borderLeft: `2px solid ${color}`, borderBottom: "1px solid var(--line-dim)" }}>
      <span style={{ color }}><Icon name={icon} size={13} /></span>
      <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", minWidth: 128 }}>{h.name}</span>
      <span className="mono" style={{ fontSize: 12, color }}>{h.detail}</span>
    </div>
  );
}

function StratRow({ h }: { h: StratHealth }) {
  const cfg = STRAT_STATUS[h.status] || STRAT_STATUS.insufficient_data;
  const ratio = h.expectancy_ratio !== null ? `${(h.expectancy_ratio * 100).toFixed(0)}% of base` : "—";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 14px",
      background: cfg.tint, borderLeft: `2px solid ${cfg.color}`, borderBottom: "1px solid var(--line-dim)" }}>
      <span style={{ color: cfg.color }}><Icon name={cfg.icon} size={13} /></span>
      <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", minWidth: 150 }}>
        {h.strategy}
      </span>
      <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: "0.08em",
        color: cfg.color, minWidth: 78 }}>{cfg.label}</span>
      <span className="mono" style={{ fontSize: 13, fontWeight: 700, minWidth: 44,
        color: h.score >= 70 ? "var(--green)" : h.score >= 45 ? "var(--amber)" : "var(--red)" }}>
        {h.score.toFixed(0)}
      </span>
      <span className="mono" style={{ fontSize: 11, color: "var(--ink-dim)", display: "flex", gap: 14, flexWrap: "wrap" }}>
        <span>n={h.sample_size}</span>
        <span>win {(h.win_rate * 100).toFixed(0)}%</span>
        <span style={{ color: h.expectancy >= 0 ? "var(--green)" : "var(--red)" }}>
          exp {h.expectancy >= 0 ? "+" : ""}{h.expectancy.toFixed(0)}
        </span>
        <span>{ratio}</span>
      </span>
    </div>
  );
}

export default function ExecutiveSummary() {
  const isMobile = useIsMobile();
  const [s, setS] = useState<Summary | null>(null);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [heat, setHeat] = useState<Heat | null>(null);
  const [strat, setStrat] = useState<StratHealthResp | null>(null);
  const [detail, setDetail] = useState<HealthDetail | null>(null);
  const [meta, setMeta] = useState<MetaResp | null>(null);
  const [alloc, setAlloc] = useState<AllocResp | null>(null);
  const [scen, setScen] = useState<ScenariosResp | null>(null);
  const [varRep, setVarRep] = useState<VarResp | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(() => {
    try {
      return localStorage.getItem("olbos.execSummary.advanced") === "1";
    } catch {
      return false;
    }
  });

  const toggleAdvanced = () => {
    setShowAdvanced((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("olbos.execSummary.advanced", next ? "1" : "0");
      } catch {
        /* ignore quota / private mode */
      }
      return next;
    });
  };

  useEffect(() => {
    const j = (u: string) => fetch(u, { signal: AbortSignal.timeout(12000) }).then(r => { if (!r.ok) throw new Error("Unavailable"); return r.json(); }).then(d => d?.error ? null : d).catch(() => null);
    const load = async () => {
      setS(await j("/api/dashboard/summary"));
      setPerf(await j("/api/analytics/performance"));
      setHeat(await j("/api/portfolio/heat"));
      setStrat(await j("/api/strategy/health"));
      setDetail(await j("/api/health/detail"));
      setMeta(await j("/api/strategy/meta"));
      setAlloc(await j("/api/portfolio/allocation"));
      setScen(await j("/api/risk/scenarios"));
      setVarRep(await j("/api/risk/var"));
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const pnlAvailable = !!s && typeof s.total_pnl === "number" && typeof s.day_pnl === "number" && !s.health?.some(h => h.name === "PostgreSQL" && h.status !== "ok");
  const hasSample = !!perf && perf.total_trades > 0;
  const pnlColor = (n: number) => (n >= 0 ? "var(--green)" : "var(--red)");
  const pct = (n: number) => `${n >= 0 ? "+" : ""}${n}%`;

  return (
    <div className="instrument-card" style={{ padding: "18px 16px 14px", margin: "10px 12px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <span style={{ width: 3, height: 17, background: "var(--cyan)", boxShadow: "0 0 10px var(--cyan-glow)" }} />
        <span className="mono" style={{ fontSize: 14, fontWeight: 700, letterSpacing: "0.16em", color: "var(--ink)" }}>
          EXECUTIVE SUMMARY
        </span>
        <div style={{ flex: 1 }} />
        <button
          type="button"
          className="btn-t"
          onClick={toggleAdvanced}
          aria-expanded={showAdvanced}
        >
          {showAdvanced ? "Hide advanced analytics" : "Show advanced analytics"}
        </button>
      </div>

      {/* KPI cards */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
        <Kpi
          label="Broker account value"
          icon="dollar"
          value={s && s.total_equity != null ? plain(s.total_equity) : "—"}
          sub={s && s.total_equity == null ? "Broker unavailable" : undefined}
        />
        <Kpi label="Recorded closed-trade P&L" icon="pnl"
          value={pnlAvailable ? money(s!.total_pnl) : "—"} color={pnlAvailable ? pnlColor(s!.total_pnl) : undefined}
          sub="Desk-wide closed trades · not broker account return" />
        <Kpi label="Desk day P&L" icon="trend"
          value={pnlAvailable ? money(s!.day_pnl) : "—"} color={pnlAvailable ? pnlColor(s!.day_pnl) : undefined}
          sub="Recorded closed trades today · desk-wide" />
      </div>

      <p style={{fontSize:11,color:"var(--ink-dim)",margin:"0 0 14px",lineHeight:1.5}}>Broker balance, recorded desk-wide P&amp;L, and the performance sample have different scopes. An empty sample is shown as unavailable.</p>
      {/* Performance + Portfolio Heat */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1.4fr 1fr", gap: 12, marginBottom: 14 }}>
        {/* Performance — short strip by default */}
        <div className="instrument-card exec-card">
          <PanelHead icon="perf" title="Performance"
            right={perf && perf.sample_size_warning
              ? <span className="mono" style={{ fontSize: 9.5, color: "var(--amber)" }}>
                  {perf.total_trades} trades · low sample
                </span>
              : perf && <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-dim)" }}>{perf.total_trades} trades</span>} />
          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2,1fr)" : "repeat(4,1fr)", gap: 8, padding: 10 }}>
            <Tile label="Win Rate" value={hasSample ? `${(perf.win_rate * 100).toFixed(0)}%` : "—"}
              color={perf && perf.win_rate >= 0.5 ? "var(--green)" : "var(--ink)"} />
            <Tile label="Profit Factor" value={hasSample ? perf.profit_factor.toFixed(2) : "—"}
              color={perf && perf.profit_factor >= 1.5 ? "var(--green)" : "var(--ink)"} />
            <Tile label="Max DD" value={hasSample ? `${perf.max_drawdown_pct}%` : "—"}
              color={perf && perf.max_drawdown_pct > 15 ? "var(--red)" : "var(--ink)"} />
            <Tile label="Current DD" value={hasSample ? `${perf.current_drawdown_pct}%` : "—"}
              color={perf && perf.current_drawdown_pct > 8 ? "var(--red)" : "var(--ink)"} />
            {showAdvanced && (
              <>
                <Tile label="Sharpe" value={hasSample ? perf.sharpe.toFixed(2) : "—"}
                  color={perf && perf.sharpe >= 1 ? "var(--cyan)" : "var(--ink)"} />
                <Tile label="Sortino" value={hasSample ? perf.sortino.toFixed(2) : "—"}
                  color={perf && perf.sortino >= 1 ? "var(--cyan)" : "var(--ink)"} />
                <Tile label="Calmar" value={hasSample ? perf.calmar.toFixed(2) : "—"} />
                <Tile label="Rolling DD (30)" value={hasSample ? `${perf.rolling_max_dd_30_pct}%` : "—"}
                  color={perf && perf.rolling_max_dd_30_pct > 10 ? "var(--amber)" : "var(--ink)"} />
                <Tile label="Expectancy" value={hasSample ? money(perf.expectancy) : "—"}
                  color={perf ? pnlColor(perf.expectancy) : undefined} />
                <Tile label="Avg Hold" value={hasSample ? `${perf.avg_hold_days}d` : "—"} />
              </>
            )}
          </div>
        </div>

        {/* Portfolio Heat */}
        <div className="instrument-card exec-card">
          <PanelHead icon="gauge" title="Managed-position heat"
            right={heat && <span className="mono" style={{ fontSize: 9.5, color: "var(--ink-dim)" }}>{heat.position_count} managed</span>} />
          <div style={{ padding: "14px" }}>
            <p style={{fontSize:11,color:"var(--ink-dim)",margin:"0 0 10px"}}>Recorded positions only. Untracked broker holdings may be excluded.</p>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
              <span className="mono" style={{ fontSize: 24, fontWeight: 700,
                color: heat ? HEAT[heat.heat_status] : "var(--ink)" }}>
                {heat ? `${heat.portfolio_heat_pct}%` : "—"}
              </span>
              <span className="kicker">{heat ? `${plain(heat.total_risk_dollars)} at risk` : ""}</span>
            </div>
            <div className="instrument-card--flat" style={{ height: 8, overflow: "hidden", marginBottom: 12, padding: 0 }}>
              <div style={{ height: "100%", transition: "width .5s ease",
                width: `${heat ? Math.min(100, heat.portfolio_heat_pct) : 0}%`,
                background: heat ? HEAT[heat.heat_status] : "var(--ink-faint)" }} />
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--ink-dim)" }}>
              <span>Top name: <b style={{ color: "var(--ink)" }}>{heat?.largest_underlying || "—"}</b> {heat ? `${heat.largest_underlying_pct}%` : ""}</span>
              <span>Sector: <b style={{ color: "var(--ink)" }}>{heat?.largest_sector || "—"}</b></span>
            </div>
            {heat && (heat.concentration_flags?.length ?? 0) > 0 && (
              <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
                {heat.concentration_flags.map((f, i) => (
                  <span key={i} className="mono" style={{ fontSize: 9, padding: "2px 8px", borderRadius: 2,
                    background: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.3)", color: "var(--red)" }}>
                    {f.split(" ")[0]}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {showAdvanced && (
        <>
      {/* Strategy · Meta · Allocation · Stress — 2-column grid to use the width */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: 14, marginBottom: 14, alignItems: "start" }}>
      {/* Strategy health */}
      <div className="instrument-card exec-card">
        <PanelHead icon="check" title="Strategy Health"
          right={strat && <span className="mono" style={{ fontSize: 10.5,
            color: (strat.suspended?.length ?? 0) === 0 ? "var(--green)" : "var(--red)" }}>
            {(strat.suspended?.length ?? 0) === 0
              ? `${strat.total_strategies ?? 0} tracked · none suspended`
              : `${strat.suspended.length} suspended`}
          </span>} />
        {strat && (strat.strategies?.length ?? 0) > 0
          ? strat.strategies.map(h => <StratRow key={h.strategy} h={h} />)
          : <div style={{ padding: 14, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              {strat ? "No closed trades yet — collecting data." : "Loading…"}
            </div>}
      </div>

      {/* Meta-strategy — which strategies are active given regime + health */}
      <div className="instrument-card exec-card">
        <PanelHead icon="strategy" title="Meta-Strategy"
          right={meta && <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-dim)" }}>
            regime: <b style={{ color: "var(--cyan)" }}>{meta.regime || "—"}</b> · {meta.active_strategies?.length ?? 0} active
          </span>} />
        {meta && (meta.decisions?.length ?? 0) > 0
          ? meta.decisions.map(d => (
            <div key={d.strategy} style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 14px",
              borderBottom: "1px solid var(--line-dim)",
              borderLeft: `2px solid ${d.active ? "var(--green)" : "var(--ink-faint)"}`,
              opacity: d.active ? 1 : 0.6 }}>
              <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", minWidth: 150 }}>{d.strategy}</span>
              <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, minWidth: 60,
                color: d.active ? "var(--green)" : "var(--red)" }}>{d.active ? "ACTIVE" : "OFF"}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--ink-dim)", flex: 1 }}>
                tilt <b style={{ color: "var(--ink)" }}>{(d.tilt * 100).toFixed(0)}%</b> · {d.reason}
              </span>
            </div>
          ))
          : <div style={{ padding: 14, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              {meta ? "No strategies tracked yet." : "Loading…"}
            </div>}
      </div>

      {/* Capital allocation — target weights per strategy */}
      <div className="instrument-card exec-card">
        <PanelHead icon="gauge" title="Capital Allocation"
          right={alloc && <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-dim)" }}>
            {alloc.method} · cash <b style={{ color: "var(--ink)" }}>{((alloc.cash_weight ?? 0) * 100).toFixed(0)}%</b>
          </span>} />
        {alloc && Object.keys(alloc.weights || {}).length > 0
          ? Object.entries(alloc.weights).sort((a, b) => b[1] - a[1]).map(([k, w]) => (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 14px",
              borderBottom: "1px solid var(--line-dim)" }}>
              <span className="mono" style={{ fontSize: 12, color: "var(--ink)", minWidth: 150 }}>{k}</span>
              <div style={{ flex: 1, height: 7, background: "var(--bg-4)", borderRadius: 4, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${Math.min(100, w * 100)}%`,
                  background: "var(--cyan)", borderRadius: 4 }} />
              </div>
              <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--cyan)", minWidth: 44, textAlign: "right" }}>
                {(w * 100).toFixed(0)}%
              </span>
            </div>
          ))
          : <div style={{ padding: 14, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              {alloc ? "All capital in cash — no eligible strategies." : "Loading…"}
            </div>}
      </div>

      {/* Stress scenarios + parametric VaR */}
      <div className="instrument-card exec-card">
        <PanelHead icon="risk" title="Stress & VaR"
          right={varRep && varRep.available !== false && <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-dim)" }}>
            VaR {Math.round((varRep.confidence ?? 0) * 100)}%: <b style={{ color: "var(--red)" }}>${Math.round(varRep.var ?? 0).toLocaleString()}</b>
            {varRep.var_pct != null ? ` (${varRep.var_pct}%)` : ""} · ES ${Math.round(varRep.expected_shortfall ?? 0).toLocaleString()}
          </span>} />
        {varRep && varRep.available === false && (
          <div style={{ padding: "8px 14px", fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--amber)", borderBottom: "1px solid var(--line-dim)" }}>
            VaR unavailable{varRep.reason ? ` — ${varRep.reason}` : ""}.
          </div>
        )}
        {scen && (scen.scenarios?.length ?? 0) > 0
          ? scen.scenarios.map(r => (
            <div key={r.scenario} style={{ display: "flex", alignItems: "center", gap: 12, padding: "7px 14px",
              borderBottom: "1px solid var(--line-dim)" }}>
              <span className="mono" style={{ fontSize: 11.5, color: "var(--ink)", minWidth: 150 }}>{r.scenario}</span>
              <span className="mono" style={{ flex: 1, fontSize: 12, fontWeight: 600,
                color: r.portfolio_pnl < 0 ? "var(--red)" : "var(--green)" }}>
                {r.portfolio_pnl < 0 ? "-" : "+"}${Math.abs(r.portfolio_pnl).toLocaleString()}
                {r.portfolio_pnl_pct !== null ? `  (${r.portfolio_pnl_pct}%)` : ""}
              </span>
            </div>
          ))
          : <div style={{ padding: 14, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              {scen ? "No open positions to stress." : "Loading…"}
            </div>}
      </div>
      </div>{/* end 2-col grid */}

      {/* System health — full width (dense checklist) */}
      <div className="instrument-card exec-card">
        <PanelHead icon="activity" title="System Health"
          right={s && <span className="mono" style={{ fontSize: 10.5,
            color: s.issues === 0 ? "var(--green)" : "var(--amber)" }}>
            {s.issues === 0 ? "All systems nominal" : `${s.issues} issue${s.issues > 1 ? "s" : ""} detected`}
          </span>} />
        {s?.health ? (
          <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr" }}>
            {s.health.map(h => <HealthRow key={h.name} h={h} />)}
          </div>
        ) : <div style={{ padding: 16, fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>Loading…</div>}
        {detail && (
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", padding: "9px 14px",
            fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--ink-dim)",
            borderTop: "1px solid var(--line-dim)" }}>
            <span>Scanner <b style={{ color: detail.scanner?.alive ? "var(--green)" : "var(--red)" }}>
              {detail.scanner?.alive ? "live" : "stalled"}</b>
              {detail.scanner?.last_tick_age_seconds != null ? ` ${detail.scanner.last_tick_age_seconds}s ago` : ""}
            </span>
            <span>Attempts <b style={{ color: "var(--ink)" }}>{detail.observability?.counters?.["execute.attempt"] || 0}</b></span>
            <span>Submitted <b style={{ color: "var(--green)" }}>{detail.observability?.counters?.["execute.submitted"] || 0}</b></span>
            <span>Blocked <b style={{ color: "var(--amber)" }}>{detail.observability?.counters?.["execute.blocked"] || 0}</b></span>
            <span>Errors <b style={{ color: "var(--red)" }}>{detail.observability?.counters?.["execute.error"] || 0}</b></span>
          </div>
        )}
      </div>
        </>
      )}
    </div>
  );
}
