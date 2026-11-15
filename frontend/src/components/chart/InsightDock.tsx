import { useRef, useState } from "react";
import {
  DASH, INSIGHT_TABS, ageLabel, distancePct, fmtPrice, fmtSignedPct, nextTab, rewardToRisk,
  type InsightTab,
} from "../../utils/chartInsight";

export interface InsightSignal {
  action: "BUY" | "SELL" | "HOLD";
  /** A model score, not a probability — shown under that name. */
  confidence: number;
  generated_at: string;
  source?: string | null;
  reasons?: string[];
  trade_plan?: { entry_price?: number; stop_price?: number; target_price?: number; risk_reward?: number };
  indicators?: { rsi?: number; macd?: number; bb_pct_b?: number; volume_ratio?: number };
}

export interface InsightLevel { label: string; value: number; color: string }

export interface InsightCatalyst { name: string; date: string; days_away: number; severity: string }

export interface InsightVol {
  iv_rank?: number | null;
  iv_now_pct?: number | null;
  iv_high_pct?: number | null;
  iv_low_pct?: number | null;
  method?: string | null;
}

const LABELS: Record<InsightTab, string> = {
  signal: "Signal", levels: "Levels", volatility: "Volatility", catalysts: "Catalysts",
};

const num = (n: number | null | undefined, digits = 1) =>
  typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : DASH;

/**
 * The research dock under the chart: four tabs, each reading data the page has
 * already loaded. Nothing here is fetched or computed beyond display arithmetic,
 * and a missing value is a dash. Collapsible so a short window keeps its chart.
 */
export default function InsightDock({
  symbol, signal, planned, entry, levels, vol, volFailed, catalysts, catalystsFailed, defaultOpen = true,
}: {
  symbol: string;
  signal: InsightSignal | null;
  /** True when the levels are the signal's own plan, false when chart-derived support/resistance. */
  planned: boolean;
  entry: number;
  levels: InsightLevel[];
  vol: InsightVol | null;
  /** True when the volatility request failed, so a missing rank is not read as "no data". */
  volFailed: boolean;
  catalysts: InsightCatalyst[];
  /** True when the catalyst request failed, so an empty list is not read as "none upcoming". */
  catalystsFailed: boolean;
  defaultOpen?: boolean;
}) {
  const [tab, setTab] = useState<InsightTab>("signal");
  const [open, setOpen] = useState(defaultOpen);
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const onKey = (e: React.KeyboardEvent) => {
    const next = nextTab(tab, e.key);
    if (!next) return;
    e.preventDefault();
    setTab(next);
    refs.current[next]?.focus();
  };

  const plan = signal?.trade_plan;
  const rr = plan?.risk_reward ?? rewardToRisk(plan?.entry_price, plan?.stop_price, plan?.target_price);

  return (
    <section className={`insight-dock${open ? "" : " insight-dock--closed"}`} aria-label="Research dock">
      <div className="insight-dock__bar">
        <div role="tablist" aria-label={`${symbol} research`} className="insight-dock__tabs" onKeyDown={onKey}>
          {INSIGHT_TABS.map((k) => (
            <button
              key={k} type="button" role="tab" id={`insight-tab-${k}`}
              ref={(el) => { refs.current[k] = el; }}
              aria-selected={tab === k} aria-controls="insight-panel"
              tabIndex={tab === k ? 0 : -1}
              onClick={() => { setTab(k); setOpen(true); }}
            >
              {LABELS[k]}
            </button>
          ))}
        </div>
        <button type="button" className="insight-dock__toggle" aria-expanded={open}
          aria-controls="insight-panel" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide" : "Show"}
        </button>
      </div>

      {open && (
        <div id="insight-panel" role="tabpanel" aria-labelledby={`insight-tab-${tab}`} className="insight-dock__panel">
          {tab === "signal" && (
            signal ? (
              <div className="insight-grid">
                <dl className="insight-facts">
                  <div><dt>Signal</dt><dd><strong>{signal.action}</strong> {symbol}</dd></div>
                  <div><dt>Model score</dt><dd className="tnum">{Math.round(signal.confidence * 100)}% <span className="insight-dim">(not a probability)</span></dd></div>
                  <div><dt>Generated</dt><dd className="tnum">{ageLabel(signal.generated_at)}</dd></div>
                  <div><dt>Source</dt><dd>{signal.source || DASH}</dd></div>
                  <div><dt>Reward : risk</dt><dd className="tnum">{rr != null ? `${rr.toFixed(2)} : 1` : DASH}</dd></div>
                </dl>
                <dl className="insight-facts">
                  <div><dt>RSI</dt><dd className="tnum">{num(signal.indicators?.rsi)}</dd></div>
                  <div><dt>MACD</dt><dd className="tnum">{num(signal.indicators?.macd, 2)}</dd></div>
                  <div><dt>Bollinger %B</dt><dd className="tnum">{num(signal.indicators?.bb_pct_b, 2)}</dd></div>
                  <div><dt>Volume ×</dt><dd className="tnum">{num(signal.indicators?.volume_ratio, 2)}</dd></div>
                </dl>
                <div className="insight-reasons">
                  <h3>Why</h3>
                  {signal.reasons?.length
                    ? <ul>{signal.reasons.slice(0, 4).map((r, i) => <li key={i}>{r}</li>)}</ul>
                    : <p className="insight-dim">No reasons recorded.</p>}
                </div>
              </div>
            ) : (
              <p className="insight-dim">No signal for {symbol}. This is the absence of a signal, not a neutral reading.</p>
            )
          )}

          {tab === "levels" && (
            levels.length ? (
              <>
                <table className="dock-table insight-levels" aria-label="Price levels">
                  <thead><tr><th>Level</th><th>Price</th><th>From entry</th></tr></thead>
                  <tbody>
                    {levels.map((l) => (
                      <tr key={l.label}>
                        <td><span className="insight-swatch" style={{ background: l.color }} aria-hidden="true" />{l.label}</td>
                        <td className="tnum">{fmtPrice(l.value)}</td>
                        <td className="tnum">{l.label === "ENTRY" ? DASH : fmtSignedPct(distancePct(l.value, entry))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="dock-note">
                  {planned
                    ? "The signal's own plan. TP1 and TP2 are one-third and two-thirds of the way to its single target."
                    : "No signal plan: entry is the last bar, support and resistance are the 20-bar low and high. Not a trade plan."}
                </p>
              </>
            ) : <p className="insight-dim">No price levels: the chart has no bars.</p>
          )}

          {tab === "volatility" && (
            vol && vol.iv_rank != null ? (
              <>
                <dl className="insight-facts">
                  <div><dt>Volatility rank</dt><dd className="tnum">{num(vol.iv_rank, 0)} / 100</dd></div>
                  <div><dt>Now (annualised)</dt><dd className="tnum">{num(vol.iv_now_pct)}%</dd></div>
                  <div><dt>1-year high</dt><dd className="tnum">{num(vol.iv_high_pct)}%</dd></div>
                  <div><dt>1-year low</dt><dd className="tnum">{num(vol.iv_low_pct)}%</dd></div>
                </dl>
                <p className="dock-note">
                  {vol.method === "atr_rv_proxy"
                    ? "Realized volatility from 14-day ATR on daily bars, ranked over one year. It is a proxy, not implied volatility from the options market."
                    : "Method not reported by the server."}
                </p>
              </>
            ) : (
              <p className="dock-note dock-note--warn">
                {volFailed ? "Volatility rank unavailable: the request failed." : "Not enough history for a volatility rank."}
              </p>
            )
          )}

          {tab === "catalysts" && (
            catalysts.length ? (
              <table className="dock-table" aria-label="Upcoming catalysts">
                <thead><tr><th>Event</th><th>Date</th><th>In</th><th>Severity</th></tr></thead>
                <tbody>
                  {[...catalysts].sort((a, b) => a.days_away - b.days_away).map((c, i) => (
                    <tr key={`${c.name}-${c.date}-${i}`}>
                      <td className="dock-why">{c.name}</td>
                      <td className="tnum">{c.date}</td>
                      <td className="tnum">{c.days_away === 0 ? "today" : `${c.days_away}d`}</td>
                      <td>{c.severity.replace(/_/g, " ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : catalystsFailed
              ? <p className="dock-note dock-note--warn">Catalysts unavailable: the request failed. This is not an empty calendar.</p>
              : <p className="insight-dim">No catalysts listed in the next 21 days.</p>
          )}
        </div>
      )}
    </section>
  );
}
