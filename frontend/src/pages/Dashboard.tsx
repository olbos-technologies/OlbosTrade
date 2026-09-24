/**
 * Dashboard — Bloomberg terminal style.
 * 4-panel grid: equity curve, portfolio stats, positions, Greeks.
 *
 * Equity curve sources (in priority order):
 *  1. Real closed-trade P&L from DB  (/api/paper-trade/history)
 *  2. Latest backtest equity_curve    (/api/backtest/history)
 *  3. Flat line at current account value (no data yet)
 * Time-range buttons filter the curve by calendar window.
 */

import React, { useEffect, useRef, useState, useCallback } from "react";
import { api }           from "../api/client";
import { usePaperTrade } from "../hooks/usePaperTrade";
import { useRisk }       from "../hooks/useRisk";
import ExecutiveSummary  from "../components/ExecutiveSummary";
import ErrorBoundary     from "../components/ErrorBoundary";
import WelcomeBanner, { WELCOME_DISMISS_KEY } from "../components/WelcomeBanner";
import MetricHint, { resolveMetricHint } from "../components/MetricHint";
import { useIsMobile }   from "../hooks/useIsMobile";
import { StatTile, Badge, Button, Panel } from "../components/ui";
import { useTerminalNav } from "../components/TerminalNavContext";
import { lifecycleColor, lifecycleFromExecution, lifecycleLabel } from "../trade-desk/executionStatus";

function hintFor(label: string): React.ReactNode {
  return resolveMetricHint(label) ? <MetricHint id={label} /> : label;
}

function NextAction({
  label,
  detail,
  action,
  onClick,
}: {
  label: string;
  detail: string;
  action: string;
  onClick: () => void;
}) {
  return (
    <section className="dashboard-next-action" aria-label="Next action">
      <div>
        <div className="kicker">Next action</div>
        <div className="dashboard-next-action__label">{label}</div>
        <p>{detail}</p>
      </div>
      <Button onClick={onClick}>{action}</Button>
    </section>
  );
}

// ── Equity chart canvas ───────────────────────────────────────────────────────
interface ChartPoint { date: string; value: number; }

function EquityChart({ points, loading }: { points: ChartPoint[]; loading: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.offsetWidth;
    const H = canvas.offsetHeight;
    canvas.width  = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    if (loading) {
      ctx.fillStyle = "rgba(100,116,139,0.4)";
      ctx.font = "11px 'JetBrains Mono'";
      ctx.textAlign = "center";
      ctx.fillText("LOADING…", W / 2, H / 2);
      return;
    }

    const pts = points.map(p => p.value);

    if (pts.length < 2) {
      ctx.fillStyle = "rgba(100,116,139,0.4)";
      ctx.font = "11px 'JetBrains Mono'";
      ctx.textAlign = "center";
      ctx.fillText("NO TRADE DATA YET — START TRADING TO SEE CURVE", W / 2, H / 2);
      return;
    }

    const minV = Math.min(...pts);
    const maxV = Math.max(...pts);
    const range = maxV - minV || 1;
    const pad = { t: 12, b: 28, l: 62, r: 12 };
    const cW = W - pad.l - pad.r;
    const cH = H - pad.t - pad.b;

    // Grid
    for (let i = 0; i <= 4; i++) {
      const y = pad.t + (cH / 4) * i;
      ctx.strokeStyle = "rgba(255,255,255,0.04)";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.setLineDash([]);
      const val = maxV - (range / 4) * i;
      ctx.fillStyle = "rgba(100,116,139,0.8)";
      ctx.font = "10px 'JetBrains Mono'";
      ctx.textAlign = "right";
      ctx.fillText("$" + (val >= 1000 ? (val / 1000).toFixed(1) + "k" : val.toFixed(0)), pad.l - 4, y + 3.5);
    }

    // Date labels (3 evenly spaced)
    const labelIdxs = [0, Math.floor(pts.length / 2), pts.length - 1];
    labelIdxs.forEach(idx => {
      if (idx >= points.length) return;
      const x = pad.l + (cW * idx) / (pts.length - 1);
      ctx.fillStyle = "rgba(100,116,139,0.6)";
      ctx.font = "9px 'JetBrains Mono'";
      ctx.textAlign = "center";
      const d = new Date(points[idx].date);
      ctx.fillText(d.toLocaleDateString("en-US", { month: "short", day: "numeric" }), x, H - 6);
    });

    const X = (i: number) => pad.l + (cW * i) / (pts.length - 1);
    const Y = (v: number) => pad.t + cH - (cH * (v - minV)) / range;

    // Determine color: green if end > start, red if not
    const trending = pts[pts.length - 1] >= pts[0];
    const lineColor   = trending ? "#22c55e" : "#ef4444";
    const fillColor0  = trending ? "rgba(34,197,94,0.18)"  : "rgba(239,68,68,0.18)";
    const fillColor1  = trending ? "rgba(34,197,94,0.03)"  : "rgba(239,68,68,0.03)";

    // Fill
    ctx.beginPath();
    ctx.moveTo(X(0), H - pad.b);
    pts.forEach((v, i) => ctx.lineTo(X(i), Y(v)));
    ctx.lineTo(X(pts.length - 1), H - pad.b);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
    grad.addColorStop(0, fillColor0);
    grad.addColorStop(0.7, fillColor1);
    grad.addColorStop(1, "transparent");
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.beginPath();
    pts.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 1.8;
    ctx.shadowColor = lineColor;
    ctx.shadowBlur = 8;
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Dot at end
    const lx = X(pts.length - 1), ly = Y(pts[pts.length - 1]);
    ctx.beginPath();
    ctx.arc(lx, ly, 3, 0, Math.PI * 2);
    ctx.fillStyle = lineColor;
    ctx.shadowColor = lineColor;
    ctx.shadowBlur = 10;
    ctx.fill();
    ctx.shadowBlur = 0;
  }, [points, loading]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function PositionRow({ pos }: { pos: any }) {
  const pnl = pos.unrealized_pnl ?? 0;
  // Untracked = a broker holding Olbos never opened (no DB record). Render
  // it muted and badged "UNTRACKED" so it is never mistaken for a managed trade.
  const untracked = pos.tracked === false;
  const pnlKnown = pos.unrealized_pnl != null;
  return (
    <tr style={untracked ? { opacity: 0.55 } : undefined}>
      <td className="mono" style={{ color: "var(--cyan)" }}>{pos.symbol || "—"}</td>
      <td className="mono">{pos.option_type?.toUpperCase() || pos.strategy?.toUpperCase() || "EQUITY"}</td>
      <td className="mono">{pos.strike || pos.avg_cost?.toFixed(2) || "—"}</td>
      <td className="mono">{pos.expiration || pos.entry_date || "—"}</td>
      <td className="mono">{pos.quantity ?? 1}</td>
      <td className="mono" style={{ color: !pnlKnown ? "var(--ink-faint)" : pnl >= 0 ? "var(--green)" : "var(--red)" }}>
        {pnlKnown ? `${pnl >= 0 ? "+" : ""}$${Math.abs(pnl).toLocaleString("en-US", { maximumFractionDigits: 0 })}` : "—"}
      </td>
      <td>
        <Badge
          kind="tag"
          tone={untracked ? "var(--amber)" : "var(--green)"}
          bg={untracked ? "rgba(245,158,11,0.1)" : "rgba(34,197,94,0.1)"}
        >
          {untracked ? "UNTRACKED" : "OPEN"}
        </Badge>
      </td>
    </tr>
  );
}

/** Phone-first counterpart to the desktop positions table. The summary carries
 *  the decision information; the remaining fields are disclosed without
 *  horizontal scrolling or deleting information available on desktop. */
function MobilePositionCard({ pos }: { pos: any }) {
  const untracked = pos.tracked === false;
  const pnlKnown = pos.unrealized_pnl != null;
  const pnl = pos.unrealized_pnl ?? 0;
  const type = pos.option_type?.toUpperCase() || pos.strategy?.toUpperCase() || "EQUITY";
  const pnlText = pnlKnown
    ? `${pnl >= 0 ? "+" : "−"}$${Math.abs(pnl).toLocaleString("en-US", { maximumFractionDigits: 0 })}`
    : "P&L unavailable";
  return (
    <details className={`mobile-position-card${untracked ? " is-untracked" : ""}`}>
      <summary>
        <span className="mobile-position-card__identity">
          <strong>{pos.symbol || "—"}</strong>
          <small>{type} · {pos.quantity ?? 1} contracts</small>
        </span>
        <span className="mobile-position-card__value" style={{ color: !pnlKnown ? "var(--ink-faint)" : pnl >= 0 ? "var(--green)" : "var(--red)" }}>
          {pnlText}
          <small>{untracked ? "UNTRACKED" : "OPEN"}</small>
        </span>
        <span className="mobile-position-card__chevron" aria-hidden="true">›</span>
      </summary>
      <div className="mobile-position-card__details">
        <div><span>Strike / average</span><strong>{pos.strike || pos.avg_cost?.toFixed(2) || "—"}</strong></div>
        <div><span>Expiry / entry</span><strong>{pos.expiration || pos.entry_date || "—"}</strong></div>
        <div><span>Quantity</span><strong>{pos.quantity ?? 1}</strong></div>
        <div><span>Status</span><strong>{untracked ? "Untracked broker holding" : "Managed by Olbos"}</strong></div>
      </div>
    </details>
  );
}

// ── Build equity curve from trade history ────────────────────────────────────
function buildCurveFromTrades(trades: any[], startingCapital: number): ChartPoint[] {
  if (!trades || trades.length === 0) return [];
  const closed = trades
    .filter(t => t.status === "closed" && t.exit_date && t.pnl != null)
    .sort((a, b) => new Date(a.exit_date).getTime() - new Date(b.exit_date).getTime());
  if (closed.length === 0) return [];

  let equity = startingCapital;
  const points: ChartPoint[] = [{ date: closed[0].entry_date || closed[0].exit_date, value: equity }];
  for (const t of closed) {
    equity += Number(t.pnl);
    points.push({ date: t.exit_date, value: Math.max(equity, 0) });
  }
  return points;
}

function filterByRange(points: ChartPoint[], range: string): ChartPoint[] {
  if (points.length === 0 || range === "ALL") return points;
  const now = Date.now();
  const ms: Record<string, number> = {
    "1W":  7  * 86400000,
    "1M":  30 * 86400000,
    "3M":  90 * 86400000,
    "YTD": now - new Date(new Date().getFullYear(), 0, 1).getTime(),
  };
  const cutoff = now - (ms[range] ?? 90 * 86400000);
  const filtered = points.filter(p => new Date(p.date).getTime() >= cutoff);
  // Always include at least the starting anchor
  if (filtered.length === 0) return points.slice(-2);
  // Prepend the last point before the cutoff as anchor
  const anchor = points.filter(p => new Date(p.date).getTime() < cutoff).slice(-1);
  return [...anchor, ...filtered];
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const isMobile = useIsMobile();
  const { positions, portfolio, portfolioError, greeks } = usePaperTrade();
  const { guardrailStatus, portfolioState } = useRisk();
  const nav = useTerminalNav();

  // Managed = Olbos-opened positions (tracked). Untracked = pre-existing
  // broker holdings in the (shared paper) account that we did not open.
  const managedPositions   = positions.filter((p: any) => p.tracked !== false);
  const untrackedPositions = positions.filter((p: any) => p.tracked === false);

  // Never fall back to a fabricated portfolio value — a fake number here
  // is indistinguishable from a real one and directly contradicts this
  // app's own "never show a default that looks safe" principle. "—" means
  // no data yet (still loading); "UNAVAILABLE" means the fetch actually
  // failed and there's still no real value to show.
  const pvAvailable = portfolio?.account_value != null || portfolio?.net_liquidation != null;
  const pv = portfolio?.account_value ?? portfolio?.net_liquidation ?? 0;
  const pvDisplay = pvAvailable ? `$${(pv / 1000).toFixed(2)}k` : (portfolioError ? "UNAVAILABLE" : "—");
  const daily   = guardrailStatus?.daily_pnl   ?? portfolioState?.state?.daily_pnl   ?? portfolio?.total_pnl ?? 0;
  const weekly  = guardrailStatus?.weekly_pnl  ?? 0;
  const monthly = guardrailStatus?.monthly_pnl ?? 0;

  const [range, setRange]           = useState("ALL");
  const [allPoints, setAllPoints]   = useState<ChartPoint[]>([]);
  const [curveLoading, setCurveLoading] = useState(true);
  const [curveSource, setCurveSource]   = useState<"trades" | "backtest" | "none">("none");
  const [showWelcome, setShowWelcome] = useState(() => {
    try {
      return localStorage.getItem(WELCOME_DISMISS_KEY) !== "1";
    } catch {
      return true;
    }
  });

  // Load equity curve data
  const loadCurve = useCallback(async () => {
    setCurveLoading(true);
    try {
      // 1. Try real trade history from DB
      const tradeData: any = await api
        .getTradeHistory({ limit: 500, status: "closed" })
        .catch(() => null);
      // Building the curve requires a real starting capital — seeding it
      // with a fabricated default would silently offset every point on
      // the equity curve by a wrong baseline. Falls through to the
      // backtest/empty-state branches below when unavailable.
      if (tradeData && portfolio?.starting_capital != null) {
        const trades = tradeData.trades ?? [];
        const curve = buildCurveFromTrades(trades, portfolio.starting_capital);
        if (curve.length >= 2) {
          setAllPoints(curve);
          setCurveSource("trades");
          setCurveLoading(false);
          return;
        }
      }

      // 2. Fall back to latest completed backtest equity_curve
      const histData: any = await api.getBacktestHistory(5).catch(() => null);
      if (histData) {
        const completed = (histData.runs ?? []).find((r: any) => r.status === "completed");
        if (completed) {
          // Fetch full result to get equity_curve array
          const runData: any = await api
            .getBacktestResults(completed.run_id)
            .catch(() => null);
          if (runData) {
            const ec: number[] = runData.equity_curve ?? [];
            if (ec.length >= 2) {
              // Generate synthetic dates for backtest curve
              const start = new Date(runData.start_date ?? "2023-01-01");
              const pts: ChartPoint[] = ec.map((v, i) => {
                const d = new Date(start);
                d.setDate(d.getDate() + Math.round((i / (ec.length - 1)) * 365 * 2));
                return { date: d.toISOString().slice(0, 10), value: v };
              });
              setAllPoints(pts);
              setCurveSource("backtest");
              setCurveLoading(false);
              return;
            }
          }
        }
      }

      // 3. No data — show empty state
      setAllPoints([]);
      setCurveSource("none");
    } catch {
      setAllPoints([]);
      setCurveSource("none");
    } finally {
      setCurveLoading(false);
    }
  }, [portfolio?.starting_capital]);

  useEffect(() => { loadCurve(); }, [loadCurve]);

  // Execution activity — Command Center third row (pending approvals + recent
  // orders). Same endpoints CopilotQueue.tsx / ExecutionMonitor.tsx already poll.
  const [pending, setPending] = useState<any[]>([]);
  const [execLog, setExecLog] = useState<any[]>([]);
  useEffect(() => {
    const load = () => {
      (api.getPendingApprovals() as any).then((d: any) => setPending(d.pending || [])).catch(() => {});
      (api.getExecutionLog() as any).then((d: any) => setExecLog(d.log || [])).catch(() => {});
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const visiblePoints = filterByRange(allPoints, range);

  // P&L delta for the visible range
  const rangePnl = visiblePoints.length >= 2
    ? visiblePoints[visiblePoints.length - 1].value - visiblePoints[0].value
    : 0;

  // Recent equity-curve deltas for the Day P&L sparkline — real data derived
  // from the already-loaded curve, not decorative/fabricated.
  const recentPoints = allPoints.slice(-7);
  const dayPnlSpark = recentPoints.length >= 2
    ? recentPoints.slice(1).map((p, i) => p.value - recentPoints[i].value)
    : undefined;

  const dismissWelcome = () => {
    try {
      localStorage.setItem(WELCOME_DISMISS_KEY, "1");
    } catch {
      /* ignore */
    }
    setShowWelcome(false);
  };

  // One deterministic action keeps Command Center operational rather than a
  // collection of equally weighted panels. Safety and live review take
  // precedence; the default takes an operator to signals, not an order form.
  const nextAction = guardrailStatus?.trading_allowed === false
    ? {
        label: "Review trading restrictions",
        detail: "Guardrails currently prevent new trading. Review the active restriction before scanning or approving a signal.",
        action: "Review risk",
        onClick: () => nav("risk:heat"),
      }
    : portfolioError
      ? {
          label: "Confirm broker and portfolio data",
          detail: "Portfolio data is unavailable. Confirm the paper broker connection before relying on account metrics.",
          action: "Open broker",
          onClick: () => nav("system:broker"),
        }
      : pending.length > 0
        ? {
            label: `${pending.length} signal${pending.length === 1 ? "" : "s"} ready for review`,
            detail: "Review queued signals and their risk attribution before approving any execution.",
            action: "Review queue",
            onClick: () => nav("trade:copilot"),
          }
        : {
            label: "Review the latest signals",
            detail: "Start with an attributed signal; Manual mode does not place an order.",
            action: "Open signals",
            onClick: () => nav("equity"),
          };

  return (
    <div style={{ display: "grid", gridTemplateRows: "auto auto auto 1fr auto", height: "100%", overflow: "auto", gap: 0 }}>

      {showWelcome && (
        <ErrorBoundary label="Welcome">
          <WelcomeBanner onDismiss={dismissWelcome} />
        </ErrorBoundary>
      )}

      <NextAction {...nextAction} />

      {/* Executive summary header */}
      <ErrorBoundary label="Executive Summary">
        <ExecutiveSummary />
      </ErrorBoundary>

      {/* Top stat bar */}
      <div
        className="instrument-stat-strip"
        style={{
          gridTemplateColumns: isMobile ? "repeat(2, 1fr)" : "repeat(8, 1fr)",
          margin: "0 12px 12px",
        }}
      >
        <StatTile
          variant="divider" size="default"
          label="Portfolio Value" hint={hintFor("Portfolio Value")}
          value={pvDisplay}
          sub={portfolio?.return_pct != null ? `${portfolio.return_pct >= 0 ? "+" : ""}${portfolio.return_pct.toFixed(2)}% all-time` : undefined}
        />
        <StatTile
          variant="divider" size="default"
          label="Day P&L" hint={hintFor("Day P&L")}
          value={`${daily >= 0 ? "+" : ""}$${Math.abs(daily).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          sub={portfolio?.win_rate != null ? `Win rate: ${(portfolio.win_rate * 100).toFixed(1)}%` : undefined}
          tone={daily >= 0 ? "var(--green)" : "var(--red)"}
          spark={dayPnlSpark}
        />
        <StatTile
          variant="divider" size="default"
          label="Week P&L" hint={hintFor("Week P&L")}
          value={`${weekly >= 0 ? "+" : ""}$${Math.abs(weekly).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          tone={weekly >= 0 ? "var(--green)" : "var(--red)"}
        />
        <StatTile
          variant="divider" size="default"
          label="Month P&L" hint={hintFor("Month P&L")}
          value={`${monthly >= 0 ? "+" : ""}$${Math.abs(monthly).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          tone={monthly >= 0 ? "var(--green)" : "var(--red)"}
        />
        <StatTile variant="divider" size="default" label="Buying Power" hint={hintFor("Buying Power")}
          value={portfolio?.buying_power != null ? `$${(portfolio.buying_power / 1000).toFixed(1)}k` : "—"} />
        <StatTile variant="divider" size="default" label="Net Delta" hint={hintFor("Net Delta")}
          value={(greeks?.net_delta || 0).toFixed(3)} />
        <StatTile variant="divider" size="default" label="Net Theta (daily)" hint={hintFor("Net Theta (daily)")}
          value={(greeks?.net_theta || 0).toFixed(3)} tone="var(--cyan)" />
        <StatTile
          variant="divider" size="default"
          label="Open Positions" hint={hintFor("Open Positions")}
          value={String(portfolio?.open_positions ?? managedPositions.length)}
          sub={untrackedPositions.length > 0 ? `+${untrackedPositions.length} untracked` : undefined}
        />
      </div>

      {/* Main panels */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 320px", gap: 12, margin: "0 12px 12px", overflow: isMobile ? "visible" : "hidden" }}>

        {/* Left: equity + positions */}
        <div className="instrument-card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>

          {/* Equity curve */}
          <div style={{ flex: "0 0 220px", borderBottom: "1px solid var(--line-dim)" }}>
            <div className="panel-head">
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="panel-title">Equity Curve</span>
                {curveSource === "backtest" && <Badge kind="tag" tone="var(--amber)">BACKTEST</Badge>}
                {curveSource === "trades" && <Badge kind="tag" tone="var(--green)">LIVE</Badge>}
                {visiblePoints.length >= 2 && (
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: rangePnl >= 0 ? "var(--green)" : "var(--red)", marginLeft: 8 }}>
                    {rangePnl >= 0 ? "+" : ""}${rangePnl.toFixed(0)}
                  </span>
                )}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                {["1W","1M","3M","YTD","ALL"].map(t => (
                  <Button key={t} size="sm" active={t === range} onClick={() => setRange(t)}>{t}</Button>
                ))}
              </div>
            </div>
            <div style={{ height: 175, padding: "8px 4px 4px" }}>
              <EquityChart points={visiblePoints} loading={curveLoading} />
            </div>
          </div>

          {/* Positions table */}
          <div style={{ flex: 1, overflow: "auto" }}>
            <div className="panel-head">
              <span className="panel-title">Open Positions</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                {managedPositions.length} managed
                {untrackedPositions.length > 0 && (
                  <span style={{ color: "var(--amber)" }}> · {untrackedPositions.length} untracked</span>
                )}
              </span>
            </div>
            {untrackedPositions.length > 0 && (
              <div style={{
                padding: "7px 14px", fontFamily: "var(--mono)", fontSize: 10,
                color: "var(--amber)", background: "rgba(245,158,11,0.06)",
                borderBottom: "1px solid var(--line-dim)",
              }}>
                ⚠ {untrackedPositions.length} broker holding{untrackedPositions.length > 1 ? "s" : ""} not opened by Olbos — review/reconcile in the paper account.
              </div>
            )}
            {positions.length === 0 ? (
              <div className="dashboard-positions-empty">
                No open positions. Review attributed signals before creating a new trade.
              </div>
            ) : isMobile ? (
              <div className="mobile-position-list">
                {[...managedPositions, ...untrackedPositions].map((p: any, i: number) => <MobilePositionCard key={i} pos={p} />)}
              </div>
            ) : (
              <table className="t-table">
                <thead>
                  <tr>
                    {["Symbol","Type","Strike / Avg","Expiry / Entry","Qty","Unreal P&L","Status"].map(h => (
                      <th key={h}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...managedPositions, ...untrackedPositions].map((p: any, i: number) => <PositionRow key={i} pos={p} />)}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Right: status panels */}
        <div className="instrument-card" style={{ display: "flex", flexDirection: "column", overflow: "auto" }}>

          {/* Guardrail status */}
          <div style={{ borderBottom: "1px solid var(--line-dim)" }}>
            <div className="panel-head">
              <span className="panel-title">Guardrails</span>
              <span className={`dot ${guardrailStatus?.trading_allowed ? "live" : "dead"}`} />
            </div>
            <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                // daily/weekly_loss_pct are signed P&L ratios (positive on a
                // gain day) — clamp to the negative portion only, so a gain
                // shows 0% of the loss budget used instead of its magnitude.
                { label: "Daily Loss",    val: Math.max(0, -(guardrailStatus?.daily_loss_pct  || 0)) * 100, max: 2,  unit: "%" },
                { label: "Weekly Loss",   val: Math.max(0, -(guardrailStatus?.weekly_loss_pct || 0)) * 100, max: 5, unit: "%" },
                { label: "Trades Today",  val: guardrailStatus?.trades_today || 0, max: 3, unit: "" },
                { label: "Consec. Loss",  val: guardrailStatus?.consecutive_losses || 0, max: 3, unit: "" },
              ].map(g => {
                const pct = Math.min((g.val / g.max) * 100, 100);
                const color = pct < 60 ? "var(--green)" : pct < 85 ? "var(--amber)" : "var(--red)";
                return (
                  <div key={g.label}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                        {hintFor(g.label)}
                      </span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color }}>
                        {g.val.toFixed(1)}{g.unit} / {g.max}{g.unit}
                      </span>
                    </div>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Active risk profile */}
          <div style={{ borderBottom: "1px solid var(--line-dim)" }}>
            <div className="panel-head">
              <span className="panel-title">Active Risk Profile</span>
            </div>
            <div style={{ padding: "12px 14px" }}>
              <Badge
                kind="mode"
                tone={(guardrailStatus?.trading_mode as any) || "balanced"}
                style={{ fontSize: 12, padding: "4px 12px" }}
              >
                {(guardrailStatus?.trading_mode || "balanced").toUpperCase()}
              </Badge>
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-faint)", marginTop: 8 }}>
                {guardrailStatus?.trading_allowed ? "TRADING ACTIVE" : "TRADING SUSPENDED"}
              </div>
            </div>
          </div>

          {/* Portfolio Greeks */}
          <div>
            <div className="panel-head">
              <span className="panel-title">Portfolio Greeks</span>
            </div>
            <div style={{ padding: "12px 14px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              {[
                { label: "Δ DELTA", val: (greeks?.net_delta || 0).toFixed(4) },
                { label: "Γ GAMMA", val: (greeks?.net_gamma || 0).toFixed(4) },
                { label: "Θ THETA", val: (greeks?.net_theta || 0).toFixed(4) },
                { label: "ν VEGA",  val: (greeks?.net_vega  || 0).toFixed(4) },
              ].map(g => (
                <div key={g.label} style={{ background: "var(--bg-3)", borderRadius: 4, padding: "8px 10px" }}>
                  <div className="kicker" style={{ marginBottom: 4 }}>{g.label}</div>
                  <div className="data-val sm">{g.val}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Execution activity — Command Center third row */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr" }}>
        <Panel
          title="Execution Queue"
          padding={0}
          action={
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {pending.length > 0 && <Badge kind="tag" tone="var(--orange)">{`${pending.length} PENDING`}</Badge>}
              <Button size="sm" onClick={() => nav("trade:copilot")}>View all</Button>
            </div>
          }
        >
          {pending.length === 0 ? (
            <div style={{ padding: 20, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              Queue is clear
            </div>
          ) : (
            pending.slice(0, 5).map((s: any) => (
              <div key={s.id} style={{
                display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
                borderBottom: "1px solid var(--line-dim)", flexWrap: "wrap",
              }}>
                <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", minWidth: 60 }}>{s.ticker}</span>
                <Badge kind="tag" tone="var(--ink-dim)">{s.asset_type?.toUpperCase() || "EQUITY"}</Badge>
                <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-dim)" }}>{s.action || s.strategy || "—"}</span>
                <span className="mono" style={{ fontSize: 10, color: "var(--ink-faint)", marginLeft: "auto" }}>
                  {s.queued_at ? new Date(s.queued_at).toLocaleTimeString() : "—"}
                </span>
              </div>
            ))
          )}
        </Panel>

        <Panel
          title="Recent Orders"
          padding={0}
          action={<Button size="sm" onClick={() => nav("trade:execlog")}>View all</Button>}
        >
          {execLog.length === 0 ? (
            <div style={{ padding: 20, textAlign: "center", fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
              No execution events
            </div>
          ) : (
            execLog.slice(0, 5).map((e: any, i: number) => {
              const life = lifecycleFromExecution(e);
              const ts = e.executed_at || e.rejected_at;
              return (
                <div key={e.signal_id || i} style={{
                  display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
                  borderBottom: "1px solid var(--line-dim)", flexWrap: "wrap",
                }}>
                  <span className="mono" style={{ fontSize: 12, fontWeight: 600, color: "var(--ink)", minWidth: 60 }}>{e.ticker || "—"}</span>
                  <Badge kind="tag" tone={lifecycleColor(life)}>{lifecycleLabel(life)}</Badge>
                  <span className="mono" style={{ fontSize: 10.5, color: "var(--ink-dim)" }}>{e.action || e.strategy || "—"}</span>
                  <span className="mono" style={{ fontSize: 10, color: "var(--ink-faint)", marginLeft: "auto" }}>
                    {ts ? new Date(ts).toLocaleTimeString() : "—"}
                  </span>
                </div>
              );
            })
          )}
        </Panel>
      </div>
    </div>
  );
}
