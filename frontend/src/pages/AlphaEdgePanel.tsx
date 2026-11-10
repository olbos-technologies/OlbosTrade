/**
 * Alpha Edge Signal — portfolio watchlist + scan candidates with live scores.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { Panel, StatTile } from "../components/ui";
import { AssetToggle, type AssetTab } from "./EquitySignals";
import SignalAttribution from "../components/SignalAttribution";
import SignalDirectionBadge from "../components/SignalDirectionBadge";
import type { SignalAttributionData } from "../types/signal";
import { useAlphaEdgeWatchlist } from "../hooks/useAlphaEdgeWatchlist";
import type { AlphaEdgeCandidate } from "../utils/alphaEdgeCandidates";
import MissionCard, { MissionCardSkeleton } from "../components/MissionCard";
import { tint } from "../utils/tint";

interface AlphaEdgeResponse {
  ticker: string;
  asset_type: string;
  entry_score: number | null;
  hold_score: number | null;
  exit_score: number | null;
  exit_score_basis: string;
  risk_score: number | null;
  lifecycle_state: "new" | "confirmed" | "decaying" | "expired";
  score_trend: { direction: string; delta: number | null; basis: string };
  current_action: string | null;
  current_confidence: number | null;
  position: { held: boolean; direction?: string; quantity?: number };
  supporting_evidence: { feature: string; impact: number }[];
  deterioration_evidence: { feature: string; impact: number }[];
  data_sources: Record<string, string>;
  error: string | null;
  opportunity_score: number | null;
}

type ScoreState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: AlphaEdgeResponse }
  | { status: "error"; message: string };

const LIFECYCLE_COLOR: Record<string, string> = {
  new: "var(--ink-faint)",
  confirmed: "var(--green)",
  decaying: "var(--amber)",
  expired: "var(--red)",
};

const LIFECYCLE_LABEL: Record<string, string> = {
  new: "NEW",
  confirmed: "CONFIRMED",
  decaying: "DECAYING",
  expired: "EXPIRED",
};

function scoreColor(score: number): string {
  return score >= 70 ? "var(--green)" : score >= 45 ? "var(--amber)" : "var(--red)";
}

function tickerColor(ticker: string): string {
  let hash = 0;
  for (let i = 0; i < ticker.length; i++) {
    hash = ticker.charCodeAt(i) + ((hash << 5) - hash);
  }
  return `hsl(${Math.abs(hash) % 360}, 55%, 42%)`;
}

function TickerLogo({ ticker, size = 32 }: { ticker: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div style={{
        width: size, height: size, borderRadius: 6,
        background: tickerColor(ticker),
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: "var(--mono)", fontSize: size * 0.34, fontWeight: 700, color: "#fff",
        flexShrink: 0,
      }}>
        {ticker.slice(0, 2)}
      </div>
    );
  }
  return (
    <img
      src={`https://companiesmarketcap.com/img/company-logos/64/${ticker}.png`}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{ borderRadius: 6, objectFit: "contain", background: "var(--bg-3)", flexShrink: 0 }}
    />
  );
}

async function fetchScoresConcurrent(
  tickers: string[],
  assetType: "equity" | "options",
  onUpdate: (ticker: string, state: ScoreState) => void,
  cancelled: () => boolean,
  concurrency = 4,
) {
  const queue = [...tickers];
  const worker = async () => {
    while (queue.length > 0) {
      if (cancelled()) return;
      const ticker = queue.shift();
      if (!ticker) return;
      onUpdate(ticker, { status: "loading" });
      try {
        const data = await api.getAlphaEdge(ticker, assetType) as AlphaEdgeResponse;
        if (cancelled()) return;
        if (data.error) {
          onUpdate(ticker, { status: "error", message: data.error });
        } else {
          onUpdate(ticker, { status: "ready", data });
        }
      } catch (e) {
        if (cancelled()) return;
        onUpdate(ticker, {
          status: "error",
          message: e instanceof Error ? e.message : "Failed to load",
        });
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, tickers.length || 1) }, worker));
}

function SourceBadge({ source }: { source: AlphaEdgeCandidate["source"] }) {
  const isHeld = source === "held";
  return (
    <span className="mono" style={{
      fontSize: 8, fontWeight: 700, letterSpacing: "0.08em",
      padding: "2px 7px", borderRadius: 10,
      background: isHeld ? "rgba(59,130,246,0.12)" : "var(--bg-4)",
      border: `1px solid ${isHeld ? "rgba(59,130,246,0.35)" : "var(--line-dim)"}`,
      color: isHeld ? "var(--accent)" : "var(--ink-dim)",
    }}>
      {isHeld ? "HELD" : "SCAN"}
    </span>
  );
}

function ScoreTile({ label, value, sublabel }: { label: string; value: number | null; sublabel?: string }) {
  return (
    <div className="instrument-card instrument-card--flat" style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="kicker">{label}</span>
      <span className="data-val sm" style={{ color: value != null ? scoreColor(value) : "var(--ink-faint)" }}>
        {value != null ? value.toFixed(0) : "—"}
      </span>
      {sublabel && <span className="mono" style={{ fontSize: 9, color: "var(--ink-faint)" }}>{sublabel}</span>}
    </div>
  );
}

function AlphaEdgeDetail({ data }: { data: AlphaEdgeResponse }) {
  const attribution: SignalAttributionData = {
    direction: data.current_action || "NEUTRAL",
    source: "Alpha Edge Signal",
    confidence: data.current_confidence,
    updatedAt: null,
    authority: "advisory",
    topPositiveFactors: data.supporting_evidence,
    topNegativeFactors: data.deterioration_evidence,
  };

  return (
    <Panel padding={0} title={
      <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <TickerLogo ticker={data.ticker} size={24} />
        <span className="mono" style={{ fontWeight: 700 }}>{data.ticker}</span>
      </span>
    }>
      <div className="workspace-page-root" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className="pill" style={{
            color: LIFECYCLE_COLOR[data.lifecycle_state],
            border: `1px solid ${tint(LIFECYCLE_COLOR[data.lifecycle_state], 0.376)}`,
            background: `${tint(LIFECYCLE_COLOR[data.lifecycle_state], 0.082)}`,
          }}>
            {LIFECYCLE_LABEL[data.lifecycle_state] || data.lifecycle_state.toUpperCase()}
          </span>
          {data.current_action && (
            <SignalDirectionBadge
              action={data.current_action}
              showSide={false}
            />
          )}
          {!data.current_action && data.position.held && data.position.direction && (
            <SignalDirectionBadge action={data.position.direction} showSide={false} />
          )}
          {data.position.held && data.position.quantity != null && (
            <span className="mono" style={{ fontSize: 10, color: "var(--ink-dim)" }}>
              × {data.position.quantity}
            </span>
          )}
          <span style={{ flex: 1 }} />
          <span className="mono" style={{
            fontSize: 10,
            color: data.score_trend.direction === "improving" ? "var(--green)"
              : data.score_trend.direction === "declining" ? "var(--red)"
                : "var(--ink-faint)",
          }}>
            {data.score_trend.direction === "not_tracked" ? "Trend n/a" : data.score_trend.direction}
            {data.score_trend.delta != null ? ` ${data.score_trend.delta >= 0 ? "+" : ""}${data.score_trend.delta.toFixed(1)}` : ""}
          </span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: 8 }}>
          <ScoreTile label="Opportunity" value={data.opportunity_score} />
          <ScoreTile label="Entry" value={data.entry_score} />
          <ScoreTile label="Hold" value={data.hold_score} sublabel={data.hold_score == null ? "no position" : undefined} />
          <ScoreTile label="Exit" value={data.exit_score} />
          <ScoreTile label="Risk" value={data.risk_score} />
        </div>

        <div>
          <div className="kicker" style={{ marginBottom: 6 }}>Evidence</div>
          <SignalAttribution data={attribution} />
        </div>

        <div className="mono" style={{ fontSize: 9, color: "var(--ink-faint)" }}>
          {Object.entries(data.data_sources).map(([k, v]) => `${k}: ${v}`).join(" · ")}
        </div>
      </div>
    </Panel>
  );
}

function CandidateCard({
  candidate,
  scoreState,
  selected,
  onSelect,
}: {
  candidate: AlphaEdgeCandidate;
  scoreState: ScoreState;
  selected: boolean;
  onSelect: () => void;
}) {
  const data = scoreState.status === "ready" ? scoreState.data : null;
  const loading = scoreState.status === "loading" || scoreState.status === "idle";

  if (loading && !data) {
    return <MissionCardSkeleton />;
  }

  const opp = data?.opportunity_score ?? null;
  const entry = data?.entry_score ?? null;
  const lifecycle = data?.lifecycle_state;
  const lifecycleTone = lifecycle ? LIFECYCLE_COLOR[lifecycle] : undefined;

  const subtitle = scoreState.status === "error"
    ? <span style={{ color: "var(--red)" }}>{scoreState.message}</span>
    : candidate.scanAction && !data?.current_action
      ? candidate.scanConfidence != null
        ? `${Math.round(candidate.scanConfidence * 100)}% scan confidence · ${candidate.source === "held" ? "Open position" : "Fresh scan hit"}`
        : candidate.source === "held" ? "Open position" : "Fresh scan hit"
      : data
        ? [
            data.current_action ? `${data.current_action} signal` : data.position.held ? "Position held" : "Monitoring",
            data.entry_score != null ? `Entry ${data.entry_score.toFixed(0)}` : null,
            data.hold_score != null ? `Hold ${data.hold_score.toFixed(0)}` : null,
          ].filter(Boolean).join(" · ")
        : undefined;

  return (
    <MissionCard
      as="button"
      onClick={onSelect}
      selected={selected}
      aria-pressed={selected}
      aria-label={`${candidate.ticker} Alpha Edge`}
      reward={opp != null ? {
        prefix: "OPP",
        value: opp.toFixed(0),
        tone: scoreColor(opp),
      } : undefined}
      title={(
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <TickerLogo ticker={candidate.ticker} size={22} />
          <span className="mono" style={{ fontWeight: 700 }}>{candidate.ticker}</span>
          <SourceBadge source={candidate.source} />
          {(data?.current_action || candidate.scanAction) && (
            <SignalDirectionBadge
              action={data?.current_action || candidate.scanAction}
              size="sm"
              showSide={false}
            />
          )}
          {!data?.current_action && !candidate.scanAction && data?.position.held && data.position.direction && (
            <SignalDirectionBadge action={data.position.direction} size="sm" showSide={false} />
          )}
        </span>
      )}
      subtitle={subtitle}
      meta={lifecycle ? {
        label: LIFECYCLE_LABEL[lifecycle] || lifecycle.toUpperCase(),
        tone: lifecycleTone,
        icon: "⏳",
      } : {
        label: candidate.source === "held" ? "HELD" : "SCAN",
      }}
      progress={entry != null ? {
        value: entry,
        tone: scoreColor(entry),
        label: `${candidate.ticker} entry score`,
      } : undefined}
    />
  );
}

function SectionHeader({ title, count, hint }: { title: string; count: number; hint?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10, gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="panel-title">{title}</span>
        <span className="mono" style={{ fontSize: 11, color: "var(--accent)", fontWeight: 700 }}>{count}</span>
      </div>
      {hint && <span className="mono" style={{ fontSize: 9, color: "var(--ink-faint)" }}>{hint}</span>}
    </div>
  );
}

function oppFromScores(scores: Record<string, ScoreState>, ticker: string): number {
  const st = scores[ticker];
  if (st?.status === "ready") return st.data.opportunity_score ?? -1;
  return -1;
}

function sortByOpportunity(list: AlphaEdgeCandidate[], scores: Record<string, ScoreState>): AlphaEdgeCandidate[] {
  return [...list].sort((a, b) => oppFromScores(scores, b.ticker) - oppFromScores(scores, a.ticker));
}

export default function AlphaEdgePanel() {
  const [assetTab, setAssetTab] = useState<AssetTab>("equities");
  const [symbolInput, setSymbolInput] = useState("");
  const [manualLookup, setManualLookup] = useState<AlphaEdgeResponse | null>(null);
  const [manualLoading, setManualLoading] = useState(false);
  const [manualError, setManualError] = useState<string | null>(null);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [scores, setScores] = useState<Record<string, ScoreState>>({});

  const { candidates, loading, error, refresh, lastRefresh, assetType } = useAlphaEdgeWatchlist(assetTab);
  const fetchGen = useRef(0);

  const held = useMemo(() => candidates.filter(c => c.source === "held"), [candidates]);
  const scan = useMemo(
    () => sortByOpportunity(candidates.filter(c => c.source === "scan"), scores),
    [candidates, scores],
  );

  const scoreStats = useMemo(() => {
    const total = candidates.length;
    let ready = 0;
    let topOpp = -1;
    for (const c of candidates) {
      const st = scores[c.ticker];
      if (st?.status === "ready") {
        ready += 1;
        const o = st.data.opportunity_score;
        if (o != null && o > topOpp) topOpp = o;
      }
    }
    return { total, ready, topOpp: topOpp >= 0 ? topOpp : null };
  }, [candidates, scores]);

  const setScore = useCallback((ticker: string, state: ScoreState) => {
    setScores(prev => ({ ...prev, [ticker]: state }));
  }, []);

  useEffect(() => {
    const tickers = candidates.map(c => c.ticker);
    if (tickers.length === 0) {
      setScores({});
      setSelectedTicker(null);
      return;
    }

    const gen = ++fetchGen.current;
    const cancelled = () => gen !== fetchGen.current;

    setScores(Object.fromEntries(tickers.map(t => [t, { status: "idle" as const }])));
    setSelectedTicker(prev => (prev && tickers.includes(prev) ? prev : tickers[0]));

    fetchScoresConcurrent(tickers, assetType, setScore, cancelled).catch(() => {});

    return () => { fetchGen.current += 1; };
  }, [candidates, assetType, setScore]);

  const lookup = () => {
    const ticker = symbolInput.trim().toUpperCase();
    if (!ticker) return;
    setManualLoading(true);
    setManualError(null);
    setManualLookup(null);
    setSelectedTicker(ticker);
    (api.getAlphaEdge(ticker, assetType) as Promise<AlphaEdgeResponse>)
      .then(d => {
        setManualLookup(d);
        if (d.error) setManualError(d.error);
        setScore(ticker, d.error
          ? { status: "error", message: d.error }
          : { status: "ready", data: d });
      })
      .catch(e => setManualError(e?.message || "Failed to load Alpha Edge"))
      .finally(() => setManualLoading(false));
  };

  const selectedData = useMemo(() => {
    if (!selectedTicker) return null;
    const st = scores[selectedTicker];
    if (st?.status === "ready") return st.data;
    if (manualLookup?.ticker === selectedTicker) return manualLookup;
    return null;
  }, [selectedTicker, scores, manualLookup]);

  const onTabChange = (t: AssetTab) => {
    setAssetTab(t);
    setManualLookup(null);
    setManualError(null);
    setSymbolInput("");
  };

  const watchlistBody = (held.length > 0 || scan.length > 0) ? (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {held.length > 0 && (
        <section>
          <SectionHeader title="Open positions" count={held.length} hint="Live book" />
          <div className="mission-list">
            {held.map(c => (
              <CandidateCard
                key={c.ticker}
                candidate={c}
                scoreState={scores[c.ticker] || { status: "idle" }}
                selected={selectedTicker === c.ticker}
                onSelect={() => setSelectedTicker(c.ticker)}
              />
            ))}
          </div>
        </section>
      )}
      {scan.length > 0 && (
        <section>
          <SectionHeader title="Scan candidates" count={scan.length} hint="Sorted by opportunity" />
          <div className="mission-list">
            {scan.map(c => (
              <CandidateCard
                key={c.ticker}
                candidate={c}
                scoreState={scores[c.ticker] || { status: "idle" }}
                selected={selectedTicker === c.ticker}
                onSelect={() => setSelectedTicker(c.ticker)}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  ) : !loading ? (
    <div className="instrument-card instrument-card--flat" style={{ padding: 28, textAlign: "center" }}>
      <p className="mono" style={{ fontSize: 12, color: "var(--ink-dim)", marginBottom: 8 }}>
        No {assetType} positions or recent scan hits
      </p>
      <p className="kicker">
        Run a scan on <strong style={{ color: "var(--ink)" }}>Live Signals</strong>, or look up a symbol above.
      </p>
    </div>
  ) : null;

  return (
    <div className="workspace-page-root" style={{ display: "flex", flexDirection: "column", gap: 16, padding: 16 }}>
      <div className="instrument-card" style={{
        padding: "12px 16px",
        borderLeft: "2px solid var(--accent)",
        display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12,
      }}>
        <div>
          <div className="panel-title">Alpha Edge Signal</div>
          <p className="kicker" style={{ marginTop: 4 }}>
            Portfolio watchlist · live Entry / Hold / Exit / Risk per symbol
          </p>
        </div>
        <span style={{ flex: 1 }} />
        <AssetToggle tab={assetTab} onChange={onTabChange} />
      </div>

      <div className="instrument-stat-strip" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
        <StatTile variant="divider" size="sm" label="Open positions" value={held.length} />
        <StatTile variant="divider" size="sm" label="Scan candidates" value={scan.length} />
        <StatTile
          variant="divider"
          size="sm"
          label="Top opportunity"
          value={scoreStats.topOpp != null ? scoreStats.topOpp.toFixed(0) : "—"}
          tone={scoreStats.topOpp != null ? scoreColor(scoreStats.topOpp) : undefined}
        />
        <StatTile
          variant="divider"
          size="sm"
          label="Scores loaded"
          value={`${scoreStats.ready}/${scoreStats.total}`}
          sub={lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : undefined}
        />
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={() => refresh()}
          disabled={loading}
          className="mono instrument-card--flat"
          style={{
            padding: "8px 14px", color: "var(--ink-dim)", fontSize: 10,
            cursor: loading ? "wait" : "pointer", border: "1px solid var(--line-dim)",
          }}
        >
          {loading ? "Refreshing…" : "Refresh watchlist"}
        </button>
        <span style={{ flex: 1 }} />
        <input
          value={symbolInput}
          onChange={e => setSymbolInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") lookup(); }}
          placeholder="Symbol lookup…"
          aria-label="Alpha Edge symbol lookup"
          className="mono instrument-card--flat"
          style={{
            fontSize: 11, padding: "8px 12px", color: "var(--ink)",
            border: "1px solid var(--line-dim)", width: 140,
          }}
        />
        <button
          onClick={lookup}
          disabled={manualLoading || !symbolInput.trim()}
          className="mono"
          style={{
            padding: "8px 16px", background: "var(--accent)", color: "var(--bg-0)",
            border: "none", borderRadius: "var(--radius-control)", fontWeight: 700, fontSize: 11,
            cursor: manualLoading ? "wait" : "pointer", opacity: manualLoading ? 0.6 : 1,
          }}
        >
          {manualLoading ? "…" : "Look up"}
        </button>
      </div>

      {(error || manualError) && (
        <div style={{
          background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: "var(--radius-control)", padding: "10px 14px",
          fontFamily: "var(--mono)", fontSize: 11, color: "var(--red)",
        }}>
          {error || manualError}
        </div>
      )}

      <div className="alpha-edge-layout">
        <div className="alpha-edge-main">{watchlistBody}</div>
        {selectedData && (
          <div className="alpha-edge-detail-pane">
            <AlphaEdgeDetail data={selectedData} />
          </div>
        )}
      </div>
    </div>
  );
}
