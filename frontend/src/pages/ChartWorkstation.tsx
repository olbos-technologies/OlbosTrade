import { useIsMobile } from "../hooks/useIsMobile";
import { pnlColor } from "../utils/pnlTone";
import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import SignalAttribution from "../components/SignalAttribution";
import SignalDetailDrawer from "../components/SignalDetailDrawer";
import { chartLevelColors, toChartSignalAttribution } from "../utils/chartWorkstationDisplay";
import MetricHint, { resolveMetricHint } from "../components/MetricHint";
import { Panel, StatTile, Button } from "../components/ui";
import CandlestickChart from "../components/CandlestickChart";
import ChartDock from "../components/chart/ChartDock";
import InstrumentBar from "../components/chart/InstrumentBar";
import PositionsTable from "../components/chart/PositionsTable";
import WatchlistTable from "../components/chart/WatchlistTable";
import { usePaperTrade } from "../hooks/usePaperTrade";
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

function fmtPct(value: number | null | undefined, digits = 1) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function hintFor(label: string): React.ReactNode {
  return resolveMetricHint(label) ? <MetricHint id={label} /> : label;
}

function UpcomingCatalysts({ events }: { events: CatalystEvent[] }) {
  if (!events.length) return null;
  // Nearest 3, soonest first — above the plot so chips never cover candles.
  // Not spatially mapped onto the x-axis (candles are index-spaced, and these
  // events mostly fall beyond the last loaded bar).
  const upcoming = [...events].sort((a, b) => a.days_away - b.days_away).slice(0, 3);
  return (
    <div
      className="chart-catalysts"
      aria-label="Upcoming catalysts"
      style={{
        position: "relative",
        zIndex: 1,
        display: "flex",
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 4,
        justifyContent: "flex-end",
        marginBottom: 8,
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

  const { positions, positionsError } = usePaperTrade();
  const [internalSymbol, setInternalSymbol] = useState(() => {
    const requested = new URLSearchParams(window.location.search).get("symbol")?.toUpperCase();
    return controlledSymbol || (requested && /^[A-Z0-9.^=:-]{1,20}$/.test(requested) ? requested : "AAPL");
  });
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
  const [ivRank, setIvRank] = useState<any>(null);
  const [chartBars, setChartBars] = useState<ChartBar[]>([]);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);
  const [catalystEvents, setCatalystEvents] = useState<CatalystEvent[]>([]);
  const [signalDrawerOpen, setSignalDrawerOpen] = useState(false);
  const [watchlist, setWatchlist] = useState<string[]>(loadStoredWatchlist);

  // Desktop chart height follows the window.
  const [viewportH, setViewportH] = useState(() => window.innerHeight);
  // The chart takes the left two thirds of the page and the panel the right third,
  // so the chart is sized from the space it was actually given, not from a guess.
  const [bottomTab, setBottomTab] = useState<"positions" | "watchlist">("positions");
  const [chartWrap, setChartWrap] = useState<HTMLDivElement | null>(null);
  const [wrapH, setWrapH] = useState(0);
  useEffect(() => {
    if (!chartWrap || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setWrapH(chartWrap.clientHeight));
    ro.observe(chartWrap);
    setWrapH(chartWrap.clientHeight);
    return () => ro.disconnect();
  }, [chartWrap]);
  useEffect(() => {
    const onResize = () => setViewportH(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const persistWatchlist = (next: string[]) => {
    setWatchlist(next);
    try {
      localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignore quota/storage errors — in-memory state still updates */
    }
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
    return () => {
      clearInterval(si);
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


  // The chart page, on desktop and on phones: just the instrument bar and a tall
  // chart. Only the compact Equity/Options desk chart uses the layout below.
  if (!compact) {
    const chipsH = catalystEvents.length && !chartLoading ? 30 : 0;
    const chartH = isMobile
      ? Math.max(300, Math.min(720, viewportH - 330))
      : Math.max(260, (wrapH || viewportH - 430) - chipsH);
    const goTo = (raw: string) => {
      const next = raw.trim().toUpperCase();
      if (!/^[A-Z0-9.^=:-]{1,20}$/.test(next)) return false;
      if (!watchlist.includes(next)) persistWatchlist([...watchlist, next]);
      setSymbol(next);
      return true;
    };
    return (
      <div className="chart-workspace chart-workspace--dock">
        <div className="workstation-center">
          <InstrumentBar
            symbols={Array.from(new Set([symbol, ...watchlist])).slice(0, 6)}
            symbol={symbol}
            onSelect={setSymbol}
            onGoTo={goTo}
            onUnpin={watchlist.includes(symbol) && watchlist.length > 1 ? () => { const rest = watchlist.filter((t) => t !== symbol); removeTicker(symbol); setSymbol(rest[0]); } : undefined}
            price={selectedSnapshot?.last_close ?? latestBar?.close}
            changePct={selectedSnapshot?.change_pct}
            bar={latestBar}
            freshness={latestBar ? `Last close snapshot · Chart bar: ${new Date(latestBar.timestamp).toLocaleString()}` : "Chart data unavailable"}
            timeframes={TIMEFRAMES}
            timeframe={timeframe}
            onTimeframe={(k) => setTimeframe(k as Timeframe)}
            signalBadge={selectedSignal
              ? <SignalAttribution data={toChartSignalAttribution(selectedSignal)} size="sm" />
              : <span className="dock-nosignal">No signal</span>}
            onSignalDetail={() => setSignalDrawerOpen(true)}
          />
          <div className="chart-split">
          <div ref={setChartWrap} className="dock-chart instrument-card--flat chart-canvas-wrap" style={{ position: "relative" }}>
            {!chartLoading && <UpcomingCatalysts events={catalystEvents} />}
            {chartLoading ? (
              <div className="skeleton-block" style={{ height: chartH }} aria-busy="true" aria-label="Loading chart">
                <div className="skeleton-shimmer" />
              </div>
            ) : (
              <CandlestickChart bars={chartBars} levels={levels} height={chartH} />
            )}
            {chartError && <div className="dock-chart__error" role="status">{chartError}</div>}
          </div>
          <ChartDock
            tabs={[
              { key: "positions", label: "Positions", count: positionsError ? "—" : positions.length },
              { key: "watchlist", label: "Watchlist", count: watchlist.length },
            ]}
            active={bottomTab}
            onChange={(k) => setBottomTab(k as "positions" | "watchlist")}
          >
            {bottomTab === "positions"
              ? <PositionsTable positions={positions as never[]} error={Boolean(positionsError)} />
              : <WatchlistTable watchlist={watchlist} snapshots={watchSnapshots} symbol={symbol}
                  onSelect={setSymbol} onRemove={(t) => { removeTicker(t); if (t === symbol) setSymbol(watchlist.find((w) => w !== t) ?? symbol); }} />}
          </ChartDock>
          </div>
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

  return (
    <div className="chart-workspace chart-workspace--compact" style={{ padding: 0, display: "flex", flexDirection: "column", gap: 14, height: "100%" }}>
      <div className="workstation-grid" style={{ gridTemplateColumns: "1fr", flex: 1, minHeight: 0 }}>
        <div className="workstation-center">
          <div className="chart-quote" aria-label="Selected instrument quote">
            <div><span className="design-eyebrow">{symbol} · MARKET SNAPSHOT</span><strong>{fmtMoney(selectedSnapshot?.last_close)}</strong></div>
            <span style={{ color: selectedSnapshot?.change_pct == null ? "var(--ink-dim)" : pnlColor(selectedSnapshot.change_pct) }}>{fmtPct(selectedSnapshot?.change_pct)}</span>
            <p>Last close snapshot · {latestBar ? `Chart bar: ${new Date(latestBar.timestamp).toLocaleString()}` : "Chart data unavailable"}</p>
          </div>
          <Panel
            className="workstation-chart-panel"
            sectionStyle={{ overflow: "hidden" }}
            padding={0}
            title={
              <div className="chart-instrument-title" style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
                <span style={{ fontFamily: "var(--mono)", fontSize: 28, fontWeight: 700, color: "var(--cyan)" }}>{symbol}</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-dim)" }}>
                  {selectedSnapshot?.last_close != null ? fmtMoney(selectedSnapshot.last_close, 2) : "—"} · {fmtPct(selectedSnapshot?.change_pct, 2)}
                </span>
              </div>
            }
            action={
              <div className="chart-panel-actions">
                <div className="chart-timeframes" role="group" aria-label="Chart timeframe">
                  {TIMEFRAMES.map((item) => (
                    <Button selector key={item.key} active={timeframe === item.key} onClick={() => setTimeframe(item.key)}>
                      {item.label}
                    </Button>
                  ))}
                </div>
                <Button flat onClick={() => setSignalDrawerOpen(true)}>Signal Detail</Button>
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

              {!chartLoading && <UpcomingCatalysts events={catalystEvents} />}
              <div className="instrument-card--flat chart-canvas-wrap" style={{ position: "relative" }}>
                {chartLoading ? (
                  <div className="skeleton-block" style={{ height: isMobile ? 420 : 560 }} aria-busy="true" aria-label="Loading chart">
                    <div className="skeleton-shimmer" />
                  </div>
                ) : (
                  <CandlestickChart bars={chartBars} levels={levels} height={isMobile ? 420 : 560} />
                )}
              </div>
              {chartError && (
                <div style={{ marginTop: 10, fontFamily: "var(--mono)", fontSize: 10, color: "var(--red)" }}>
                  {chartError}
                </div>
              )}
            </div>
          </Panel>
        </div>
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
