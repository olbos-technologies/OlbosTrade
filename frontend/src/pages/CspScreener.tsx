import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { StatTile, Badge } from "../components/ui";

type RiskProfile = "conservative" | "balanced" | "aggressive";

interface RiskFlag {
  level: "low" | "moderate" | "high" | "unavailable";
  label: string;
  detail: string;
}

interface Eligibility {
  status: "eligible" | "caution" | "blocked";
  blocked_reasons: string[];
  caution_reasons: string[];
  unblock_condition: string | null;
  modes_allowed: string[];
}

interface CspCandidate {
  ticker: string;
  stock_price: number;
  put_strike: number;
  dte: number;
  delta: number;
  premium: number;
  collateral_required: number;
  breakeven: number;
  wheel_quality_score: number;
  wqs_contributions: Record<string, number>;
  assignment_risk: RiskFlag;
  macro_event_risk: RiskFlag;
  earnings_warning: RiskFlag | null;
  portfolio_overlap_pct: number;
  suggested_contracts: number;
  eligibility: Eligibility;
}

interface ScreenContext {
  trading_mode: string;
  environment: string;
  risk_profile: string;
  buying_power: number;
  portfolio_heat_pct: number;
  active_wheel_count: number;
  macro_event_risk: string;
}

interface ScreenResponse {
  as_of: string;
  data_complete: boolean;
  context: ScreenContext;
  candidates: CspCandidate[];
}

const DEFAULT_WATCHLIST = ["KO", "PFE", "T", "INTC", "F", "VZ", "WBA", "KMI"];

// Tone maps a risk level to a terminal color var. Color is never the only cue —
// every badge also carries a text label.
function levelTone(level: string): string {
  switch (level) {
    case "low": return "var(--green)";
    case "moderate": return "var(--amber)";
    case "high": return "var(--red)";
    case "blocked": return "var(--red)";
    default: return "var(--ink-dim)";
  }
}

function statusTone(status: string): string {
  if (status === "eligible") return "var(--green)";
  if (status === "caution") return "var(--amber)";
  return "var(--red)";
}

function fmtUsd(n: number): string {
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function RiskBadge({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <Badge kind="tag" tone={tone} style={{ fontSize: 11, letterSpacing: "0.03em", borderRadius: 3, padding: "2px 7px", opacity: 1, whiteSpace: "nowrap" }}>
      {children}
    </Badge>
  );
}

export default function CspScreener() {
  const [profile, setProfile] = useState<RiskProfile>("balanced");
  const [dteMin, setDteMin] = useState(21);
  const [dteMax, setDteMax] = useState(45);
  const [deltaMin, setDeltaMin] = useState(0.15);
  const [deltaMax, setDeltaMax] = useState(0.30);
  const [minOi, setMinOi] = useState(500);
  const [minWqs, setMinWqs] = useState(60);
  const [excludeEarnings, setExcludeEarnings] = useState(7);

  const [data, setData] = useState<ScreenResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const runScreen = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = {
        watchlist: DEFAULT_WATCHLIST,
        dte_min: dteMin, dte_max: dteMax,
        delta_min: deltaMin, delta_max: deltaMax,
        min_open_interest: minOi,
        exclude_earnings_within_days: excludeEarnings,
        risk_profile: profile,
        min_wheel_quality_score: minWqs,
      };
      const res = (await api.screenCsp(body)) as ScreenResponse;
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Screen failed");
    } finally {
      setLoading(false);
    }
  }, [profile, dteMin, dteMax, deltaMin, deltaMax, minOi, minWqs, excludeEarnings]);

  useEffect(() => { runScreen(); }, []); // initial load

  const ctx = data?.context;
  const warnings = useMemo(() => {
    const w: string[] = [];
    if (ctx?.environment === "paper") w.push("Paper trading — no live orders");
    if (ctx && ctx.macro_event_risk !== "low") w.push(`Macro event risk: ${ctx.macro_event_risk}`);
    if (ctx && ctx.portfolio_heat_pct >= 70) w.push(`Portfolio heat elevated (${ctx.portfolio_heat_pct}%)`);
    if (data && !data.data_complete) w.push("Options data stale or incomplete");
    return w;
  }, [ctx, data]);

  return (
    <div style={{ padding: 16, fontFamily: "var(--sans)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 4 }}>
        <h1 style={{ fontSize: 18, letterSpacing: "0.04em", margin: 0 }}>CSP Screener</h1>
        <span style={{ color: "var(--ink-dim)", fontSize: 12 }}>Wheel &amp; Income Lab</span>
      </div>

      {/* Status bar */}
      <div className="instrument-stat-strip" style={{
        display: "flex", alignItems: "center", flexWrap: "wrap", gap: 4,
        margin: "10px 0",
      }}>
        <StatTile variant="divider" size="sm" label="Env" value={(ctx?.environment || "—").toUpperCase()} tone="var(--amber)" />
        <StatTile variant="divider" size="sm" label="Trading style" value={(ctx?.trading_mode || "—").toUpperCase()} tone="var(--cyan)" />
        <StatTile variant="divider" size="sm" label="Profile" value={profile.toUpperCase()} />
        <StatTile variant="divider" size="sm" label="Buying power" value={ctx ? fmtUsd(ctx.buying_power) : "—"} />
        <StatTile variant="divider" size="sm" label="Heat" value={ctx ? `${ctx.portfolio_heat_pct}%` : "—"}
          tone={ctx && ctx.portfolio_heat_pct >= 70 ? "var(--amber)" : "var(--ink)"} />
        <StatTile variant="divider" size="sm" label="Wheels" value={ctx ? String(ctx.active_wheel_count) : "—"} />
        <StatTile variant="divider" size="sm" label="Macro risk" value={(ctx?.macro_event_risk || "—").toUpperCase()}
          tone={levelTone(ctx?.macro_event_risk || "")} />
      </div>

      {/* Warning banners */}
      {warnings.map((w) => (
        <div key={w} style={{
          display: "flex", alignItems: "center", gap: 8, fontSize: 12,
          color: "var(--amber)", border: "1px solid var(--amber)", borderRadius: 4,
          padding: "7px 12px", marginBottom: 6, fontFamily: "var(--mono)",
        }}>
          <span aria-hidden="true">▲</span> {w}
        </div>
      ))}

      <div style={{ display: "grid", gridTemplateColumns: "220px minmax(0,1fr)", gap: 14, marginTop: 12 }}>
        {/* Filter panel */}
        <div className="instrument-card" style={{ padding: 14 }}>
          <div className="kicker" style={{ marginBottom: 12 }}>Filters</div>

          <label style={{ fontSize: 12, color: "var(--ink-dim)" }}>Screener risk filter</label>
          <select value={profile} onChange={(e) => setProfile(e.target.value as RiskProfile)}
            style={{ width: "100%", margin: "4px 0 12px" }}>
            <option value="conservative">Conservative</option>
            <option value="balanced">Balanced</option>
            <option value="aggressive">Aggressive</option>
          </select>

          <NumRow label="DTE min" value={dteMin} onChange={setDteMin} />
          <NumRow label="DTE max" value={dteMax} onChange={setDteMax} />
          <NumRow label="Delta min" value={deltaMin} step={0.01} onChange={setDeltaMin} />
          <NumRow label="Delta max" value={deltaMax} step={0.01} onChange={setDeltaMax} />
          <NumRow label="Min OI" value={minOi} step={100} onChange={setMinOi} />
          <NumRow label="Excl. earnings (d)" value={excludeEarnings} onChange={setExcludeEarnings} />
          <NumRow label="Min WQS" value={minWqs} onChange={setMinWqs} />

          <button onClick={runScreen} disabled={loading}
            style={{ width: "100%", marginTop: 12 }}>
            {loading ? "Scanning…" : "Run screen"}
          </button>
        </div>

        {/* Results */}
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--ink-dim)", marginBottom: 8 }}>
            <span>{data ? `${data.candidates.length} candidates · sorted by Wheel Quality` : "—"}</span>
            {data && <span>as of {new Date(data.as_of).toLocaleTimeString()}</span>}
          </div>

          {error && (
            <div style={{ color: "var(--red)", fontFamily: "var(--mono)", fontSize: 12, padding: 12 }}>
              {error}
            </div>
          )}

          {data && data.candidates.length === 0 && !error && (
            <div style={{ border: "1px dashed var(--line-dim)", borderRadius: 6, padding: 24, textAlign: "center", color: "var(--ink-dim)", fontSize: 13 }}>
              No candidates yet. Options-chain data source is being wired — the
              scoring pipeline and risk gates are live.
            </div>
          )}

          {data?.candidates.map((c) => (
            <CandidateCard key={`${c.ticker}-${c.put_strike}`} c={c}
              expanded={expanded === `${c.ticker}-${c.put_strike}`}
              onToggle={() => setExpanded(expanded === `${c.ticker}-${c.put_strike}` ? null : `${c.ticker}-${c.put_strike}`)} />
          ))}
        </div>
      </div>
    </div>
  );
}

function NumRow({ label, value, step = 1, onChange }: {
  label: string; value: number; step?: number; onChange: (n: number) => void;
}) {
  return (
    <div style={{ marginBottom: 8 }}>
      <label style={{ fontSize: 12, color: "var(--ink-dim)", display: "block", marginBottom: 3 }}>{label}</label>
      <input type="number" value={value} step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ width: "100%" }} />
    </div>
  );
}

function CandidateCard({ c, expanded, onToggle }: {
  c: CspCandidate; expanded: boolean; onToggle: () => void;
}) {
  const elig = c.eligibility;
  const blocked = elig.status === "blocked";
  const accent = statusTone(elig.status);

  return (
    <div className="instrument-card" style={{
      border: `1px solid ${elig.status === "eligible" ? "var(--cyan-dim)" : "var(--line-dim)"}`,
      borderLeft: `3px solid ${accent}`,
      padding: 12, marginBottom: 8,
      opacity: blocked ? 0.9 : 1,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", cursor: "pointer" }}
        onClick={onToggle} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter") onToggle(); }}
        aria-expanded={expanded}>
        <div>
          <span style={{ fontFamily: "var(--mono)", fontSize: 15 }}>{c.ticker}</span>
          <span style={{ color: "var(--ink-dim)", fontSize: 12, marginLeft: 8 }}>
            ${c.stock_price} · put ${c.put_strike} · {c.dte} DTE
          </span>
        </div>
        <RiskBadge tone={accent}>
          {blocked ? "BLOCKED" : `WQS ${c.wheel_quality_score}`}
        </RiskBadge>
      </div>

      <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--ink-dim)", marginTop: 8, flexWrap: "wrap", fontFamily: "var(--mono)" }}>
        <span>Δ {c.delta}</span>
        <span>Prem ${c.premium}</span>
        <span>Collat {fmtUsd(c.collateral_required)}</span>
        <span>B/E ${c.breakeven}</span>
      </div>

      <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        <RiskBadge tone={levelTone(c.assignment_risk.level)}>{c.assignment_risk.label}</RiskBadge>
        <RiskBadge tone={levelTone(c.macro_event_risk.level)}>{c.macro_event_risk.label}</RiskBadge>
        {c.earnings_warning && <RiskBadge tone={levelTone(c.earnings_warning.level)}>{c.earnings_warning.label}</RiskBadge>}
        <RiskBadge tone="var(--ink-dim)">Overlap {c.portfolio_overlap_pct}%</RiskBadge>
      </div>

      {/* Blocked reasons are never hidden */}
      {blocked && (
        <div style={{ marginTop: 8, fontSize: 12, color: "var(--red)", fontFamily: "var(--mono)" }}>
          {elig.blocked_reasons.map((r) => <div key={r}>■ {r}</div>)}
          {elig.unblock_condition && (
            <div style={{ color: "var(--ink-dim)", marginTop: 4 }}>→ {elig.unblock_condition}</div>
          )}
        </div>
      )}

      {/* Detail drawer */}
      {expanded && !blocked && (
        <div style={{ marginTop: 10, borderTop: "1px solid var(--line-dim)", paddingTop: 10 }}>
          <div className="kicker" style={{ marginBottom: 6 }}>Why it ranked</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, fontFamily: "var(--mono)", fontSize: 11 }}>
            {Object.entries(c.wqs_contributions)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "var(--ink-dim)" }}>{k.replace(/_/g, " ")}</span>
                  <span>+{v}</span>
                </div>
              ))}
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--ink-dim)" }}>
            {c.assignment_risk.detail} · {c.macro_event_risk.detail}
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
            <button style={{ fontSize: 12 }}>Add to watchlist</button>
            <button style={{ fontSize: 12 }}>Simulate in paper</button>
            {elig.modes_allowed.includes("copilot") && (
              <button style={{ fontSize: 12, color: "var(--cyan)", borderColor: "var(--cyan-dim)" }}>
                Create Copilot review
              </button>
            )}
            <span style={{ fontSize: 11, color: "var(--ink-dim)", alignSelf: "center" }}>
              Automation: {elig.modes_allowed.join(", ") || "none"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
