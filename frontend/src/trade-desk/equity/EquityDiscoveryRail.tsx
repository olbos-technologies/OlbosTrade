/**
 * Equity discovery rail — watchlist + signal candidates + open positions.
 */

import React, { useEffect, useState } from "react";
import { pnlColor } from "../../utils/pnlTone";
import { api } from "../../api/client";
import SignalDirectionBadge from "../../components/SignalDirectionBadge";
import MissionCard from "../../components/MissionCard";
import { useDeskBlockContext } from "../../hooks/useDeskBlockContext";
import { deriveSignalBlockReason } from "../../utils/signalBlockReason";

const DEFAULT_WATCH = ["AAPL", "NVDA", "MSFT", "META", "AMZN", "QQQ", "SPY"];

function railConfidenceTone(pct: number): string {
  return pct >= 75 ? "var(--green)" : pct >= 62 ? "var(--cyan)" : "var(--amber)";
}

type DiscTab = "watch" | "signals" | "positions";

function RailEmpty({ title, hint, tone }: { title: string; hint?: string; tone?: "error" }) {
  return (
    <div className="instrument-card instrument-card--flat empty-chassis empty-chassis--compact">
      <p className="empty-chassis__title" style={tone === "error" ? { color: "var(--red)" } : undefined}>
        {title}
      </p>
      {hint && <p className="empty-chassis__hint">{hint}</p>}
    </div>
  );
}

export default function EquityDiscoveryRail({
  symbol,
  onSelect,
}: {
  symbol: string;
  onSelect: (symbol: string) => void;
}) {
  const [tab, setTab] = useState<DiscTab>("watch");
  const [snaps, setSnaps] = useState<Record<string, { last_close: number | null; change_pct: number | null }>>({});
  const [signals, setSignals] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [signalsError, setSignalsError] = useState(false);
  const [positionsError, setPositionsError] = useState(false);
  const blockCtx = useDeskBlockContext();
  const { minConfidence } = blockCtx;

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const entries = await Promise.all(
        DEFAULT_WATCH.map(async (t) => {
          try {
            const s: any = await api.getSnapshot(t);
            return [t, { last_close: s.last_close ?? null, change_pct: s.change_pct ?? null }] as const;
          } catch {
            return [t, { last_close: null, change_pct: null }] as const;
          }
        }),
      );
      if (!alive) return;
      setSnaps(Object.fromEntries(entries));
      try {
        const sig: any = await api.getEquitySignals(30);
        if (alive) { setSignals(sig.signals || []); setSignalsError(false); }
      } catch {
        if (alive) { setSignals([]); setSignalsError(true); }
      }
      try {
        const pos: any = await api.getPositions();
        if (alive) { setPositions(pos.positions || (Array.isArray(pos) ? pos : [])); setPositionsError(false); }
      } catch {
        if (alive) { setPositions([]); setPositionsError(true); }
      }
    };
    load();
    const id = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const tabs: { key: DiscTab; label: string }[] = [
    { key: "watch", label: "Watch" },
    { key: "signals", label: "Signals" },
    { key: "positions", label: "Held" },
  ];

  const actionable = signals.filter((s) => s.action !== "HOLD").slice(0, 20);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div className="discovery-rail-tabs" role="tablist" aria-label="Equity discovery">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`discovery-rail-tabs__btn${tab === t.key ? " discovery-rail-tabs__btn--active" : ""}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="mission-list" style={{ flex: 1, overflow: "auto", padding: 8 }}>
        {tab === "watch" &&
          DEFAULT_WATCH.map((t) => {
            const s = snaps[t];
            const on = t === symbol;
            const pct = s?.change_pct;
            const pctTone = pnlColor(pct);
            return (
              <MissionCard
                key={t}
                variant="compact"
                as="button"
                onClick={() => onSelect(t)}
                selected={on}
                aria-pressed={on}
                aria-label={`Select ${t}`}
                reward={typeof pct === "number" ? {
                  value: `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`,
                  tone: pctTone,
                } : undefined}
                title={t}
                subtitle={typeof s?.last_close === "number" ? `$${s.last_close.toFixed(2)}` : "—"}
              />
            );
          })}

        {tab === "signals" &&
          (actionable.length === 0 ? (
            <RailEmpty
              title={signalsError ? "Failed to load signals" : "No equity signals yet"}
              hint={signalsError ? undefined : "Run a scan on Live Signals to fill this rail."}
              tone={signalsError ? "error" : undefined}
            />
          ) : (
            actionable.map((s) => {
              const confPct = Math.round((s.confidence ?? 0) * 100);
              const tone = railConfidenceTone(confPct);
              const block = deriveSignalBlockReason(s, blockCtx);
              return (
                <MissionCard
                  key={s.id || `${s.ticker}-${s.generated_at}`}
                  variant="compact"
                  as="button"
                  onClick={() => onSelect(s.ticker)}
                  aria-label={`Select ${s.ticker} signal`}
                  reward={{ prefix: "CONF", value: `${confPct}%`, tone }}
                  title={(
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      {s.ticker}
                      <SignalDirectionBadge action={s.action} size="sm" />
                    </span>
                  )}
                  subtitle={block ? String(block) : "Above desk floor"}
                  meta={{
                    label: s.action === "HOLD" ? "HOLD" : confPct >= Math.round(minConfidence * 100) ? "LIVE" : "LOW",
                    tone: confPct >= Math.round(minConfidence * 100) ? "var(--green)" : "var(--amber)",
                  }}
                  progress={{ value: confPct, tone, label: `${s.ticker} confidence` }}
                />
              );
            })
          ))}

        {tab === "positions" &&
          (positions.length === 0 ? (
            <RailEmpty
              title={positionsError ? "Failed to load positions" : "No open positions"}
              hint={positionsError ? undefined : "Held equity will appear here."}
              tone={positionsError ? "error" : undefined}
            />
          ) : (
            positions.map((p: any) => {
              const pnl = p.unrealized_pnl;
              const pnlTone = pnlColor(pnl);
              return (
                <MissionCard
                  key={`${p.symbol}-${p.entry_date || "x"}`}
                  variant="compact"
                  as="button"
                  onClick={() => onSelect(p.symbol || p.underlying)}
                  aria-label={`Select ${p.symbol || p.underlying}`}
                  reward={typeof pnl === "number" ? {
                    prefix: "P&L",
                    value: `${pnl >= 0 ? "+" : ""}$${Math.abs(pnl).toFixed(0)}`,
                    tone: pnlTone,
                  } : undefined}
                  title={p.symbol || p.underlying}
                  subtitle={typeof p.quantity === "number" ? `${p.quantity} sh` : "Open position"}
                  meta={{ label: "HELD", tone: "var(--accent)" }}
                />
              );
            })
          ))}
      </div>
    </div>
  );
}
