/**
 * Options discovery — underlying picker + recent options signals.
 */

import React, { useEffect, useState } from "react";
import { pnlColor } from "../../utils/pnlTone";
import { api } from "../../api/client";
import SignalDirectionBadge from "../../components/SignalDirectionBadge";
import MissionCard from "../../components/MissionCard";

const UNDERLYINGS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "MSFT", "META", "AMZN", "TSLA"];

function railConfidenceTone(pct: number): string {
  return pct >= 75 ? "var(--green)" : pct >= 62 ? "var(--cyan)" : "var(--amber)";
}

type DiscTab = "underlyings" | "signals" | "positions";

function RailEmpty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="instrument-card instrument-card--flat empty-chassis empty-chassis--compact">
      <p className="empty-chassis__title">{title}</p>
      {hint && <p className="empty-chassis__hint">{hint}</p>}
    </div>
  );
}

export default function OptionsDiscoveryRail({
  symbol,
  onSelect,
}: {
  symbol: string;
  onSelect: (symbol: string) => void;
}) {
  const [tab, setTab] = useState<DiscTab>("underlyings");
  const [snaps, setSnaps] = useState<Record<string, { last_close: number | null; change_pct: number | null }>>({});
  const [signals, setSignals] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [signalsError, setSignalsError] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const entries = await Promise.all(
        UNDERLYINGS.map(async (t) => {
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
        const sig: any = await api.getOptionsSignals(30);
        if (alive) { setSignals(sig.signals || []); setSignalsError(false); }
      } catch {
        if (alive) { setSignals([]); setSignalsError(true); }
      }
      try {
        const pos: any = await api.getPositions();
        const list = pos.positions || (Array.isArray(pos) ? pos : []);
        if (alive) {
          setPositions(
            list.filter(
              (p: any) =>
                (p.asset_type || "").toLowerCase().includes("option") ||
                p.option_type ||
                p.short_strike ||
                p.spread_type,
            ),
          );
        }
      } catch {
        if (alive) setPositions([]);
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
    { key: "underlyings", label: "Underlyings" },
    { key: "signals", label: "Signals" },
    { key: "positions", label: "Held" },
  ];

  const actionable = signals
    .filter((s) => s.action === "BUY_SPREAD" || s.action === "SELL_SPREAD" || s.action === "BUY" || s.action === "SELL")
    .slice(0, 20);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div className="discovery-rail-tabs" role="tablist" aria-label="Options discovery">
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
        {tab === "underlyings" &&
          UNDERLYINGS.map((t) => {
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
              title={signalsError ? "Failed to load signals" : "No options signals yet"}
              hint={signalsError ? undefined : "Run the Options Scanner to fill this rail."}
            />
          ) : (
            actionable.map((s) => {
              const rawConf = s.confidence ?? s.signal_score ?? 0;
              const pct = Math.round(rawConf <= 1 ? rawConf * 100 : rawConf);
              const tone = railConfidenceTone(pct);
              const credit = s.spread?.net_credit;
              return (
                <MissionCard
                  key={s.id || `${s.ticker}-${s.generated_at}`}
                  variant="compact"
                  as="button"
                  onClick={() => onSelect(s.ticker)}
                  aria-label={`Select ${s.ticker} signal`}
                  reward={credit != null
                    ? { prefix: "CR", value: `$${Math.abs(credit).toFixed(2)}`, tone: "var(--green)" }
                    : { prefix: "CONF", value: `${pct}%`, tone }}
                  title={(
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                      {s.ticker}
                      <SignalDirectionBadge action={s.action} size="sm" />
                    </span>
                  )}
                  subtitle={s.strategy ? String(s.strategy).replace(/_/g, " ") : "Options signal"}
                  meta={{
                    label: s.spread?.dte != null ? `${s.spread.dte}D` : "OPT",
                    tone: "var(--cyan)",
                    icon: "⏳",
                  }}
                  progress={{ value: pct, tone, label: `${s.ticker} signal strength` }}
                />
              );
            })
          ))}

        {tab === "positions" &&
          (positions.length === 0 ? (
            <RailEmpty
              title="No open options positions"
              hint="Held spreads will appear here."
            />
          ) : (
            positions.map((p: any, i: number) => (
              <MissionCard
                key={`${p.symbol || p.underlying}-${i}`}
                variant="compact"
                as="button"
                onClick={() => onSelect(p.underlying || p.symbol)}
                aria-label={`Select ${p.underlying || p.symbol}`}
                title={p.underlying || p.symbol}
                subtitle={(p.spread_type || p.option_type || "options").toString().replace(/_/g, " ")}
                meta={{ label: "HELD", tone: "var(--accent)" }}
              />
            ))
          ))}
      </div>
    </div>
  );
}
