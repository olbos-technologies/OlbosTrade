import { useIsMobile } from "../hooks/useIsMobile";
import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import MarketBiasPanel from "../components/MarketBiasPanel";
import MarketStructurePanel from "../components/MarketStructurePanel";
import SignalAttribution from "../components/SignalAttribution";
import SignalDetailDrawer from "../components/SignalDetailDrawer";
import SetupScannerPanel from "../components/SetupScannerPanel";
import TimeframeAlignmentPanel from "../components/TimeframeAlignmentPanel";
import { usePaperTrade } from "../hooks/usePaperTrade";
import { useRisk } from "../hooks/useRisk";
import {
  chartLevelColors,
  formatExecutionModeDisplay,
  toChartSignalAttribution,
  tradePlanCardTitle,
  type ExecMode,
} from "../utils/chartWorkstationDisplay";
import MetricHint, { resolveMetricHint } from "../components/MetricHint";
import { Panel, StatTile, Badge, Button } from "../components/ui";
import CandlestickChart from "../components/CandlestickChart";
import { tint } from "../utils/tint";
import "./markets/Markets.css";

type Timeframe = "5m" | "15m" | "1h" | "1d";

interface ChartBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface WatchSnapshot {
  symbol: string;
  last_close: number | null;
  change_pct: number | null;
}

interface CatalystEvent {
  name: string;
  symbol?: string;
  date: string;
  days_away: number;
  severity: string;
  kind: string;
  source: string;
}

const CATALYST_SEVERITY_COLOR: Record<string, string> = {
  very_high: "var(--red)",
  high: "var(--amber)",
  moderate: "var(--cyan)",
  low: "var(--ink-faint)",
};

interface Signal {
  id: string;
  ticker: string;
  action: "BUY" | "SELL" | "HOLD";
  confidence: number;
  generated_at: string;
  /** Producer name from payload when present — never invent client-side. */
  source?: string | null;
  reasons?: string[];
  trade_plan?: {
    entry_price?: number;
    stop_price?: number;
    target_price?: number;
    shares?: number;
    risk_reward?: number;
    risk_dollars?: number;
  };
  indicators?: {
    rsi?: number;
    macd?: number;
    bb_pct_b?: number;
    volume_ratio?: number;
  };
}

// Mag7 + broad index ETFs as the default — user can add/remove any ticker
// beyond this from the Market Watch panel; the list persists to localStorage.
const DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "SPY", "QQQ", "IWM"];
const WATCHLIST_STORAGE_KEY = "olbos.chart.watchlist";

function loadStoredWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.every((s) => typeof s === "string") && parsed.length) {
        return parsed;
      }
    }
  } catch {
    /* ignore malformed storage */
  }
  return DEFAULT_WATCHLIST;
}
const TIMEFRAMES: { key: Timeframe; label: string; limit: number }[] = [
  { key: "5m", label: "5M", limit: 78 },
  { key: "15m", label: "15M", limit: 96 },
  { key: "1h", label: "1H", limit: 90 },
  { key: "1d", label: "1D", limit: 120 },
];

function fmtMoney(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return `$${value.toFixed(digits)}`;
}

function fmtSigned(value: number | null | undefined, digits = 2) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : "-"}$${Math.abs(value).toFixed(digits)}`;
}

function fmtPct(value: number | null | undefined, digits = 1) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function hintFor(label: string): React.ReactNode {
  return resolveMetricHint(label) ? <MetricHint id={label} /> : label;
}

function normalizeReasons(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string");
  if (typeof value === "string" && value.trim()) return [value];
  return [];
}

function UpcomingCatalysts({ events }: { events: CatalystEvent[] }) {
  if (!events.length) return null;
  // Nearest 3, soonest first — a glance at "what's coming up for this
  // symbol" pinned near the chart, not spatially mapped onto the x-axis
  // (candles are index-spaced, and these events mostly fall beyond the
  // last loaded bar, so a literal date→pixel marker would be misleading).
  const upcoming = [...events].sort((a, b) => a.days_away - b.days_away).slice(0, 3);
  return (
    <div
      aria-label="Upcoming catalysts"
      style={{
        position: "absolute",
        right: 12,
        top: 10,
        zIndex: 2,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        alignItems: "flex-end",
        pointerEvents: "none",
      }}
    >
      {upcoming.map((ev, idx) => {
        const color = CATALYST_SEVERITY_COLOR[ev.severity] ?? "var(--ink-dim)";
        return (
          <div
            key={`${ev.name}-${ev.date}-${idx}`}
            title={`${ev.name} · ${ev.date} · ${ev.severity.replace(/_/g, " ")}`}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "2px 8px",
              background: "rgba(6,11,23,0.82)",
              border: `1px solid ${tint(color, 0.333)}`,
              pointerEvents: "auto",
            }}
          >
            <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: color, flexShrink: 0 }} />
            <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, color: "var(--ink-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 140 }}>
              {ev.name}
            </span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, color, flexShrink: 0 }}>
              {ev.days_away === 0 ? "today" : `${ev.days_away}d`}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default function ChartWorkstation({
  symbol: controlledSymbol,
  onSymbolChange,
  compact = false,
}: {
  /** When set, symbol is controlled by the parent (Equity Desk). */
  symbol?: string;
  onSymbolChange?: (symbol: string) => void;
  /** Hide top strip + left watch rail — discovery lives in Equity Desk. */
  compact?: boolean;
} = {}) {
  const isMobile = useIsMobile();
  const { positions, portfolio, portfolioError, positionsError } = usePaperTrade();
  const { guardrailStatus, reconciliation, killSwitch } = useRisk();

  const [internalSymbol, setInternalSymbol] = useState(() => {
    const requested = new URLSearchParams(window.location.search).get("symbol")?.toUpperCase();
    return controlledSymbol || (requested && /^[A-Z0-9.^=:-]{1,20}$/.test(requested) ? requested : "AAPL");
  });
  const [focusChart, setFocusChart] = useState(false);
  const symbol = controlledSymbol ?? internalSymbol;
  const setSymbol = (next: string) => {
    if (controlledSymbol === undefined) setInternalSymbol(next);
    onSymbolChange?.(next);
  };

  useEffect(() => {
    if (controlledSymbol != null && controlledSymbol !== internalSymbol) {
      setInternalSymbol(controlledSymbol);
    }
  }, [controlledSymbol]); // eslint-disable-line react-hooks/exhaustive-deps

  const [timeframe, setTimeframe] = useState<Timeframe>("15m");
  const [watchSnapshots, setWatchSnapshots] = useState<Record<string, WatchSnapshot>>({});
  const [signals, setSignals] = useState<Signal[]>([]);
  const [regime, setRegime] = useState<any>(null);
  const [ivRank, setIvRank] = useState<any>(null);
  const [chartBars, setChartBars] = useState<ChartBar[]>([]);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);
  const [catalystEvents, setCatalystEvents] = useState<CatalystEvent[]>([]);
  const [signalDrawerOpen, setSignalDrawerOpen] = useState(false);
  const [execMode, setExecMode] = useState<ExecMode | null>(null);
  const [watchlist, setWatchlist] = useState<string[]>(loadStoredWatchlist);
  const [tickerInput, setTickerInput] = useState("");

  const persistWatchlist = (next: string[]) => {
    setWatchlist(next);
    try {
      localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignore quota/storage errors — in-memory state still updates */
    }
  };

  const addTicker = () => {
    const t = tickerInput.trim().toUpperCase();
    if (!t || watchlist.includes(t)) { setTickerInput(""); return; }
    persistWatchlist([...watchlist, t]);
    setTickerInput("");
    setSymbol(t);
  };

  const removeTicker = (ticker: string) => {
    persistWatchlist(watchlist.filter((t) => t !== ticker));
  };

  const loadWatchlist = async () => {
    const entries = await Promise.all(
      watchlist.map(async (ticker) => {
        try {
          const snap = await (api.getSnapshot(ticker) as Promise<WatchSnapshot>);
          return [ticker, { symbol: ticker, last_close: snap.last_close, change_pct: snap.change_pct }] as const;
        } catch {
          return [ticker, { symbol: ticker, last_close: null, change_pct: null }] as const;
        }
      }),
    );
    setWatchSnapshots(Object.fromEntries(entries));
  };

  const loadSignals = async () => {
    try {
      // Must cover the full watchlist (~102 tickers, deduped server-side to
      // one entry per ticker) — selectedSignal below looks up the CURRENTLY
      // VIEWED symbol by ticker, not just "recent" signals. A small limit
      // here silently showed "NO SIGNAL" for any symbol that wasn't among
      // the last few scanned, even when a real signal existed for it.
      const res: any = await api.getEquitySignals(150);
      setSignals(res.signals || []);
    } catch {
      setSignals([]);
    }
  };

  useEffect(() => {
    loadSignals();
    const si = setInterval(loadSignals, 60000);
    (api.getRegime() as Promise<any>).then(setRegime).catch(() => setRegime(null));
    const loadExec = () =>
      (api.getExecutionMode() as Promise<{ mode?: string }>)
        .then((d) => {
          if (d.mode === "manual" || d.mode === "copilot" || d.mode === "autopilot") {
            setExecMode(d.mode);
          }
        })
        .catch(() => setExecMode(null));
    loadExec();
    const ei = setInterval(loadExec, 15000);
    return () => {
      clearInterval(si);
      clearInterval(ei);
    };
  }, []);

  useEffect(() => {
    loadWatchlist();
  }, [watchlist]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    (api.getIVRank(symbol) as Promise<any>).then(setIvRank).catch(() => setIvRank(null));
  }, [symbol]);

  useEffect(() => {
    (api.getCatalystCalendar(symbol, 21) as Promise<any>)
      .then((res) => setCatalystEvents(res?.events || []))
      .catch(() => setCatalystEvents([]));
  }, [symbol]);

  useEffect(() => {
    const config = TIMEFRAMES.find((item) => item.key === timeframe) || TIMEFRAMES[1];
    setChartLoading(true);
    setChartError(null);
    (api.getEquityChart(symbol, { timeframe, limit: config.limit }) as Promise<any>)
      .then((res) => setChartBars(res.bars || []))
      .catch((err: Error) => {
        setChartBars([]);
        setChartError(err.message);
      })
      .finally(() => setChartLoading(false));
  }, [symbol, timeframe]);

  // No fallback to signals[0] — a symbol with no signal of its own must show
  // an explicit "no signal" state, not another ticker's trade plan.
  const selectedSignal = useMemo(
    () => signals.find((item) => item.ticker === symbol) || null,
    [signals, symbol],
  );
  const selectedSnapshot = watchSnapshots[symbol];
  const latestBar = chartBars[chartBars.length - 1] || null;
  const recentBars = chartBars.slice(-20);
  const support = recentBars.length ? Math.min(...recentBars.map((bar) => bar.low)) : latestBar?.low ?? 0;
  const resistance = recentBars.length ? Math.max(...recentBars.map((bar) => bar.high)) : latestBar?.high ?? 0;
  const entry = selectedSignal?.trade_plan?.entry_price ?? latestBar?.close ?? 0;
  const stop = selectedSignal?.trade_plan?.stop_price ?? support;
  const target = selectedSignal?.trade_plan?.target_price ?? resistance;
  const levelColors = chartLevelColors();

  // A real trade plan (live signal with its own stop/target, not the
  // chart-derived support/resistance fallback) gets the full risk ladder:
  // stop-loss plus a 3-tier scale-out target. TP1/TP2/TP3 are NOT separate
  // numbers the system plans for — they're 1/3, 2/3, and the full distance
  // to the one real target_price the signal already committed to, the same
  // convention a scale-out exit uses. Nothing here is a predicted price path;
  // every level is a real, already-computed number.
  const planStop = selectedSignal?.trade_plan?.stop_price;
  const planTarget = selectedSignal?.trade_plan?.target_price;
  const hasRealPlan = entry > 0 && planStop != null && planTarget != null;

  const levels = hasRealPlan
    ? [
        { label: "ENTRY", value: entry, color: levelColors.entry },
        { label: "STOP", value: planStop!, color: levelColors.stop },
        { label: "TP1", value: entry + (planTarget! - entry) * (1 / 3), color: levelColors.tp1 },
        { label: "TP2", value: entry + (planTarget! - entry) * (2 / 3), color: levelColors.tp2 },
        { label: "TP3", value: planTarget!, color: levelColors.tp3 },
      ].filter((level) => level.value > 0)
    : [
        { label: "ENTRY", value: entry, color: levelColors.entry },
        { label: "SUPPORT", value: support, color: levelColors.support },
        { label: "RESISTANCE", value: resistance, color: levelColors.resistance },
      ].filter((level) => level.value > 0);

  const actionableSignals = signals.filter((item) => item.action !== "HOLD");
  const netLiq = portfolioError ? null : portfolio?.account_value ?? portfolio?.net_liquidation;
  const dayPnl = guardrailStatus?.daily_pnl;
  const execDisplay = execMode === null && !killSwitch?.active
    ? { label: "Execution mode", value: "UNKNOWN", tone: "var(--amber)" }
    : formatExecutionModeDisplay(execMode, Boolean(killSwitch?.active));
  const planTitle = tradePlanCardTitle(Boolean(selectedSignal), selectedSignal?.source);

  return (
    <div className={compact ? "chart-workspace chart-workspace--compact" : "chart-workspace"} style={{ padding: compact ? 0 : 16, display: "flex", flexDirection: "column", gap: 14, height: compact ? "100%" : undefined }}>
      {!compact && <div className="markets-toolbar" style={{marginBottom:0}}>
        <div><div className="markets-kicker">MARKET INTELLIGENCE</div><h1>Market workspace</h1><p>Signal direction and model score are research inputs, not permission to execute.</p></div>
        <div className="markets-controls"><button aria-pressed={focusChart} onClick={()=>setFocusChart(v=>!v)}>{focusChart ? "Show research panels" : "Focus chart"}</button></div>
      </div>}
      {!compact && <div className="chart-symbol-picker" aria-label="Chart instruments">
        {Array.from(new Set([symbol, ...watchlist])).slice(0, 5).map(ticker => (
          <button key={ticker} aria-pressed={symbol === ticker} onClick={() => setSymbol(ticker)}>{ticker}</button>
        ))}
      </div>}
      {!compact && !focusChart && (
      <div style={{
        display: "grid",

        gap: 10,
      }} className="workstation-top-strip">
        {[
          { label: execDisplay.label, value: execDisplay.value, tone: execDisplay.tone },
          { label: "Day P&L", value: typeof dayPnl === "number" ? fmtSigned(dayPnl) : "—", tone: dayPnl >= 0 ? "var(--green)" : "var(--red)" },
          { label: "Portfolio", value: typeof netLiq === "number" ? fmtMoney(netLiq, 2) : "—", tone: "var(--cyan)" },
          { label: "Reconciliation", value: !reconciliation ? "UNAVAILABLE" : reconciliation.clean ? "CLEAN" : "REVIEW", tone: reconciliation?.clean ? "var(--green)" : "var(--amber)" },
        ].map((item) => (
          <StatTile key={item.label} variant="boxed" label={item.label} hint={hintFor(item.label)} value={item.value} tone={item.tone} />
        ))}
      </div>
      )}

      <div className="workstation-grid" style={compact || focusChart ? { gridTemplateColumns: "1fr", flex: 1, minHeight: 0 } : undefined}>
        {!compact && !focusChart && (
        <div className="workstation-col workstation-watchlist">
          <Panel title="Your watchlist" sectionStyle={{ overflow: "hidden" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  value={tickerInput}
                  onChange={(e) => setTickerInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") addTicker(); }}
                  placeholder="Add ticker…"
                  aria-label="Add ticker to Market Watch"
                  style={{
                    flex: 1, minWidth: 0, background: "var(--bg-3)",
                    border: "1px solid var(--line-dim)", color: "var(--ink)",
                    fontFamily: "var(--mono)", fontSize: 11, padding: "6px 8px",
                    outline: "none", textTransform: "uppercase",
                  }}
                />
                <button className="btn-t" onClick={addTicker} style={{ flexShrink: 0 }}>+</button>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                {watchlist.map((ticker) => {
                  const snap = watchSnapshots[ticker];
                  const active = ticker === symbol;
                  return (
                    <div key={ticker} style={{ position: "relative" }}>
                      <button
                        onClick={() => setSymbol(ticker)}
                        className={`watch-item ${active ? "active" : ""}`}
                        style={{ width: "100%", paddingRight: 24 }}
                      >
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                          <span style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 700 }}>{ticker}</span>
                          <span style={{
                            fontFamily: "var(--mono)",
                            fontSize: 10,
                            color: (snap?.change_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)",
                          }}>
                            {fmtPct(snap?.change_pct, 2)}
                          </span>
                        </div>
                        <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)", marginTop: 2 }}>
                          {fmtMoney(snap?.last_close, 2)}
                        </div>
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); removeTicker(ticker); }}
                        aria-label={`Remove ${ticker} from Market Watch`}
                        title={`Remove ${ticker}`}
                        style={{
                          position: "absolute", right: 4, top: 4,
                          background: "transparent", border: "none", cursor: "pointer",
                          color: "var(--ink-faint)", fontFamily: "var(--mono)", fontSize: 12,
                          padding: "2px 4px", lineHeight: 1,
                        }}
                      >
                        ×
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </Panel>

          <Panel title="Positions" sectionStyle={{ overflow: "hidden" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {positionsError ? <p role="status">Positions unavailable. Check broker connection.</p> : positions.length === 0 ? (
                <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-faint)" }}>
                  NO OPEN POSITIONS
                </div>
              ) : positions.slice(0, 6).map((position: any) => (
                <div key={`${position.symbol}-${position.entry_date || "open"}`} style={{
                  border: "1px solid var(--line-dim)",
                  padding: "10px 12px",
                  background: "rgba(255,255,255,0.01)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 14, color: "var(--ink)" }}>{position.symbol}</span>
                    <span style={{
                      fontFamily: "var(--mono)",
                      fontSize: 12,
                      color: (position.unrealized_pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)",
                    }}>
                      {fmtSigned(position.unrealized_pnl)}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontFamily: "var(--mono)", fontSize: 10, color: "var(--ink-dim)" }}>
                    <span>{position.quantity ?? "—"} sh</span>
                    <span>@ {fmtMoney(position.avg_cost, 2)}</span>
                    <span>hold {position.hold_days ?? 0}d</span>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>
        )}

        <div className="workstation-center">
          <div className="chart-quote" aria-label="Selected instrument quote">
            <div><span className="design-eyebrow">{symbol} · MARKET SNAPSHOT</span><strong>{fmtMoney(selectedSnapshot?.last_close)}</strong></div>
            <span style={{ color: selectedSnapshot?.change_pct == null ? "var(--ink-dim)" : selectedSnapshot.change_pct >= 0 ? "var(--green)" : "var(--red)" }}>{fmtPct(selectedSnapshot?.change_pct)}</span>
            <p>Last close snapshot · {latestBar ? `Chart bar: ${new Date(latestBar.timestamp).toLocaleString()}` : "Chart data unavailable"}</p>
          </div>
          <Panel
            sectionStyle={{ overflow: "hidden" }}
            padding={0}
            title={
              <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 28, fontWeight: 700, color: "var(--cyan)" }}>{symbol}</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-dim)" }}>
                  {selectedSnapshot?.last_close != null ? fmtMoney(selectedSnapshot.last_close, 2) : "—"} · {fmtPct(selectedSnapshot?.change_pct, 2)}
                </span>
              </div>
            }
            action={
              <div style={{ display: "flex", gap: 8, marginLeft: "auto", flexWrap: "wrap" }}>
                {TIMEFRAMES.map((item) => (
                  <Button key={item.key} active={timeframe === item.key} onClick={() => setTimeframe(item.key)}>
                    {item.label}
                  </Button>
                ))}
                <Button onClick={() => setSignalDrawerOpen(true)}>Signal Detail</Button>
              </div>
            }
          >
            <div className="chart-panel-body" style={{ padding: 14 }}>
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
                gap: 10,
                marginBottom: 12,
              }} className="workstation-metrics-grid">
                <StatTile
                  variant="boxed" label="Signal" hint={hintFor("Signal")}
                  value={selectedSignal
                    ? <SignalAttribution data={toChartSignalAttribution(selectedSignal)} size="sm" />
                    : <span style={{ color: "var(--ink-dim)" }}>NO SIGNAL</span>}
                />
                {[
                  { label: "Model score", value: selectedSignal ? `${Math.round(selectedSignal.confidence * 100)}%` : "—", tone: "var(--amber)" },
                  { label: "IV Rank", value: ivRank?.iv_rank != null ? `${Math.round(ivRank.iv_rank)}` : "—", tone: "var(--cyan)" },
                  { label: "RSI", value: selectedSignal?.indicators?.rsi != null ? selectedSignal.indicators.rsi.toFixed(1) : "—", tone: "var(--ink)" },
                  { label: "Volume x", value: selectedSignal?.indicators?.volume_ratio != null ? selectedSignal.indicators.volume_ratio.toFixed(2) : "—", tone: "var(--ink)" },
                ].map((item) => (
                  <StatTile key={item.label} variant="boxed" label={item.label} hint={hintFor(item.label)} value={item.value} tone={item.tone} />
                ))}
              </div>

              <div className="instrument-card--flat" style={{ position: "relative" }}>
                {chartLoading ? (
                  <div className="skeleton-block" style={{ height: 460 }} aria-busy="true" aria-label="Loading chart">
                    <div className="skeleton-shimmer" />
                  </div>
                ) : (
                  <>
                    <UpcomingCatalysts events={catalystEvents} />
                    <CandlestickChart bars={chartBars} levels={levels} />
                  </>
                )}
              </div>
              {chartError && (
                <div style={{ marginTop: 10, fontFamily: "var(--mono)", fontSize: 10, color: "var(--red)" }}>
                  {chartError}
                </div>
              )}
            </div>
          </Panel>

          {!compact && (
          <details className="chart-research" open={!isMobile}><summary>Research evidence</summary><section className="chart-intel-grid">
            <MarketBiasPanel symbol={symbol} />
            <TimeframeAlignmentPanel symbol={symbol} />
            <MarketStructurePanel symbol={symbol} timeframe="1d" />
          </section></details>
          )}

          {!compact && (
          <details className="chart-research" open={!isMobile}><summary>Trade plan and context</summary><div className="workstation-bottom-grid">
            <Panel title={planTitle} sectionStyle={{ overflow: "hidden" }}>
              {selectedSignal?.source && selectedSignal.source !== "unknown" && (
                <div className="instrument-card--flat" style={{
                  marginBottom: 10, fontFamily: "var(--mono)", fontSize: 11,
                  color: "var(--ink-dim)", padding: "8px 10px",
                }}>
                  Source: {selectedSignal.source}
                </div>
              )}
              {!selectedSignal && (
                <div className="instrument-card--flat" style={{
                  marginBottom: 10, fontFamily: "var(--mono)", fontSize: 11,
                  color: "var(--ink-dim)", padding: "8px 10px",
                }}>
                  No signal for {symbol} — levels below are chart-derived
                  (recent support/resistance), not the signal engine's own plan.
                </div>
              )}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }} className="workstation-plan-grid">
                {[
                  { label: "Entry", value: fmtMoney(entry, 2), tone: "var(--cyan)" },
                  { label: "Stop", value: fmtMoney(stop, 2), tone: "var(--red)" },
                  { label: "Target", value: fmtMoney(target, 2), tone: "var(--green)" },
                  { label: "R:R", value: selectedSignal?.trade_plan?.risk_reward != null ? `${selectedSignal.trade_plan.risk_reward.toFixed(2)}x` : "—", tone: "var(--amber)" },
                ].map((item) => (
                  <StatTile key={item.label} variant="boxed" label={item.label} hint={hintFor(item.label)} value={item.value} tone={item.tone} />
                ))}
              </div>
            </Panel>

            <Panel title="Context Stack" sectionStyle={{ overflow: "hidden" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <Badge kind="mode" tone="balanced">{regime?.regime ? regime.regime.replace(/_/g, " ") : "unknown"}</Badge>
                  <Badge kind="mode" tone="conservative">VIX {regime?.vix != null ? regime.vix.toFixed(1) : "—"}</Badge>
                  <Badge kind="mode" tone="aggressive">IVR {ivRank?.iv_rank != null ? Math.round(ivRank.iv_rank) : "—"}</Badge>
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.7 }}>
                  {normalizeReasons(selectedSignal?.reasons).length
                    ? normalizeReasons(selectedSignal?.reasons).join(" · ")
                    : "Signal stack will describe momentum, trend, volume, and volatility context when available."}
                </div>
              </div>
            </Panel>
          </div></details>
          )}
        </div>

        {!compact && !focusChart && (
        <div className="workstation-col workstation-right">
          <SetupScannerPanel />
          <Panel
            title="Research & Execution"
            sectionStyle={{ overflow: "hidden" }}
            action={<Button onClick={() => loadSignals()}>Refresh</Button>}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="instrument-card--flat" style={{
                padding: "10px 12px",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: regime?.equity_allowed === true ? "var(--green)" : "var(--amber)" }}>
                    Macro {regime?.equity_allowed === true ? "ENTRIES ALLOWED" : regime?.equity_allowed === false ? "PAUSED" : "UNVERIFIED"}
                  </span>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)" }}>
                    VIX {regime?.vix != null ? regime.vix.toFixed(1) : "—"}
                  </span>
                </div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.7 }}>
                  {regime?.description || "Regime engine warming up. Once available, this panel will summarize volatility, trend, and execution allowance."}
                </div>
              </div>

              {(actionableSignals.length ? actionableSignals : signals).slice(0, 4).map((item) => (
                <button
                  key={item.id}
                  onClick={() => setSymbol(item.ticker)}
                  style={{
                    textAlign: "left",
                    background: item.ticker === symbol ? "rgba(244,198,79,0.08)" : "var(--bg-2)",
                    border: item.ticker === symbol ? "1px solid rgba(244,198,79,0.4)" : "1px solid var(--line-dim)",
                    padding: "12px 14px",
                    cursor: "pointer",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 16, color: "var(--ink)", fontWeight: 700 }}>{item.ticker}</span>
                    <SignalAttribution data={toChartSignalAttribution(item)} size="sm" />
                  </div>
                  <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--ink-dim)", lineHeight: 1.7 }}>
                    {normalizeReasons(item.reasons).slice(0, 3).join(" · ") || "Signal narrative not available yet."}
                  </div>
                </button>
              ))}
            </div>
          </Panel>

          <Panel title="Operator Safety" sectionStyle={{ overflow: "hidden" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {/* daily_loss_pct is a signed P&L ratio (fraction, e.g. 0.0015
                  for 0.15%) that's positive on a gain day — clamp to the
                  negative portion and scale to a percent before display, so
                  a gain doesn't render as a small red "loss" (it also wasn't
                  being multiplied by 100 here, unlike every other consumer
                  of this field). */}
              {(() => {
                const raw = guardrailStatus?.daily_loss_pct;
                const dailyLossPct = typeof raw === "number" ? Math.max(0, -raw) * 100 : null;
                return [
                  { label: "Kill Switch", value: !killSwitch || typeof killSwitch.active !== "boolean" ? "UNKNOWN" : killSwitch.active ? "ACTIVE" : "INACTIVE", tone: !killSwitch || typeof killSwitch.active !== "boolean" ? "var(--amber)" : killSwitch.active ? "var(--red)" : "var(--ink-dim)" },
                  { label: "Daily Loss", value: fmtPct(dailyLossPct, 2), tone: dailyLossPct ? "var(--red)" : "var(--green)" },
                  { label: "Open Positions", value: positionsError ? "—" : String(positions.length), tone: "var(--ink)" },
                  { label: "Win Rate", value: portfolio?.win_rate != null ? `${Math.round(portfolio.win_rate * 100)}%` : "—", tone: "var(--cyan)" },
                ].map((row) => (
                  <div key={row.label} style={{ display: "flex", justifyContent: "space-between", gap: 16, borderBottom: "1px solid var(--line-dim)", paddingBottom: 8 }}>
                    <span className="kicker">{row.label}</span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: row.tone }}>{row.value}</span>
                  </div>
                ));
              })()}
            </div>
          </Panel>
        </div>
        )}
      </div>
      <SignalDetailDrawer
        open={signalDrawerOpen}
        onClose={() => setSignalDrawerOpen(false)}
        signal={selectedSignal}
        symbol={symbol}
        timeframe={timeframe}
        latestBar={latestBar}
      />
    </div>
  );
}
